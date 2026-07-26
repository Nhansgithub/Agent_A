"""SQLite connection, schema, and transaction management — the single durable store (AD-2, AD-11).

This module and its siblings in ``app/repository/`` are the **only** place in the system that runs
SQL (AD-1, enforced by the import-linter contract that forbids ``sqlite3`` everywhere else). That
boundary is what makes NFR-03 true: swapping SQLite for Postgres is a change here, not a change
scattered across the orchestrator and six agents.

Two deliberate choices:

* **WAL journal mode** — required by litestream, which continuously streams the write-ahead log to
  DigitalOcean Spaces (AD-23). Run state is authoritative *only* on the Droplet disk, so losing the
  disk mid-run would cause double-create or re-publish on webhook redelivery. WAL is what makes
  continuous off-box replication possible.
* **One connection guarded by a lock** — the demo processes one PRD at a time (AD-5), so there is no
  real contention, and a single in-process connection keeps the memory footprint minimal on the 1 GB
  box (AD-21). SQLite here is a file, not a co-located database server.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
-- The per-PRD state record (PRD §10). The single authoritative durable truth for run state.
CREATE TABLE IF NOT EXISTS prd_state (
    prd_id                    TEXT PRIMARY KEY,
    project_id                TEXT NOT NULL,
    stage                     TEXT NOT NULL,
    pending_gate              TEXT NOT NULL,
    queue_status              TEXT NOT NULL,
    last_good_checkpoint      TEXT,
    prd_tracking_ticket_key   TEXT,
    review_ticket_key         TEXT,
    publishing_ticket_key     TEXT,
    rename_request_ticket_key TEXT,
    userdoc_page_id           TEXT,
    prd_title                 TEXT,
    review_round              INTEGER NOT NULL DEFAULT 0,
    md_export_path            TEXT,
    pending_feedback          TEXT,
    pending_deletion_page_id  TEXT,
    restriction_applied_at    TEXT,
    moved_to_published_at     TEXT,
    md_exported_at            TEXT,
    correlation_id            TEXT NOT NULL,
    last_error                TEXT,
    liveness_alerted_at       TEXT,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL,
    completed_at              TEXT
);

-- Supports the AD-22 liveness sweep: "runs parked or errored whose updated_at is older than X".
CREATE INDEX IF NOT EXISTS idx_prd_state_stage_updated ON prd_state (stage, updated_at);
CREATE INDEX IF NOT EXISTS idx_prd_state_project ON prd_state (project_id);
-- Supports the AD-5 serial queue: "is anything in progress, and what is next?"
CREATE INDEX IF NOT EXISTS idx_prd_state_queue ON prd_state (queue_status, created_at);

-- AD-9 — the SINGLE authoritative dedupe store. Deliberately NOT nested in a PRD row: a
-- page-created event arrives before any PRD row exists. The UNIQUE constraint is the mechanism —
-- a concurrent duplicate loses the insert race and is dropped safely.
CREATE TABLE IF NOT EXISTS processed_events (
    dedupe_key     TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    entity_id      TEXT NOT NULL,
    version_marker TEXT,
    prd_id         TEXT,
    recorded_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_processed_events_prd ON processed_events (prd_id);
"""

#: Columns added after the initial schema shipped. `CREATE TABLE IF NOT EXISTS` never alters an
#: existing table, so an already-provisioned store (the Droplet's) would be missing these — they are
#: added idempotently at open. Each entry is `column_name -> full column DDL`; all must be nullable
#: (an existing row has no value for a new column).
_ADDED_COLUMNS: dict[str, str] = {
    "pending_deletion_page_id": "pending_deletion_page_id TEXT",  # FR-16
}


def to_iso(value: datetime | None) -> str | None:
    """Persist timestamps as ISO-8601 UTC (Spine → Consistency Conventions)."""
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def from_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class Database:
    """Owns the connection to the single durable store and applies the schema."""

    __slots__ = ("_connection", "_lock", "_path")

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = threading.RLock()
        self._connection = self._connect()
        self._apply_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self._path,
            # The serial queue (AD-5) means only one unit of work runs at a time, but FastAPI may
            # dispatch it from a different thread than the one that opened the connection.
            check_same_thread=False,
            isolation_level=None,  # explicit transactions via the `transaction()` context manager
        )
        connection.row_factory = sqlite3.Row
        # WAL is required for litestream replication (AD-23).
        if self._path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _apply_schema(self) -> None:
        with self._lock:
            self._connection.executescript(SCHEMA)
            self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """Idempotent additive migration for a store created before a column existed (AD-2/AD-23).

        Additive-only and nullable, so it never rewrites data and is safe to run on every open — the
        alternative (a versioned migration framework) is overkill for the demo's single table.
        """
        existing = {row["name"] for row in self._connection.execute("PRAGMA table_info(prd_state)")}
        for name, ddl in _ADDED_COLUMNS.items():
            if name not in existing:
                self._connection.execute(f"ALTER TABLE prd_state ADD COLUMN {ddl}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """An atomic unit of work.

        AD-11 leans on this directly: a stage advance and the external id recorded by that stage are
        persisted in **one** transaction, so a crash can never leave a run pointing at a stage whose
        artifact id was not saved (which is what would cause a double-create on replay).
        """
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """A read-only borrow of the connection."""
        with self._lock:
            yield self._connection

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @property
    def path(self) -> str:
        return self._path
