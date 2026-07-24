"""Epic 3 stage handler: draft → publish → Review ticket → park at awaiting_review (FR-05…FR-07).

This fills `Stage.DRAFTED`'s handler. It is a single stage that does the whole authoring-and-publish
sequence and then parks on the PM, because none of its steps waits on an external event — they all
run within the one invocation, and only the *review* waits (AD-15).

The sequence, each step guarded so a resume converges (AD-11):
  1. Author drafts + self-critiques the UserDoc (FR-05).
  2. Publisher creates/adopts the draft page in the draft folder, stamped as agent output (FR-06).
  3. Ticket manager creates/adopts the Review ticket assigned to the PM (FR-06).
  4. Post the framed review-request comment (FR-07).
  5. Park at `awaiting_review` on the PM gate (AD-15) — the run now waits, indefinitely, for a human.

Idempotency: steps 2 and 3 reuse `userdoc_page_id` / `review_ticket_key` if already recorded, so a
re-run after a crash mid-sequence adopts what exists rather than creating duplicates. The draft is
authored again on a bare re-run (LLM output is not persisted mid-stage), but it *updates* the same
page rather than making a new one — cost, not correctness, and the redraft loop is where iteration
belongs anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.agents.author.agent import AuthorAgent
from app.agents.llm import CallMetadata
from app.agents.publisher import Publisher
from app.agents.review_request import build_review_request
from app.agents.ticket_manager import TicketManager
from app.config.schema import TenantConfig
from app.domain.events import ConfluencePageEvent
from app.domain.stage import PendingGate, Stage
from app.domain.state import PrdState
from app.orchestrator.stages import Park, StageOutcome


class Epic3Context(Protocol):
    @property
    def prd_id(self) -> str: ...
    @property
    def correlation_id(self) -> str: ...
    @property
    def tenant(self) -> TenantConfig: ...
    @property
    def page_event(self) -> ConfluencePageEvent: ...
    @property
    def author(self) -> AuthorAgent: ...
    @property
    def publisher(self) -> Publisher: ...
    @property
    def ticket_manager(self) -> TicketManager: ...

    def page_markdown(self) -> str: ...
    def draft_page_url(self, page_id: str) -> str: ...
    def confluence_space_id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class AuthoringHandlers:
    async def on_drafted(self, context: Epic3Context, state: PrdState) -> StageOutcome:
        # 1. FR-05 — draft + one self-critique pass.
        draft = await context.author.draft(
            prd_title=context.page_event.title,
            prd_markdown=context.page_markdown(),
            metadata=self._metadata(context, state, "author"),
        )

        # 2. FR-06 — publish the draft page (create or adopt), stamped as agent output.
        published = await context.publisher.publish_draft(
            tenant=context.tenant,
            prd_id=context.prd_id,
            title=draft.title,
            markdown=draft.markdown,
            space_id=context.confluence_space_id(),
            existing_page_id=state.userdoc_page_id,
        )
        page_id = published.page.id
        page_url = context.draft_page_url(page_id)

        # 3. FR-06 — create (or adopt) the Review ticket assigned to the PM. Find-or-create by the
        #    correlation marker so a crash after create-before-persist does not duplicate it (AD-11).
        review_ticket_key = state.review_ticket_key
        if review_ticket_key is None:
            orphan = await context.ticket_manager.find_ticket_by_marker(
                context.tenant.jira_review_project_key, context.prd_id
            )
            ticket = orphan or await context.ticket_manager.create_review_ticket(
                tenant=context.tenant,
                prd_id=context.prd_id,
                userdoc_title=draft.title,
                draft_page_url=page_url,
            )
            review_ticket_key = ticket.key

        # 4. FR-07 — the framed review-request comment.
        await context.ticket_manager.comment(
            review_ticket_key,
            build_review_request(
                pm_account_id=context.tenant.pm_account_id, draft_page_url=page_url
            ),
        )

        # 5. AD-15 — park on the PM. No timeout; the run waits for a human.
        return Park(
            to_stage=Stage.AWAITING_REVIEW,
            gate=PendingGate.PM_REVIEW,
            recorded={"userdoc_page_id": page_id, "review_ticket_key": review_ticket_key},
            note=f"draft published ({page_id}); review requested on {review_ticket_key}",
        )

    @staticmethod
    def _metadata(context: Epic3Context, state: PrdState, role: str) -> CallMetadata:
        return CallMetadata(
            correlation_id=context.correlation_id,
            prd_id=context.prd_id,
            agent_role=role,
            review_round=state.review_round,
            tenant=context.tenant.project_id,
        )
