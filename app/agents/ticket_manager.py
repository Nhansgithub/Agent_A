"""Ticket manager — all Jira search/create/transition/assign (FR-04, FR-02a, FR-06, FR-13, AD-13).

This is the one component allowed to drive a Jira transition, and it enforces the rule that keeps
AD-15 intact: it auto-transitions **only the PRD-tracking ticket**. It never transitions a Review or
Publishing ticket — those are human gates, and detecting a human moving them is the sole approval
signal. That restriction is a method-level guard here, not just a convention.

Driving a ticket to Done follows AD-13 exactly, because a workflow may have no direct path:

1. already `done`-category → skip (idempotent, FR-04);
2. else read the legal transitions *from the current status*;
3. a direct transition to a `done`-category status → take it;
4. none directly available → walk the **config-declared** `preferred_transition_path` hop by hop,
   re-reading the legal set at each hop;
5. no path configured, or a hop is illegal → escalate to the admin (EH-01) rather than guess.

"Done-ness" is always `statusCategory == done`, never a literal status name (AD-13), so this holds on
a workflow whose final status is "Closed" or "Shipped".
"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.jira import JiraAdapter
from app.config.schema import TenantConfig
from app.domain.atlassian import JiraIssue
from app.domain.errors import AgentError

#: Ticket types the agent must NEVER auto-transition — those move by a human (AD-15).
_HUMAN_GATE = frozenset({"review", "publishing"})

_MAX_TRANSITION_HOPS = 6  # a sane ceiling on the configured path; also stops any cyclic workflow


@dataclass(frozen=True, slots=True)
class TrackingTicketResult:
    issue: JiraIssue
    created: bool
    """True if the agent created it; False if it found and reused an existing one (FR-04)."""

    transitioned: bool
    """True if the agent moved it to Done; False if it was already there (idempotent skip)."""


class TicketManager:
    """Jira ticket operations, with the AD-13 transition rules and the AD-15 gate guard."""

    __slots__ = ("_jira",)

    def __init__(self, jira: JiraAdapter) -> None:
        self._jira = jira

    # -- FR-04: the PRD-tracking ticket --------------------------------------------------------

    async def locate_or_create_tracking_ticket(
        self,
        *,
        tenant: TenantConfig,
        prd_id: str,
        prd_name: str,
        prd_url: str,
    ) -> TrackingTicketResult:
        """Find the PRD-tracking ticket anywhere, or create it, then drive it to Done (FR-04).

        Order matters for AD-11 idempotency: first adopt an orphan created by a previous crashed
        attempt (found by the `prd_id` correlation label), then search by name, then create.
        """
        issue = await self._jira.find_issue_by_prd_marker(tenant.jira_main_project_key, prd_id)
        created = False

        if issue is None:
            issue = await self._search_by_name(tenant.jira_main_project_key, prd_name)

        if issue is None:
            issue = await self._jira.create_issue(
                project_key=tenant.jira_main_project_key,
                summary=f"PRD tracking: {prd_name}",
                description=self._tracking_description(prd_name, prd_url),
                issue_type="Task",
                prd_id=prd_id,
            )
            created = True
            # Re-read: the create response is sparse, and the transition logic needs the status.
            issue = await self._jira.get_issue(issue.key)

        transitioned = await self.drive_to_done(issue, tenant)
        return TrackingTicketResult(issue=issue, created=created, transitioned=transitioned)

    async def _search_by_name(self, project_key: str, prd_name: str) -> JiraIssue | None:
        """FR-04 — search across the project, not under a fixed parent; a human ticket may be anywhere."""
        escaped = prd_name.replace('"', '\\"')
        candidates = await self._jira.search_issues(
            f'project = "{project_key}" AND summary ~ "{escaped}" ORDER BY created ASC', limit=5
        )
        return candidates[0] if candidates else None

    # -- AD-13: drive a ticket to a done-category status ---------------------------------------

    async def drive_to_done(
        self, issue: JiraIssue, tenant: TenantConfig, *, ticket_role: str = "tracking"
    ) -> bool:
        """Move a ticket to Done. Returns True if it transitioned, False if already done.

        `ticket_role` is a safety interlock: the agent may drive only the tracking ticket. Passing a
        human-gate role raises rather than risking a short-circuited gate (AD-15).
        """
        if ticket_role in _HUMAN_GATE:
            raise AgentError(
                message=f"Refusing to auto-transition a {ticket_role} ticket ({issue.key}).",
                suggested_fix=(
                    "Review and Publishing tickets are human gates — only a human moves them to "
                    "Done, and detecting that is the sole approval signal (AD-15). This is a bug in "
                    "the caller, not an operational issue."
                ),
                operation="jira.transition_issue",
                context={"issue": issue.key, "role": ticket_role},
            )

        # Re-read the status: the caller's copy may be stale, and step 2 must reflect reality.
        current = await self._jira.get_issue(issue.key)
        if current.is_done:  # AD-13 step 2 — idempotent skip
            return False

        transitions = await self._jira.get_transitions(current.key)
        direct = next((t for t in transitions if t.leads_to_done), None)  # step 3
        if direct is not None:
            await self._jira.transition_issue(current.key, direct.id)
            return True

        # step 4: no direct path — walk the config-declared preferred path hop by hop.
        if tenant.preferred_transition_path:
            await self._walk_preferred_path(current, tenant)
            return True

        # step 5: nothing configured — escalate rather than guess a multi-hop route.
        raise AgentError(
            message=(
                f"No direct transition to a Done-category status is available for {current.key} "
                f"(current status: {current.status_name!r})."
            ),
            suggested_fix=(
                "This workflow needs intermediate steps. Set `preferred_transition_path` for this "
                "tenant in config/registry.yaml to the ordered transition names that reach Done "
                "(AD-13), then reply to resume."
            ),
            operation="jira.drive_to_done",
            context={"issue": current.key, "status": current.status_name},
        )

    async def _walk_preferred_path(self, issue: JiraIssue, tenant: TenantConfig) -> None:
        """Traverse the configured transition names, re-checking the legal set at each hop (AD-13)."""
        current = issue
        for _ in range(_MAX_TRANSITION_HOPS):
            if current.is_done:
                return
            legal = await self._jira.get_transitions(current.key)
            legal_by_name = {t.name.lower(): t for t in legal}

            hop = next(
                (
                    legal_by_name[name.lower()]
                    for name in tenant.preferred_transition_path
                    if name.lower() in legal_by_name
                ),
                None,
            )
            if hop is None:
                raise AgentError(
                    message=(
                        f"Configured transition path is stuck at {current.key} "
                        f"(status {current.status_name!r}): none of "
                        f"{tenant.preferred_transition_path} is legal here."
                    ),
                    suggested_fix=(
                        "Fix `preferred_transition_path` for this tenant to match the real workflow "
                        "from this status (AD-13), then reply to resume."
                    ),
                    operation="jira.drive_to_done",
                    context={"issue": current.key, "status": current.status_name},
                )
            await self._jira.transition_issue(current.key, hop.id)
            current = await self._jira.get_issue(current.key)

        if not current.is_done:
            raise AgentError(
                message=f"Transition path for {current.key} did not reach Done within "
                f"{_MAX_TRANSITION_HOPS} hops.",
                suggested_fix="Check `preferred_transition_path` for a loop or a missing final hop.",
                operation="jira.drive_to_done",
                context={"issue": current.key},
            )

    # -- FR-02a: the rename-request task -------------------------------------------------------

    async def create_rename_request(
        self,
        *,
        tenant: TenantConfig,
        prd_id: str,
        page_title: str,
        page_url: str,
        assignee_account_id: str | None,
        reason: str,
    ) -> JiraIssue:
        """Create the rename-request task in the **Review** project (FR-02a).

        Entirely separate from the draft-review ticket. Assigned to the Uploading PM (the page
        creator, resolved by `IdentityResolver`), **not** the config Reviewer PM. If the assignee
        could not be resolved (cross-org, no override, no email match), the task is still created —
        unassigned and tagged for the admin — rather than silently mis-assigned or dropped.
        """
        return await self._jira.create_issue(
            project_key=tenant.jira_review_project_key,
            summary=f"Please confirm & rename PRD page: {page_title}",
            description=self._rename_description(page_title, page_url, reason, assignee_account_id),
            issue_type="Task",
            assignee_account_id=assignee_account_id,
            prd_id=prd_id,
        )

    @staticmethod
    def _rename_description(title: str, url: str, reason: str, assignee: str | None) -> dict:
        from app.domain import adf

        blocks = [
            adf.paragraph(
                adf.text("A page in the watched PRD folder could not be processed automatically: "),
                adf.strong(reason),
                adf.text("."),
            ),
            adf.paragraph(
                adf.text("Page: "),
                adf.link(title, url) if url else adf.text(title),
            ),
            adf.paragraph(adf.text("If this is a finalized PRD, please:")),
            adf.bullet_list(
                "Rename it to the form `final_PRD_<name>` (this is what detection matches).",
                "Make sure it is a complete PRD, not a template or draft.",
            ),
            adf.paragraph(
                adf.text(
                    "Once renamed, the flow re-starts automatically — no further action here. If "
                    "this is not a PRD, you can close this task."
                )
            ),
        ]
        if not assignee:
            blocks.insert(
                0,
                adf.paragraph(
                    adf.strong("Note: "),
                    adf.text(
                        "the page author could not be resolved to a Jira user, so this task is "
                        "unassigned. Please route it to whoever uploaded the page."
                    ),
                ),
            )
        return adf.doc(*blocks)

    # -- FR-06/FR-13: create a ticket + post a comment -----------------------------------------

    async def create_review_ticket(
        self, *, tenant: TenantConfig, prd_id: str, userdoc_title: str, draft_page_url: str
    ) -> JiraIssue:
        """Create the Review ticket in the Review project, assigned to the Reviewer PM (FR-06).

        The agent never transitions this ticket — the PM moving it to Done is the sole PASS signal
        (AD-15). The `prd_id` marker lets a resume adopt it instead of creating a second one (AD-11).
        """
        return await self._jira.create_issue(
            project_key=tenant.jira_review_project_key,
            summary=f"Review UserDoc: {userdoc_title}",
            description=self._review_description(userdoc_title, draft_page_url),
            issue_type="Task",
            assignee_account_id=tenant.pm_account_id,
            prd_id=prd_id,
        )

    async def create_publishing_ticket(
        self, *, tenant: TenantConfig, prd_id: str, userdoc_title: str, draft_page_url: str
    ) -> JiraIssue:
        """Create the Publishing ticket in the Main project for the Head of Product (FR-13).

        Also a human gate — the agent never transitions it (AD-15).
        """
        return await self._jira.create_issue(
            project_key=tenant.jira_main_project_key,
            summary=f"Approve & publish UserDoc: {userdoc_title}",
            description=self._publishing_description(userdoc_title, draft_page_url),
            issue_type="Task",
            assignee_account_id=tenant.head_of_product_account_id,
            reporter_account_id=tenant.head_of_product_account_id,
            prd_id=prd_id,
        )

    async def find_ticket_by_marker(self, project_key: str, prd_id: str) -> JiraIssue | None:
        """Adopt-orphan helper for the Review/Publishing tickets (AD-11)."""
        return await self._jira.find_issue_by_prd_marker(project_key, prd_id)

    async def comment(self, issue_key: str, body: dict) -> str:
        """Post an ADF comment (FR-07, FR-11, FR-13). Body must be an ADF document."""
        return await self._jira.add_comment(issue_key, body)

    async def discussion(self, issue_key: str, *, limit: int = 30):
        """The ticket's comment thread, oldest first — the review conversation (FR-10).

        Domain verb over the Jira read: the review loop reasons about the *discussion* on a ticket,
        not raw comments. Returns `JiraComment`s flattened to plain text.
        """
        return await self._jira.get_comments(issue_key, limit=limit)

    # -- descriptions --------------------------------------------------------------------------

    @staticmethod
    def _review_description(title: str, url: str) -> dict:
        from app.domain import adf

        return adf.doc(
            adf.paragraph(
                adf.text("Review ticket for the UserDoc draft "),
                adf.strong(title),
                adf.text("."),
            ),
            adf.paragraph(
                adf.text("Draft: "),
                adf.link("open the draft", url) if url else adf.text("(link unavailable)"),
            ),
            adf.paragraph(
                adf.text(
                    "See the pinned comment for how to give feedback and how to approve. Moving this "
                    "ticket to Done is the only way to pass the draft."
                )
            ),
        )

    @staticmethod
    def _publishing_description(title: str, url: str) -> dict:
        from app.domain import adf

        return adf.doc(
            adf.paragraph(
                adf.text("The Reviewer PM has passed the UserDoc "),
                adf.strong(title),
                adf.text(". It is ready to publish to production."),
            ),
            adf.paragraph(
                adf.text("Passed draft: "),
                adf.link("open the draft", url) if url else adf.text("(link unavailable)"),
            ),
            adf.paragraph(
                adf.strong("To approve publishing: "),
                adf.text(
                    "transition this ticket to Done. That is the sole approval signal — nothing is "
                    "published until you do."
                ),
            ),
        )

    @staticmethod
    def _tracking_description(prd_name: str, prd_url: str) -> dict:
        from app.domain import adf

        return adf.doc(
            adf.paragraph(
                adf.text("Automated PRD-tracking ticket for "),
                adf.strong(prd_name),
                adf.text("."),
            ),
            adf.paragraph(
                adf.text("Source PRD: "),
                adf.link(prd_name, prd_url) if prd_url else adf.text("(link unavailable)"),
            ),
            adf.paragraph(
                adf.text(
                    "This ticket is created and moved to Done automatically to record that UserDoc "
                    "generation is underway. It is not a human gate."
                )
            ),
        )
