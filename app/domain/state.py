"""The per-PRD state record — the single authoritative durable truth (AD-2, AD-11, PRD §10).

Atlassian holds the *content* artifacts (pages, tickets, comments), but the **run state** — the stage
cursor, the recorded external ids, and the dedupe log — is durable in exactly one place: the
repository-owned SQLite store. It is deliberately **not** reconstructable from Atlassian: lose it
mid-run and a redelivered webhook would double-create or re-publish. That is precisely why AD-23
replicates the store off-box.

This module is pure domain — no I/O, no SQL. The repository maps it to and from rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from app.domain.stage import PendingGate, QueueStatus, Stage


def utc_now() -> datetime:
    """ISO-8601 UTC everywhere (Spine → Consistency Conventions)."""
    return datetime.now(UTC)


def new_correlation_id() -> str:
    """The id that ties a run's LangSmith traces, logs, and EH-01 error comment together."""
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class PrdState:
    """One PRD's run state. Mutated only through the repository; `stage` only by the orchestrator."""

    # --- identity -----------------------------------------------------------------------------
    prd_id: str
    """Confluence page id — the stable key, and the correlation marker stamped on every artifact
    this run creates so a resume can adopt an orphan instead of double-creating it (AD-11)."""

    project_id: str
    """Which tenant config applies (AD-3)."""

    # --- lifecycle ----------------------------------------------------------------------------
    stage: Stage = Stage.DETECTED
    pending_gate: PendingGate = PendingGate.NONE
    queue_status: QueueStatus = QueueStatus.QUEUED
    last_good_checkpoint: Stage | None = None
    """EH-02 — the stage to re-enter on `@agent resume`. Never the start of the flow."""

    # --- recorded external ids (AD-11 idempotent-create guards) -------------------------------
    # A step reuses the id if present; if absent it does a find-or-create keyed on the `prd_id`
    # correlation marker, adopting an orphan rather than creating a duplicate.
    prd_tracking_ticket_key: str | None = None
    review_ticket_key: str | None = None
    publishing_ticket_key: str | None = None
    rename_request_ticket_key: str | None = None
    """FR-02a — entirely separate from the Review ticket; tracked so a re-upload does not re-file it."""

    userdoc_page_id: str | None = None

    # --- content / metrics --------------------------------------------------------------------
    prd_title: str | None = None
    review_round: int = 0
    """FR-11 / NFR-09 — increments per applied feedback round. Surfaced in LangSmith with per-round
    token cost as the guardrail on the uncapped redraft loop. Never a hard cap."""

    md_export_path: str | None = None
    """**Deprecated (D-44/S-B8):** the `.md` export is retired; this stays nullable and is never
    written (Agent B captures the published UserDoc via its pull). Kept so the DB needs no rebuild."""

    pending_feedback: str | None = None
    """FR-10/FR-11 — the confirmed structured feedback awaiting application in the `revising` stage.
    Persisted (not held in memory) so a crash between confirming feedback and applying it resumes
    correctly rather than losing the PM's input (AD-11)."""

    pending_deletion_page_id: str | None = None
    """FR-16 — set to the deleted draft's page id when a deletion has been detected and the agent has
    asked the Reviewer PM whether it was intentional. While set, the run is awaiting the PM's
    decision (`pending_gate = PM_DELETION_DECISION`) and a PM comment routes to the deletion-decision
    handler rather than the feedback loop. The agent recovers the draft **only** if the PM confirms it
    was a mistake — it never auto-recovers."""

    active_reviewer_account_id: str | None = None
    """FR-17 — the account the review loop is currently in conversation with, when it is **not** the
    configured Reviewer PM. Set to the exact author of a Confluence inline comment so the whole
    confirmation sub-conversation @-mentions the person who raised the note, not the config PM. `None`
    for an ordinary Jira-comment thread, in which case the loop addresses the configured PM as before."""

    # --- AD-18 publish sub-checkpoints ---------------------------------------------------------
    # The publish transaction's side-effects are individually guarded so resuming the `publishing`
    # stage skips what already succeeded and never re-applies it.
    restriction_applied_at: datetime | None = None
    moved_to_published_at: datetime | None = None
    md_exported_at: datetime | None = None
    """**Deprecated (D-44/S-B8):** export retired; nullable, never written. Kept — no DB rebuild."""

    # --- error / observability ------------------------------------------------------------------
    correlation_id: str = field(default_factory=new_correlation_id)
    last_error: str | None = None
    liveness_alerted_at: datetime | None = None
    """AD-22 — set when the liveness sweep alerts, so a stuck run is alerted once per threshold
    crossing rather than on every sweep."""

    # --- timestamps ------------------------------------------------------------------------------
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None

    # --- §10 `dedupe_keys` -----------------------------------------------------------------------
    dedupe_keys: tuple[str, ...] = ()
    """A **read-only projection** of the `processed_events` table, populated on request. AD-9 makes
    `processed_events` the single authoritative dedupe store; this is never a second write target.
    Empty unless a caller explicitly asked the repository to populate it."""

    # -- derived views ----------------------------------------------------------------------------

    @property
    def is_parked(self) -> bool:
        """Waiting on a human, indefinitely and by design (FR-12, FR-14, EH-09)."""
        from app.domain.stage import PARKED_STAGES

        return self.stage in PARKED_STAGES

    @property
    def is_complete(self) -> bool:
        return self.stage is Stage.COMPLETE

    @property
    def is_errored(self) -> bool:
        return self.stage is Stage.ERROR

    def with_changes(self, **changes: object) -> PrdState:
        """A copy with `updated_at` refreshed. The repository persists the result."""
        changes.setdefault("updated_at", utc_now())
        return replace(self, **changes)  # type: ignore[arg-type]
