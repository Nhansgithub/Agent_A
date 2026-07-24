"""Story 1.3 — repository + single SQLite store, state record, and the §9 stage enum (AD-2, AD-11)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.domain.stage import (
    LIVENESS_WATCHED_STAGES,
    IllegalStageTransition,
    PendingGate,
    QueueStatus,
    Stage,
    is_legal_transition,
)
from app.domain.state import PrdState, utc_now
from app.repository.database import Database
from app.repository.state_repository import StateRepository, UnknownPrd


@pytest.fixture
def repo() -> StateRepository:
    """A fresh in-memory store per test — no file, no fixtures to clean up."""
    return StateRepository(Database(":memory:"))


@pytest.fixture
def state(repo: StateRepository) -> PrdState:
    return repo.create(PrdState(prd_id="page-1", project_id="tenant_one", prd_title="final_PRD_X"))


# ---------------------------------------------------------------------------------------------
# AC 1: the state record carries every PRD §10 field, and `stage` is exactly the §9 enum.
# ---------------------------------------------------------------------------------------------

PRD_SECTION_10_FIELDS = [
    "prd_id",
    "project_id",
    "stage",
    "review_ticket_key",
    "prd_tracking_ticket_key",
    "publishing_ticket_key",
    "userdoc_page_id",
    "review_round",
    "pending_gate",
    "last_good_checkpoint",
    "dedupe_keys",
    "md_export_path",
    "created_at",
    "updated_at",
    "completed_at",
]


@pytest.mark.parametrize("field", PRD_SECTION_10_FIELDS)
def test_state_record_carries_every_prd_section_10_field(field: str) -> None:
    assert hasattr(PrdState(prd_id="p", project_id="t"), field), f"§10 field {field!r} missing"


def test_stage_enum_is_exactly_the_prd_section_9_values() -> None:
    assert [s.value for s in Stage] == [
        "detected",
        "confirmed",
        "prd_ticket_done",
        "drafted",
        "awaiting_review",
        "awaiting_clarification",
        "awaiting_structure_confirm",
        "revising",
        "passed",
        "awaiting_publish_approval",
        "publishing",
        "complete",
        "error",
    ]


def test_a_new_run_starts_detected_and_queued() -> None:
    fresh = PrdState(prd_id="p", project_id="t")
    assert fresh.stage is Stage.DETECTED
    assert fresh.queue_status is QueueStatus.QUEUED
    assert fresh.review_round == 0
    assert fresh.correlation_id, "every run needs a correlation id for EH-01 and LangSmith (AD-20)"


def test_round_trips_through_the_store(repo: StateRepository, state: PrdState) -> None:
    loaded = repo.require("page-1")
    assert loaded.prd_id == state.prd_id
    assert loaded.project_id == state.project_id
    assert loaded.stage is Stage.DETECTED
    assert loaded.correlation_id == state.correlation_id
    assert loaded.created_at == state.created_at


def test_missing_prd_raises(repo: StateRepository) -> None:
    assert repo.get("nope") is None
    with pytest.raises(UnknownPrd):
        repo.require("nope")


# ---------------------------------------------------------------------------------------------
# AD-11: a stage advance and the ids that stage recorded are one atomic write.
# ---------------------------------------------------------------------------------------------


def test_advance_persists_stage_and_recorded_id_together(repo: StateRepository, state) -> None:
    """A crash between these two would cause a double-create on replay, so they are one write."""
    repo.advance_stage("page-1", Stage.CONFIRMED)
    updated = repo.advance_stage("page-1", Stage.PRD_TICKET_DONE, prd_tracking_ticket_key="MAIN-42")
    assert updated.stage is Stage.PRD_TICKET_DONE
    assert updated.prd_tracking_ticket_key == "MAIN-42"

    reloaded = repo.require("page-1")
    assert (reloaded.stage, reloaded.prd_tracking_ticket_key) == (Stage.PRD_TICKET_DONE, "MAIN-42")


def test_failed_advance_writes_nothing(repo: StateRepository, state) -> None:
    """The transaction rolls back, so an illegal transition cannot half-apply its recorded ids."""
    with pytest.raises(IllegalStageTransition):
        repo.advance_stage("page-1", Stage.PUBLISHING, userdoc_page_id="page-draft-1")

    unchanged = repo.require("page-1")
    assert unchanged.stage is Stage.DETECTED
    assert unchanged.userdoc_page_id is None


def test_unknown_field_in_advance_is_rejected(repo: StateRepository, state) -> None:
    with pytest.raises(ValueError, match="unknown state fields"):
        repo.advance_stage("page-1", Stage.CONFIRMED, reviewer_ticket="typo")


def test_advance_sets_the_resume_checkpoint(repo: StateRepository, state) -> None:
    """EH-02 resumes the *failed stage*, so each advance records where to re-enter."""
    updated = repo.advance_stage("page-1", Stage.CONFIRMED)
    assert updated.last_good_checkpoint is Stage.CONFIRMED


def test_advance_infers_the_pending_gate(repo: StateRepository, state) -> None:
    repo.advance_stage("page-1", Stage.CONFIRMED)
    repo.advance_stage("page-1", Stage.PRD_TICKET_DONE)
    repo.advance_stage("page-1", Stage.DRAFTED)
    parked = repo.advance_stage("page-1", Stage.AWAITING_REVIEW)
    assert parked.pending_gate is PendingGate.PM_REVIEW
    assert parked.queue_status is QueueStatus.IDLE, "a parked run holds no queue slot (AD-5)"
    assert parked.is_parked


# ---------------------------------------------------------------------------------------------
# §9 state machine — the edges exist to stop a run skipping a human gate (AD-15).
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (Stage.DETECTED, Stage.CONFIRMED),
        (Stage.CONFIRMED, Stage.PRD_TICKET_DONE),
        (Stage.PRD_TICKET_DONE, Stage.DRAFTED),
        (Stage.DRAFTED, Stage.AWAITING_REVIEW),
        (Stage.AWAITING_REVIEW, Stage.REVISING),
        (Stage.AWAITING_REVIEW, Stage.PASSED),
        (Stage.REVISING, Stage.AWAITING_REVIEW),
        (Stage.AWAITING_STRUCTURE_CONFIRM, Stage.REVISING),
        (Stage.PASSED, Stage.AWAITING_PUBLISH_APPROVAL),
        (Stage.AWAITING_PUBLISH_APPROVAL, Stage.PUBLISHING),
        (Stage.PUBLISHING, Stage.COMPLETE),
    ],
)
def test_happy_path_edges_are_legal(current: Stage, target: Stage) -> None:
    assert is_legal_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target", "gate_skipped"),
    [
        (Stage.AWAITING_REVIEW, Stage.PUBLISHING, "the Head of Product publish gate (FR-14)"),
        (Stage.DRAFTED, Stage.PASSED, "the Reviewer PM PASS gate (FR-12)"),
        (Stage.PASSED, Stage.PUBLISHING, "the Head of Product publish gate (FR-14)"),
        (Stage.DETECTED, Stage.COMPLETE, "every gate"),
        (Stage.AWAITING_PUBLISH_APPROVAL, Stage.COMPLETE, "the publish transaction (FR-15)"),
    ],
)
def test_gate_skipping_edges_are_illegal(current: Stage, target: Stage, gate_skipped: str) -> None:
    assert not is_legal_transition(current, target), f"this edge would skip {gate_skipped}"


def test_any_stage_may_fail_to_error() -> None:
    assert all(is_legal_transition(stage, Stage.ERROR) for stage in Stage)


def test_error_may_return_to_any_stage() -> None:
    """EH-02 — resume re-enters at `last_good_checkpoint`, whatever stage that was."""
    assert all(is_legal_transition(Stage.ERROR, stage) for stage in Stage)


def test_replaying_the_same_stage_is_legal() -> None:
    """AD-11 resume is idempotent-create *replay* — re-running a stage must be allowed."""
    assert all(is_legal_transition(stage, stage) for stage in Stage)


def test_complete_is_terminal() -> None:
    assert not is_legal_transition(Stage.COMPLETE, Stage.PUBLISHING)


def test_illegal_transition_message_explains_the_risk() -> None:
    from app.domain.stage import assert_legal_transition

    with pytest.raises(IllegalStageTransition, match="skipping a human gate"):
        assert_legal_transition(Stage.DRAFTED, Stage.COMPLETE)


# ---------------------------------------------------------------------------------------------
# AD-2: `stage` is written only by the orchestrator's advance path.
# ---------------------------------------------------------------------------------------------


def test_update_fields_refuses_to_write_stage(repo: StateRepository, state) -> None:
    """The reconciler and agents use update_fields; neither may advance a stage (AD-2, AD-22)."""
    with pytest.raises(ValueError, match="only by the orchestrator"):
        repo.update_fields("page-1", stage=Stage.PASSED)


def test_update_fields_persists_non_stage_markers(repo: StateRepository, state) -> None:
    alerted = utc_now()
    updated = repo.update_fields("page-1", liveness_alerted_at=alerted)
    assert repo.require("page-1").liveness_alerted_at == alerted
    assert updated.stage is Stage.DETECTED, "a marker write must not disturb the stage"


# ---------------------------------------------------------------------------------------------
# Error, resume, and the review-round guardrail.
# ---------------------------------------------------------------------------------------------


def test_mark_error_preserves_the_failed_stage_as_the_resume_point(repo, state) -> None:
    repo.advance_stage("page-1", Stage.CONFIRMED)
    errored = repo.mark_error("page-1", "Jira returned 503 after 3 retries")

    assert errored.stage is Stage.ERROR
    assert errored.last_good_checkpoint is Stage.CONFIRMED, "resume re-runs the failed stage only"
    assert errored.pending_gate is PendingGate.ADMIN_RESUME
    assert errored.last_error == "Jira returned 503 after 3 retries"


def test_resume_returns_to_the_checkpoint_and_clears_the_error(repo, state) -> None:
    repo.advance_stage("page-1", Stage.CONFIRMED)
    errored = repo.mark_error("page-1", "boom")

    resumed = repo.advance_stage("page-1", errored.last_good_checkpoint)

    assert resumed.stage is Stage.CONFIRMED
    assert resumed.last_error is None
    assert resumed.pending_gate is PendingGate.NONE


def test_review_round_increments_per_applied_round(repo, state) -> None:
    """NFR-09 — the observability guardrail on the uncapped redraft loop."""
    assert repo.increment_review_round("page-1").review_round == 1
    assert repo.increment_review_round("page-1").review_round == 2
    assert repo.require("page-1").review_round == 2


def test_completing_a_run_stamps_completed_at(repo, state) -> None:
    for stage in (
        Stage.CONFIRMED,
        Stage.PRD_TICKET_DONE,
        Stage.DRAFTED,
        Stage.AWAITING_REVIEW,
        Stage.PASSED,
        Stage.AWAITING_PUBLISH_APPROVAL,
        Stage.PUBLISHING,
        Stage.COMPLETE,
    ):
        final = repo.advance_stage("page-1", stage)

    assert final.is_complete
    assert final.completed_at is not None
    assert final.queue_status is QueueStatus.IDLE


# ---------------------------------------------------------------------------------------------
# AD-22 liveness sweep + AD-5 serial queue queries.
# ---------------------------------------------------------------------------------------------


def test_find_stale_returns_only_runs_older_than_the_threshold(repo) -> None:
    fresh = PrdState(prd_id="fresh", project_id="t", stage=Stage.AWAITING_REVIEW)
    stale = PrdState(
        prd_id="stale",
        project_id="t",
        stage=Stage.AWAITING_REVIEW,
        updated_at=utc_now() - timedelta(days=3),
    )
    repo.create(fresh)
    repo.create(stale)

    found = repo.find_stale(LIVENESS_WATCHED_STAGES, timedelta(days=1))

    assert [s.prd_id for s in found] == ["stale"]


def test_find_stale_ignores_stages_that_are_not_parked_or_errored(repo) -> None:
    repo.create(
        PrdState(
            prd_id="old-but-complete",
            project_id="t",
            stage=Stage.COMPLETE,
            updated_at=utc_now() - timedelta(days=30),
        )
    )
    assert repo.find_stale(LIVENESS_WATCHED_STAGES, timedelta(days=1)) == []


def test_queue_status_query_separates_queued_from_in_progress(repo) -> None:
    """AD-5 — the state store tracks queued vs in-progress; one PRD is worked at a time."""
    repo.create(PrdState(prd_id="a", project_id="t"))
    repo.create(PrdState(prd_id="b", project_id="t"))
    repo.advance_stage("a", Stage.CONFIRMED)

    assert [s.prd_id for s in repo.list_by_queue_status(QueueStatus.IN_PROGRESS)] == ["a"]
    assert [s.prd_id for s in repo.list_by_queue_status(QueueStatus.QUEUED)] == ["b"]


def test_dedupe_keys_projection_is_empty_before_any_event_is_recorded(repo, state) -> None:
    """§10 `dedupe_keys` is a read-only view of `processed_events`, never a second store (AD-9)."""
    assert repo.dedupe_keys_for("page-1") == ()
    assert repo.with_dedupe_keys(state).dedupe_keys == ()
