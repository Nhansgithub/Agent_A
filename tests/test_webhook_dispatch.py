"""The webhook endpoint maps each event to the right orchestrator call (AD-8 → Epics 2-6).

Tests the *routing mapping* — page → advance, PM comment → apply_pm_comment, admin resume →
apply_admin_resume, gate Done → apply_gate_done — with a fake orchestrator recording calls, so no
credentials or network are needed. The orchestrator methods themselves are tested elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config.registry import ConfigRegistry
from app.domain.stage import Stage
from app.domain.state import PrdState
from app.orchestrator.runner import RunResult
from app.repository import Repository
from app.repository.database import Database
from app.webhooks.router import _dispatch, _find_prd_by_ticket
from tests.conftest import registry_mapping

SECRET = "hook-secret"


class FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def advance(self, prd_id):
        self.calls.append(("advance", prd_id))
        return RunResult(prd_id, Stage.AWAITING_REVIEW)

    async def apply_pm_comment(self, prd_id, *, comment_text):
        self.calls.append(("apply_pm_comment", prd_id))
        return RunResult(prd_id, Stage.AWAITING_REVIEW)

    async def apply_gate_done(self, prd_id, *, issue_key):
        self.calls.append(("apply_gate_done", issue_key))
        return RunResult(prd_id, Stage.PASSED)

    async def apply_admin_resume(self, prd_id):
        self.calls.append(("apply_admin_resume", prd_id))
        return RunResult(prd_id, Stage.CONFIRMED)


@dataclass
class FakeComposition:
    repository: Repository
    orchestrator: FakeOrchestrator
    _registry: ConfigRegistry
    _env: dict = field(default_factory=dict)

    def _adapters_for(self, tenant):  # only reached on the error-surfacing path
        raise AssertionError("adapters should not be needed in these tests")

    def stash_event(self, prd_id, event):  # no-op for routing tests
        pass


@dataclass
class Accepted:
    """Stands in for an accepted IngressResult."""

    event: object
    tenant: object
    dedupe_key: object = None


def make(tenant_state: PrdState | None = None):
    repository = Repository(Database(":memory:"))
    if tenant_state is not None:
        repository.state.create(tenant_state)
    registry = ConfigRegistry.from_mapping(registry_mapping())
    composition = FakeComposition(
        repository=repository,
        orchestrator=FakeOrchestrator(),
        _registry=registry,
    )
    return composition, registry.by_project_id("tenant_one")


def page_event(page_id="page-1", title="final_PRD_Widget"):
    from app.domain.events import ConfluencePageEvent, EventType

    return ConfluencePageEvent(
        event_type=EventType.CONFLUENCE_PAGE_CREATED,
        page_id=page_id,
        version_number=1,
        title=title,
        container_id="folder-source-1",
    )


def comment_event(
    issue_key="TESTREV-1", author="acct-pm-1", body="Section: x\nIssue: y\nSuggested change: z"
):
    from app.domain.events import EventType, JiraCommentEvent

    return JiraCommentEvent(
        event_type=EventType.JIRA_COMMENT_CREATED,
        comment_id="c-1",
        issue_key=issue_key,
        project_key="TESTREV",
        author_account_id=author,
        body_text=body,
    )


def transition_event(issue_key="TESTREV-1", done=True):
    from app.domain.events import EventType, JiraIssueUpdatedEvent

    return JiraIssueUpdatedEvent(
        event_type=EventType.JIRA_ISSUE_UPDATED,
        issue_key=issue_key,
        project_key="TESTREV",
        changelog_id="chg-1",
        status_category="done" if done else "indeterminate",
        transitioned_status=True,
    )


# ---------------------------------------------------------------------------------------------


async def test_a_new_page_admits_a_prd_and_advances() -> None:
    composition, tenant = make()
    from app.domain.dedupe import dedupe_key_for

    event = page_event()
    await _dispatch(composition, Accepted(event, tenant, dedupe_key_for("tenant_one", event)))

    assert ("advance", "page-1") in composition.orchestrator.calls
    assert composition.repository.state.get("page-1") is not None, "the PRD was admitted"


async def test_a_pm_comment_routes_to_apply_pm_comment() -> None:
    state = PrdState(
        prd_id="page-1",
        project_id="tenant_one",
        stage=Stage.AWAITING_REVIEW,
        review_ticket_key="TESTREV-1",
    )
    composition, tenant = make(state)

    await _dispatch(composition, Accepted(comment_event(), tenant))

    assert ("apply_pm_comment", "page-1") in composition.orchestrator.calls


async def test_an_admin_resume_comment_on_an_errored_run_resumes() -> None:
    state = PrdState(
        prd_id="page-1", project_id="tenant_one", stage=Stage.ERROR, review_ticket_key="TESTREV-1"
    )
    composition, tenant = make(state)

    await _dispatch(
        composition, Accepted(comment_event(author="acct-admin-1", body="@agent resume"), tenant)
    )

    assert ("apply_admin_resume", "page-1") in composition.orchestrator.calls


async def test_a_gate_done_transition_routes_to_apply_gate_done() -> None:
    state = PrdState(
        prd_id="page-1",
        project_id="tenant_one",
        stage=Stage.AWAITING_REVIEW,
        review_ticket_key="TESTREV-1",
    )
    composition, tenant = make(state)

    await _dispatch(composition, Accepted(transition_event(), tenant))

    assert ("apply_gate_done", "TESTREV-1") in composition.orchestrator.calls


async def test_a_non_done_transition_is_not_dispatched() -> None:
    state = PrdState(
        prd_id="page-1",
        project_id="tenant_one",
        stage=Stage.AWAITING_REVIEW,
        review_ticket_key="TESTREV-1",
    )
    composition, tenant = make(state)

    await _dispatch(composition, Accepted(transition_event(done=False), tenant))

    assert composition.orchestrator.calls == [], "only a move to Done is a gate signal (FR-12)"


def test_find_prd_by_ticket_resolves_across_ticket_fields() -> None:
    repository = Repository(Database(":memory:"))
    repository.state.create(
        PrdState(
            prd_id="page-1",
            project_id="tenant_one",
            stage=Stage.AWAITING_PUBLISH_APPROVAL,
            publishing_ticket_key="TESTMAIN-2",
        )
    )
    assert _find_prd_by_ticket(repository, "TESTMAIN-2") == "page-1"
    assert _find_prd_by_ticket(repository, "UNKNOWN-9") is None


# -- resolving the version Confluence Automation cannot send (AD-9) -----------------------------


class FakeConfluenceForVersion:
    """Returns the page's authoritative version, as `_resolve_version` fetches it."""

    def __init__(self, version: int = 7, title: str = "final_PRD_Widget") -> None:
        self.version, self.title, self.calls = version, title, 0

    async def get_page(self, page_id, *, with_body=True):
        from app.domain.atlassian import ConfluencePage

        self.calls += 1
        return ConfluencePage(
            id=page_id, title=self.title, version=self.version, parent_id="folder-source-1"
        )


def make_with_confluence(version: int = 7, tenant_state: PrdState | None = None):
    composition, tenant = make(tenant_state)
    confluence = FakeConfluenceForVersion(version)

    class Adapters:
        pass

    adapters = Adapters()
    adapters.confluence = confluence
    composition._adapters_for = lambda _t: adapters  # type: ignore[method-assign]
    return composition, tenant, confluence


def unversioned_page_event(page_id="page-1"):
    from app.domain.events import ConfluencePageEvent, EventType

    return ConfluencePageEvent(
        event_type=EventType.CONFLUENCE_PAGE_CREATED,
        page_id=page_id,
        version_number=None,
        title="final_PRD_Widget",
        container_id="folder-source-1",
    )


async def test_an_unversioned_page_event_is_admitted_after_resolving_the_version() -> None:
    """The real Automation payload carries no version; the run must still start."""
    composition, tenant, confluence = make_with_confluence(version=7)

    await _dispatch(composition, Accepted(event=unversioned_page_event(), tenant=tenant))

    assert confluence.calls == 1, "the version should be fetched once"
    assert ("advance", "page-1") in composition.orchestrator.calls
    assert composition.repository.state.get("page-1") is not None


async def test_a_redelivery_of_the_same_unversioned_event_is_dropped() -> None:
    """Two deliveries of the same page version must not start two runs (AD-9)."""
    composition, tenant, confluence = make_with_confluence(version=7)

    await _dispatch(composition, Accepted(event=unversioned_page_event(), tenant=tenant))
    composition.orchestrator.calls.clear()
    await _dispatch(composition, Accepted(event=unversioned_page_event(), tenant=tenant))

    assert composition.orchestrator.calls == [], "the duplicate started a second run"


async def test_a_later_edit_of_the_same_page_re_enters(tmp_path) -> None:
    """EH-04: a rename arrives as a NEW version and must re-enter, not be swallowed as a duplicate."""
    composition, tenant, confluence = make_with_confluence(version=7)

    await _dispatch(composition, Accepted(event=unversioned_page_event(), tenant=tenant))
    composition.orchestrator.calls.clear()

    confluence.version = 8  # the page was renamed → new version
    await _dispatch(composition, Accepted(event=unversioned_page_event(), tenant=tenant))

    assert ("advance", "page-1") in composition.orchestrator.calls, "the rename did not re-enter"
