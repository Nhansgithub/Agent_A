"""Stories 2.6 / 2.8 — rename-request task + cross-org identity fallback (FR-02a, AD-12, AD-9)."""

from __future__ import annotations

from app.agents.identity import IdentityResolver, ResolutionMethod
from app.agents.ticket_manager import TicketManager
from app.domain.atlassian import JiraIssue
from app.domain.dedupe import DedupeKey
from app.domain.events import EventType
from tests.conftest import tenant_entry
from tests.test_ticket_manager import FakeJira

from app.config.schema import TenantConfig  # isort: skip

TENANT = TenantConfig.model_validate({**tenant_entry(), "project_id": "tenant_one"})


class IdentityJira(FakeJira):
    def __init__(self, *, email_hits: dict[str, str] | None = None) -> None:
        super().__init__()
        self._email_hits = email_hits or {}

    async def find_user_by_email(self, email):
        return self._email_hits.get(email.lower())


# ---------------------------------------------------------------------------------------------
# Story 2.8 — identity resolution (AD-12).
# ---------------------------------------------------------------------------------------------


async def test_same_org_uses_the_confluence_account_id_directly() -> None:
    """AD-12 — within one org the accountId is shared; no mapping table for the common case."""
    resolution = await IdentityResolver(IdentityJira()).resolve_uploading_pm(
        confluence_account_id="acct-uploader", confluence_email=None, tenant=TENANT
    )
    assert resolution.account_id == "acct-uploader"
    assert resolution.method is ResolutionMethod.SAME_ORG


async def test_config_override_takes_precedence() -> None:
    """A deliberate cross-org mapping must not be silently ignored."""
    tenant = TenantConfig.model_validate(
        {**tenant_entry(identity_overrides={"conf-123": "jira-456"}), "project_id": "t"}
    )
    resolution = await IdentityResolver(IdentityJira()).resolve_uploading_pm(
        confluence_account_id="conf-123", confluence_email=None, tenant=tenant
    )
    assert resolution.account_id == "jira-456"
    assert resolution.method is ResolutionMethod.CONFIG_OVERRIDE


async def test_email_fallback_when_the_event_carried_no_account_id() -> None:
    """AD-12 — the email-match fallback ships (full hardening), not just as a seam."""
    jira = IdentityJira(email_hits={"uploader@example.com": "jira-from-email"})
    resolution = await IdentityResolver(jira).resolve_uploading_pm(
        confluence_account_id=None, confluence_email="uploader@example.com", tenant=TENANT
    )
    assert resolution.account_id == "jira-from-email"
    assert resolution.method is ResolutionMethod.EMAIL_MATCH


async def test_unresolved_when_nothing_matches() -> None:
    """Fully-automatic zero-config cross-org resolution stays deferred — this returns unresolved."""
    jira = IdentityJira(email_hits={})
    resolution = await IdentityResolver(jira).resolve_uploading_pm(
        confluence_account_id=None, confluence_email="stranger@example.com", tenant=TENANT
    )
    assert not resolution.resolved
    assert resolution.method is ResolutionMethod.UNRESOLVED


async def test_unresolved_when_no_identity_at_all() -> None:
    resolution = await IdentityResolver(IdentityJira()).resolve_uploading_pm(
        confluence_account_id=None, confluence_email=None, tenant=TENANT
    )
    assert not resolution.resolved


# ---------------------------------------------------------------------------------------------
# Story 2.6 — the rename-request task (FR-02a).
# ---------------------------------------------------------------------------------------------


async def test_rename_task_is_created_in_the_review_project() -> None:
    """FR-02a — the Review project, not Main; and assigned to the uploader, not the config PM."""
    jira = FakeJira()

    async def create_issue(
        *,
        project_key,
        summary,
        description,
        issue_type="Task",
        assignee_account_id=None,
        prd_id=None,
        **kw,
    ):
        jira.created.append(
            {
                "project_key": project_key,
                "assignee": assignee_account_id,
                "prd_id": prd_id,
                "summary": summary,
            }
        )
        return JiraIssue(key=f"{project_key}-1", summary=summary)

    jira.create_issue = create_issue  # type: ignore[assignment]

    await TicketManager(jira).create_rename_request(
        tenant=TENANT,
        prd_id="page-1",
        page_title="Widget Notes",
        page_url="https://x/page-1",
        assignee_account_id="acct-uploader",
        reason="title does not match final_PRD_<name>",
    )

    created = jira.created[0]
    assert created["project_key"] == TENANT.jira_review_project_key
    assert created["project_key"] != TENANT.jira_main_project_key
    assert created["assignee"] == "acct-uploader", "assigned to the Uploading PM, not the config PM"
    assert created["assignee"] != TENANT.pm_account_id
    assert created["prd_id"] == "page-1", (
        "AD-11 correlation marker so a re-upload does not re-file it"
    )


async def test_rename_task_is_created_unassigned_when_identity_is_unresolved() -> None:
    """Cross-org with no override/email: create it for the admin rather than mis-assign or drop."""
    jira = FakeJira()
    captured: dict = {}

    async def create_issue(*, project_key, summary, description, assignee_account_id=None, **kw):
        captured["assignee"] = assignee_account_id
        captured["description"] = str(description)
        return JiraIssue(key="TESTREV-1", summary=summary)

    jira.create_issue = create_issue  # type: ignore[assignment]

    await TicketManager(jira).create_rename_request(
        tenant=TENANT,
        prd_id="page-1",
        page_title="Notes",
        page_url="url",
        assignee_account_id=None,
        reason="classifier rejected: looks like a template",
    )

    assert captured["assignee"] is None
    assert "unassigned" in captured["description"]


# ---------------------------------------------------------------------------------------------
# EH-04 — the rename re-enters cleanly; the dedupe key distinguishes a new version from a duplicate.
# ---------------------------------------------------------------------------------------------


def test_a_rename_produces_a_new_version_and_re_enters() -> None:
    """EH-04 / AD-9 — a rename bumps version.number, so its dedupe key differs and it is not suppressed."""
    before = DedupeKey("tenant_one", EventType.CONFLUENCE_PAGE_UPDATED, "page-1", "1")
    after = DedupeKey("tenant_one", EventType.CONFLUENCE_PAGE_UPDATED, "page-1", "2")
    assert before.value != after.value
    # A duplicate delivery of the pre-rename version collides; the rename (new version) does not.
    assert before.value.endswith(":1")
    assert after.value.endswith(":2")
