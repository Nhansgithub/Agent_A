"""The AD-9 composite idempotency key.

    <tenant_id>:<event_type>:<entity_id>:<version_marker>

Lives in ``app/domain/`` because two layers must agree on it exactly: the webhook layer derives it
from an event, and the repository stores it under a UNIQUE constraint. A disagreement between those
two would either loop on duplicates or silently drop genuine updates — which is why AD-9 insists
there is one definition, not two.

Per event shape (AD-9):

* Confluence page events — ``entity_id`` = page id, ``version_marker`` = ``version.number``
  (monotonic; bumps on every edit, rename, or move, so EH-04's rename re-enters).
* Jira comment-created — ``entity_id`` = comment id, which is already unique; no version marker.
* Jira issue-updated — ``entity_id`` = issue key, ``version_marker`` = the changelog (history) id of
  that transition. This is what lets an AD-22 reconcile-poll and a webhook observing the *same*
  transition collide on the UNIQUE constraint, so a gate-Done can never double-advance a stage.
"""

from __future__ import annotations

from dataclasses import dataclass

SEPARATOR = ":"


@dataclass(frozen=True, slots=True)
class DedupeKey:
    """One event's idempotency identity."""

    tenant_id: str
    event_type: str
    entity_id: str
    version_marker: str = ""

    def __post_init__(self) -> None:
        for name in ("tenant_id", "event_type", "entity_id"):
            if not getattr(self, name):
                raise ValueError(f"dedupe key component {name!r} must not be empty")

    @property
    def value(self) -> str:
        return SEPARATOR.join(
            (self.tenant_id, self.event_type, self.entity_id, self.version_marker)
        )

    def __str__(self) -> str:
        return self.value


def dedupe_key_for(tenant_id: str, event: object) -> DedupeKey:
    """Derive the key from any parsed webhook event.

    The event objects expose ``event_type``, ``entity_id``, and ``version_marker`` precisely so this
    derivation is shape-agnostic — a new event type cannot accidentally get a different key scheme.
    """
    event_type = getattr(event, "event_type", None)
    entity_id = getattr(event, "entity_id", None)
    if event_type is None or entity_id is None:
        raise TypeError(f"{type(event).__name__} does not expose a dedupe identity")
    return DedupeKey(
        tenant_id=tenant_id,
        event_type=str(event_type),
        entity_id=str(entity_id),
        version_marker=str(getattr(event, "version_marker", "") or ""),
    )
