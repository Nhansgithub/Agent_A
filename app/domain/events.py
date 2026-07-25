"""Parsed Atlassian webhook events and their AD-9 dedupe identity.

These are **domain** types: they describe *what happened* (a page was created, a gate ticket moved to
Done), independent of the HTTP transport that carried the news. Parsing a raw payload into one of
them is a transport concern and lives in `app/webhooks/events.py`.

Keeping the types here rather than in the webhook layer is what lets the router (AD-3) and the
orchestrator consume them without importing upward — the AD-1 dependency rule, which import-linter
enforces.

Four event shapes are subscribed (AD-8): Confluence ``page-created`` / ``page-updated``, and Jira
``comment-created`` / ``issue-updated``. Each exposes the two components AD-9's composite key needs —
``entity_id`` and ``version_marker`` — so the dedupe layer never has to know about payload shapes.

**Why the version marker matters.** Keying on entity id alone would be wrong in both directions: a
rename would be suppressed as a duplicate (breaking EH-04), or duplicate deliveries would loop. A
Confluence page's ``version.number`` is a monotonic integer that bumps on every edit, rename, or move
— so a genuine rename produces a *new* key and re-enters the flow, while a redelivery of the same
version collides and is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EventType(StrEnum):
    """The subscribed event types. The value is the `<event_type>` component of the dedupe key."""

    CONFLUENCE_PAGE_CREATED = "confluence.page_created"
    CONFLUENCE_PAGE_UPDATED = "confluence.page_updated"
    CONFLUENCE_PAGE_TRASHED = "confluence.page_trashed"
    """A page was deleted (moved to trash). Used to detect a UserDoc draft being removed mid-flow so
    the agent can restore/recreate it and alert the PM (FR-16), rather than silently stranding the run."""

    JIRA_COMMENT_CREATED = "jira.comment_created"
    JIRA_ISSUE_UPDATED = "jira.issue_updated"


class UnsupportedEvent(ValueError):
    """The payload is not one of the four subscribed event shapes."""


@dataclass(frozen=True, slots=True)
class ConfluencePageEvent:
    """A page created or updated in Confluence (FR-01, EH-04)."""

    event_type: EventType
    page_id: str
    version_number: int | None
    """The page's version. ``None`` when the trigger could not supply it — **Confluence Cloud
    Automation has no page-version smart value at all**, and an Automation rule is the only way to
    trigger on a page event without writing a Connect app. The webhook router resolves it from the
    page itself before the AD-9 key is computed, so an unversioned event never reaches the dedupe
    store; see :meth:`version_marker`."""

    title: str
    creator_account_id: str | None = None
    """PRD §13 Q4 / AD-12 — the Uploading PM for an FR-02a rename task. PAYLOAD-UNVERIFIED: if the
    page-created payload omits this, the Confluence adapter does a follow-up `GET page`."""

    container_id: str | None = None
    """The folder or parent this page sits in, used for AD-3 tenant routing and the FR-01 watched-
    folder check. PAYLOAD-UNVERIFIED: may be absent, in which case detection resolves it via the
    adapter's ancestors lookup (AD-14)."""

    space_key: str | None = None
    labels: tuple[str, ...] = ()
    """AD-10 defense-in-depth: a page carrying `agent-generated` never enters detection."""

    @property
    def is_trashed_event(self) -> bool:
        return self.event_type is EventType.CONFLUENCE_PAGE_TRASHED

    @property
    def entity_id(self) -> str:
        return self.page_id

    @property
    def version_marker(self) -> str:
        """Empty when the version is unknown — the caller must resolve it before keying.

        Keying on an empty marker would be actively harmful: every future edit of the page would
        produce the *same* key, so the first rename would be recorded and every later one dropped as
        a duplicate, silently killing EH-04 re-entry. `WebhookIngress` therefore refuses to emit a
        dedupe key for an unversioned page event.
        """
        return "" if self.version_number is None else str(self.version_number)

    @property
    def needs_version_resolution(self) -> bool:
        return self.version_number is None


@dataclass(frozen=True, slots=True)
class JiraCommentEvent:
    """A comment created on a Jira issue (FR-09 feedback, EH-02 admin resume)."""

    event_type: EventType
    comment_id: str
    issue_key: str
    project_key: str
    author_account_id: str
    body_text: str

    @property
    def entity_id(self) -> str:
        """A comment id is globally unique, so it needs no version marker."""
        return self.comment_id

    @property
    def version_marker(self) -> str:
        return ""


@dataclass(frozen=True, slots=True)
class JiraIssueUpdatedEvent:
    """An issue transition — the signal both human gates are detected through (FR-12, FR-14).

    ``status_category`` is what "Done" is judged by (AD-13): the *category*, never a literal status
    name, so the rule holds across differently-named workflows.
    """

    event_type: EventType
    issue_key: str
    project_key: str
    changelog_id: str
    status_category: str | None = None
    status_name: str | None = None
    author_account_id: str | None = None
    transitioned_status: bool = False
    """True when this update actually changed the status field, as opposed to any other edit."""

    @property
    def entity_id(self) -> str:
        return self.issue_key

    @property
    def version_marker(self) -> str:
        """The changelog (history) id of *this* transition — monotonic per issue."""
        return self.changelog_id

    @property
    def moved_to_done(self) -> bool:
        """AD-13 / AD-15 — done-ness is `statusCategory == done`, not a status name."""
        return self.transitioned_status and (self.status_category or "").lower() == "done"


WebhookEvent = ConfluencePageEvent | JiraCommentEvent | JiraIssueUpdatedEvent
