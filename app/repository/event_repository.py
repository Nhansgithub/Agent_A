"""The `processed_events` dedupe store — AD-9's single authoritative source of "already seen".

Two properties do the real work:

* **A UNIQUE constraint, not a read-then-write.** Two concurrent deliveries of the same event both
  see "not processed" if you check first and insert later. Here the insert *is* the check: one wins,
  the loser gets an ``IntegrityError`` and is dropped safely.
* **Recorded at admission, not on receipt.** "Processed" means "admitted to the flow". If the service
  crashes after recording but before admitting, the event would be permanently lost — Atlassian
  delivery is best-effort and will not send it again on request. So the key is written in the *same
  transaction* as the first state write, and a crash before that leaves the event safely redeliverable.

This table is deliberately **not** nested inside a PRD row: a `page-created` event arrives before any
PRD row exists. The §10 `dedupe_keys` field is at most a read-only projection of this table
(`StateRepository.dedupe_keys_for`), never a second write target.
"""

from __future__ import annotations

import sqlite3

from app.domain.dedupe import DedupeKey
from app.domain.state import utc_now
from app.repository.database import Database, to_iso


class EventRepository:
    """Reads and writes the dedupe log."""

    __slots__ = ("_db",)

    def __init__(self, database: Database) -> None:
        self._db = database

    def is_processed(self, key: DedupeKey) -> bool:
        """The cheap ingestion-time check (AD-8 step 2).

        Advisory only — it lets an obvious duplicate exit early without doing work. The *authoritative*
        guard is the UNIQUE constraint at admission, because between this check and that write another
        delivery may arrive.
        """
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_events WHERE dedupe_key = ?", (key.value,)
            ).fetchone()
        return row is not None

    def record(self, key: DedupeKey, *, prd_id: str | None = None) -> bool:
        """Record an event as admitted.

        Returns:
            True if this call admitted the event; False if it was already recorded (a duplicate
            delivery, or a webhook and an AD-22 reconcile-poll observing the same transition).
        """
        with self._db.transaction() as conn:
            return self.record_in(conn, key, prd_id=prd_id)

    @staticmethod
    def record_in(conn: sqlite3.Connection, key: DedupeKey, *, prd_id: str | None = None) -> bool:
        """Record within a caller's transaction, so the key and the state write commit together."""
        try:
            conn.execute(
                "INSERT INTO processed_events "
                "(dedupe_key, tenant_id, event_type, entity_id, version_marker, prd_id, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    key.value,
                    key.tenant_id,
                    key.event_type,
                    key.entity_id,
                    key.version_marker or None,
                    prd_id,
                    to_iso(utc_now()),
                ),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def count(self) -> int:
        with self._db.read() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM processed_events").fetchone()["n"])
