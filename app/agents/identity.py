"""Confluence → Jira identity resolution for the FR-02a rename task (AD-12).

The rename-request task is assigned to the **Uploading PM** — the person who created the mislabeled
page — not the config Reviewer PM. Within one Atlassian org the `accountId` is shared across Jira and
Confluence, so the common case is trivial: use the page-creator's `accountId` directly.

Full-hardening cross-org fallback (AD-12, un-deferred by the r2 roundtable), tried in order:

1. **Same-org** — the Confluence `accountId` is valid as a Jira assignee. The default; no mapping.
2. **Config override** — `identity_overrides[confluence_account_id]` gives the Jira `accountId`
   explicitly, for orgs that do not share ids.
3. **Email match** — resolve the Jira user whose email matches the Confluence account's email.

Only *fully-automatic* cross-org auto-resolution (neither an override nor an email match) stays
deferred. When all three fail, the caller escalates rather than mis-assigning to the wrong human.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.adapters.jira import JiraAdapter
from app.config.schema import TenantConfig


class ResolutionMethod(StrEnum):
    SAME_ORG = "same_org"
    CONFIG_OVERRIDE = "config_override"
    EMAIL_MATCH = "email_match"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    account_id: str | None
    method: ResolutionMethod

    @property
    def resolved(self) -> bool:
        return self.account_id is not None


class IdentityResolver:
    """Resolves a Confluence page-creator to a Jira assignee (AD-12)."""

    __slots__ = ("_jira",)

    def __init__(self, jira: JiraAdapter) -> None:
        self._jira = jira

    async def resolve_uploading_pm(
        self,
        *,
        confluence_account_id: str | None,
        confluence_email: str | None,
        tenant: TenantConfig,
    ) -> IdentityResolution:
        """Resolve the assignee for an FR-02a rename task.

        Args:
            confluence_account_id: the page creator's Confluence accountId, from the event.
            confluence_email: their email if the event carried it — enables the email fallback.
        """
        if confluence_account_id:
            # 2. An explicit config override wins — an operator's deliberate cross-org mapping must
            #    never be silently ignored in favour of the same-org assumption.
            override = tenant.identity_overrides.get(confluence_account_id)
            if override:
                return IdentityResolution(override, ResolutionMethod.CONFIG_OVERRIDE)
            # 1. Same-org common case: the shared accountId is the Jira assignee, no mapping.
            return IdentityResolution(confluence_account_id, ResolutionMethod.SAME_ORG)

        # 3. The event carried no accountId (some payloads omit it) — fall back to email match.
        if confluence_email:
            matched = await self._jira.find_user_by_email(confluence_email)
            if matched:
                return IdentityResolution(matched, ResolutionMethod.EMAIL_MATCH)

        return IdentityResolution(None, ResolutionMethod.UNRESOLVED)
