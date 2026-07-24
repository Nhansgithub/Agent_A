"""The in-invocation LangGraph control flow (AD-6, AD-11).

LangGraph models *which stage runs next within one webhook invocation* — and nothing else. It is
**not** a durable store: the checkpointer is an `InMemorySaver` scoped to a single invocation and
discarded when it ends. Durable truth is the repository state record (AD-2, AD-11).

That distinction is the whole reason AD-11 was rewritten in the r2 roundtable. An earlier design
tried to commit a LangGraph checkpoint *and* the state-record stage in one cross-store transaction,
which is unbuildable — `SqliteSaver` owns its own connection and commits inside its own `.put()`.
Collapsing to one durable store removes the problem by construction: there is no second store to
drift.

The graph is **built fresh per invocation**, closing over that run's stage runner. Rebuilding is
cheap, and it guarantees no cross-PRD mutable singleton survives between runs (AD-5) — so lifting
the serializer later yields parallelism rather than a data race.
"""

from __future__ import annotations

import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.domain.stage import Stage
from app.orchestrator.stages import ADVANCING_STAGES

#: A defensive ceiling on node executions per invocation. The advancing stages form a chain toward a
#: park or `complete`, so this is never reached in practice — it exists so a future edge that
#: accidentally cycles fails loudly instead of spinning.
RECURSION_LIMIT = len(ADVANCING_STAGES) + 3


@dataclass(frozen=True, slots=True)
class StageStep:
    """What running one stage produced."""

    next_stage: Stage
    stop: bool
    """True when the flow cannot continue without a new external event (a human gate, an error, or
    a terminal stage)."""

    reason: str = ""


#: Runs one stage and persists its result. Supplied by the orchestrator; the graph only routes.
StageRunner = Callable[[Stage], Awaitable[StageStep]]


class GraphState(TypedDict):
    """Ephemeral per-invocation state. Nothing here is durable — the state record is (AD-11)."""

    prd_id: str
    stage: str
    advanced: Annotated[list[str], operator.add]
    stopped: str


def build_graph(runner: StageRunner):
    """Compile a graph that enters at a recorded stage and runs until it must stop.

    The entry router is what makes "re-enter at `stage` / `last_good_checkpoint`" work: a plain
    `StateGraph` always begins at START, so START dispatches conditionally to the node named by the
    stage the state record carries.
    """
    builder = StateGraph(GraphState)

    for stage in ADVANCING_STAGES:
        builder.add_node(stage.value, _make_node(stage, runner))

    # START -> the stage the run is actually at (AD-11 step 2), or straight to END if that stage
    # waits on a human or is terminal.
    builder.add_conditional_edges(
        START,
        _entry_router,
        {stage.value: stage.value for stage in ADVANCING_STAGES} | {END: END},
    )

    # After a stage runs: continue to the next stage, or stop (AD-11 step 5).
    for stage in ADVANCING_STAGES:
        builder.add_conditional_edges(
            stage.value,
            _continue_router,
            {other.value: other.value for other in ADVANCING_STAGES} | {END: END},
        )

    # In-invocation only. Discarded when this invocation ends; never read by a later webhook.
    return builder.compile(checkpointer=InMemorySaver())


def _make_node(stage: Stage, runner: StageRunner):
    async def node(state: GraphState) -> dict[str, object]:
        step = await runner(stage)
        return {
            "stage": step.next_stage.value,
            "advanced": [stage.value],
            "stopped": step.reason if step.stop else "",
        }

    node.__name__ = f"stage_{stage.value}"
    return node


def _entry_router(state: GraphState) -> str:
    stage = state["stage"]
    return stage if stage in {s.value for s in ADVANCING_STAGES} else END


def _continue_router(state: GraphState) -> str:
    if state.get("stopped"):
        return END
    return _entry_router(state)
