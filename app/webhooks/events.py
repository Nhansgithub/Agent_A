"""Parsing raw Atlassian webhook payloads into domain events.

The event *types* live in `app/domain/events.py`; this module owns only the transport concern of
turning a JSON payload into one of them.

Payload field names follow the documented Atlassian Cloud webhook shapes. They carry
``PAYLOAD-UNVERIFIED`` notes where the exact shape must be confirmed against the real tenant
(BLOCKERS B-3); the parser is deliberately tolerant of the known alternatives rather than assuming
one, because guessing wrong here means events silently fail to parse in production.
"""

from __future__ import annotations

from typing import Any

from app.domain.events import (
    ConfluencePageEvent,
    EventType,
    JiraCommentEvent,
    JiraIssueUpdatedEvent,
    UnsupportedEvent,
    WebhookEvent,
)

__all__ = [
    "ConfluencePageEvent",
    "EventType",
    "JiraCommentEvent",
    "JiraIssueUpdatedEvent",
    "UnsupportedEvent",
    "WebhookEvent",
    "parse_confluence_page_event",
    "parse_event",
    "parse_jira_comment_event",
    "parse_jira_issue_updated_event",
]


# ---------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------


def _first(payload: dict[str, Any], *paths: str) -> Any:
    """Return the first present value among dotted paths. Tolerates the known payload variants."""
    for path in paths:
        current: Any = payload
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            return current
    return None


def _optional_int(value: Any) -> int | None:
    """Parse a version that may be absent, empty, or an unresolved smart-value placeholder.

    An Automation rule renders an unknown smart value as an empty string, so `""` must read as
    "unknown" rather than crashing the parse.
    """
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _require(value: Any, field_name: str) -> str:
    if value is None or str(value) == "":
        raise UnsupportedEvent(f"webhook payload is missing required field {field_name!r}")
    return str(value)


def parse_confluence_page_event(payload: dict[str, Any]) -> ConfluencePageEvent:
    event_name = str(_first(payload, "webhookEvent", "eventType") or "")
    if "page_created" in event_name or "page_create" in event_name:
        event_type = EventType.CONFLUENCE_PAGE_CREATED
    elif "page_updated" in event_name or "page_update" in event_name:
        event_type = EventType.CONFLUENCE_PAGE_UPDATED
    else:
        raise UnsupportedEvent(f"unrecognised Confluence event {event_name!r}")

    page = payload.get("page") if isinstance(payload.get("page"), dict) else payload
    version = _first(page, "version.number", "version") or _first(payload, "page.version.number")

    # The version is OPTIONAL, not required. Confluence Cloud Automation — the only way to trigger on
    # a page event without a Connect app — exposes no page-version smart value, so a rule physically
    # cannot send one. Rejecting the event here would make the product untriggerable; instead the
    # router resolves the version from the page before the AD-9 key is built.
    return ConfluencePageEvent(
        event_type=event_type,
        page_id=_require(_first(page, "id", "contentId"), "page.id"),
        version_number=_optional_int(version),
        title=str(_first(page, "title") or ""),
        creator_account_id=_first(
            page,
            "history.createdBy.accountId",
            "version.by.accountId",
            "authorId",
            "creator.accountId",
        ),
        container_id=_first(
            page, "parentId", "parentRef.id", "containerId", "ancestors.0.id", "parent.id"
        ),
        space_key=_first(page, "spaceKey", "space.key"),
        labels=tuple(_extract_labels(page)),
    )


def _extract_labels(page: dict[str, Any]) -> list[str]:
    raw = _first(page, "metadata.labels.results", "labels.results", "labels")
    if not isinstance(raw, list):
        return []
    labels: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = item.get("name") or item.get("label")
            if name:
                labels.append(str(name))
        elif isinstance(item, str):
            labels.append(item)
    return labels


def parse_jira_comment_event(payload: dict[str, Any]) -> JiraCommentEvent:
    comment = payload.get("comment") or {}
    issue = payload.get("issue") or {}
    issue_key = _require(_first(issue, "key"), "issue.key")
    return JiraCommentEvent(
        event_type=EventType.JIRA_COMMENT_CREATED,
        comment_id=_require(_first(comment, "id"), "comment.id"),
        issue_key=issue_key,
        project_key=str(_first(issue, "fields.project.key") or issue_key.split("-", 1)[0]),
        author_account_id=str(_first(comment, "author.accountId") or ""),
        body_text=_comment_text(comment.get("body")),
    )


def _comment_text(body: Any) -> str:
    """Flatten a comment body to plain text.

    Jira v3 returns ADF (a nested document), not a string — so a naive ``str(body)`` would give the
    Feedback interpreter a Python dict repr instead of what the PM wrote.
    """
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        if body.get("type") == "text" and "text" in body:
            return str(body["text"])
        content = body.get("content")
        if isinstance(content, list):
            parts = [_comment_text(node) for node in content]
            # Block-level nodes become separate lines; inline nodes stay joined.
            separator = "\n" if body.get("type") in {"doc", "blockquote", "bulletList"} else ""
            return separator.join(p for p in parts if p)
    if isinstance(body, list):
        return "".join(_comment_text(node) for node in body)
    return ""


def parse_jira_issue_updated_event(payload: dict[str, Any]) -> JiraIssueUpdatedEvent:
    issue = payload.get("issue") or {}
    changelog = payload.get("changelog") or {}
    issue_key = _require(_first(issue, "key"), "issue.key")

    items = changelog.get("items") if isinstance(changelog.get("items"), list) else []
    status_change = next(
        (item for item in items if isinstance(item, dict) and item.get("field") == "status"), None
    )

    return JiraIssueUpdatedEvent(
        event_type=EventType.JIRA_ISSUE_UPDATED,
        issue_key=issue_key,
        project_key=str(_first(issue, "fields.project.key") or issue_key.split("-", 1)[0]),
        changelog_id=_require(_first(changelog, "id"), "changelog.id"),
        status_category=_first(issue, "fields.status.statusCategory.key"),
        status_name=_first(issue, "fields.status.name"),
        author_account_id=_first(payload, "user.accountId"),
        transitioned_status=status_change is not None,
    )


#: Atlassian `webhookEvent` values → the parser that handles them.
_PARSERS: dict[str, Any] = {
    "page_created": parse_confluence_page_event,
    "page_updated": parse_confluence_page_event,
    "comment_created": parse_jira_comment_event,
    "jira:issue_updated": parse_jira_issue_updated_event,
    "issue_updated": parse_jira_issue_updated_event,
}


def parse_event(payload: dict[str, Any]) -> WebhookEvent:
    """Parse any subscribed payload, or raise :class:`UnsupportedEvent`.

    An unsupported event is not an error to escalate — Atlassian sends more than we subscribe to, and
    the ingress simply drops what it does not handle, without side effects (AD-8).
    """
    event_name = str(_first(payload, "webhookEvent", "eventType") or "").strip()
    parser = _PARSERS.get(event_name)
    if parser is None:
        # Some Automation-rule payloads omit `webhookEvent`; fall back to structural detection.
        if "comment" in payload and "issue" in payload:
            parser = parse_jira_comment_event
        elif "changelog" in payload and "issue" in payload:
            parser = parse_jira_issue_updated_event
        else:
            raise UnsupportedEvent(f"no parser for webhook event {event_name!r}")
    return parser(payload)
