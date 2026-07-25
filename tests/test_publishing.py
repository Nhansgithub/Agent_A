"""Epic 5 — approval & publishing (FR-13, FR-14, FR-15, AD-18, AD-14, AD-10, AD-15)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.publisher import Publisher
from app.domain.atlassian import ConfluencePage, JiraIssue
from app.domain.stage import PendingGate, QueueStatus, Stage
from app.domain.state import PrdState, utc_now
from app.orchestrator.handlers_publishing import PublishingHandlers
from app.orchestrator.runner import Orchestrator
from app.orchestrator.stages import HandlerRegistry
from app.repository import Repository
from app.repository.database import Database
from tests.conftest import tenant_entry

from app.config.schema import TenantConfig  # isort: skip

AGENT_ACCOUNT = "acct-agent-self"


def make_tenant(md_dir: str) -> TenantConfig:
    return TenantConfig.model_validate(
        {**tenant_entry(md_export_dir=md_dir), "project_id": "tenant_one"}
    )


# ---------------------------------------------------------------------------------------------
# FR-15 / AD-18 — the publish transaction, at the Publisher level (idempotent side-effects).
# ---------------------------------------------------------------------------------------------


@dataclass
class FakeConfluence:
    restrictions: list[tuple[str, list[str]]] = field(default_factory=list)
    moves: list[tuple[str, str]] = field(default_factory=list)
    page_storage: str = "<h1>Guide</h1><p>Body.</p>"

    async def set_edit_restriction(self, page_id, *, allowed_account_ids):
        if not allowed_account_ids:
            from app.domain.errors import AgentError

            raise AgentError(message="empty", suggested_fix="x", operation="op")
        self.restrictions.append((page_id, list(allowed_account_ids)))

    async def move_page(self, page_id, folder_id):
        self.moves.append((page_id, folder_id))

    async def get_page(self, page_id, *, with_body=True):
        return ConfluencePage(id=page_id, title="Guide", version=3, body_storage=self.page_storage)

    def storage_to_markdown(self, storage):
        from app.adapters.markdown import storage_to_markdown

        return storage_to_markdown(storage)


async def _collect(markers: dict, **kw):
    markers.update(kw)


async def test_publish_runs_all_four_side_effects_in_order(tmp_path) -> None:
    confluence = FakeConfluence()
    tenant = make_tenant(str(tmp_path))
    markers: dict = {}

    result = await Publisher(confluence).publish(
        tenant=tenant,
        prd_id="page-1",
        page_id="draft-1",
        page_title="Widget Guide",
        agent_account_id=AGENT_ACCOUNT,
        on_step=lambda **kw: _collect(markers, **kw),
    )

    # (1) restriction includes the agent account (AD-18 — or it locks itself out).
    assert confluence.restrictions == [("draft-1", [AGENT_ACCOUNT])]
    # (2) moved into the published folder (AD-14).
    assert confluence.moves == [("draft-1", "folder-published-1")]
    # (3) exported to disk.
    assert result.md_export_path is not None
    exported = tmp_path / "page-1-widget-guide.md"
    assert exported.exists()
    assert "# Guide" in exported.read_text()
    # sub-checkpoints were recorded for each step.
    assert "restriction_applied_at" in markers
    assert "moved_to_published_at" in markers
    assert "md_exported_at" in markers


async def test_the_restriction_always_includes_the_agent_and_space_admins(tmp_path) -> None:
    confluence = FakeConfluence()
    await Publisher(confluence).publish(
        tenant=make_tenant(str(tmp_path)),
        prd_id="page-1",
        page_id="draft-1",
        page_title="Guide",
        agent_account_id=AGENT_ACCOUNT,
        space_admin_account_ids=("acct-admin-x",),
        on_step=lambda **kw: _collect({}, **kw),
    )
    assert confluence.restrictions[0][1] == [AGENT_ACCOUNT, "acct-admin-x"]


async def test_a_resume_skips_completed_side_effects(tmp_path) -> None:
    """AD-18 — resuming the publishing stage must not re-apply a done side-effect."""
    confluence = FakeConfluence()
    result = await Publisher(confluence).publish(
        tenant=make_tenant(str(tmp_path)),
        prd_id="page-1",
        page_id="draft-1",
        page_title="Guide",
        agent_account_id=AGENT_ACCOUNT,
        on_step=lambda **kw: _collect({}, **kw),
        restriction_done=True,  # already applied before the crash
        move_done=True,  # already moved
        export_done=False,  # export had not happened
        existing_md_path=None,
    )

    assert confluence.restrictions == [], "restriction not re-applied"
    assert confluence.moves == [], "move not repeated"
    assert result.md_export_path is not None, "export still ran"


async def test_export_is_overwrite_safe(tmp_path) -> None:
    """A re-export on resume overwrites rather than erroring or duplicating."""
    confluence = FakeConfluence()
    tenant = make_tenant(str(tmp_path))
    for _ in range(2):
        await Publisher(confluence).publish(
            tenant=tenant,
            prd_id="page-1",
            page_id="draft-1",
            page_title="Guide",
            agent_account_id=AGENT_ACCOUNT,
            on_step=lambda **kw: _collect({}, **kw),
        )
    assert len(list(tmp_path.glob("*.md"))) == 1


# ---------------------------------------------------------------------------------------------
# The handlers, through the orchestrator.
# ---------------------------------------------------------------------------------------------


@dataclass
class FakeTickets:
    publishing_created: int = 0
    comments: list[str] = field(default_factory=list)
    marker_hit: JiraIssue | None = None

    async def find_ticket_by_marker(self, project_key, prd_id, *, summary_prefix=None):
        return self.marker_hit

    async def create_publishing_ticket(self, *, tenant, prd_id, userdoc_title, draft_page_url):
        self.publishing_created += 1
        return JiraIssue(key="TESTMAIN-2", summary=f"Approve & publish UserDoc: {userdoc_title}")


@dataclass
class FakePublisher:
    published: int = 0
    published_page_ids: list[str] = field(default_factory=list)

    async def publish(
        self,
        *,
        tenant,
        prd_id,
        page_id,
        page_title,
        agent_account_id,
        space_admin_account_ids,
        on_step,
        restriction_done,
        move_done,
        export_done,
        existing_md_path,
    ):
        from app.agents.publisher import PublishResult

        self.published += 1
        self.published_page_ids.append(page_id)
        await on_step(restriction_applied_at=utc_now())
        await on_step(moved_to_published_at=utc_now())
        path = f"{tenant.md_export_dir}/{prd_id}.md"
        await on_step(md_exported_at=utc_now(), md_export_path=path)
        return PublishResult(
            md_export_path=path, restriction_applied=True, moved=True, exported=True
        )


@dataclass
class FakePublishContext:
    repository: Repository
    prd_id: str = "page-1"
    tenant: TenantConfig = field(default_factory=lambda: make_tenant("/tmp/x"))
    publisher: FakePublisher = field(default_factory=FakePublisher)
    ticket_manager: FakeTickets = field(default_factory=FakeTickets)
    comments: list[tuple[str, dict]] = field(default_factory=list)
    recovery_action: str = "healthy"

    def draft_page_url(self, page_id):
        return f"https://x/{page_id}"

    async def agent_account_id(self):
        return AGENT_ACCOUNT

    async def space_admin_account_ids(self):
        return ()

    async def post_comment(self, issue_key, body):
        self.comments.append((issue_key, body))

    async def record_publish_progress(self, **markers):
        self.repository.state.update_fields(self.prd_id, **markers)

    async def recover_draft(self):
        from app.agents.publisher import DraftRecovery

        page_id = self.repository.state.require(self.prd_id).userdoc_page_id
        return DraftRecovery(action=self.recovery_action, page_id=page_id)


def build(*, stage: Stage, **state_kwargs):
    repository = Repository(Database(":memory:"))
    repository.state.create(
        PrdState(
            prd_id="page-1",
            project_id="tenant_one",
            stage=stage,
            userdoc_page_id="draft-1",
            review_ticket_key="TESTREV-1",
            prd_title="Widget Guide",
            **state_kwargs,
        )
    )
    context = FakePublishContext(repository=repository)
    handlers = PublishingHandlers()
    registry = HandlerRegistry(
        {Stage.PASSED: handlers.on_passed, Stage.PUBLISHING: handlers.on_publishing}
    )
    orchestrator = Orchestrator(repository, registry, context_factory=lambda _s: context)
    return orchestrator, repository, context


# -- Story 5.1 — PASS → Publishing ticket → park -------------------------------------------


async def test_passed_creates_the_publishing_ticket_and_parks() -> None:
    orchestrator, repository, context = build(stage=Stage.PASSED)

    result = await orchestrator.advance("page-1")

    assert result.final_stage is Stage.AWAITING_PUBLISH_APPROVAL
    final = repository.state.require("page-1")
    assert final.publishing_ticket_key == "TESTMAIN-2"
    assert final.pending_gate is PendingGate.HEAD_OF_PRODUCT_APPROVAL
    assert final.queue_status is QueueStatus.IDLE
    assert context.ticket_manager.publishing_created == 1
    assert context.comments[0][0] == "TESTREV-1", "PASS confirmation posted to the Review ticket"


async def test_passed_does_not_publish_on_its_own() -> None:
    """AD-15 — creating the Publishing ticket parks on the Head of Product; it must not publish."""
    orchestrator, repository, context = build(stage=Stage.PASSED)
    await orchestrator.advance("page-1")
    assert context.publisher.published == 0
    assert repository.state.require("page-1").stage is Stage.AWAITING_PUBLISH_APPROVAL


# -- Story 5.2 — the Head of Product gate --------------------------------------------------


async def test_head_of_product_done_triggers_publishing() -> None:
    orchestrator, repository, context = build(
        stage=Stage.AWAITING_PUBLISH_APPROVAL, publishing_ticket_key="TESTMAIN-2"
    )

    await orchestrator.apply_gate_done("page-1", issue_key="TESTMAIN-2")

    assert repository.state.require("page-1").stage is Stage.COMPLETE
    assert context.publisher.published == 1


async def test_a_done_on_the_wrong_ticket_does_not_publish() -> None:
    orchestrator, repository, context = build(
        stage=Stage.AWAITING_PUBLISH_APPROVAL, publishing_ticket_key="TESTMAIN-2"
    )

    await orchestrator.apply_gate_done("page-1", issue_key="SOME-OTHER-99")

    assert repository.state.require("page-1").stage is Stage.AWAITING_PUBLISH_APPROVAL
    assert context.publisher.published == 0


async def test_no_head_of_product_action_parks_indefinitely() -> None:
    """FR-14 — no timeout; the run waits."""
    orchestrator, repository, _ = build(
        stage=Stage.AWAITING_PUBLISH_APPROVAL, publishing_ticket_key="TESTMAIN-2"
    )
    result = await orchestrator.advance("page-1")
    assert not result.progressed
    assert repository.state.require("page-1").stage is Stage.AWAITING_PUBLISH_APPROVAL


# -- Story 5.3 — the publish transaction marks complete ------------------------------------


async def test_publishing_marks_the_run_complete() -> None:
    orchestrator, repository, context = build(
        stage=Stage.PUBLISHING, publishing_ticket_key="TESTMAIN-2"
    )

    result = await orchestrator.advance("page-1")

    assert result.final_stage is Stage.COMPLETE
    final = repository.state.require("page-1")
    assert final.is_complete
    assert final.completed_at is not None
    assert final.md_export_path == "/tmp/x/page-1.md"


async def test_publishing_records_each_sub_checkpoint() -> None:
    """AD-18 — the per-side-effect markers are persisted, so a resume could skip them."""
    orchestrator, repository, _ = build(stage=Stage.PUBLISHING, publishing_ticket_key="TESTMAIN-2")

    await orchestrator.advance("page-1")

    final = repository.state.require("page-1")
    assert final.restriction_applied_at is not None
    assert final.moved_to_published_at is not None
    assert final.md_exported_at is not None


async def test_the_full_publish_gate_to_complete_sequence() -> None:
    """End of the happy path: park at publish gate → HoP Done → publish → complete."""
    orchestrator, repository, context = build(
        stage=Stage.PASSED,
    )
    # PASS handler runs → parks at awaiting_publish_approval.
    await orchestrator.advance("page-1")
    key = repository.state.require("page-1").publishing_ticket_key

    # Head of Product approves.
    await orchestrator.apply_gate_done("page-1", issue_key=key)

    assert repository.state.require("page-1").is_complete
    assert context.publisher.published == 1


# ---------------------------------------------------------------------------------------------
# `require_edit_restriction: false` — the explicit opt-out for Confluence Free (B-7, D-21).
# The default stays True everywhere; only a tenant that sets it False skips FR-15 step 1.
# ---------------------------------------------------------------------------------------------


def make_unrestricted_tenant(md_dir: str) -> TenantConfig:
    return TenantConfig.model_validate(
        {
            **tenant_entry(md_export_dir=md_dir, require_edit_restriction=False),
            "project_id": "tenant_one",
        }
    )


def test_the_restriction_is_required_by_default() -> None:
    """The spec'd behaviour must be what you get without saying anything (FR-15 step 1)."""
    assert make_tenant("/tmp/x").require_edit_restriction is True


async def test_opting_out_skips_the_restriction_but_still_moves_and_exports(tmp_path) -> None:
    confluence = FakeConfluence()
    markers: dict = {}

    result = await Publisher(confluence).publish(
        tenant=make_unrestricted_tenant(str(tmp_path)),
        prd_id="page-1",
        page_id="draft-1",
        page_title="Widget Guide",
        agent_account_id=AGENT_ACCOUNT,
        on_step=lambda **kw: _collect(markers, **kw),
    )

    assert confluence.restrictions == [], "no restriction call should be made"
    assert confluence.moves == [("draft-1", "folder-published-1")]
    assert result.md_export_path is not None
    assert result.restriction_skipped is True
    assert result.restriction_applied is False


async def test_a_skipped_restriction_is_never_checkpointed_as_applied(tmp_path) -> None:
    """The checkpoint must not claim protection the page does not have.

    If a skip recorded `restriction_applied_at`, a later resume — or an admin reading the state —
    would believe the page is write-protected when anyone with space access can edit it.
    """
    markers: dict = {}

    await Publisher(FakeConfluence()).publish(
        tenant=make_unrestricted_tenant(str(tmp_path)),
        prd_id="page-1",
        page_id="draft-1",
        page_title="Widget Guide",
        agent_account_id=AGENT_ACCOUNT,
        on_step=lambda **kw: _collect(markers, **kw),
    )

    assert "restriction_applied_at" not in markers
    assert "moved_to_published_at" in markers
    assert "md_exported_at" in markers


async def test_a_skipped_restriction_is_announced_on_the_publishing_ticket() -> None:
    """The Head of Product approved expecting publishing to lock the page — they must be told it did not.

    A silent skip is the dangerous outcome: the ticket closes, the doc looks published, and everyone
    believes it is write-protected when anyone with space access can still edit it.
    """
    from app.agents.publisher import PublishResult
    from app.domain import adf

    class SkippingPublisher(FakePublisher):
        async def publish(self, **kwargs):
            self.published += 1
            await kwargs["on_step"](moved_to_published_at=utc_now())
            path = f"{kwargs['tenant'].md_export_dir}/{kwargs['prd_id']}.md"
            await kwargs["on_step"](md_exported_at=utc_now(), md_export_path=path)
            return PublishResult(
                md_export_path=path,
                restriction_applied=False,
                moved=True,
                exported=True,
                restriction_skipped=True,
            )

    orchestrator, repository, context = build(
        stage=Stage.PUBLISHING, publishing_ticket_key="TESTMAIN-9"
    )
    context.publisher = SkippingPublisher()
    context.tenant = make_unrestricted_tenant("/tmp/x")

    await orchestrator.advance("page-1")

    assert repository.state.require("page-1").stage is Stage.COMPLETE
    notice = [c for c in context.comments if c[0] == "TESTMAIN-9"]
    assert notice, "the Head of Product was not told the page is unrestricted"
    text = adf.extract_text(notice[-1][1]).lower()
    assert "not edit-restricted" in text
    # and it must tag them, or the notice reaches nobody (adf.mention, not plain text)
    assert "mention" in str(notice[-1][1])


async def test_a_normal_publish_posts_no_unrestricted_notice() -> None:
    orchestrator, repository, context = build(
        stage=Stage.PUBLISHING, publishing_ticket_key="TESTMAIN-9"
    )

    await orchestrator.advance("page-1")

    assert repository.state.require("page-1").stage is Stage.COMPLETE
    assert [c for c in context.comments if c[0] == "TESTMAIN-9"] == []


# -- FR-16: publish self-heals a draft deleted before the Head of Product's approval ------------


async def test_publishing_recreates_a_deleted_draft_before_publishing() -> None:
    """A draft trashed while parked at awaiting_publish_approval must NOT dead-end the publish. The
    self-heal recreates it and the transaction runs against the new page id (audit finding #3)."""
    orchestrator, repository, context = build(stage=Stage.PUBLISHING, publishing_ticket_key="M-9")
    context.recovery_action = "recreated"

    # simulate recreate producing a new id: recover_draft returns the state's page id, so repoint it
    async def recreate(self=context):
        from app.agents.publisher import DraftRecovery

        return DraftRecovery(action="recreated", page_id="recreated-42")

    context.recover_draft = recreate  # type: ignore[method-assign]

    result = await orchestrator.advance("page-1")

    assert result.final_stage is Stage.COMPLETE, "publish completed despite the deletion"
    assert context.publisher.published_page_ids == ["recreated-42"], "published the recovered page"
    assert repository.state.require("page-1").userdoc_page_id == "recreated-42", "state repointed"


async def test_publishing_errors_actionably_when_the_draft_is_unrecoverable() -> None:
    orchestrator, repository, context = build(stage=Stage.PUBLISHING, publishing_ticket_key="M-9")

    async def gone(self=context):
        from app.agents.publisher import DraftRecovery

        return DraftRecovery(action="unrecoverable", page_id=None)

    context.recover_draft = gone  # type: ignore[method-assign]

    result = await orchestrator.advance("page-1")

    assert result.final_stage is Stage.ERROR
    assert context.publisher.published == 0, "never attempted to publish a page that isn't there"
    assert "resume" in (result.error.suggested_fix if result.error else ""), "actionable EH-01 fix"
