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

    async def apply_draft_deleted(self, prd_id, deleted_page_id):
        self.calls.append(("apply_draft_deleted", prd_id))
        return RunResult(prd_id, Stage.AWAITING_REVIEW)

    async def apply_deletion_decision(self, prd_id, *, comment_text):
        self.calls.append(("apply_deletion_decision", prd_id))
        return RunResult(prd_id, Stage.AWAITING_REVIEW)

    async def apply_inline_comment(self, prd_id, *, comment_id, commenter_account_id=""):
        self.calls.append(("apply_inline_comment", prd_id))
        return RunResult(prd_id, Stage.AWAITING_STRUCTURE_CONFIRM)


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

    def __init__(
        self,
        version: int = 7,
        title: str = "final_PRD_Widget",
        parent_id: str = "folder-source-1",
        ancestors: tuple[str, ...] = ("folder-source-1",),
        status: str = "current",
    ) -> None:
        self.version, self.title, self.calls = version, title, 0
        self.parent_id, self.ancestors, self.status = parent_id, ancestors, status

    async def get_page(self, page_id, *, with_body=True):
        from app.domain.atlassian import ConfluencePage

        self.calls += 1
        return ConfluencePage(
            id=page_id,
            title=self.title,
            version=self.version,
            parent_id=self.parent_id,
            status=self.status,
        )

    async def get_page_ancestors(self, page_id):
        return self.ancestors


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


async def test_a_rename_correction_re_enters_a_run_awaiting_the_rename(tmp_path) -> None:
    """EH-04 / FR-02a: a run parked awaiting a corrected upload MUST re-enter when the page is renamed.

    This is the one existing-run case where a source-page event is actionable — the guard keys on the
    `UPLOADING_PM_RENAME` gate exactly to preserve it.
    """
    from app.domain.stage import PendingGate

    parked = PrdState(
        prd_id="page-1",
        project_id="tenant_one",
        stage=Stage.DETECTED,
        pending_gate=PendingGate.UPLOADING_PM_RENAME,
    )
    composition, tenant, confluence = make_with_confluence(version=8, tenant_state=parked)

    await _dispatch(composition, Accepted(event=unversioned_page_event(), tenant=tenant))

    assert ("advance", "page-1") in composition.orchestrator.calls, (
        "the rename correction re-enters"
    )


async def test_a_rename_after_drafting_is_ignored(tmp_path) -> None:
    """FR-01a rename-churn guard: once past the rename waits, toggling the name is a no-op.

    The reported fear — the agent catching the same PRD over and over, producing duplicate tickets and
    drafts, as the name is corrected back and forth. A run in review must not re-enter on a rename, and
    must not even pay for the version-resolving GET.
    """
    from app.domain.stage import PendingGate

    drafted = PrdState(
        prd_id="page-1",
        project_id="tenant_one",
        stage=Stage.AWAITING_REVIEW,
        pending_gate=PendingGate.PM_REVIEW,
        review_ticket_key="TESTREV-1",
        userdoc_page_id="draft-1",
    )
    composition, tenant, confluence = make_with_confluence(version=9, tenant_state=drafted)

    await _dispatch(composition, Accepted(event=unversioned_page_event(), tenant=tenant))

    assert composition.orchestrator.calls == [], (
        "a rename after drafting must not re-enter the flow"
    )
    assert confluence.calls == 0, (
        "the churn guard must short-circuit before the version-resolving GET"
    )


# -- AD-10 at the door: a space-wide Automation rule also fires on the agent's own pages ---------


def agent_page_event(*, labels=(), container_id="folder-source-1"):
    from app.domain.events import ConfluencePageEvent, EventType

    return ConfluencePageEvent(
        event_type=EventType.CONFLUENCE_PAGE_CREATED,
        page_id="draft-1",
        version_number=1,
        title="Widget Guide",
        container_id=container_id,
        labels=labels,
    )


async def test_a_page_carrying_the_reserved_label_is_never_admitted() -> None:
    """The agent's own draft must not become a run that can never advance."""
    composition, tenant = make()

    await _dispatch(
        composition,
        Accepted(event=agent_page_event(labels=("agent-generated",)), tenant=tenant),
    )

    assert composition.repository.state.get("draft-1") is None
    assert composition.orchestrator.calls == []


async def test_a_page_in_the_draft_folder_is_never_admitted() -> None:
    composition, tenant = make()

    await _dispatch(
        composition, Accepted(event=agent_page_event(container_id="folder-draft-1"), tenant=tenant)
    )

    assert composition.repository.state.get("draft-1") is None


async def test_a_page_in_the_published_folder_is_never_admitted() -> None:
    composition, tenant = make()

    await _dispatch(
        composition,
        Accepted(event=agent_page_event(container_id="folder-published-1"), tenant=tenant),
    )

    assert composition.repository.state.get("draft-1") is None


async def test_a_real_prd_in_the_source_folder_is_still_admitted() -> None:
    """The guard must be certain, never a heuristic that could refuse a genuine PRD."""
    composition, tenant = make()

    await _dispatch(composition, Accepted(event=page_event(), tenant=tenant))

    assert composition.repository.state.get("page-1") is not None
    assert ("advance", "page-1") in composition.orchestrator.calls


async def test_a_nested_prd_under_the_source_folder_is_admitted() -> None:
    """A PRD nested under a page inside the source folder has a page (not the folder) as its parent;
    the ancestors lookup must still recognise it as in-source and admit it."""
    composition, tenant, confluence = make_with_confluence(version=3)
    confluence.ancestors = ("parent-page", "folder-source-1")  # source is an ancestor
    event = agent_page_event(container_id="parent-page")

    await _dispatch(composition, Accepted(event=event, tenant=tenant))

    assert composition.repository.state.get("draft-1") is not None, (
        "a nested source PRD is admitted"
    )


async def test_a_page_in_an_unrelated_folder_is_not_admitted() -> None:
    """The source-folder gate: a page created elsewhere in the space must NOT become a dead run."""
    composition, tenant, confluence = make_with_confluence(version=3)
    confluence.ancestors = ("some-other-folder",)  # source is nowhere in the ancestry
    event = agent_page_event(container_id="some-other-folder")

    await _dispatch(composition, Accepted(event=event, tenant=tenant))

    assert composition.repository.state.get("draft-1") is None, "a non-source page is refused"
    assert composition.orchestrator.calls == []


# -- FR-16: a trashed draft page routes to recovery --------------------------------------------


def trashed_event(page_id="draft-1"):
    from app.domain.events import ConfluencePageEvent, EventType

    return ConfluencePageEvent(
        event_type=EventType.CONFLUENCE_PAGE_TRASHED,
        page_id=page_id,
        version_number=None,
        title="",
    )


async def test_a_trashed_draft_page_routes_to_recovery() -> None:
    """The trashed page belongs to a run (by userdoc_page_id) → apply_draft_deleted."""
    state = PrdState(
        prd_id="page-1",
        project_id="tenant_one",
        stage=Stage.AWAITING_REVIEW,
        review_ticket_key="TESTREV-1",
        userdoc_page_id="draft-1",
    )
    composition, tenant = make(state)

    await _dispatch(composition, Accepted(event=trashed_event("draft-1"), tenant=tenant))

    assert ("apply_draft_deleted", "page-1") in composition.orchestrator.calls


async def test_a_trashed_page_that_is_not_a_tracked_draft_is_ignored() -> None:
    state = PrdState(
        prd_id="page-1",
        project_id="tenant_one",
        stage=Stage.AWAITING_REVIEW,
        review_ticket_key="TESTREV-1",
        userdoc_page_id="draft-1",
    )
    composition, tenant = make(state)

    await _dispatch(
        composition, Accepted(event=trashed_event("some-unrelated-page"), tenant=tenant)
    )

    assert composition.orchestrator.calls == [], "a non-draft deletion must not touch any run"


async def test_a_draft_arriving_as_a_page_update_but_now_trashed_triggers_the_audit() -> None:
    """The real bug: the Automation rule fires a generic 'page updated' (not 'page trashed') on a
    delete, so the event isn't labelled a trash. The agent must check the page's real STATUS and act
    on the deletion anyway — instead of dropping it via the AD-10 self-ingestion guard."""
    state = PrdState(
        prd_id="page-1",
        project_id="tenant_one",
        stage=Stage.AWAITING_REVIEW,
        review_ticket_key="TESTREV-1",
        userdoc_page_id="draft-1",
    )
    # A normal page_updated event for the draft; the live page reports status=trashed.
    composition, tenant, confluence = make_with_confluence(tenant_state=state)
    confluence.status = "trashed"
    from app.domain.events import ConfluencePageEvent, EventType

    event = ConfluencePageEvent(
        event_type=EventType.CONFLUENCE_PAGE_UPDATED,
        page_id="draft-1",
        version_number=None,
        title="",
    )

    await _dispatch(composition, Accepted(event=event, tenant=tenant))

    assert ("apply_draft_deleted", "page-1") in composition.orchestrator.calls


async def test_a_page_update_for_a_healthy_draft_is_ignored_not_admitted() -> None:
    """A normal edit of the agent's own draft is not a new PRD and not a deletion — just ignore it."""
    state = PrdState(
        prd_id="page-1",
        project_id="tenant_one",
        stage=Stage.AWAITING_REVIEW,
        review_ticket_key="TESTREV-1",
        userdoc_page_id="draft-1",
    )
    composition, tenant, confluence = make_with_confluence(tenant_state=state)
    confluence.status = "current"
    from app.domain.events import ConfluencePageEvent, EventType

    event = ConfluencePageEvent(
        event_type=EventType.CONFLUENCE_PAGE_UPDATED,
        page_id="draft-1",
        version_number=None,
        title="",
    )

    await _dispatch(composition, Accepted(event=event, tenant=tenant))

    assert composition.orchestrator.calls == []
    assert composition.repository.state.get("draft-1") is None, "the draft is not admitted as a PRD"


async def test_a_pm_reply_while_a_deletion_is_pending_routes_to_the_decision() -> None:
    state = PrdState(
        prd_id="page-1",
        project_id="tenant_one",
        stage=Stage.AWAITING_REVIEW,
        review_ticket_key="TESTREV-1",
        userdoc_page_id="draft-1",
        pending_deletion_page_id="draft-1",
    )
    composition, tenant = make(state)

    await _dispatch(composition, Accepted(comment_event(body="restore it please"), tenant))

    assert ("apply_deletion_decision", "page-1") in composition.orchestrator.calls
    assert ("apply_pm_comment", "page-1") not in composition.orchestrator.calls


# -- FR-17: an inline comment on a tracked draft routes to the feedback pickup --------------------


def inline_comment_event(page_id="draft-1", comment_id="ic-1", author="acct-designer"):
    from app.domain.events import ConfluenceCommentEvent, EventType

    return ConfluenceCommentEvent(
        event_type=EventType.CONFLUENCE_INLINE_COMMENT_CREATED,
        comment_id=comment_id,
        page_id=page_id,
        author_account_id=author,
    )


async def test_an_inline_comment_on_a_tracked_draft_routes_to_pickup() -> None:
    state = PrdState(
        prd_id="page-1",
        project_id="tenant_one",
        stage=Stage.AWAITING_REVIEW,
        review_ticket_key="TESTREV-1",
        userdoc_page_id="draft-1",
    )
    composition, tenant = make(state)

    await _dispatch(composition, Accepted(inline_comment_event(page_id="draft-1"), tenant))

    assert ("apply_inline_comment", "page-1") in composition.orchestrator.calls


async def test_an_inline_comment_on_a_non_draft_page_is_ignored() -> None:
    """A comment on a source PRD or any unrelated page resolves to no run and never enters the flow."""
    state = PrdState(
        prd_id="page-1",
        project_id="tenant_one",
        stage=Stage.AWAITING_REVIEW,
        review_ticket_key="TESTREV-1",
        userdoc_page_id="draft-1",
    )
    composition, tenant = make(state)

    await _dispatch(composition, Accepted(inline_comment_event(page_id="some-other-page"), tenant))

    assert composition.orchestrator.calls == [], "a comment off the tracked draft touches no run"


async def test_a_rename_correction_of_a_wrong_named_prd_re_enters_the_flow() -> None:
    """Reported regression: a wrong-named PRD, renamed correctly, must re-enter and advance (so it
    reaches the tracking-ticket stage). The run is parked at `detected` on UPLOADING_PM_RENAME."""
    from app.domain.stage import PendingGate

    state = PrdState(
        prd_id="page-7",
        project_id="tenant_one",
        stage=Stage.DETECTED,
        pending_gate=PendingGate.UPLOADING_PM_RENAME,
        rename_request_ticket_key="TESTREV-9",
        prd_title="wrong_name",
    )
    composition, tenant, confluence = make_with_confluence(tenant_state=state)
    confluence.title = "final_PRD_Widget"  # the corrected name
    from app.domain.events import ConfluencePageEvent, EventType

    event = ConfluencePageEvent(
        event_type=EventType.CONFLUENCE_PAGE_UPDATED,
        page_id="page-7",
        version_number=None,
        title="",
    )

    await _dispatch(composition, Accepted(event=event, tenant=tenant))

    assert ("advance", "page-7") in composition.orchestrator.calls, (
        "the rename must re-enter the flow"
    )
