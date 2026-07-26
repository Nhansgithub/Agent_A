"""Value objects returned by the Atlassian adapters.

Domain types rather than raw JSON, so agents reason about `issue.is_done` instead of digging through
`fields.status.statusCategory.key` — and so the adapters stay swappable behind AD-7.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Jira's status categories. "Done-ness" is judged by category, never by a literal status name
#: (AD-13), so the rule holds across workflows that call it "Closed", "Shipped", or "Complete".
DONE_CATEGORY = "done"


@dataclass(frozen=True, slots=True)
class JiraTransition:
    """One legal transition from an issue's *current* status.

    Jira's `GET /issue/{key}/transitions` returns only transitions legal right now — which is exactly
    why AD-13 forbids assuming a direct-to-Done path exists.
    """

    id: str
    name: str
    to_status_name: str
    to_status_category: str

    @property
    def leads_to_done(self) -> bool:
        return self.to_status_category.lower() == DONE_CATEGORY


@dataclass(frozen=True, slots=True)
class JiraIssue:
    key: str
    summary: str = ""
    status_name: str = ""
    status_category: str = ""
    assignee_account_id: str | None = None
    reporter_account_id: str | None = None
    labels: tuple[str, ...] = ()
    issue_type: str = ""

    @property
    def is_done(self) -> bool:
        """AD-13 step 2 — if already done, the transition is skipped (idempotent, FR-04)."""
        return self.status_category.lower() == DONE_CATEGORY


@dataclass(frozen=True, slots=True)
class JiraComment:
    """One comment on a Jira issue, flattened to plain text (FR-09 feedback / EH-02 resume).

    The body arrives as ADF; the adapter flattens it with `adf.extract_text` so callers read what the
    human actually wrote, not a JSON tree. `author_account_id` is what distinguishes a human comment
    from the agent's own (AD-10) — the review loop only acts on human comments.
    """

    id: str
    author_account_id: str
    body_text: str
    created: str = ""


@dataclass(frozen=True, slots=True)
class InlineComment:
    """One Confluence comment on a draft page — the FR-17 inline-feedback channel.

    A reviewer highlighting a passage and choosing "Add inline comment" is giving feedback the same
    way a Jira comment does, but anchored to a specific place in the doc. `section` carries that anchor
    — the highlighted text the comment hangs off (Confluence's ``inlineProperties.originalSelection``)
    — so the agent can tell the reviewer *which* part of the draft it understood the note to be about.

    `is_inline` distinguishes a highlighted-passage comment from a page-level (footer) one: the
    Confluence "Page commented" Automation trigger fires for both, and only an inline comment carries a
    section anchor. `author_account_id` is site-wide, so it is directly usable to @-mention the exact
    reviewer on the Jira Review ticket (AD-10 — one Atlassian identity across Jira and Confluence).
    """

    id: str
    page_id: str
    author_account_id: str
    body_text: str
    section: str = ""
    is_inline: bool = True
    resolved: bool = False

    @property
    def has_suggestion(self) -> bool:
        """Whether the reviewer's note is non-empty (a bare highlight with no text still triggers)."""
        return bool(self.body_text.strip())


@dataclass(frozen=True, slots=True)
class ConfluencePage:
    id: str
    title: str
    version: int = 1
    space_id: str | None = None
    parent_id: str | None = None
    body_storage: str = ""
    labels: tuple[str, ...] = ()
    author_account_id: str | None = None
    ancestor_ids: tuple[str, ...] = field(default_factory=tuple)
    status: str = "current"
    """Confluence content status: ``current``, ``trashed`` (in the bin, restorable), or ``draft``.
    A trashed page is still readable via the API, which is what lets FR-16 recover its last content."""

    def in_folder(self, folder_id: str) -> bool:
        """FR-01 / AD-14 — is this page located in the given folder?"""
        return self.parent_id == folder_id or folder_id in self.ancestor_ids

    @property
    def is_trashed(self) -> bool:
        return self.status == "trashed"
