"""The orchestrator — the only component that advances `stage` (AD-2, AD-11, AD-5).

One invocation does exactly five things (AD-11):

1. **load** the state record,
2. **re-enter** the graph at the recorded `stage` (`thread_id = prd_id`),
3. **run** the stages that can advance without a new external event,
4. **persist** the new stage and any ids that stage recorded, in one transaction,
5. **stop.**

Nothing about a run is held in memory between invocations. A run parked for three days and a run
resumed after a restart take exactly the same path, because the only thing carried across is the
state record on disk.

**Serial by design** (AD-5, NFR-06). One PRD is processed at a time. That is a concurrency decision
*and* a memory-safety measure: a large PRD payload is the main RAM consumer on the 1 GB box (AD-21),
and only one is ever resident. The serializer is a single lock — deliberately the *only* cross-PRD
object — so removing it later yields parallelism rather than a redesign.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from app.agents.llm import CallMetadata
from app.domain.errors import AgentError
from app.domain.feedback import DeletionDecision, FeedbackRoute
from app.domain.stage import (
    PARKED_STAGES,
    TERMINAL_STAGES,
    PendingGate,
    QueueStatus,
    Stage,
    gate_for_stage,
)
from app.domain.state import PrdState
from app.orchestrator.feedback_routing import FeedbackAction, route_feedback
from app.orchestrator.graph import RECURSION_LIMIT, StageStep, build_graph
from app.orchestrator.stages import (
    Advance,
    HandlerRegistry,
    Park,
    RunContext,
    Stay,
)

#: The review-loop waits a PM comment may legitimately act on (FR-09/10/12).
_REVIEW_STAGES = frozenset(
    {Stage.AWAITING_REVIEW, Stage.AWAITING_STRUCTURE_CONFIRM, Stage.AWAITING_CLARIFICATION}
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunResult:
    """What one invocation did. Returned for logging and for tests to assert on."""

    prd_id: str
    final_stage: Stage
    advanced: tuple[Stage, ...] = ()
    stopped_reason: str = ""
    error: AgentError | None = None

    @property
    def progressed(self) -> bool:
        return bool(self.advanced)


@dataclass
class _Context:
    """The concrete `RunContext` handed to stage handlers."""

    prd_id: str
    correlation_id: str
    tenant: object = None
    repository: object = None
    adapters: dict[str, object] = field(default_factory=dict)


class Orchestrator:
    """Advances one PRD's flow as far as it can go, then stops."""

    __slots__ = ("_context_factory", "_handlers", "_lock", "_repository")

    def __init__(
        self,
        repository,
        handlers: HandlerRegistry,
        *,
        context_factory=None,
    ) -> None:
        """
        Args:
            repository: the `Repository` facade — the single durable store (AD-2).
            handlers: stage → handler. Injected rather than global, so tests supply fakes and no
                cross-PRD mutable singleton is held (AD-5).
            context_factory: builds the `RunContext` for a run (tenant config + adapters). Injected
                so handlers never construct a transport themselves (AD-1).
        """
        self._repository = repository
        self._handlers = handlers
        self._context_factory = context_factory or self._default_context
        # AD-5: the ONE cross-PRD object in the system, and only a serializer. Nothing about a
        # specific run lives outside its state record.
        self._lock = asyncio.Lock()

    @staticmethod
    def _default_context(state: PrdState) -> RunContext:
        return _Context(prd_id=state.prd_id, correlation_id=state.correlation_id)

    async def advance(self, prd_id: str) -> RunResult:
        """Run the flow for one PRD as far as it can go without a new external event.

        Serialized: concurrent calls queue rather than interleave (AD-5, EH-05).
        """
        async with self._lock:
            return await self._advance_unlocked(prd_id)

    # -- webhook-driven re-entry (Epic 4/5) ----------------------------------------------------

    async def apply_pm_comment(self, prd_id: str, *, comment_text: str) -> RunResult:
        """A PM comment arrived on the Review ticket (FR-09/10/08).

        Interprets the comment, routes on the typed decision **deterministically** (AD-16), and acts:
        revise, ask for structure confirmation, or ask a clarifying question. Runs under the serial
        lock so it never interleaves with `advance()` (AD-5).
        """
        async with self._lock:
            state = self._repository.state.require(prd_id)
            if state.stage not in _REVIEW_STAGES:
                # EH-06 — feedback after Done (or in any non-review stage) is not processed.
                return RunResult(
                    prd_id,
                    state.stage,
                    stopped_reason=f"comment ignored: run is at {state.stage.value}, not in review",
                )

            context = self._context_factory(state)
            awaiting_reply = state.stage in {
                Stage.AWAITING_STRUCTURE_CONFIRM,
                Stage.AWAITING_CLARIFICATION,
            }

            try:
                decision = await context.interpret_comment(
                    comment_text=comment_text,
                    awaiting_reply=awaiting_reply,
                    metadata=self._llm_metadata(state, "feedback_interpreter"),
                )
                outcome = route_feedback(decision, current_stage=state.stage)
                await self._act_on_feedback(prd_id, state, context, decision, outcome)
            except AgentError as error:
                return self._to_error(prd_id, state, error)

            # If we routed into `revising`, run it now (still holding the lock). Otherwise the run
            # is parked (structure-confirm / clarification) and this is a no-op.
            return await self._advance_unlocked(prd_id)

    async def apply_gate_done(self, prd_id: str, *, issue_key: str) -> RunResult:
        """A human moved a gate ticket to a Done-category status (FR-12 / FR-14, AD-15).

        The agent only *detects* this — it never transitions a gate ticket itself. Advances the
        internal stage past the gate, matched to the specific ticket so an unrelated Done is ignored.
        """
        async with self._lock:
            state = self._repository.state.require(prd_id)

            if state.stage is Stage.AWAITING_REVIEW and issue_key == state.review_ticket_key:
                self._repository.state.advance_stage(prd_id, Stage.PASSED)  # FR-12 PASS
                return await self._advance_unlocked(prd_id)

            if (
                state.stage is Stage.AWAITING_PUBLISH_APPROVAL
                and issue_key == state.publishing_ticket_key
            ):
                self._repository.state.advance_stage(prd_id, Stage.PUBLISHING)  # FR-14 approve
                return await self._advance_unlocked(prd_id)

            # A Done on the wrong ticket, or in the wrong stage — ignore (EH-06/EH-09 park).
            return RunResult(
                prd_id,
                state.stage,
                stopped_reason=f"gate Done on {issue_key} ignored at stage {state.stage.value}",
            )

    async def apply_admin_resume(self, prd_id: str) -> RunResult:
        """EH-02 — an admin asked to resume an errored run; re-run from the failed stage.

        Re-enters at `last_good_checkpoint` (the failed stage), never the whole flow. Only acts on a
        run that is actually in `error`; a resume on a healthy run is a no-op. Duplicate-delivery
        protection is the webhook layer's dedupe guard (AD-9), so this need not de-duplicate itself.
        """
        async with self._lock:
            state = self._repository.state.require(prd_id)
            if state.stage is not Stage.ERROR:
                return RunResult(
                    prd_id,
                    state.stage,
                    stopped_reason=f"resume ignored: run is at {state.stage.value}, not error",
                )

            checkpoint = state.last_good_checkpoint or Stage.DETECTED
            # Return to the failed stage; the next _advance_unlocked runs it. Clears the error and
            # the liveness alert (advance_stage does this for any non-error target).
            self._repository.state.advance_stage(
                prd_id, checkpoint, queue_status=QueueStatus.IN_PROGRESS
            )
            return await self._advance_unlocked(prd_id)

    async def apply_draft_deleted(self, prd_id: str, deleted_page_id: str) -> RunResult:
        """FR-16 — a human deleted the run's UserDoc draft page. **Ask the PM; do NOT auto-recover.**

        The agent never silently restores a page a human deleted: it might have been deliberate. It
        posts a question on the Review ticket (@-mentioning the PM) asking whether the deletion was
        intentional, and parks awaiting the answer (`pending_gate = PM_DELETION_DECISION`). The reply
        is handled by :meth:`apply_deletion_decision` — restore only on a confirmed mistake.

        Idempotent: if a decision is already pending for this page, or the page is not actually
        trashed (a stale/duplicate event), it is a no-op. Under the serial lock (AD-5).
        """
        async with self._lock:
            state = self._repository.state.require(prd_id)
            if not state.userdoc_page_id or state.userdoc_page_id != deleted_page_id:
                # Not this run's current draft (already recreated, or an unrelated page).
                return RunResult(prd_id, state.stage, stopped_reason="not the run's current draft")
            if state.pending_deletion_page_id == deleted_page_id:
                return RunResult(
                    prd_id, state.stage, stopped_reason="deletion decision already pending"
                )

            context = self._context_factory(state)
            # Confirm it is really trashed before bothering the PM (guards a stale/duplicate event).
            try:
                status = await context.draft_status(deleted_page_id)
            except AgentError as error:
                return self._to_error(prd_id, state, error)
            if status == "current":
                return RunResult(prd_id, state.stage, stopped_reason="draft is not deleted")

            if state.review_ticket_key:
                await context.post_comment(
                    state.review_ticket_key, self._deletion_question_body(context)
                )
            self._repository.state.update_fields(
                prd_id,
                pending_deletion_page_id=deleted_page_id,
                pending_gate=PendingGate.PM_DELETION_DECISION,
                queue_status=QueueStatus.IDLE,
            )
            logger.info(
                "draft %s deleted for run %s; asked the PM (FR-16)", deleted_page_id, prd_id
            )
            return RunResult(prd_id, state.stage, stopped_reason="asked the PM about the deletion")

    async def apply_deletion_decision(self, prd_id: str, *, comment_text: str) -> RunResult:
        """FR-16 — the PM answered whether the draft deletion was intentional.

        Restore only on a confirmed **mistake**; leave it on **intentional**; **re-ask** on an
        ambiguous reply (never guess). Only acts on a run that is actually awaiting this decision.
        """
        async with self._lock:
            state = self._repository.state.require(prd_id)
            if not state.pending_deletion_page_id:
                return RunResult(prd_id, state.stage, stopped_reason="no deletion decision pending")

            context = self._context_factory(state)
            try:
                decision = await context.classify_deletion_reply(
                    comment_text, self._llm_metadata(state, "feedback_interpreter")
                )
                if decision is DeletionDecision.RESTORE:
                    return await self._restore_after_confirmation(prd_id, state, context)
                if decision is DeletionDecision.LEAVE:
                    if state.review_ticket_key:
                        await context.post_comment(
                            state.review_ticket_key, self._deletion_left_body(context)
                        )
                    self._repository.state.update_fields(
                        prd_id,
                        pending_deletion_page_id=None,
                        pending_gate=gate_for_stage(state.stage),
                    )
                    return RunResult(
                        prd_id, state.stage, stopped_reason="PM: deletion was intentional"
                    )
                # UNCLEAR — do not guess; ask again and stay parked.
                if state.review_ticket_key:
                    await context.post_comment(
                        state.review_ticket_key, self._deletion_reask_body(context)
                    )
                return RunResult(
                    prd_id, state.stage, stopped_reason="deletion reply unclear; re-asked"
                )
            except AgentError as error:
                return self._to_error(prd_id, state, error)

    async def _restore_after_confirmation(self, prd_id, state, context) -> RunResult:
        """The PM confirmed the deletion was a mistake — recover the draft now (FR-16)."""
        recovery = await context.recover_draft()
        if recovery.action == "recreated" and recovery.page_id:
            self._repository.state.update_fields(prd_id, userdoc_page_id=recovery.page_id)
            state = self._repository.state.require(prd_id)

        if state.review_ticket_key:
            await context.post_comment(
                state.review_ticket_key, self._draft_recovered_body(context, recovery, state)
            )
        # Clear the pending-deletion wait and restore the stage's natural gate.
        self._repository.state.update_fields(
            prd_id, pending_deletion_page_id=None, pending_gate=gate_for_stage(state.stage)
        )

        # If the run had errored because the page was gone, self-recover now that it is back.
        if state.stage is Stage.ERROR and recovery.action in ("restored", "recreated"):
            checkpoint = state.last_good_checkpoint or Stage.DETECTED
            self._repository.state.advance_stage(
                prd_id, checkpoint, queue_status=QueueStatus.IN_PROGRESS
            )
            return await self._advance_unlocked(prd_id)
        return RunResult(
            prd_id, state.stage, stopped_reason=f"PM confirmed mistake → {recovery.action}"
        )

    @staticmethod
    def _deletion_question_body(context) -> dict:
        from app.domain import adf

        return adf.doc(
            adf.paragraph(
                adf.mention(context.tenant.pm_account_id),
                adf.text(" I noticed the UserDoc draft page for this review was just "),
                adf.strong("deleted (moved to trash)"),
                adf.text(". Was that intentional? Reply "),
                adf.strong("“restore”"),
                adf.text(
                    " (or tell me it was a mistake) and I'll bring it back exactly as it was; "
                ),
                adf.text("reply "),
                adf.strong("“leave it”"),
                adf.text(" if you meant to delete it. I won't touch the page until you say."),
            )
        )

    @staticmethod
    def _deletion_reask_body(context) -> dict:
        from app.domain import adf

        return adf.doc(
            adf.paragraph(
                adf.mention(context.tenant.pm_account_id),
                adf.text(
                    " sorry, I couldn't tell from that whether to restore the deleted draft. Please "
                    "reply "
                ),
                adf.strong("“restore”"),
                adf.text(" to bring it back, or "),
                adf.strong("“leave it”"),
                adf.text(" to keep it deleted."),
            )
        )

    @staticmethod
    def _deletion_left_body(context) -> dict:
        from app.domain import adf

        return adf.doc(
            adf.paragraph(
                adf.mention(context.tenant.pm_account_id),
                adf.text(
                    " understood — I'll leave the draft deleted and won't restore it. If you change "
                    "your mind, say so here and I'll bring it back."
                ),
            )
        )

    @staticmethod
    def _draft_recovered_body(context, recovery, state) -> dict:
        from app.domain import adf

        pm = adf.mention(context.tenant.pm_account_id)
        url = context.draft_page_url(recovery.page_id or state.userdoc_page_id or "")
        if recovery.action == "restored":
            return adf.doc(
                adf.paragraph(
                    pm,
                    adf.text(" done — I "),
                    adf.strong("restored the draft from the trash"),
                    adf.text(", no content lost: "),
                    adf.link("open the draft", url),
                    adf.text(". The review continues from where it was."),
                )
            )
        if recovery.action == "recreated":
            return adf.doc(
                adf.paragraph(
                    pm,
                    adf.text(" I couldn't restore the original, so I "),
                    adf.strong("recreated it with its latest content"),
                    adf.text(" here: "),
                    adf.link("open the new draft", url),
                    adf.text(" (the previous link is now dead). The review continues."),
                )
            )
        return adf.doc(  # unrecoverable
            adf.paragraph(
                pm,
                adf.text(" I tried to restore the draft but "),
                adf.strong("could not recover it"),
                adf.text(
                    " — it may have been permanently purged from the trash. Please restore it from "
                    "the trash if you can, or reply here and I'll re-draft it from the PRD."
                ),
            )
        )

    async def _act_on_feedback(self, prd_id, state, context, decision, outcome) -> None:
        """Persist the routing outcome. The *decision* is the LLM's; the *routing* is deterministic."""
        if outcome.action is FeedbackAction.APPLY_FEEDBACK:
            feedback = decision.structured_feedback.strip() or (state.pending_feedback or "")
            self._repository.state.advance_stage(
                prd_id,
                Stage.REVISING,
                pending_feedback=feedback,
                queue_status=QueueStatus.IN_PROGRESS,
            )
        elif outcome.action is FeedbackAction.ASK_STRUCTURE_CONFIRM:
            await context.post_comment(
                state.review_ticket_key or "",
                self._structure_confirm_body(context, decision),
            )
            self._repository.state.advance_stage(
                prd_id,
                Stage.AWAITING_STRUCTURE_CONFIRM,
                pending_gate=PendingGate.PM_STRUCTURE_CONFIRM,
                queue_status=QueueStatus.IDLE,
                pending_feedback=decision.structured_feedback.strip(),
            )
        elif outcome.action is FeedbackAction.ASK_CLARIFICATION:
            await context.post_comment(
                state.review_ticket_key or "", self._clarification_body(context, decision)
            )
            self._repository.state.advance_stage(
                prd_id,
                Stage.AWAITING_CLARIFICATION,
                pending_gate=PendingGate.PM_CLARIFICATION,
                queue_status=QueueStatus.IDLE,
            )
        elif outcome.action is FeedbackAction.IGNORE and state.stage is not outcome.target_stage:
            # PM did not confirm the restatement (a bare "no"). Don't dead-end silently: acknowledge
            # on the ticket and ask what to change instead, so the loop stays a conversation. The
            # next comment is then interpreted fresh (it will carry this exchange as transcript).
            # A "no, I meant X" is NOT this branch — the interpreter, now conversation-aware, routes
            # that to APPLY / CONFIRM_STRUCTURE with the correction already applied.
            if decision.route is FeedbackRoute.CONFIRMATION:
                await context.post_comment(
                    state.review_ticket_key or "", self._not_confirmed_body(context)
                )
            self._repository.state.advance_stage(
                prd_id,
                outcome.target_stage,
                pending_gate=outcome.gate,
                queue_status=QueueStatus.IDLE,
                pending_feedback=None,  # the rejected restatement is stale — don't carry it forward
            )

    @staticmethod
    def _structure_confirm_body(context, decision) -> dict:
        from app.domain import adf

        return adf.doc(
            adf.paragraph(
                adf.mention(context.tenant.pm_account_id),
                adf.text(
                    " you didn't use the feedback format, so I curated it like this — is this what "
                    "you mean? I won't change anything until you confirm."
                ),
            ),
            adf.code_block(decision.structured_feedback or "(no structured feedback)"),
            adf.paragraph(adf.text(decision.question or "Reply to confirm or correct.")),
        )

    @staticmethod
    def _clarification_body(context, decision) -> dict:
        from app.domain import adf

        return adf.doc(
            adf.paragraph(
                adf.mention(context.tenant.pm_account_id),
                adf.text(" before I revise, I need to check one thing:"),
            ),
            adf.paragraph(adf.text(decision.question or "Could you clarify?")),
        )

    @staticmethod
    def _not_confirmed_body(context) -> dict:
        from app.domain import adf

        return adf.doc(
            adf.paragraph(
                adf.mention(context.tenant.pm_account_id),
                adf.text(
                    " understood — I won't apply that. Tell me what you'd like changed instead. "
                    "The Section / Issue / Suggested change format is easiest for me, but plain "
                    "language is fine too — I'll restate it and check with you before editing."
                ),
            ),
        )

    def _llm_metadata(self, state: PrdState, role: str) -> CallMetadata:
        return CallMetadata(
            correlation_id=state.correlation_id,
            prd_id=state.prd_id,
            agent_role=role,
            review_round=state.review_round,
            tenant=state.project_id,
        )

    def _to_error(self, prd_id: str, state: PrdState, error: AgentError) -> RunResult:
        failed = self._repository.state.mark_error(prd_id, str(error))
        logger.error(
            "review-loop step failed: prd_id=%s stage=%s operation=%s correlation_id=%s",
            prd_id,
            failed.last_good_checkpoint,
            error.operation,
            state.correlation_id,
        )
        return RunResult(prd_id, Stage.ERROR, stopped_reason=str(error), error=error)

    async def _advance_unlocked(self, prd_id: str) -> RunResult:
        state = self._repository.state.require(prd_id)

        if state.stage in TERMINAL_STAGES:
            return RunResult(prd_id, state.stage, stopped_reason="run is already complete")
        if state.stage in PARKED_STAGES:
            return RunResult(
                prd_id,
                state.stage,
                stopped_reason=f"parked at {state.stage.value} awaiting {state.pending_gate.value}",
            )
        if state.stage is Stage.ERROR:
            # EH-02: an errored run resumes only on an explicit `@agent resume`, which re-enters at
            # `last_good_checkpoint`. It must never quietly restart on the next unrelated webhook.
            return RunResult(
                prd_id, state.stage, stopped_reason="parked in error awaiting `@agent resume`"
            )

        context = self._context_factory(state)
        advanced: list[Stage] = []
        stopped_reason = ""

        async def run_stage(stage: Stage) -> StageStep:
            nonlocal stopped_reason
            step = await self._run_one_stage(context, prd_id, stage)
            if step.next_stage is not stage:
                advanced.append(step.next_stage)
            if step.stop:
                stopped_reason = step.reason
            return step

        self._repository.state.update_fields(prd_id, queue_status=QueueStatus.IN_PROGRESS)

        try:
            graph = build_graph(run_stage)
            await graph.ainvoke(
                {"prd_id": prd_id, "stage": state.stage.value, "advanced": [], "stopped": ""},
                config={
                    # thread_id = prd_id (AD-11). Scoped to this invocation's InMemorySaver only.
                    "configurable": {"thread_id": prd_id},
                    "recursion_limit": RECURSION_LIMIT,
                },
            )
        except AgentError as error:
            # AD-19 / EH-01: move to `error` preserving `last_good_checkpoint`, so `@agent resume`
            # re-runs the failed stage rather than the whole flow.
            failed = self._repository.state.mark_error(prd_id, str(error))
            logger.error(
                "stage failed: prd_id=%s stage=%s operation=%s correlation_id=%s",
                prd_id,
                failed.last_good_checkpoint,
                error.operation,
                state.correlation_id,
            )
            return RunResult(
                prd_id,
                Stage.ERROR,
                tuple(advanced),
                stopped_reason=str(error),
                error=error,
            )

        final = self._repository.state.require(prd_id)
        if final.queue_status is QueueStatus.IN_PROGRESS:
            self._repository.state.update_fields(prd_id, queue_status=QueueStatus.IDLE)

        return RunResult(prd_id, final.stage, tuple(advanced), stopped_reason)

    async def _run_one_stage(self, context: RunContext, prd_id: str, stage: Stage) -> StageStep:
        """Execute one stage's handler and persist its outcome.

        The handler decides *what happened*; this method decides *what is written* — AD-2's split
        between agents returning results and the orchestrator persisting them.
        """
        handler = self._handlers.get(stage)
        if handler is None:
            # Stop rather than skip. A stage that silently advanced without doing its work would
            # push the run past a human gate — the exact failure AD-15 prevents.
            return StageStep(stage, stop=True, reason=f"no handler registered for {stage.value}")

        # Re-read inside the loop: an earlier stage in this same invocation may have recorded ids
        # (a ticket key, a page id) that this stage needs for its AD-11 idempotency guard.
        state = self._repository.state.require(prd_id)
        outcome = await handler(context, state)

        if isinstance(outcome, Stay):
            return StageStep(stage, stop=True, reason=outcome.reason or "nothing to do")

        if isinstance(outcome, Advance):
            self._repository.state.advance_stage(prd_id, outcome.to_stage, **outcome.recorded)
            stop = outcome.to_stage in TERMINAL_STAGES or outcome.to_stage not in _runnable_stages()
            return StageStep(outcome.to_stage, stop=stop, reason=outcome.note)

        if isinstance(outcome, Park):
            self._repository.state.advance_stage(
                prd_id,
                outcome.to_stage,
                pending_gate=outcome.gate,
                queue_status=QueueStatus.IDLE,
                **outcome.recorded,
            )
            return StageStep(
                outcome.to_stage,
                stop=True,
                reason=outcome.note or f"awaiting {outcome.gate.value}",
            )

        raise TypeError(f"stage handler for {stage.value} returned {type(outcome).__name__}")


def _runnable_stages() -> frozenset[Stage]:
    from app.orchestrator.stages import ADVANCING_STAGES

    return frozenset(ADVANCING_STAGES)
