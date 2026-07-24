"""Stage handlers and their outcomes (AD-2, AD-11).

A **stage handler** is the unit of work for one §9 stage. It receives the run's context and current
state, does its job (call an agent, create a ticket, publish a page), and returns an outcome saying
what should happen to `stage` — it never writes `stage` itself. The orchestrator persists the result
(AD-2: agents return results, the orchestrator persists them).

Three outcomes, matching the three things a stage can do:

* :class:`Advance` — this stage finished; move to the next one and keep going.
* :class:`Park` — the flow now waits on a human. The orchestrator stops; nothing further happens
  until an event arrives. **There is no timeout** (FR-12, FR-14, EH-09).
* :class:`Stay` — nothing to do right now. Distinct from `Park` in intent, identical in effect;
  used when an event turns out not to require work (a comment after Done, EH-06).

An `AgentError` raised out of a handler moves the run to `error` with the checkpoint preserved
(AD-19, EH-01) — handlers do not catch their own failures.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.domain.stage import PendingGate, Stage
from app.domain.state import PrdState


@dataclass(frozen=True, slots=True)
class Advance:
    """This stage completed; continue to `to_stage`.

    `recorded` carries any external ids the stage created (`review_ticket_key`, `userdoc_page_id`,
    …). They are persisted in the **same transaction** as the stage advance (AD-11) — a crash between
    the two is precisely what would cause a double-create on replay.
    """

    to_stage: Stage
    recorded: dict[str, object] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True, slots=True)
class Park:
    """The flow waits on a human. Indefinitely, by design.

    `to_stage` may differ from the current stage — e.g. `drafted → awaiting_review` both advances and
    parks, because posting the review request is what creates the wait.
    """

    to_stage: Stage
    gate: PendingGate
    recorded: dict[str, object] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True, slots=True)
class Stay:
    """Nothing to do. The stage does not change and nothing is persisted."""

    reason: str = ""


StageOutcome = Advance | Park | Stay


class RunContext(Protocol):
    """What a handler is given. Structural, so tests can pass a simple stand-in.

    Everything a handler needs is **injected** — it never constructs an adapter or opens a
    connection itself (AD-1).
    """

    @property
    def prd_id(self) -> str: ...

    @property
    def correlation_id(self) -> str: ...


StageHandler = Callable[[RunContext, PrdState], Awaitable[StageOutcome]]


#: Stages the graph can run on its own, without waiting for a new external event. Everything else
#: either waits on a human (`awaiting_*`, `error`) or is terminal (`complete`).
ADVANCING_STAGES: tuple[Stage, ...] = (
    Stage.DETECTED,
    Stage.CONFIRMED,
    Stage.PRD_TICKET_DONE,
    Stage.DRAFTED,
    Stage.REVISING,
    Stage.PASSED,
    Stage.PUBLISHING,
)


class HandlerRegistry:
    """Maps each advancing stage to its handler.

    Deliberately an instance rather than a module-level dict: the orchestrator is constructed with
    one, so a test can supply fake handlers without monkey-patching global state, and two tenants
    could later run different handler sets without a shared mutable singleton (AD-5).
    """

    __slots__ = ("_handlers",)

    def __init__(self, handlers: dict[Stage, StageHandler] | None = None) -> None:
        self._handlers = dict(handlers or {})

    def register(self, stage: Stage, handler: StageHandler) -> None:
        if stage not in ADVANCING_STAGES:
            raise ValueError(
                f"{stage.value!r} is not an advancing stage — it waits on a human or is terminal, "
                f"so it has no handler. Advancing stages: {[s.value for s in ADVANCING_STAGES]}"
            )
        self._handlers[stage] = handler

    def get(self, stage: Stage) -> StageHandler | None:
        return self._handlers.get(stage)

    def missing(self) -> list[Stage]:
        """Advancing stages with no handler yet. Useful while Epics 2-5 are still being built."""
        return [stage for stage in ADVANCING_STAGES if stage not in self._handlers]

    def __contains__(self, stage: object) -> bool:
        return stage in self._handlers


async def park_until_implemented(_context: RunContext, state: PrdState) -> StageOutcome:
    """Placeholder for a stage whose handler has not been built yet.

    Parks rather than advancing: a stage that quietly advanced without doing its work would push the
    run past a human gate, which is the exact failure AD-15 exists to prevent. Parking is visible and
    safe — the run stops and the AD-22 liveness sweep will eventually surface it.
    """
    return Stay(reason=f"no handler registered for stage {state.stage.value!r} yet")
