"""Story 2.5 — locate-or-create the PRD-tracking ticket and drive it to Done (FR-04, AD-13, AD-15)."""

from __future__ import annotations

import pytest

from app.agents.ticket_manager import TicketManager
from app.domain.atlassian import JiraIssue, JiraTransition
from app.domain.errors import AgentError
from tests.conftest import tenant_entry

from app.config.schema import TenantConfig  # isort: skip

TENANT = TenantConfig.model_validate({**tenant_entry(), "project_id": "tenant_one"})


class FakeJira:
    """Scripts Jira reads/writes for the ticket-manager tests. No network."""

    def __init__(self) -> None:
        self.issues: dict[str, JiraIssue] = {}
        self.transitions: dict[str, list[JiraTransition]] = {}
        self.marker_hits: dict[str, JiraIssue] = {}
        self.search_hits: list[JiraIssue] = []
        self.created: list[dict] = []
        self.performed_transitions: list[tuple[str, str]] = []
        self._next_key = 1

    def add_issue(self, issue: JiraIssue, transitions: list[JiraTransition] | None = None) -> None:
        self.issues[issue.key] = issue
        if transitions is not None:
            self.transitions[issue.key] = transitions

    async def find_issue_by_prd_marker(self, _project, prd_id):
        return self.marker_hits.get(prd_id)

    async def search_issues(self, _jql, *, limit=50):
        return list(self.search_hits)

    async def get_issue(self, key):
        return self.issues[key]

    async def get_transitions(self, key):
        return self.transitions.get(key, [])

    async def create_issue(
        self, *, project_key, summary, description, issue_type="Task", prd_id=None, **kw
    ):
        key = f"{project_key}-{self._next_key}"
        self._next_key += 1
        self.created.append({"key": key, "summary": summary, "prd_id": prd_id})
        issue = JiraIssue(key=key, summary=summary, status_name="To Do", status_category="new")
        self.issues[key] = issue
        return issue

    async def transition_issue(self, key, transition_id):
        self.performed_transitions.append((key, transition_id))
        # Reflect the move: resolve the target from the SAME source get_transitions uses, so an
        # overridden get_transitions and this method never disagree about a transition's target.
        for t in await self.get_transitions(key):
            if t.id == transition_id:
                self.issues[key] = JiraIssue(
                    key=key,
                    summary=self.issues[key].summary,
                    status_name=t.to_status_name,
                    status_category=t.to_status_category,
                )
                break


def done_transition(tid="31", name="Done"):
    return JiraTransition(id=tid, name=name, to_status_name="Done", to_status_category="done")


def intermediate_transition(tid, name, to_name):
    return JiraTransition(
        id=tid, name=name, to_status_name=to_name, to_status_category="indeterminate"
    )


# ---------------------------------------------------------------------------------------------
# FR-04: locate-or-create, no fixed location assumed, then drive to Done.
# ---------------------------------------------------------------------------------------------


async def test_creates_a_tracking_ticket_when_none_exists() -> None:
    jira = FakeJira()
    # created issue is fetched back with a legal direct-to-Done transition
    manager = TicketManager(jira)

    async def get_issue(key):
        return JiraIssue(key=key, summary="s", status_name="To Do", status_category="new")

    jira.get_issue = get_issue  # type: ignore[assignment]
    jira.transitions = {"TESTMAIN-1": [done_transition()]}

    result = await manager.locate_or_create_tracking_ticket(
        tenant=TENANT, prd_id="page-1", prd_name="Widget", prd_url="https://x/page-1"
    )

    assert result.created
    assert jira.created[0]["prd_id"] == "page-1", "AD-11 — the correlation marker is stamped"


async def test_reuses_an_existing_ticket_found_by_name() -> None:
    """FR-04 — a human-created ticket may already exist anywhere; do not create a duplicate."""
    jira = FakeJira()
    existing = JiraIssue(
        key="TESTMAIN-99", summary="Widget", status_name="To Do", status_category="new"
    )
    jira.add_issue(existing, [done_transition()])
    jira.search_hits = [existing]

    result = await TicketManager(jira).locate_or_create_tracking_ticket(
        tenant=TENANT, prd_id="page-1", prd_name="Widget", prd_url="url"
    )

    assert not result.created
    assert result.issue.key == "TESTMAIN-99"
    assert jira.created == []


async def test_adopts_an_orphan_by_the_correlation_marker_before_searching() -> None:
    """AD-11 — a ticket created by a previous crashed attempt is adopted, not duplicated."""
    jira = FakeJira()
    orphan = JiraIssue(
        key="TESTMAIN-7", summary="PRD tracking: Widget", status_name="Done", status_category="done"
    )
    jira.add_issue(orphan)
    jira.marker_hits["page-1"] = orphan

    result = await TicketManager(jira).locate_or_create_tracking_ticket(
        tenant=TENANT, prd_id="page-1", prd_name="Widget", prd_url="url"
    )

    assert not result.created
    assert result.issue.key == "TESTMAIN-7"


# ---------------------------------------------------------------------------------------------
# AD-13: transition legality.
# ---------------------------------------------------------------------------------------------


async def test_an_already_done_ticket_is_skipped() -> None:
    """AD-13 step 2 — idempotent. Re-running FR-04 must not try to transition a Done ticket."""
    jira = FakeJira()
    done = JiraIssue(key="TESTMAIN-1", summary="s", status_name="Closed", status_category="done")
    jira.add_issue(done, [])

    transitioned = await TicketManager(jira).drive_to_done(done, TENANT)

    assert not transitioned
    assert jira.performed_transitions == []


async def test_done_ness_is_by_category_not_name() -> None:
    """A status literally named 'Done' but in the 'new' category is NOT done; 'Shipped'/done IS."""
    jira = FakeJira()
    shipped = JiraIssue(
        key="TESTMAIN-1", summary="s", status_name="Shipped", status_category="done"
    )
    jira.add_issue(shipped, [])
    assert not await TicketManager(jira).drive_to_done(shipped, TENANT)


async def test_takes_a_direct_transition_to_done() -> None:
    jira = FakeJira()
    issue = JiraIssue(key="TESTMAIN-1", summary="s", status_name="To Do", status_category="new")
    jira.add_issue(
        issue, [intermediate_transition("21", "Start", "In Progress"), done_transition("31")]
    )

    transitioned = await TicketManager(jira).drive_to_done(issue, TENANT)

    assert transitioned
    assert jira.performed_transitions == [("TESTMAIN-1", "31")]


async def test_no_path_and_no_config_escalates_to_admin() -> None:
    """AD-13 step 5 — escalate rather than guess a multi-hop route."""
    jira = FakeJira()
    issue = JiraIssue(key="TESTMAIN-1", summary="s", status_name="To Do", status_category="new")
    jira.add_issue(issue, [intermediate_transition("21", "Start", "In Progress")])

    with pytest.raises(AgentError, match="No direct transition") as caught:
        await TicketManager(jira).drive_to_done(issue, TENANT)
    assert "preferred_transition_path" in caught.value.suggested_fix


async def test_walks_the_configured_multi_hop_path() -> None:
    """AD-13 step 4 — full hardening: traverse the config-declared path, re-checking each hop."""
    tenant = TenantConfig.model_validate(
        {
            **tenant_entry(preferred_transition_path=["Start Progress", "Ready", "Done"]),
            "project_id": "t",
        }
    )
    jira = FakeJira()
    start = JiraIssue(key="TESTMAIN-1", summary="s", status_name="To Do", status_category="new")
    jira.add_issue(start)
    # From To Do: only "Start Progress" is legal (no direct Done).
    jira.transitions["TESTMAIN-1"] = [
        intermediate_transition("11", "Start Progress", "In Progress")
    ]

    # After each transition FakeJira updates the stored issue; script the legal sets per status.
    async def get_transitions(key):
        status = jira.issues[key].status_name
        return {
            "To Do": [intermediate_transition("11", "Start Progress", "In Progress")],
            "In Progress": [intermediate_transition("12", "Ready", "Ready for Review")],
            "Ready for Review": [done_transition("13", "Done")],
        }.get(status, [])

    jira.get_transitions = get_transitions  # type: ignore[assignment]

    transitioned = await TicketManager(jira).drive_to_done(start, tenant)

    assert transitioned
    assert [t[1] for t in jira.performed_transitions] == ["11", "12", "13"]
    assert jira.issues["TESTMAIN-1"].is_done


async def test_a_configured_path_that_does_not_fit_the_workflow_escalates() -> None:
    tenant = TenantConfig.model_validate(
        {**tenant_entry(preferred_transition_path=["Nonexistent Transition"]), "project_id": "t"}
    )
    jira = FakeJira()
    issue = JiraIssue(key="TESTMAIN-1", summary="s", status_name="To Do", status_category="new")
    jira.add_issue(issue, [intermediate_transition("21", "Start", "In Progress")])

    with pytest.raises(AgentError, match="stuck"):
        await TicketManager(jira).drive_to_done(issue, tenant)


# ---------------------------------------------------------------------------------------------
# AD-15: the agent NEVER auto-transitions a human-gate ticket.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["review", "publishing"])
async def test_refuses_to_transition_a_human_gate_ticket(role: str) -> None:
    jira = FakeJira()
    issue = JiraIssue(key="TESTREV-1", summary="s", status_name="To Do", status_category="new")
    jira.add_issue(issue, [done_transition()])

    with pytest.raises(AgentError, match="Refusing to auto-transition") as caught:
        await TicketManager(jira).drive_to_done(issue, TENANT, ticket_role=role)
    assert "human gate" in caught.value.suggested_fix

    assert jira.performed_transitions == [], (
        "not a single transition may be attempted on a gate ticket"
    )
