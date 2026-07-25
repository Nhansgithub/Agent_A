"""`JiraAdapter` — every Jira call in the system goes through here (AD-7).

Exposes **domain verbs** (`transition_issue`, `add_comment`) rather than HTTP, so no agent writes a
request. Targets Jira Cloud REST **v3**, which means two rules the adapter enforces centrally:

* **ADF bodies.** v3 rejects a plain string for a comment or description. Every body is an ADF
  document built by `app/domain/adf.py`. The adapter refuses a string outright rather than letting it
  surface as a 400 mid-run.
* **Done-ness by category.** `statusCategory == "done"`, never a literal status name (AD-13), so the
  rule holds on a workflow whose final status is called "Closed" or "Shipped".

Retry-with-backoff and `AgentError` normalization live one layer down in `AtlassianClient`.
"""

from __future__ import annotations

from typing import Any

from app.adapters.http import AtlassianClient
from app.config.constants import prd_marker_label
from app.domain import adf
from app.domain.atlassian import JiraComment, JiraIssue, JiraTransition
from app.domain.errors import AgentError

API = "/rest/api/3"

#: Fields fetched on every issue read — enough to build a `JiraIssue` in one round trip.
_ISSUE_FIELDS = "summary,status,assignee,reporter,labels,issuetype"


class JiraAdapter:
    """Domain-verb access to one tenant's Jira."""

    __slots__ = ("_client",)

    def __init__(self, client: AtlassianClient) -> None:
        self._client = client

    # -- identity ----------------------------------------------------------------------------

    async def get_current_user(self) -> str:
        """The agent's own `accountId` (AD-10).

        Resolved once per tenant and cached by the caller — each tenant's token is a different
        account, and AD-10 is explicit that this id must have **one** source rather than being
        guessed independently by the detection guard and the publish restriction (AD-18).
        """
        body = await self._client.request("GET", f"{API}/myself", operation="get_current_user")
        return str(body.get("accountId", ""))

    # -- reads -------------------------------------------------------------------------------

    async def get_issue(self, issue_key: str) -> JiraIssue:
        body = await self._client.request(
            "GET",
            f"{API}/issue/{issue_key}",
            operation="get_issue",
            params={"fields": _ISSUE_FIELDS},
            context={"issue": issue_key},
        )
        return self._to_issue(body)

    async def search_issues(self, jql: str, *, limit: int = 50) -> list[JiraIssue]:
        """Search by JQL.

        FR-04 requires the tracking-ticket search **not** to assume a fixed location — a human may
        have created it anywhere — so callers pass a JQL that spans the configured project(s) rather
        than looking under a known parent.
        """
        body = await self._client.request(
            "POST",
            f"{API}/search/jql",
            operation="search_issues",
            json={"jql": jql, "maxResults": limit, "fields": _ISSUE_FIELDS.split(",")},
            context={"jql": jql},
        )
        return [self._to_issue(issue) for issue in (body or {}).get("issues", [])]

    async def find_issue_by_prd_marker(
        self, project_key: str, prd_id: str, *, summary_prefix: str | None = None
    ) -> JiraIssue | None:
        """Find a ticket this run already created, by its AD-11 correlation label.

        This is the *adopt an orphan* half of idempotent-create replay: a create can succeed remotely
        a beat before its id is persisted, and on replay the id looks absent. Searching for the marker
        finds that orphan instead of creating a second ticket.

        `summary_prefix` disambiguates when **several ticket types share the marker in one project** —
        a rename-request and a Review ticket both live in the Review project (FR-02a / FR-06), and a
        tracking and a Publishing ticket both live in Main (FR-04 / FR-13). Without it the search
        returns the *oldest* marked ticket, which after a rename detour is the rename request — so the
        drafting step would adopt that as the Review ticket and never create a real one. With it, the
        search scans a bounded, oldest-first set and returns the first ticket of the requested **type**,
        so it adopts a genuine orphan of that type if one exists but never mistakes another type for it.
        """
        label = prd_marker_label(prd_id)
        limit = 1 if summary_prefix is None else 25
        issues = await self.search_issues(
            f'project = "{project_key}" AND labels = "{label}" ORDER BY created ASC', limit=limit
        )
        if summary_prefix is None:
            return issues[0] if issues else None
        prefix = summary_prefix.strip().lower()
        return next((i for i in issues if i.summary.strip().lower().startswith(prefix)), None)

    async def get_transitions(self, issue_key: str) -> list[JiraTransition]:
        """The transitions legal from the issue's **current** status (AD-13 step 3)."""
        body = await self._client.request(
            "GET",
            f"{API}/issue/{issue_key}/transitions",
            operation="get_transitions",
            params={"expand": "transitions.fields"},
            context={"issue": issue_key},
        )
        transitions: list[JiraTransition] = []
        for item in (body or {}).get("transitions", []):
            to = item.get("to") or {}
            transitions.append(
                JiraTransition(
                    id=str(item.get("id", "")),
                    name=str(item.get("name", "")),
                    to_status_name=str(to.get("name", "")),
                    to_status_category=str((to.get("statusCategory") or {}).get("key", "")),
                )
            )
        return transitions

    async def get_comments(self, issue_key: str, *, limit: int = 50) -> list[JiraComment]:
        """The issue's comments, oldest first, flattened to plain text.

        The webhook path never needs this — a `comment-created` event carries the body. It exists for
        the *poll* path: the AD-22 reconciler and the local driver observe the same PM feedback by
        reading it back, and the AD-9 dedupe key (comment id) makes a poll and a webhook that saw the
        same comment collide, so feedback is interpreted once however it was noticed.
        """
        body = await self._client.request(
            "GET",
            f"{API}/issue/{issue_key}/comment",
            operation="get_comments",
            params={"maxResults": limit, "orderBy": "created"},
            context={"issue": issue_key},
        )
        comments: list[JiraComment] = []
        for item in (body or {}).get("comments", []):
            author = item.get("author") or {}
            comments.append(
                JiraComment(
                    id=str(item.get("id", "")),
                    author_account_id=str(author.get("accountId") or ""),
                    body_text=adf.extract_text(item.get("body")).strip(),
                    created=str(item.get("created") or ""),
                )
            )
        return comments

    # -- writes ------------------------------------------------------------------------------

    async def create_issue(
        self,
        *,
        project_key: str,
        summary: str,
        description: dict[str, Any],
        issue_type: str = "Task",
        assignee_account_id: str | None = None,
        reporter_account_id: str | None = None,
        prd_id: str | None = None,
        extra_labels: tuple[str, ...] = (),
    ) -> JiraIssue:
        """Create an issue at the top of the project hierarchy (never a subtask — FR-04).

        `prd_id` stamps the AD-11 correlation label in the **create payload itself**, so the marker
        is atomic with the create on the Jira side and an orphan is always findable.
        """
        self._require_adf(description, "description")

        labels = list(extra_labels)
        if prd_id:
            labels.append(prd_marker_label(prd_id))

        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": issue_type},
        }
        if labels:
            fields["labels"] = labels
        if assignee_account_id:
            fields["assignee"] = {"accountId": assignee_account_id}
        if reporter_account_id:
            fields["reporter"] = {"accountId": reporter_account_id}

        body = await self._client.request(
            "POST",
            f"{API}/issue",
            operation="create_issue",
            json={"fields": fields},
            context={"project": project_key, "summary": summary[:80]},
        )
        return JiraIssue(
            key=str((body or {}).get("key", "")),
            summary=summary,
            assignee_account_id=assignee_account_id,
            reporter_account_id=reporter_account_id,
            labels=tuple(labels),
            issue_type=issue_type,
        )

    async def add_comment(self, issue_key: str, body: dict[str, Any]) -> str:
        """Post an ADF comment. Returns the new comment id."""
        self._require_adf(body, "comment body")
        response = await self._client.request(
            "POST",
            f"{API}/issue/{issue_key}/comment",
            operation="add_comment",
            json={"body": body},
            context={"issue": issue_key},
        )
        return str((response or {}).get("id", ""))

    async def transition_issue(self, issue_key: str, transition_id: str) -> None:
        """Perform one transition.

        **Only ever called for the PRD-tracking ticket.** AD-15 forbids the agent transitioning a
        human-gate ticket (Review or Publishing) — those move by a human, and detecting that move is
        the sole approval signal. The guard for that lives in the Ticket manager (Story 2.5), which is
        the only caller.
        """
        await self._client.request(
            "POST",
            f"{API}/issue/{issue_key}/transitions",
            operation="transition_issue",
            json={"transition": {"id": transition_id}},
            context={"issue": issue_key, "transition": transition_id},
        )

    async def assign_issue(self, issue_key: str, account_id: str) -> None:
        await self._client.request(
            "PUT",
            f"{API}/issue/{issue_key}/assignee",
            operation="assign_issue",
            json={"accountId": account_id},
            context={"issue": issue_key},
        )

    async def find_user_by_email(self, email: str) -> str | None:
        """AD-12 cross-org fallback — resolve an assignee by email when accountIds are not shared.

        Consulted only after the config `identity_overrides` map; returns None if the site's privacy
        settings hide emails, in which case the caller escalates rather than mis-assigning.
        """
        body = await self._client.request(
            "GET",
            f"{API}/user/search",
            operation="find_user_by_email",
            params={"query": email},
            context={"email": email},
        )
        for user in body or []:
            if str(user.get("emailAddress", "")).lower() == email.lower():
                return str(user.get("accountId"))
        return None

    # -- internals ---------------------------------------------------------------------------

    @staticmethod
    def _require_adf(body: Any, what: str) -> None:
        """Fail fast on a plain string rather than letting Jira v3 return an opaque 400."""
        if not isinstance(body, dict) or body.get("type") != "doc":
            raise AgentError(
                message=f"Jira {what} was not an ADF document.",
                suggested_fix=(
                    "Build the body with app.domain.adf (e.g. adf.doc(adf.paragraph(...))). "
                    "Jira REST v3 rejects plain strings."
                ),
                operation="jira.validate_adf",
            )

    @staticmethod
    def _to_issue(body: dict[str, Any]) -> JiraIssue:
        fields = body.get("fields") or {}
        status = fields.get("status") or {}
        category = status.get("statusCategory") or {}
        assignee = fields.get("assignee") or {}
        reporter = fields.get("reporter") or {}
        issue_type = fields.get("issuetype") or {}
        return JiraIssue(
            key=str(body.get("key", "")),
            summary=str(fields.get("summary") or ""),
            status_name=str(status.get("name") or ""),
            status_category=str(category.get("key") or ""),
            assignee_account_id=assignee.get("accountId"),
            reporter_account_id=reporter.get("accountId"),
            labels=tuple(fields.get("labels") or ()),
            issue_type=str(issue_type.get("name") or ""),
        )
