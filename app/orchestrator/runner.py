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
from app.domain.stage import PARKED_STAGES, TERMINAL_STAGES, PendingGate, QueueStatus, Stage
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
            # PM did not confirm a restatement → back to open review for another round.
            self._repository.state.advance_stage(
                prd_id,
                outcome.target_stage,
                pending_gate=outcome.gate,
                queue_status=QueueStatus.IDLE,
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
