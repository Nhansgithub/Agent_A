"""Epic 4 — the review loop: the `revising` handler and the review-loop context contract.

The review loop is **webhook-driven**, not graph-driven. A run parks at `awaiting_review` and does
nothing until the PM acts. Two things can happen:

* the PM **leaves a comment** → the orchestrator interprets it (`FeedbackInterpreter`), routes on the
  typed decision (`route_feedback`, deterministic — AD-16), and either revises, asks for structure
  confirmation, or asks a clarifying question;
* the PM **moves the Review ticket to Done** → the orchestrator detects PASS and advances to `passed`.

Only the `revising` stage is an *advancing* stage the graph runs. Everything else in the loop is a
park that waits on the human (AD-15). This module holds `on_revising`; the event-application methods
live on the `Orchestrator` (they must run under its serial lock).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.agents.author.agent import AuthorAgent
from app.agents.feedback_interpreter.agent import FeedbackInterpreter
from app.agents.llm import CallMetadata
from app.agents.publisher import Publisher
from app.agents.review_request import build_change_summary
from app.agents.ticket_manager import TicketManager
from app.config.schema import TenantConfig
from app.domain.feedback import FeedbackDecision
from app.domain.stage import PendingGate, Stage
from app.domain.state import PrdState
from app.orchestrator.stages import Park, StageOutcome


class ReviewContext(Protocol):
    """What the review loop needs. The production context implements it; tests fake it."""

    @property
    def prd_id(self) -> str: ...
    @property
    def correlation_id(self) -> str: ...
    @property
    def tenant(self) -> TenantConfig: ...
    @property
    def author(self) -> AuthorAgent: ...
    @property
    def publisher(self) -> Publisher: ...
    @property
    def ticket_manager(self) -> TicketManager: ...
    @property
    def feedback_interpreter(self) -> FeedbackInterpreter: ...

    def page_markdown(self) -> str: ...
    async def current_draft_markdown(self) -> str: ...
    def draft_page_url(self, page_id: str) -> str: ...

    async def interpret_comment(
        self, *, comment_text: str, awaiting_reply: bool, metadata: CallMetadata
    ) -> FeedbackDecision: ...

    async def post_comment(self, issue_key: str, body: dict) -> None: ...


@dataclass(frozen=True, slots=True)
class ReviewHandlers:
    async def on_revising(self, context: ReviewContext, state: PrdState) -> StageOutcome:
        """Apply the confirmed feedback, update the draft, summarize, re-request (FR-11)."""
        feedback = (state.pending_feedback or "").strip()
        if not feedback:
            # Nothing to apply — a revising stage with no stored feedback would be a bug. Park back
            # rather than loop or call the LLM for nothing.
            return Park(
                to_stage=Stage.AWAITING_REVIEW,
                gate=PendingGate.PM_REVIEW,
                note="no pending feedback to apply",
            )

        metadata = CallMetadata(
            correlation_id=context.correlation_id,
            prd_id=context.prd_id,
            agent_role="author",
            review_round=state.review_round + 1,
            tenant=context.tenant.project_id,
        )
        before = await context.current_draft_markdown()

        draft = await context.author.revise(
            current_markdown=before, structured_feedback=feedback, metadata=metadata
        )
        await context.publisher.update_draft(
            page_id=state.userdoc_page_id or "", title=draft.title, markdown=draft.markdown
        )
        summary = await context.author.summarize_changes(
            before=before, after=draft.markdown, feedback=feedback, metadata=metadata
        )
        await context.post_comment(
            state.review_ticket_key or "",
            build_change_summary(
                pm_account_id=context.tenant.pm_account_id,
                summary=summary,
                draft_page_url=context.draft_page_url(state.userdoc_page_id or ""),
            ),
        )

        # FR-11 / NFR-09 — one increment per applied round; the guardrail is observability, not a cap.
        # Clear the pending feedback so a resume of a *future* round does not re-apply this one.
        return Park(
            to_stage=Stage.AWAITING_REVIEW,
            gate=PendingGate.PM_REVIEW,
            recorded={"review_round": state.review_round + 1, "pending_feedback": None},
            note=f"revised (round {state.review_round + 1}); re-requested review",
        )
