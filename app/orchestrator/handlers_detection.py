"""Epic 2 stage handlers: detection → confirmation → tracking ticket (FR-01…FR-04).

These wire the Epic 2 agents into the orchestrator's stage machine. Each handler returns a stage
outcome; the orchestrator persists it (AD-2). The handlers hold no state — everything comes from the
injected `Epic2Context`, so the layering rule (AD-1) holds and tests inject fakes.

Two of the three stages can *branch off the happy path* to the rename-request wait:

* `detected` — a title mismatch (FR-02a) files the rename task and **self-parks at `detected`** with
  `pending_gate = UPLOADING_PM_RENAME`. The run has not advanced past detection; it is waiting for a
  human to fix the input. When the corrected page is re-uploaded it arrives as a new page version
  (EH-04), the orchestrator re-enters at `detected`, and detection re-runs — now passing.
* `confirmed` — a Classifier REJECT (EH-07) self-parks at `confirmed` the same way; the re-upload
  re-runs classification on the corrected content.

Self-parking at the current stage (rather than inventing an `awaiting_rename` stage or overloading
`awaiting_clarification`, which is the FR-08 PM loop) keeps the §9 stage set intact while giving the
wait a distinct, observable marker via `pending_gate`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.agents.classifier.agent import ClassifierAgent
from app.agents.detection import DetectionAgent, DetectionResult, DetectionVerdict
from app.agents.identity import IdentityResolver
from app.agents.llm import CallMetadata
from app.agents.ticket_manager import TicketManager
from app.config.schema import TenantConfig
from app.domain.events import ConfluencePageEvent
from app.domain.stage import PendingGate, Stage
from app.domain.state import PrdState
from app.orchestrator.stages import Advance, Park, StageOutcome, Stay


class Epic2Context(Protocol):
    """What the Epic 2 handlers need. Structural, so tests pass a simple stand-in."""

    @property
    def prd_id(self) -> str: ...
    @property
    def correlation_id(self) -> str: ...
    @property
    def tenant(self) -> TenantConfig: ...
    @property
    def detection(self) -> DetectionAgent: ...
    @property
    def classifier(self) -> ClassifierAgent: ...
    @property
    def ticket_manager(self) -> TicketManager: ...
    @property
    def identity(self) -> IdentityResolver: ...

    async def get_page_event(self) -> ConfluencePageEvent: ...
    def page_url(self) -> str: ...
    async def page_markdown(self) -> str: ...


@dataclass(frozen=True, slots=True)
class DetectionHandlers:
    """The `detected`, `confirmed`, and `prd_ticket_done` stage handlers."""

    # -- stage: detected (FR-01, FR-02, FR-02a) ------------------------------------------------

    async def on_detected(self, context: Epic2Context, state: PrdState) -> StageOutcome:
        event = await context.get_page_event()
        result: DetectionResult = await context.detection.evaluate(event, context.tenant)

        if result.verdict in (
            DetectionVerdict.NOT_IN_SOURCE_FOLDER,
            DetectionVerdict.AGENT_GENERATED,
        ):
            # Should not normally reach a stage handler — ingress/detection drop these earlier — but
            # if one does, do nothing rather than start a flow for a page we must not process.
            return Stay(reason=result.reason)

        if result.needs_rename_request:
            return await self._file_rename_request(
                context,
                state,
                result.reason,
                page_title=event.title,
                creator_account_id=event.creator_account_id,
            )

        return Advance(
            to_stage=Stage.CONFIRMED, recorded={"prd_title": event.title}, note=result.reason
        )

    # -- stage: confirmed (FR-03, EH-07) -------------------------------------------------------

    async def on_confirmed(self, context: Epic2Context, state: PrdState) -> StageOutcome:
        classification = await context.classifier.classify(
            title=state.prd_title or "",
            body_markdown=await context.page_markdown(),
            metadata=self._llm_metadata(context, state, "classifier"),
        )
        if classification.accepted:
            return Advance(to_stage=Stage.PRD_TICKET_DONE, note=classification.reason)

        # EH-07 — a REJECT is handled exactly like a title mismatch: ask a human, do not guess.
        event = await context.get_page_event()
        return await self._file_rename_request(
            context,
            state,
            f"classifier rejected: {classification.reason}",
            page_title=state.prd_title or event.title,
            creator_account_id=event.creator_account_id,
        )

    # -- stage: prd_ticket_done (FR-04) --------------------------------------------------------

    async def on_prd_ticket_done(self, context: Epic2Context, state: PrdState) -> StageOutcome:
        prd_name = state.prd_title or (await context.get_page_event()).title
        result = await context.ticket_manager.locate_or_create_tracking_ticket(
            tenant=context.tenant,
            prd_id=context.prd_id,
            prd_name=prd_name,
            prd_url=context.page_url(),
        )
        # The tracking ticket is now Done (or was already). Advance to drafting (Epic 3). The
        # recorded key lets a resume adopt this ticket instead of creating a second one (AD-11).
        return Advance(
            to_stage=Stage.DRAFTED,
            recorded={"prd_tracking_ticket_key": result.issue.key, "prd_title": prd_name},
            note=f"tracking ticket {result.issue.key} "
            + ("created" if result.created else "reused"),
        )

    # -- shared: file the FR-02a rename request and park -------------------------------------

    async def _file_rename_request(
        self,
        context: Epic2Context,
        state: PrdState,
        reason: str,
        *,
        page_title: str,
        creator_account_id: str | None,
    ) -> StageOutcome:
        # A rename request may already exist from a prior attempt (AD-11) — do not file a second.
        if state.rename_request_ticket_key:
            return Park(
                to_stage=state.stage,
                gate=PendingGate.UPLOADING_PM_RENAME,
                note=f"rename request {state.rename_request_ticket_key} already open",
            )

        resolution = await context.identity.resolve_uploading_pm(
            confluence_account_id=creator_account_id,
            confluence_email=None,  # the page-created payload rarely carries an email
            tenant=context.tenant,
        )
        ticket = await context.ticket_manager.create_rename_request(
            tenant=context.tenant,
            prd_id=context.prd_id,
            page_title=page_title,
            page_url=context.page_url(),
            assignee_account_id=resolution.account_id,
            reason=reason,
        )
        return Park(
            to_stage=state.stage,
            gate=PendingGate.UPLOADING_PM_RENAME,
            recorded={"rename_request_ticket_key": ticket.key},
            note=f"filed rename request {ticket.key} ({reason})",
        )

    @staticmethod
    def _llm_metadata(context: Epic2Context, state: PrdState, role: str) -> CallMetadata:
        return CallMetadata(
            correlation_id=context.correlation_id,
            prd_id=context.prd_id,
            agent_role=role,
            review_round=state.review_round,
            tenant=context.tenant.project_id,
        )
