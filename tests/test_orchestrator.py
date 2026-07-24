"""Story 1.9 — in-invocation orchestrator, stage machine, serial queue (AD-6, AD-11, AD-2, AD-5)."""

from __future__ import annotations

import asyncio

import pytest

from app.domain.errors import AgentError
from app.domain.stage import PendingGate, QueueStatus, Stage
from app.domain.state import PrdState
from app.orchestrator.runner import Orchestrator
from app.orchestrator.stages import (
    ADVANCING_STAGES,
    Advance,
    HandlerRegistry,
    Park,
    Stay,
)
from app.repository import Repository
from app.repository.database import Database


@pytest.fixture
def repository() -> Repository:
    return Repository(Database(":memory:"))


@pytest.fixture
def state(repository: Repository) -> PrdState:
    return repository.state.create(PrdState(prd_id="page-1", project_id="tenant_one"))


def advancing(to: Stage, **recorded: object):
    async def handler(_context, _state):
        return Advance(to_stage=to, recorded=recorded)

    return handler


def parking(to: Stage, gate: PendingGate, **recorded: object):
    async def handler(_context, _state):
        return Park(to_stage=to, gate=gate, recorded=recorded)

    return handler


def failing(error: AgentError):
    async def handler(_context, _state):
        raise error

    return handler


def happy_path_handlers() -> HandlerRegistry:
    """Handlers that walk the full flow, parking at each human gate."""
    return HandlerRegistry(
        {
            Stage.DETECTED: advancing(Stage.CONFIRMED),
            Stage.CONFIRMED: advancing(Stage.PRD_TICKET_DONE, prd_tracking_ticket_key="MAIN-1"),
            Stage.PRD_TICKET_DONE: advancing(Stage.DRAFTED, userdoc_page_id="draft-1"),
            Stage.DRAFTED: parking(
                Stage.AWAITING_REVIEW, PendingGate.PM_REVIEW, review_ticket_key="REV-1"
            ),
            Stage.REVISING: parking(Stage.AWAITING_REVIEW, PendingGate.PM_REVIEW),
            Stage.PASSED: parking(
                Stage.AWAITING_PUBLISH_APPROVAL,
                PendingGate.HEAD_OF_PRODUCT_APPROVAL,
                publishing_ticket_key="MAIN-2",
            ),
            Stage.PUBLISHING: advancing(Stage.COMPLETE, md_export_path="/data/x.md"),
        }
    )


# ---------------------------------------------------------------------------------------------
# AC 1: load → re-enter at the recorded stage → run what can advance → persist → stop.
# ---------------------------------------------------------------------------------------------


async def test_runs_every_stage_that_can_advance_then_parks(repository, state) -> None:
    orchestrator = Orchestrator(repository, happy_path_handlers())

    result = await orchestrator.advance("page-1")

    assert result.advanced == (
        Stage.CONFIRMED,
        Stage.PRD_TICKET_DONE,
        Stage.DRAFTED,
        Stage.AWAITING_REVIEW,
    )
    assert result.final_stage is Stage.AWAITING_REVIEW
    assert repository.state.require("page-1").stage is Stage.AWAITING_REVIEW


async def test_stops_at_the_human_gate_rather_than_running_on(repository, state) -> None:
    """The single most important behaviour: the graph must never run through a gate (AD-15)."""
    orchestrator = Orchestrator(repository, happy_path_handlers())

    await orchestrator.advance("page-1")

    final = repository.state.require("page-1")
    assert final.stage is Stage.AWAITING_REVIEW
    assert final.pending_gate is PendingGate.PM_REVIEW
    assert final.publishing_ticket_key is None, "must not have reached the publish gate"


async def test_re_enters_at_the_recorded_stage(repository) -> None:
    """AD-11 step 2 — a run resumes where it stopped, not from the beginning."""
    repository.state.create(
        PrdState(prd_id="page-2", project_id="tenant_one", stage=Stage.PRD_TICKET_DONE)
    )
    orchestrator = Orchestrator(repository, happy_path_handlers())

    result = await orchestrator.advance("page-2")

    assert Stage.CONFIRMED not in result.advanced, "already-completed stages must not re-run"
    assert result.advanced == (Stage.DRAFTED, Stage.AWAITING_REVIEW)


async def test_ids_recorded_by_a_stage_are_persisted(repository, state) -> None:
    orchestrator = Orchestrator(repository, happy_path_handlers())

    await orchestrator.advance("page-1")

    final = repository.state.require("page-1")
    assert final.prd_tracking_ticket_key == "MAIN-1"
    assert final.userdoc_page_id == "draft-1"
    assert final.review_ticket_key == "REV-1"


async def test_a_later_stage_sees_ids_recorded_by_an_earlier_one(repository, state) -> None:
    """AD-11 — the idempotency guard depends on reading ids written earlier in the same invocation."""
    seen: dict[str, object] = {}

    async def capture(_context, current_state):
        seen["tracking_key"] = current_state.prd_tracking_ticket_key
        return Park(to_stage=Stage.AWAITING_CLARIFICATION, gate=PendingGate.PM_CLARIFICATION)

    handlers = happy_path_handlers()
    handlers.register(Stage.PRD_TICKET_DONE, capture)

    await Orchestrator(repository, handlers).advance("page-1")

    assert seen["tracking_key"] == "MAIN-1"


async def test_advancing_from_a_parked_stage_does_nothing(repository) -> None:
    """FR-12 / FR-14 — a parked run waits for a human, indefinitely. No timeout, no auto-advance."""
    repository.state.create(
        PrdState(prd_id="page-3", project_id="tenant_one", stage=Stage.AWAITING_REVIEW)
    )

    result = await Orchestrator(repository, happy_path_handlers()).advance("page-3")

    assert not result.progressed
    assert "parked" in result.stopped_reason
    assert repository.state.require("page-3").stage is Stage.AWAITING_REVIEW


async def test_a_complete_run_does_not_restart(repository) -> None:
    repository.state.create(
        PrdState(prd_id="page-4", project_id="tenant_one", stage=Stage.COMPLETE)
    )
    result = await Orchestrator(repository, happy_path_handlers()).advance("page-4")
    assert not result.progressed


async def test_an_errored_run_does_not_quietly_restart(repository) -> None:
    """EH-02 — an errored run resumes only on an explicit `@agent resume`."""
    repository.state.create(PrdState(prd_id="page-5", project_id="tenant_one", stage=Stage.ERROR))

    result = await Orchestrator(repository, happy_path_handlers()).advance("page-5")

    assert not result.progressed
    assert "@agent resume" in result.stopped_reason


async def test_the_full_flow_reaches_complete_when_gates_are_passed(repository, state) -> None:
    """Simulates the two human gates by advancing past them, as the webhooks will."""
    orchestrator = Orchestrator(repository, happy_path_handlers())

    await orchestrator.advance("page-1")  # -> awaiting_review
    repository.state.advance_stage("page-1", Stage.PASSED)  # PM moved the ticket to Done
    await orchestrator.advance("page-1")  # -> awaiting_publish_approval
    repository.state.advance_stage("page-1", Stage.PUBLISHING)  # Head of Product approved
    result = await orchestrator.advance("page-1")

    assert result.final_stage is Stage.COMPLETE
    final = repository.state.require("page-1")
    assert final.md_export_path == "/data/x.md"
    assert final.completed_at is not None


# ---------------------------------------------------------------------------------------------
# AC 2: LangGraph is in-invocation control flow only — never a cross-webhook durable store.
# ---------------------------------------------------------------------------------------------


def test_the_checkpointer_is_an_in_memory_saver() -> None:
    """AD-6 / AD-11 — a durable checkpointer would recreate the two-store divergence AD-11 removed."""
    from langgraph.checkpoint.memory import InMemorySaver

    from app.orchestrator.graph import build_graph

    async def runner(stage):  # pragma: no cover - not invoked
        raise AssertionError

    assert isinstance(build_graph(runner).checkpointer, InMemorySaver)


async def test_each_invocation_builds_a_fresh_graph(repository, state) -> None:
    """No cross-PRD mutable singleton survives between runs (AD-5)."""
    from app.orchestrator import runner as runner_module

    built: list[object] = []
    original = runner_module.build_graph

    def counting_build(runner):
        compiled = original(runner)
        built.append(compiled)
        return compiled

    runner_module.build_graph = counting_build
    try:
        orchestrator = Orchestrator(repository, happy_path_handlers())
        await orchestrator.advance("page-1")
        repository.state.advance_stage("page-1", Stage.PASSED)
        await orchestrator.advance("page-1")
    finally:
        runner_module.build_graph = original

    assert len(built) == 2
    assert built[0] is not built[1]


async def test_state_survives_a_restart_because_it_is_on_disk(repository, state) -> None:
    """A new Orchestrator over the same store continues exactly where the previous one stopped."""
    await Orchestrator(repository, happy_path_handlers()).advance("page-1")
    repository.state.advance_stage("page-1", Stage.PASSED)

    fresh_orchestrator = Orchestrator(repository, happy_path_handlers())
    result = await fresh_orchestrator.advance("page-1")

    assert result.final_stage is Stage.AWAITING_PUBLISH_APPROVAL


# ---------------------------------------------------------------------------------------------
# AD-2: `stage` is written only by the orchestrator, never by a handler.
# ---------------------------------------------------------------------------------------------


async def test_a_handler_cannot_write_the_stage_itself(repository, state) -> None:
    async def sneaky(_context, _current):
        repository.state.update_fields("page-1", stage=Stage.COMPLETE)
        return Advance(to_stage=Stage.CONFIRMED)

    handlers = happy_path_handlers()
    handlers.register(Stage.DETECTED, sneaky)

    with pytest.raises(ValueError, match="only by the orchestrator"):
        await Orchestrator(repository, handlers).advance("page-1")


async def test_a_stage_with_no_handler_stops_rather_than_skipping(repository, state) -> None:
    """Skipping would push the run past a human gate — the failure AD-15 exists to prevent."""
    result = await Orchestrator(repository, HandlerRegistry()).advance("page-1")

    assert not result.progressed
    assert "no handler registered" in result.stopped_reason
    assert repository.state.require("page-1").stage is Stage.DETECTED


async def test_stay_stops_without_changing_the_stage(repository, state) -> None:
    """EH-06 — an event that turns out to need no work leaves the run exactly as it was."""
    handlers = HandlerRegistry({Stage.DETECTED: lambda c, s: _stay()})

    result = await Orchestrator(repository, handlers).advance("page-1")

    assert repository.state.require("page-1").stage is Stage.DETECTED
    assert "late feedback" in result.stopped_reason


async def _stay():
    return Stay(reason="late feedback after Done is not processed")


# ---------------------------------------------------------------------------------------------
# AD-19 / EH-01: a failing stage moves to error, preserving the resume point.
# ---------------------------------------------------------------------------------------------


async def test_a_failing_stage_moves_the_run_to_error(repository, state) -> None:
    error = AgentError(
        message="Jira returned 503", suggested_fix="retry later", operation="jira.create_issue"
    )
    handlers = happy_path_handlers()
    handlers.register(Stage.CONFIRMED, failing(error))

    result = await Orchestrator(repository, handlers).advance("page-1")

    assert result.final_stage is Stage.ERROR
    assert result.error is error
    assert repository.state.require("page-1").stage is Stage.ERROR


async def test_the_failed_stage_is_preserved_as_the_resume_point(repository, state) -> None:
    """EH-02 — `@agent resume` re-runs the failed stage only, never the whole flow."""
    handlers = happy_path_handlers()
    handlers.register(
        Stage.CONFIRMED, failing(AgentError(message="boom", suggested_fix="fix", operation="op"))
    )

    await Orchestrator(repository, handlers).advance("page-1")

    failed = repository.state.require("page-1")
    assert failed.last_good_checkpoint is Stage.CONFIRMED
    assert failed.pending_gate is PendingGate.ADMIN_RESUME
    assert failed.last_error == "boom"


async def test_work_completed_before_the_failure_is_not_lost(repository, state) -> None:
    """Persisting per stage boundary is what makes resume cheap — earlier stages do not re-run."""
    handlers = happy_path_handlers()
    handlers.register(
        Stage.PRD_TICKET_DONE,
        failing(AgentError(message="boom", suggested_fix="fix", operation="op")),
    )

    await Orchestrator(repository, handlers).advance("page-1")

    failed = repository.state.require("page-1")
    assert failed.prd_tracking_ticket_key == "MAIN-1", (
        "the earlier stage's ticket is still recorded"
    )
    assert failed.last_good_checkpoint is Stage.PRD_TICKET_DONE


async def test_resume_after_a_fix_continues_from_the_checkpoint(repository, state) -> None:
    handlers = happy_path_handlers()
    handlers.register(
        Stage.CONFIRMED, failing(AgentError(message="boom", suggested_fix="fix", operation="op"))
    )
    orchestrator = Orchestrator(repository, handlers)
    await orchestrator.advance("page-1")

    # Admin fixes the cause and replies `@agent resume`: the run returns to the failed stage.
    checkpoint = repository.state.require("page-1").last_good_checkpoint
    repository.state.advance_stage("page-1", checkpoint)
    handlers.register(
        Stage.CONFIRMED, advancing(Stage.PRD_TICKET_DONE, prd_tracking_ticket_key="MAIN-1")
    )

    result = await Orchestrator(repository, handlers).advance("page-1")

    assert result.final_stage is Stage.AWAITING_REVIEW


# ---------------------------------------------------------------------------------------------
# AD-5 / NFR-06 / EH-05: the serial queue.
# ---------------------------------------------------------------------------------------------


async def test_concurrent_prds_are_processed_one_at_a_time(repository) -> None:
    """AD-5 — also a memory-safety measure: only one PRD payload is ever resident (AD-21)."""
    concurrent = 0
    peak = 0

    async def slow(_context, _state):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.01)
        concurrent -= 1
        return Stay(reason="probe")

    for page_id in ("a", "b", "c"):
        repository.state.create(PrdState(prd_id=page_id, project_id="tenant_one"))

    handlers = HandlerRegistry({Stage.DETECTED: slow})
    orchestrator = Orchestrator(repository, handlers)

    await asyncio.gather(*(orchestrator.advance(page_id) for page_id in ("a", "b", "c")))

    assert peak == 1, "the serial queue must never run two PRDs concurrently"


async def test_queue_status_tracks_in_progress_then_idle(repository, state) -> None:
    """AD-5 — the state store tracks queued vs in-progress."""
    orchestrator = Orchestrator(repository, happy_path_handlers())
    assert repository.state.require("page-1").queue_status is QueueStatus.QUEUED

    await orchestrator.advance("page-1")

    assert repository.state.require("page-1").queue_status is QueueStatus.IDLE


async def test_the_only_shared_object_is_the_serializer(repository) -> None:
    """AD-5 — the per-PRD row is the unit of isolation; lifting the lock must yield parallelism."""
    orchestrator = Orchestrator(repository, happy_path_handlers())
    shared = {
        name
        for name in Orchestrator.__slots__
        if name not in {"_lock", "_repository", "_handlers", "_context_factory"}
    }
    assert not shared, f"unexpected cross-PRD state on the orchestrator: {shared}"
    assert isinstance(orchestrator._lock, asyncio.Lock)


# ---------------------------------------------------------------------------------------------
# The handler registry.
# ---------------------------------------------------------------------------------------------


def test_registry_rejects_a_handler_for_a_parked_stage() -> None:
    """A stage that waits on a human has no handler — registering one would be a category error."""
    with pytest.raises(ValueError, match="not an advancing stage"):
        HandlerRegistry().register(Stage.AWAITING_REVIEW, advancing(Stage.PASSED))


def test_advancing_stages_exclude_every_gate_and_terminal_stage() -> None:
    for stage in (
        Stage.AWAITING_REVIEW,
        Stage.AWAITING_CLARIFICATION,
        Stage.AWAITING_STRUCTURE_CONFIRM,
        Stage.AWAITING_PUBLISH_APPROVAL,
        Stage.COMPLETE,
        Stage.ERROR,
    ):
        assert stage not in ADVANCING_STAGES


def test_registry_reports_which_handlers_are_still_missing() -> None:
    registry = HandlerRegistry({Stage.DETECTED: advancing(Stage.CONFIRMED)})
    assert Stage.DETECTED not in registry.missing()
    assert Stage.PUBLISHING in registry.missing()
