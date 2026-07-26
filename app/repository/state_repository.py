"""The state repository — sole owner of the per-PRD state record (AD-2, AD-11, NFR-03).

Every read and write of run state goes through here. Two invariants live in this module rather than
in prose:

* **Stage legality.** ``advance_stage`` refuses an edge the §9 state machine does not have. The
  failure this prevents is a run skipping a human gate (AD-15).
* **Atomic stage + ids.** A stage advance and the external ids that stage recorded are written in
  **one transaction** (AD-11). A crash between them is exactly what would cause a double-create on
  replay, so the two are never separable.

The repository never *decides* a stage — that is the orchestrator's job (AD-2). It only enforces that
whatever the orchestrator decided is a legal, atomic write.
"""

from __future__ import annotations

import sqlite3
from dataclasses import fields as dataclass_fields
from datetime import timedelta

from app.domain.stage import (
    PendingGate,
    QueueStatus,
    Stage,
    assert_legal_transition,
    gate_for_stage,
)
from app.domain.state import PrdState, utc_now
from app.repository.database import Database, from_iso, to_iso

_COLUMNS = (
    "prd_id",
    "project_id",
    "stage",
    "pending_gate",
    "queue_status",
    "last_good_checkpoint",
    "prd_tracking_ticket_key",
    "review_ticket_key",
    "publishing_ticket_key",
    "rename_request_ticket_key",
    "userdoc_page_id",
    "prd_title",
    "review_round",
    "md_export_path",
    "pending_feedback",
    "pending_deletion_page_id",
    "active_reviewer_account_id",
    "restriction_applied_at",
    "moved_to_published_at",
    "md_exported_at",
    "correlation_id",
    "last_error",
    "liveness_alerted_at",
    "created_at",
    "updated_at",
    "completed_at",
)

_TIMESTAMP_COLUMNS = frozenset(
    {
        "restriction_applied_at",
        "moved_to_published_at",
        "md_exported_at",
        "liveness_alerted_at",
        "created_at",
        "updated_at",
        "completed_at",
    }
)

_UPDATABLE_FIELDS = frozenset(f.name for f in dataclass_fields(PrdState)) - {
    "prd_id",
    "project_id",
    "created_at",
    "dedupe_keys",
}


class UnknownPrd(KeyError):
    """No state record exists for this `prd_id`."""


class StateRepository:
    """CRUD and queries over the per-PRD state record."""

    __slots__ = ("_db",)

    def __init__(self, database: Database) -> None:
        self._db = database

    # -- mapping ---------------------------------------------------------------------------

    @staticmethod
    def to_row(state: PrdState) -> dict[str, object]:
        row: dict[str, object] = {}
        for column in _COLUMNS:
            value = getattr(state, column)
            if column in _TIMESTAMP_COLUMNS:
                row[column] = to_iso(value)
            elif isinstance(value, Stage | PendingGate | QueueStatus):
                row[column] = value.value
            else:
                row[column] = value
        return row

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PrdState:
        return PrdState(
            prd_id=row["prd_id"],
            project_id=row["project_id"],
            stage=Stage(row["stage"]),
            pending_gate=PendingGate(row["pending_gate"]),
            queue_status=QueueStatus(row["queue_status"]),
            last_good_checkpoint=(
                Stage(row["last_good_checkpoint"]) if row["last_good_checkpoint"] else None
            ),
            prd_tracking_ticket_key=row["prd_tracking_ticket_key"],
            review_ticket_key=row["review_ticket_key"],
            publishing_ticket_key=row["publishing_ticket_key"],
            rename_request_ticket_key=row["rename_request_ticket_key"],
            userdoc_page_id=row["userdoc_page_id"],
            prd_title=row["prd_title"],
            review_round=row["review_round"],
            md_export_path=row["md_export_path"],
            pending_feedback=row["pending_feedback"],
            pending_deletion_page_id=row["pending_deletion_page_id"],
            active_reviewer_account_id=row["active_reviewer_account_id"],
            restriction_applied_at=from_iso(row["restriction_applied_at"]),
            moved_to_published_at=from_iso(row["moved_to_published_at"]),
            md_exported_at=from_iso(row["md_exported_at"]),
            correlation_id=row["correlation_id"],
            last_error=row["last_error"],
            liveness_alerted_at=from_iso(row["liveness_alerted_at"]),
            created_at=from_iso(row["created_at"]),  # type: ignore[arg-type]
            updated_at=from_iso(row["updated_at"]),  # type: ignore[arg-type]
            completed_at=from_iso(row["completed_at"]),
        )

    # -- reads -----------------------------------------------------------------------------

    def get(self, prd_id: str) -> PrdState | None:
        with self._db.read() as conn:
            row = conn.execute("SELECT * FROM prd_state WHERE prd_id = ?", (prd_id,)).fetchone()
        return self._from_row(row) if row else None

    def require(self, prd_id: str) -> PrdState:
        state = self.get(prd_id)
        if state is None:
            raise UnknownPrd(f"no state record for prd_id={prd_id!r}")
        return state

    def list_by_stage(self, *stages: Stage) -> list[PrdState]:
        if not stages:
            return []
        placeholders = ",".join("?" for _ in stages)
        with self._db.read() as conn:
            rows = conn.execute(
                f"SELECT * FROM prd_state WHERE stage IN ({placeholders}) ORDER BY updated_at",
                tuple(s.value for s in stages),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_by_queue_status(self, status: QueueStatus) -> list[PrdState]:
        """AD-5 — the serial queue's view: what is in progress, and what is waiting."""
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM prd_state WHERE queue_status = ? ORDER BY created_at",
                (status.value,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def find_stale(self, stages: frozenset[Stage], older_than: timedelta) -> list[PrdState]:
        """AD-22 liveness sweep — runs parked or errored longer than the configured threshold.

        Finding a run here does **not** advance or fail it. It only makes an otherwise-invisible
        stuck run visible to the admin. The park stays indefinite; this is not a timeout.
        """
        cutoff = utc_now() - older_than
        placeholders = ",".join("?" for _ in stages)
        with self._db.read() as conn:
            rows = conn.execute(
                f"SELECT * FROM prd_state WHERE stage IN ({placeholders}) AND updated_at < ? "
                "ORDER BY updated_at",
                (*(s.value for s in stages), to_iso(cutoff)),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def dedupe_keys_for(self, prd_id: str) -> tuple[str, ...]:
        """PRD §10 `dedupe_keys` — a **read-only projection** of `processed_events` (AD-9).

        Never a write target. `processed_events` is the single authoritative dedupe store.
        """
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT dedupe_key FROM processed_events WHERE prd_id = ? ORDER BY recorded_at",
                (prd_id,),
            ).fetchall()
        return tuple(row["dedupe_key"] for row in rows)

    # -- writes ----------------------------------------------------------------------------

    def create(self, state: PrdState) -> PrdState:
        """Insert a new run. Raises if one already exists for this `prd_id`."""
        row = self.to_row(state)
        columns = ", ".join(_COLUMNS)
        placeholders = ", ".join(f":{c}" for c in _COLUMNS)
        with self._db.transaction() as conn:
            conn.execute(f"INSERT INTO prd_state ({columns}) VALUES ({placeholders})", row)
        return state

    def advance_stage(
        self,
        prd_id: str,
        target: Stage,
        *,
        checkpoint: Stage | None = None,
        pending_gate: PendingGate | None = None,
        queue_status: QueueStatus | None = None,
        **recorded: object,
    ) -> PrdState:
        """Advance `stage` and persist the ids that stage recorded — atomically (AD-11).

        Args:
            target: the stage to advance to. Rejected if the §9 machine has no such edge.
            checkpoint: the stage to resume from on `@agent resume` (EH-02). Defaults to `target`
                for a normal advance, so an error at the next step re-runs *this* stage.
            pending_gate: what human action is now awaited. Defaults to the gate implied by `target`.
            queue_status: AD-5 queue position. Defaults to IDLE for parked/terminal stages.
            **recorded: any other state field to persist in the same transaction — typically the
                external id the stage just created (`review_ticket_key`, `userdoc_page_id`, ...).

        Returns:
            The persisted state.
        """
        unknown = set(recorded) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(
                f"unknown state fields {sorted(unknown)}; updatable: {sorted(_UPDATABLE_FIELDS)}"
            )

        with self._db.transaction() as conn:
            row = conn.execute("SELECT * FROM prd_state WHERE prd_id = ?", (prd_id,)).fetchone()
            if row is None:
                raise UnknownPrd(f"no state record for prd_id={prd_id!r}")
            current = self._from_row(row)

            assert_legal_transition(current.stage, target)

            changes: dict[str, object] = dict(recorded)
            changes["stage"] = target
            changes["pending_gate"] = (
                pending_gate if pending_gate is not None else gate_for_stage(target)
            )
            changes["last_good_checkpoint"] = checkpoint if checkpoint is not None else target
            changes["queue_status"] = (
                queue_status
                if queue_status is not None
                else self._implied_queue_status(target, current.queue_status)
            )
            if target is Stage.COMPLETE and current.completed_at is None:
                changes["completed_at"] = utc_now()
            if target is not Stage.ERROR:
                # A successful advance clears a previously-surfaced error and its liveness alert, so
                # a later stall alerts afresh rather than being suppressed by a stale marker (AD-22).
                changes.setdefault("last_error", None)
                changes["liveness_alerted_at"] = None

            updated = current.with_changes(**changes)
            self._update(conn, updated)
        return updated

    def update_fields(self, prd_id: str, **changes: object) -> PrdState:
        """Persist non-`stage` fields.

        Used by role-agents' results and by the AD-22 reconciler, which writes **only** non-`stage`
        markers — the orchestrator remains the sole `stage` writer (AD-2).
        """
        if "stage" in changes:
            raise ValueError(
                "`stage` is advanced only by the orchestrator via advance_stage() (AD-2). "
                "update_fields() deliberately refuses to write it."
            )
        unknown = set(changes) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"unknown state fields {sorted(unknown)}")

        with self._db.transaction() as conn:
            row = conn.execute("SELECT * FROM prd_state WHERE prd_id = ?", (prd_id,)).fetchone()
            if row is None:
                raise UnknownPrd(f"no state record for prd_id={prd_id!r}")
            updated = self._from_row(row).with_changes(**changes)
            self._update(conn, updated)
        return updated

    def increment_review_round(self, prd_id: str) -> PrdState:
        """FR-11 / NFR-09 — one increment per *applied* feedback round.

        The loop is uncapped by design and cannot self-spin: every round needs a fresh human comment.
        This counter is the observability guardrail, surfaced in LangSmith with per-round token cost.
        """
        with self._db.transaction() as conn:
            row = conn.execute("SELECT * FROM prd_state WHERE prd_id = ?", (prd_id,)).fetchone()
            if row is None:
                raise UnknownPrd(f"no state record for prd_id={prd_id!r}")
            current = self._from_row(row)
            updated = current.with_changes(review_round=current.review_round + 1)
            self._update(conn, updated)
        return updated

    def mark_error(self, prd_id: str, message: str) -> PrdState:
        """EH-01 / AD-19 — move to `error` preserving `last_good_checkpoint` and `pending_gate`.

        The checkpoint is deliberately *not* overwritten: it names the stage that failed, which is
        what `@agent resume` re-runs (EH-02). Resume never restarts the whole flow.
        """
        with self._db.transaction() as conn:
            row = conn.execute("SELECT * FROM prd_state WHERE prd_id = ?", (prd_id,)).fetchone()
            if row is None:
                raise UnknownPrd(f"no state record for prd_id={prd_id!r}")
            current = self._from_row(row)
            updated = current.with_changes(
                stage=Stage.ERROR,
                last_good_checkpoint=(
                    current.last_good_checkpoint
                    if current.last_good_checkpoint is not None
                    else current.stage
                ),
                pending_gate=PendingGate.ADMIN_RESUME,
                queue_status=QueueStatus.IDLE,
                last_error=message,
            )
            self._update(conn, updated)
        return updated

    def with_dedupe_keys(self, state: PrdState) -> PrdState:
        """Populate the §10 `dedupe_keys` read-only projection."""
        return state.with_changes(
            dedupe_keys=self.dedupe_keys_for(state.prd_id), updated_at=state.updated_at
        )

    # -- internals -------------------------------------------------------------------------

    @staticmethod
    def _implied_queue_status(target: Stage, current: QueueStatus) -> QueueStatus:
        from app.domain.stage import PARKED_STAGES, TERMINAL_STAGES

        if target in PARKED_STAGES or target in TERMINAL_STAGES or target is Stage.ERROR:
            return QueueStatus.IDLE
        return current if current is QueueStatus.IN_PROGRESS else QueueStatus.IN_PROGRESS

    @staticmethod
    def _update(conn: sqlite3.Connection, state: PrdState) -> None:
        assignments = ", ".join(f"{c} = :{c}" for c in _COLUMNS if c != "prd_id")
        conn.execute(
            f"UPDATE prd_state SET {assignments} WHERE prd_id = :prd_id",
            StateRepository.to_row(state),
        )
