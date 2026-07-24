"""The only layer that runs SQL. State record + processed_events (AD-2, AD-9, AD-11).

`Repository` is the single injection point the webhook layer and orchestrator receive. It exposes the
two sub-repositories and the handful of operations that must span both tables atomically — most
importantly :meth:`Repository.admit`, which is where AD-9's "recorded at admission, not on receipt"
rule is actually implemented.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.dedupe import DedupeKey
from app.domain.state import PrdState
from app.repository.database import Database
from app.repository.event_repository import EventRepository
from app.repository.state_repository import StateRepository, UnknownPrd

__all__ = [
    "Database",
    "EventRepository",
    "Repository",
    "StateRepository",
    "UnknownPrd",
]


class Repository:
    """Facade over the single durable store."""

    __slots__ = ("_db", "events", "state")

    def __init__(self, database: Database) -> None:
        self._db = database
        self.state = StateRepository(database)
        self.events = EventRepository(database)

    @classmethod
    def open(cls, path: str | Path) -> Repository:
        return cls(Database(path))

    def admit(self, key: DedupeKey, state: PrdState) -> PrdState | None:
        """Admit a **new** PRD to the flow: record the dedupe key and create its state row, atomically.

        This is the AD-9 admission point. Doing both in one transaction is what makes the two failure
        modes impossible:

        * *Recorded but not admitted* — a crash here would lose the event forever, since Atlassian
          delivery is best-effort. Rolling back leaves it safely redeliverable.
        * *Admitted twice* — a concurrent duplicate loses the UNIQUE-insert race on `processed_events`
          and the whole transaction rolls back, so no second PRD row is created.

        Returns:
            The created state, or ``None`` if this event was already admitted (drop it).
        """
        with self._db.transaction() as conn:
            if not EventRepository.record_in(conn, key, prd_id=state.prd_id):
                return None
            row = StateRepository.to_row(state)
            columns = ", ".join(row)
            placeholders = ", ".join(f":{c}" for c in row)
            conn.execute(f"INSERT INTO prd_state ({columns}) VALUES ({placeholders})", row)
        return state

    def record_event_for(self, key: DedupeKey, prd_id: str) -> bool:
        """Record a follow-up event (a comment, a transition) against an existing run.

        Returns False when the event was already recorded — which is precisely how a webhook and an
        AD-22 reconcile-poll that observed the *same* gate transition collide, so only one advances
        the stage.
        """
        return self.events.record(key, prd_id=prd_id)

    def close(self) -> None:
        self._db.close()
