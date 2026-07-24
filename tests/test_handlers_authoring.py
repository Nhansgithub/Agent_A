"""Epic 3 orchestration — the `drafted` handler wired end to end through the orchestrator.

Proves the authoring-and-publish sequence advances the run to `awaiting_review` and then parks on the
PM (AD-15), and that a re-run adopts the existing page and ticket rather than duplicating them (AD-11).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.author.agent import Draft
from app.domain.atlassian import ConfluencePage, JiraIssue
from app.domain.events import ConfluencePageEvent, EventType
from app.domain.stage import PendingGate, QueueStatus, Stage
from app.domain.state import PrdState
from app.orchestrator.handlers_authoring import AuthoringHandlers
from app.orchestrator.runner import Orchestrator
from app.orchestrator.stages import HandlerRegistry
from app.repository import Repository
from app.repository.database import Database
from tests.conftest import tenant_entry

from app.config.schema import TenantConfig  # isort: skip

TENANT = TenantConfig.model_validate({**tenant_entry(), "project_id": "tenant_one"})


class FakeAuthor:
    def __init__(self) -> None:
        self.draft_calls = 0

    async def draft(self, *, prd_title, prd_markdown, metadata) -> Draft:
        self.draft_calls += 1
        return Draft(
            title="Widget Guide", markdown="# Widget Guide\n\nBody.", self_critique_applied=True
        )


class FakePublisher:
    def __init__(self) -> None:
        self.publish_calls: list[str | None] = []

    async def publish_draft(
        self, *, tenant, prd_id, title, markdown, space_id, existing_page_id=None
    ):
        from app.agents.publisher import PublishedDraft

        self.publish_calls.append(existing_page_id)
        page = ConfluencePage(id=existing_page_id or "draft-1", title=title, version=1)
        return PublishedDraft(page=page, created=existing_page_id is None)


class FakeTickets:
    def __init__(self) -> None:
        self.review_created = 0
        self.comments: list[tuple[str, dict]] = []
        self.marker_hit: JiraIssue | None = None

    async def find_ticket_by_marker(self, project_key, prd_id):
        return self.marker_hit

    async def create_review_ticket(self, *, tenant, prd_id, userdoc_title, draft_page_url):
        self.review_created += 1
        return JiraIssue(key="TESTREV-1", summary=userdoc_title)

    async def comment(self, issue_key, body):
        self.comments.append((issue_key, body))
        return "comment-1"


@dataclass
class FakeContext:
    prd_id: str = "page-1"
    correlation_id: str = "corr-1"
    tenant: TenantConfig = TENANT
    author: FakeAuthor = field(default_factory=FakeAuthor)
    publisher: FakePublisher = field(default_factory=FakePublisher)
    ticket_manager: FakeTickets = field(default_factory=FakeTickets)
    page_event: ConfluencePageEvent = field(
        default_factory=lambda: ConfluencePageEvent(
            event_type=EventType.CONFLUENCE_PAGE_CREATED,
            page_id="page-1",
            version_number=1,
            title="final_PRD_Widget",
        )
    )

    async def page_markdown(self) -> str:
        return "The PRD body."

    def draft_page_url(self, page_id: str) -> str:
        return f"https://x/{page_id}"

    async def confluence_space_id(self) -> str:
        return "space-1"

    async def post_comment(self, issue_key: str, body: dict) -> None:
        # The real context also claims the returned comment id in `processed_events` so Jira's echo
        # of the agent's own comment is not read back as PM feedback; that is covered in
        # tests/test_orchestrator_context.py. Here it just needs to reach the ticket manager.
        await self.ticket_manager.comment(issue_key, body)


def build(*, stage: Stage = Stage.DRAFTED, **state_kwargs):
    repository = Repository(Database(":memory:"))
    repository.state.create(
        PrdState(
            prd_id="page-1",
            project_id="tenant_one",
            stage=stage,
            prd_title="final_PRD_Widget",
            **state_kwargs,
        )
    )
    context = FakeContext()
    registry = HandlerRegistry({Stage.DRAFTED: AuthoringHandlers().on_drafted})
    orchestrator = Orchestrator(repository, registry, context_factory=lambda _s: context)
    return orchestrator, repository, context


async def test_drafting_publishes_and_parks_at_awaiting_review() -> None:
    orchestrator, repository, context = build()

    result = await orchestrator.advance("page-1")

    assert result.final_stage is Stage.AWAITING_REVIEW
    final = repository.state.require("page-1")
    assert final.userdoc_page_id == "draft-1"
    assert final.review_ticket_key == "TESTREV-1"
    assert final.pending_gate is PendingGate.PM_REVIEW
    assert final.queue_status is QueueStatus.IDLE, "the run now waits on the PM (AD-15)"
    assert context.author.draft_calls == 1
    assert context.ticket_manager.review_created == 1


async def test_the_framed_review_request_is_posted_to_the_review_ticket() -> None:
    orchestrator, _, context = build()
    await orchestrator.advance("page-1")

    assert len(context.ticket_manager.comments) == 1
    issue_key, body = context.ticket_manager.comments[0]
    assert issue_key == "TESTREV-1"
    from app.domain import adf

    assert "only way to pass" in adf.extract_text(body)


async def test_the_run_does_not_advance_past_the_review_gate() -> None:
    """AD-15 — drafting parks; it must never reach passed/publishing on its own."""
    orchestrator, repository, _ = build()
    await orchestrator.advance("page-1")
    # A second advance while parked does nothing.
    result = await orchestrator.advance("page-1")
    assert not result.progressed
    assert repository.state.require("page-1").stage is Stage.AWAITING_REVIEW


async def test_a_rerun_with_recorded_ids_adopts_rather_than_duplicates() -> None:
    """AD-11 — resuming the drafted stage reuses the page and ticket ids already recorded."""
    orchestrator, repository, context = build(
        userdoc_page_id="draft-existing", review_ticket_key="TESTREV-existing"
    )

    await orchestrator.advance("page-1")

    assert context.publisher.publish_calls == ["draft-existing"], "reused the known page id"
    assert context.ticket_manager.review_created == 0, "did not create a second Review ticket"
    final = repository.state.require("page-1")
    assert final.review_ticket_key == "TESTREV-existing"
