"""Epic 6 — error surfacing + admin resume (6.1) and the reconcile/liveness sweep (6.2)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.admin.reconciler import Reconciler
from app.agents.error_handler import (
    ErrorHandler,
    build_error_comment,
    is_resume_request,
    relevant_ticket_key,
)
from app.config.registry import ConfigRegistry
from app.domain import adf
from app.domain.errors import AgentError
from app.domain.stage import Stage
from app.domain.state import PrdState, utc_now
from app.orchestrator.runner import Orchestrator
from app.orchestrator.stages import Advance, HandlerRegistry
from app.repository import Repository
from app.repository.database import Database
from tests.conftest import registry_mapping, tenant_entry

from app.config.schema import TenantConfig  # isort: skip

TENANT = TenantConfig.model_validate({**tenant_entry(), "project_id": "tenant_one"})


def an_error() -> AgentError:
    return AgentError(
        message="Jira returned 503 after 3 retries",
        suggested_fix="Check the Atlassian status page, then reply to resume.",
        operation="jira.create_issue",
        status_code=503,
    )


# ---------------------------------------------------------------------------------------------
# Story 6.1 — EH-01 error comment content.
# ---------------------------------------------------------------------------------------------


def test_error_comment_has_every_eh01_element() -> None:
    body = build_error_comment(
        admin_account_id="acct-admin-1", error=an_error(), correlation_id="corr-9"
    )
    text = adf.extract_text(body)

    assert "Jira returned 503" in text  # plain-language error
    assert "status page" in text  # suggested fix
    assert "@agent resume" in text or "agent resume" in text  # the literal resume instruction
    assert "corr-9" in text  # correlation id for LangSmith
    assert "jira.create_issue" in text  # the failed step
    # @admin mention (a real mention, not plain text).
    mentions = _mentions(body)
    assert "acct-admin-1" in mentions


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("@agent resume", True),
        ("Fixed the token, please continue", True),
        ("I think this is FIXED now", True),
        ("still looking into it", False),
        ("what happened here?", False),
    ],
)
def test_resume_request_detection(comment: str, expected: bool) -> None:
    assert is_resume_request(comment) is expected


def test_error_lands_on_the_review_ticket_during_the_review_loop() -> None:
    state = PrdState(
        prd_id="p",
        project_id="t",
        stage=Stage.ERROR,
        last_good_checkpoint=Stage.REVISING,
        review_ticket_key="REV-1",
        prd_tracking_ticket_key="MAIN-1",
    )
    assert relevant_ticket_key(state) == "REV-1"


def test_error_lands_on_the_publishing_ticket_during_publishing() -> None:
    state = PrdState(
        prd_id="p",
        project_id="t",
        stage=Stage.ERROR,
        last_good_checkpoint=Stage.PUBLISHING,
        publishing_ticket_key="MAIN-2",
        prd_tracking_ticket_key="MAIN-1",
    )
    assert relevant_ticket_key(state) == "MAIN-2"


def test_error_falls_back_to_the_tracking_ticket_early_on() -> None:
    state = PrdState(
        prd_id="p",
        project_id="t",
        stage=Stage.ERROR,
        last_good_checkpoint=Stage.CONFIRMED,
        prd_tracking_ticket_key="MAIN-1",
    )
    assert relevant_ticket_key(state) == "MAIN-1"


class FakeTicketManager:
    def __init__(self) -> None:
        self.comments: list[tuple[str, dict]] = []

    async def comment(self, issue_key, body):
        self.comments.append((issue_key, body))
        return "c-1"


async def test_error_handler_posts_exactly_one_comment() -> None:
    """AD-19 — exactly one escalation, on the relevant ticket."""
    tickets = FakeTicketManager()
    state = PrdState(
        prd_id="p",
        project_id="t",
        stage=Stage.ERROR,
        last_good_checkpoint=Stage.REVISING,
        review_ticket_key="REV-1",
    )

    landed = await ErrorHandler(tickets).surface(state=state, error=an_error(), tenant=TENANT)

    assert landed == "REV-1"
    assert len(tickets.comments) == 1


async def test_error_handler_no_ticket_yet_posts_nothing() -> None:
    """A failure before any ticket exists still records state; there is nowhere to comment."""
    tickets = FakeTicketManager()
    state = PrdState(
        prd_id="p", project_id="t", stage=Stage.ERROR, last_good_checkpoint=Stage.DETECTED
    )
    landed = await ErrorHandler(tickets).surface(state=state, error=an_error(), tenant=TENANT)
    assert landed is None
    assert tickets.comments == []


# ---------------------------------------------------------------------------------------------
# Story 6.1 — admin resume re-runs the failed stage only (EH-02).
# ---------------------------------------------------------------------------------------------


def build_orchestrator(handlers: dict) -> tuple[Orchestrator, Repository]:
    repository = Repository(Database(":memory:"))
    return Orchestrator(repository, HandlerRegistry(handlers)), repository


async def test_resume_re_runs_from_the_checkpoint_not_the_start() -> None:
    ran: list[Stage] = []

    async def confirmed(_c, _s):
        ran.append(Stage.CONFIRMED)
        return Advance(to_stage=Stage.PRD_TICKET_DONE)

    async def prd_ticket_done(_c, _s):
        ran.append(Stage.PRD_TICKET_DONE)
        return Advance(to_stage=Stage.DRAFTED)

    orchestrator, repository = build_orchestrator(
        {Stage.CONFIRMED: confirmed, Stage.PRD_TICKET_DONE: prd_ticket_done}
    )
    repository.state.create(
        PrdState(
            prd_id="p",
            project_id="t",
            stage=Stage.ERROR,
            last_good_checkpoint=Stage.PRD_TICKET_DONE,
            last_error="boom",
        )
    )

    await orchestrator.apply_admin_resume("p")

    assert Stage.CONFIRMED not in ran, "resume must not re-run already-completed stages"
    assert ran == [Stage.PRD_TICKET_DONE]


async def test_resume_clears_the_error_and_liveness_alert() -> None:
    orchestrator, repository = build_orchestrator(
        {Stage.CONFIRMED: lambda c, s: _advance_to(Stage.PRD_TICKET_DONE)}
    )
    repository.state.create(
        PrdState(
            prd_id="p",
            project_id="t",
            stage=Stage.ERROR,
            last_good_checkpoint=Stage.CONFIRMED,
            last_error="boom",
            liveness_alerted_at=utc_now(),
        )
    )

    await orchestrator.apply_admin_resume("p")

    final = repository.state.require("p")
    assert final.last_error is None
    assert final.liveness_alerted_at is None


async def test_resume_on_a_healthy_run_is_a_no_op() -> None:
    orchestrator, repository = build_orchestrator({})
    repository.state.create(PrdState(prd_id="p", project_id="t", stage=Stage.AWAITING_REVIEW))
    result = await orchestrator.apply_admin_resume("p")
    assert not result.progressed
    assert "not error" in result.stopped_reason


async def _advance_to(stage):
    return Advance(to_stage=stage)


# ---------------------------------------------------------------------------------------------
# Story 6.2 — the reconcile/liveness sweep (AD-22).
# ---------------------------------------------------------------------------------------------


class FakePoller:
    def __init__(self, done: set[str]) -> None:
        self._done = done

    async def is_gate_done(self, tenant_project_id, issue_key):
        return issue_key in self._done


class FakeGateInput:
    def __init__(self) -> None:
        self.fed: list[str] = []

    async def apply_gate_done(self, prd_id, *, issue_key):
        self.fed.append(issue_key)


class FakeAlerter:
    def __init__(self) -> None:
        self.alerted: list[str] = []

    async def alert_stale(self, state):
        self.alerted.append(state.prd_id)


def reconciler(repository, *, done=(), threshold_minutes=1440):
    return Reconciler(
        repository=repository,
        registry=ConfigRegistry.from_mapping(registry_mapping()),
        poller=FakePoller(set(done)),
        gate_input=FakeGateInput(),
        alerter=FakeAlerter(),
        threshold=timedelta(minutes=threshold_minutes),
    )


def stale_run(repository, prd_id, stage, *, review="REV-1", publishing="MAIN-2", days_old=3):
    repository.state.create(
        PrdState(
            prd_id=prd_id,
            project_id="tenant_one",
            stage=stage,
            review_ticket_key=review,
            publishing_ticket_key=publishing,
            updated_at=utc_now() - timedelta(days=days_old),
        )
    )


async def test_a_stale_parked_run_is_alerted_once() -> None:
    repository = Repository(Database(":memory:"))
    stale_run(repository, "p", Stage.AWAITING_REVIEW)
    rec = reconciler(repository)

    first = await rec.sweep()
    assert "p" in first.alerted

    # Second sweep: already alerted this threshold crossing, so no repeat (AD-22).
    second = await rec.sweep()
    assert "p" not in second.alerted


async def test_a_fresh_run_is_not_alerted() -> None:
    repository = Repository(Database(":memory:"))
    stale_run(repository, "p", Stage.AWAITING_REVIEW, days_old=0)
    result = await reconciler(repository).sweep()
    assert result.alerted == ()


async def test_a_missed_gate_done_is_recovered_and_fed_as_an_input() -> None:
    """AD-22 — a dropped webhook is recovered by re-polling; fed as an input, never a stage write."""
    repository = Repository(Database(":memory:"))
    stale_run(repository, "p", Stage.AWAITING_PUBLISH_APPROVAL)
    rec = reconciler(repository, done={"MAIN-2"})

    result = await rec.sweep()

    assert "p" in result.recovered
    assert rec.gate_input.fed == ["MAIN-2"]


async def test_a_recovered_run_is_not_also_alerted() -> None:
    """Recovery beats alerting — a run that can be advanced is not actually stuck."""
    repository = Repository(Database(":memory:"))
    stale_run(repository, "p", Stage.AWAITING_REVIEW)
    rec = reconciler(repository, done={"REV-1"})

    result = await rec.sweep()

    assert "p" in result.recovered
    assert "p" not in result.alerted


async def test_the_reconciler_never_writes_the_stage_directly() -> None:
    """AD-2 / AD-22 — a recovered gate-Done is fed as an input; the reconciler writes only markers."""
    repository = Repository(Database(":memory:"))
    stale_run(repository, "p", Stage.AWAITING_PUBLISH_APPROVAL)
    rec = reconciler(repository, done={"MAIN-2"})

    await rec.sweep()

    # The reconciler did not advance the stage itself; it only fed the input to `apply_gate_done`.
    assert repository.state.require("p").stage is Stage.AWAITING_PUBLISH_APPROVAL


async def test_error_runs_are_alerted_but_not_gate_recovered() -> None:
    repository = Repository(Database(":memory:"))
    stale_run(repository, "p", Stage.ERROR)
    rec = reconciler(repository, done={"REV-1", "MAIN-2"})

    result = await rec.sweep()

    assert "p" in result.alerted
    assert result.recovered == (), "an error run is not a gate to poll"


def _mentions(node) -> list[str]:
    found: list[str] = []

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "mention":
                found.append(n.get("attrs", {}).get("id", ""))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for item in n:
                walk(item)

    walk(node)
    return found
