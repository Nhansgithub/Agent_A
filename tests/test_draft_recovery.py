"""FR-16 — a UserDoc draft deleted mid-flow is detected, recovered, and the PM is alerted.

Covers the whole path: the Publisher's restore/recreate/healthy/unrecoverable outcomes, the
orchestrator's `apply_draft_deleted` (recover + @mention + self-heal an errored run), the webhook
routing of a trash event, and the publish-time self-heal that makes `@agent resume` recreate a
missing page instead of looping on a 404.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.publisher import DraftRecovery, Publisher
from app.domain import adf
from app.domain.atlassian import ConfluencePage
from app.domain.errors import AgentError
from app.domain.stage import PendingGate, Stage
from app.domain.state import PrdState
from app.orchestrator.runner import Orchestrator
from app.orchestrator.stages import HandlerRegistry
from app.repository import Repository
from app.repository.database import Database
from tests.conftest import tenant_entry

from app.config.schema import TenantConfig  # isort: skip

TENANT = TenantConfig.model_validate({**tenant_entry(), "project_id": "tenant_one"})
DRAFT_FOLDER = TENANT.confluence_draft_folder_id


# ---------------------------------------------------------------------------------------------
# Publisher.recover_draft — the four outcomes.
# ---------------------------------------------------------------------------------------------


@dataclass
class FakeConfluence:
    """A minimal Confluence for recovery: a page with a status, plus recorded side-effects."""

    status: str = "trashed"
    title: str = "Widget Guide"
    version: int = 3
    body: str = "<p>latest content</p>"
    space_id: str | None = "space-1"
    get_raises: bool = False
    restore_raises: bool = False
    restored: list[str] = field(default_factory=list)
    moved: list[tuple[str, str]] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    stamped: list[str] = field(default_factory=list)
    properties: list[tuple[str, str, str]] = field(default_factory=list)
    _next_id: int = 900

    async def get_page(self, page_id, *, with_body=True):
        if self.get_raises:
            raise AgentError(message="gone", suggested_fix="", operation="confluence.get_page")
        return ConfluencePage(
            id=page_id,
            title=self.title,
            version=self.version,
            space_id=self.space_id,
            body_storage=self.body,
            status=self.status,
        )

    async def restore_page(self, page_id, *, title, version):
        if self.restore_raises:
            raise AgentError(message="cannot restore", suggested_fix="", operation="restore_page")
        self.restored.append(page_id)

    async def move_page(self, page_id, folder_id):
        self.moved.append((page_id, folder_id))

    async def create_page(self, *, space_id, title, body_storage):
        self._next_id += 1
        new_id = f"recreated-{self._next_id}"
        self.created.append(new_id)
        return ConfluencePage(id=new_id, title=title, version=1, space_id=space_id)

    async def stamp_agent_generated(self, page_id):
        self.stamped.append(page_id)

    async def set_content_property(self, page_id, key, value):
        self.properties.append((page_id, key, value))

    async def get_folder(self, folder_id):
        return {"spaceId": "space-1"}


async def test_recover_restores_a_trashed_page_in_place() -> None:
    confluence = FakeConfluence(status="trashed")
    result = await Publisher(confluence).recover_draft(
        tenant=TENANT, prd_id="page-1", page_id="draft-1"
    )

    assert result.action == "restored"
    assert result.page_id == "draft-1", "same id — the review-ticket link still works"
    assert confluence.restored == ["draft-1"]
    assert confluence.moved == [("draft-1", DRAFT_FOLDER)], "re-placed in the draft folder"
    assert confluence.created == [], "no recreation needed"


async def test_recover_recreates_when_restore_fails() -> None:
    confluence = FakeConfluence(status="trashed", restore_raises=True)
    result = await Publisher(confluence).recover_draft(
        tenant=TENANT, prd_id="page-1", page_id="draft-1"
    )

    assert result.action == "recreated"
    assert result.page_id.startswith("recreated-"), "a new page id"
    assert confluence.created, "a new page was created with the last content"
    assert confluence.stamped == [result.page_id], "stamped agent-generated (AD-10)"
    marker_pages = [pid for pid, _key, value in confluence.properties if value == "page-1"]
    assert marker_pages == [result.page_id], "the AD-11 correlation marker was set on the new page"
    assert confluence.moved[-1] == (result.page_id, DRAFT_FOLDER)


async def test_recover_is_a_no_op_when_the_page_is_healthy() -> None:
    """A stale/duplicate deletion event, or a manual restore — nothing to do."""
    confluence = FakeConfluence(status="current")
    result = await Publisher(confluence).recover_draft(
        tenant=TENANT, prd_id="page-1", page_id="draft-1"
    )

    assert result.action == "healthy"
    assert confluence.restored == [] and confluence.created == []


async def test_recover_reports_unrecoverable_when_the_page_is_purged() -> None:
    confluence = FakeConfluence(get_raises=True)
    result = await Publisher(confluence).recover_draft(
        tenant=TENANT, prd_id="page-1", page_id="draft-1"
    )

    assert result.action == "unrecoverable"
    assert result.page_id is None


# ---------------------------------------------------------------------------------------------
# Orchestrator.apply_draft_deleted — recover + @mention the PM + self-heal an errored run.
# ---------------------------------------------------------------------------------------------


@dataclass
class FakeRecoveryContext:
    repository: Repository
    recovery: DraftRecovery
    prd_id: str = "page-1"
    correlation_id: str = "corr-1"
    tenant: TenantConfig = TENANT
    comments: list[tuple[str, dict]] = field(default_factory=list)

    def draft_page_url(self, page_id):
        return f"https://x/{page_id}"

    async def recover_draft(self):
        return self.recovery

    async def post_comment(self, issue_key, body):
        self.comments.append((issue_key, body))


def build(recovery: DraftRecovery, *, stage=Stage.AWAITING_REVIEW, **state_kwargs):
    repository = Repository(Database(":memory:"))
    repository.state.create(
        PrdState(
            prd_id="page-1",
            project_id="tenant_one",
            stage=stage,
            pending_gate=PendingGate.PM_REVIEW,
            userdoc_page_id="draft-1",
            review_ticket_key="TESTREV-1",
            **state_kwargs,
        )
    )
    context = FakeRecoveryContext(repository=repository, recovery=recovery)
    orchestrator = Orchestrator(repository, HandlerRegistry({}), context_factory=lambda _s: context)
    return orchestrator, repository, context


async def test_apply_draft_deleted_restores_and_alerts_the_pm() -> None:
    orchestrator, repository, context = build(DraftRecovery(action="restored", page_id="draft-1"))

    result = await orchestrator.apply_draft_deleted("page-1")

    assert result.final_stage is Stage.AWAITING_REVIEW, "stays where it was; the page is back"
    assert context.comments, "the PM was notified"
    body = context.comments[-1][1]
    assert context.comments[-1][0] == "TESTREV-1"
    assert "restored it from the trash" in adf.extract_text(body)
    assert "mention" in str(body), "the PM must be @-mentioned, or they aren't notified"


async def test_apply_draft_deleted_repoints_state_on_recreate() -> None:
    orchestrator, repository, context = build(
        DraftRecovery(action="recreated", page_id="recreated-901")
    )

    await orchestrator.apply_draft_deleted("page-1")

    assert repository.state.require("page-1").userdoc_page_id == "recreated-901", "state repointed"
    assert "recreated it" in adf.extract_text(context.comments[-1][1])


async def test_apply_draft_deleted_is_a_no_op_when_healthy() -> None:
    orchestrator, repository, context = build(DraftRecovery(action="healthy", page_id="draft-1"))

    result = await orchestrator.apply_draft_deleted("page-1")

    assert context.comments == [], "no noise when nothing was actually deleted"
    assert "intact" in result.stopped_reason


async def test_apply_draft_deleted_self_heals_an_errored_run() -> None:
    """The whole point: a run that errored because the page was gone recovers itself once it's back."""
    handlers_ran: list[str] = []

    async def on_publishing(context, state):
        handlers_ran.append("publishing")
        from app.orchestrator.stages import Advance

        return Advance(to_stage=Stage.COMPLETE, note="published")

    repository = Repository(Database(":memory:"))
    repository.state.create(
        PrdState(
            prd_id="page-1",
            project_id="tenant_one",
            stage=Stage.ERROR,
            last_good_checkpoint=Stage.PUBLISHING,
            userdoc_page_id="draft-1",
            review_ticket_key="TESTREV-1",
            publishing_ticket_key="TESTMAIN-2",
        )
    )
    context = FakeRecoveryContext(
        repository=repository, recovery=DraftRecovery(action="restored", page_id="draft-1")
    )
    orchestrator = Orchestrator(
        repository,
        HandlerRegistry({Stage.PUBLISHING: on_publishing}),
        context_factory=lambda _s: context,
    )

    result = await orchestrator.apply_draft_deleted("page-1")

    assert handlers_ran == ["publishing"], "re-entered at the failed checkpoint and ran it"
    assert result.final_stage is Stage.COMPLETE, "the errored run self-recovered to completion"


async def test_apply_draft_deleted_unrecoverable_alerts_but_does_not_resume() -> None:
    orchestrator, repository, context = build(
        DraftRecovery(action="unrecoverable", page_id=None),
        stage=Stage.ERROR,
        last_good_checkpoint=Stage.PUBLISHING,
    )

    result = await orchestrator.apply_draft_deleted("page-1")

    assert "could not recover it" in adf.extract_text(context.comments[-1][1])
    assert result.final_stage is Stage.ERROR, "cannot self-heal what it cannot recover"
