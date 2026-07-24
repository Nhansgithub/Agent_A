"""Epic 2 orchestration — the detection→confirmation→tracking stage handlers wired end to end.

Exercises `DetectionHandlers` through the real `Orchestrator` and `StateRepository`, with the Epic 2
agents faked at their own boundaries. This is where the pieces of Epic 2 are proven to compose into
the `detected → confirmed → prd_ticket_done → drafted` walk, and to branch to the rename-request wait.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.classifier.agent import ClassificationResult, ClassifierDecision
from app.agents.detection import DetectionResult, DetectionVerdict
from app.agents.identity import IdentityResolution, ResolutionMethod
from app.domain.events import ConfluencePageEvent, EventType
from app.domain.stage import PendingGate, Stage
from app.domain.state import PrdState
from app.orchestrator.handlers_detection import DetectionHandlers
from app.orchestrator.runner import Orchestrator
from app.orchestrator.stages import HandlerRegistry
from app.repository import Repository
from app.repository.database import Database
from tests.conftest import tenant_entry

from app.config.schema import TenantConfig  # isort: skip

TENANT = TenantConfig.model_validate({**tenant_entry(), "project_id": "tenant_one"})


# -- fakes for the four Epic 2 agents --------------------------------------------------------


class FakeDetection:
    def __init__(self, result: DetectionResult) -> None:
        self._result = result

    async def evaluate(self, _event, _tenant) -> DetectionResult:
        return self._result


class FakeClassifier:
    def __init__(self, decision: ClassifierDecision, reason: str = "") -> None:
        self._decision = decision
        self._reason = reason
        self.calls = 0

    async def classify(self, *, title, body_markdown, metadata) -> ClassificationResult:
        self.calls += 1
        return ClassificationResult(decision=self._decision, confidence="high", reason=self._reason)


class FakeTicketManager:
    def __init__(self) -> None:
        self.tracking_created: list[str] = []
        self.rename_requests: list[dict] = []

    async def locate_or_create_tracking_ticket(self, *, tenant, prd_id, prd_name, prd_url):
        from app.agents.ticket_manager import TrackingTicketResult
        from app.domain.atlassian import JiraIssue

        self.tracking_created.append(prd_id)
        issue = JiraIssue(key="TESTMAIN-1", summary=prd_name, status_category="done")
        return TrackingTicketResult(issue=issue, created=True, transitioned=True)

    async def create_rename_request(
        self, *, tenant, prd_id, page_title, page_url, assignee_account_id, reason
    ):
        from app.domain.atlassian import JiraIssue

        self.rename_requests.append(
            {"prd_id": prd_id, "assignee": assignee_account_id, "reason": reason}
        )
        return JiraIssue(key="TESTREV-9", summary=page_title)


class FakeIdentity:
    async def resolve_uploading_pm(self, *, confluence_account_id, confluence_email, tenant):
        return IdentityResolution(
            account_id=confluence_account_id, method=ResolutionMethod.SAME_ORG
        )


@dataclass
class FakeContext:
    """The `Epic2Context` stand-in threaded to the handlers."""

    prd_id: str
    tenant: TenantConfig
    page_event: ConfluencePageEvent
    detection: FakeDetection
    classifier: FakeClassifier
    ticket_manager: FakeTicketManager
    identity: FakeIdentity
    correlation_id: str = "corr-1"
    markdown: str = "A complete PRD body."

    def page_url(self) -> str:
        return f"https://x/{self.prd_id}"

    def page_markdown(self) -> str:
        return self.markdown


def page_event(
    title: str = "final_PRD_Widget", creator: str = "acct-uploader"
) -> ConfluencePageEvent:
    return ConfluencePageEvent(
        event_type=EventType.CONFLUENCE_PAGE_CREATED,
        page_id="page-1",
        version_number=1,
        title=title,
        creator_account_id=creator,
        container_id="folder-source-1",
    )


def build(
    *,
    detection: DetectionResult,
    decision: ClassifierDecision = ClassifierDecision.ACCEPT,
    reason: str = "looks complete",
    event: ConfluencePageEvent | None = None,
) -> tuple[Orchestrator, Repository, FakeContext, FakeTicketManager]:
    repository = Repository(Database(":memory:"))
    repository.state.create(PrdState(prd_id="page-1", project_id="tenant_one"))

    tickets = FakeTicketManager()
    context = FakeContext(
        prd_id="page-1",
        tenant=TENANT,
        page_event=event or page_event(),
        detection=FakeDetection(detection),
        classifier=FakeClassifier(decision, reason),
        ticket_manager=tickets,
        identity=FakeIdentity(),
    )

    handlers = DetectionHandlers()
    registry = HandlerRegistry(
        {
            Stage.DETECTED: handlers.on_detected,
            Stage.CONFIRMED: handlers.on_confirmed,
            Stage.PRD_TICKET_DONE: handlers.on_prd_ticket_done,
        }
    )
    orchestrator = Orchestrator(repository, registry, context_factory=lambda _state: context)
    return orchestrator, repository, context, tickets


ADMIT = DetectionResult(DetectionVerdict.ADMIT, "in source folder, titled")
MISMATCH = DetectionResult(DetectionVerdict.TITLE_MISMATCH, "title does not match")


# ---------------------------------------------------------------------------------------------
# The happy path: detect → confirm → tracking ticket → drafted (parks, since Epic 3 has no handler).
# ---------------------------------------------------------------------------------------------


async def test_a_genuine_prd_walks_to_drafted() -> None:
    orchestrator, repository, _, tickets = build(detection=ADMIT)

    result = await orchestrator.advance("page-1")

    assert result.final_stage is Stage.DRAFTED
    final = repository.state.require("page-1")
    assert final.prd_tracking_ticket_key == "TESTMAIN-1"
    assert final.prd_title == "final_PRD_Widget"
    assert tickets.tracking_created == ["page-1"]


async def test_it_stops_at_drafted_because_epic_3_is_not_wired_yet() -> None:
    """A stage with no handler stops rather than skipping — the flow never runs past a gap (AD-15)."""
    orchestrator, repository, _, _ = build(detection=ADMIT)
    result = await orchestrator.advance("page-1")
    assert "no handler registered for drafted" in result.stopped_reason


# ---------------------------------------------------------------------------------------------
# FR-02a title mismatch: file a rename request and self-park at detected.
# ---------------------------------------------------------------------------------------------


async def test_title_mismatch_files_a_rename_request_and_parks() -> None:
    orchestrator, repository, _, tickets = build(
        detection=MISMATCH, event=page_event(title="Widget Notes")
    )

    result = await orchestrator.advance("page-1")

    assert result.final_stage is Stage.DETECTED, "self-parked; not advanced past detection"
    final = repository.state.require("page-1")
    assert final.pending_gate is PendingGate.UPLOADING_PM_RENAME
    assert final.rename_request_ticket_key == "TESTREV-9"
    assert tickets.rename_requests[0]["assignee"] == "acct-uploader"
    assert tickets.tracking_created == [], "no tracking ticket for a page that never confirmed"


async def test_the_rename_request_is_not_filed_twice() -> None:
    """AD-11 — re-running the parked stage must not create a second rename task."""
    orchestrator, repository, _, tickets = build(
        detection=MISMATCH, event=page_event(title="Widget Notes")
    )
    await orchestrator.advance("page-1")
    await orchestrator.advance("page-1")  # a duplicate/idle re-entry

    assert len(tickets.rename_requests) == 1


async def test_a_corrected_re_upload_advances_past_the_rename_wait() -> None:
    """EH-04 — the renamed page re-enters at detected; detection now passes and the flow proceeds."""
    orchestrator, repository, context, _ = build(
        detection=MISMATCH, event=page_event(title="Widget Notes")
    )
    await orchestrator.advance("page-1")
    assert repository.state.require("page-1").stage is Stage.DETECTED

    # The human renames the page; the re-upload arrives. Detection now admits it.
    context.detection = FakeDetection(ADMIT)
    context.page_event = page_event(title="final_PRD_Widget")

    result = await orchestrator.advance("page-1")

    assert result.final_stage is Stage.DRAFTED


# ---------------------------------------------------------------------------------------------
# FR-03 / EH-07 classifier REJECT: same rename-request handling as a title mismatch.
# ---------------------------------------------------------------------------------------------


async def test_a_classifier_reject_files_a_rename_request_and_parks_at_confirmed() -> None:
    orchestrator, repository, _, tickets = build(
        detection=ADMIT, decision=ClassifierDecision.REJECT, reason="looks like a template"
    )

    result = await orchestrator.advance("page-1")

    assert result.final_stage is Stage.CONFIRMED, "self-parked at confirmed after the REJECT"
    final = repository.state.require("page-1")
    assert final.pending_gate is PendingGate.UPLOADING_PM_RENAME
    assert "template" in tickets.rename_requests[0]["reason"]
    assert tickets.tracking_created == []


async def test_a_reject_then_corrected_reupload_reclassifies_and_proceeds() -> None:
    orchestrator, repository, context, _ = build(
        detection=ADMIT, decision=ClassifierDecision.REJECT, reason="template"
    )
    await orchestrator.advance("page-1")
    assert repository.state.require("page-1").stage is Stage.CONFIRMED

    # Corrected content re-uploaded; the classifier now accepts it.
    context.classifier = FakeClassifier(ClassifierDecision.ACCEPT, "now complete")

    result = await orchestrator.advance("page-1")

    assert result.final_stage is Stage.DRAFTED


async def test_the_classifier_is_only_called_once_per_advance() -> None:
    """The confirm stage runs the LLM once; a redundant call is wasted spend (NFR-09)."""
    orchestrator, _, context, _ = build(detection=ADMIT)
    await orchestrator.advance("page-1")
    assert context.classifier.calls == 1
