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

from app.domain.errors import AgentError
from app.domain.stage import PARKED_STAGES, TERMINAL_STAGES, QueueStatus, Stage
from app.domain.state import PrdState
from app.orchestrator.graph import RECURSION_LIMIT, StageStep, build_graph
from app.orchestrator.stages import (
    Advance,
    HandlerRegistry,
    Park,
    RunContext,
    Stay,
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
