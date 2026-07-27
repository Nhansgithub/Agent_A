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


@dataclass(frozen=True, slots=True)
class ConfluencePageRef:
    """A lightweight reference to a page found while crawling a folder tree (Epic 7, AD-14).

    Just enough to decide whether to keep the page and how it sits in the tree; the body is fetched
    separately with a `get_page` call only for pages the crawl keeps, so listing a large space does not
    pull every body up front. `parent_id` is the immediate container — a folder id or a parent-page id,
    disambiguated by `parent_type` — which is what the Agent B deterministic linker (S-B2) turns into
    hierarchy edges.
    """

    id: str
    title: str
    parent_id: str
    parent_type: str  # "folder" | "page"


@dataclass(frozen=True, slots=True)
class ConfluenceAttachment:
    """An attachment on a Confluence page — the source for Agent B's image assets (Epic 7, S-B10).

    `download_path` is the API-relative path to the binary (Confluence's `_links.download`), fetched
    with the transport's binary read. `media_type` is the MIME type, used to keep only images.
    """

    id: str
    filename: str
    media_type: str
    download_path: str

    @property
    def is_image(self) -> bool:
        return self.media_type.lower().startswith("image/")
