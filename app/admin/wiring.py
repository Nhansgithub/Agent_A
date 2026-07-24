"""Production wiring for the AD-22 reconciler — the gate poller and the stale-run alerter.

Kept out of `reconciler.py` so the reconciler stays a pure algorithm over injected protocols
(testable with fakes), and out of `main.py` so the composition detail lives with the admin layer.
"""

from __future__ import annotations

import logging

from app.agents.error_handler import build_error_comment
from app.domain.atlassian import DONE_CATEGORY
from app.domain.errors import AgentError
from app.domain.state import PrdState

logger = logging.getLogger(__name__)


class JiraGatePoller:
    """Re-polls a gate ticket's status via the tenant's Jira adapter (AD-22 reconcile-poll)."""

    __slots__ = ("_composition",)

    def __init__(self, composition) -> None:
        self._composition = composition

    async def is_gate_done(self, tenant_project_id: str, issue_key: str) -> bool:
        tenant = self._composition._registry.by_project_id(tenant_project_id)
        if tenant is None:
            return False
        jira = self._composition._adapters_for(tenant).jira
        try:
            issue = await jira.get_issue(issue_key)
        except AgentError as exc:  # a transient poll failure is not fatal — try again next sweep
            logger.warning("reconcile-poll of %s failed: %s", issue_key, exc)
            return False
        return issue.status_category.lower() == DONE_CATEGORY


class LogAndTicketAlerter:
    """Alerts the admin about a stale run via a log/LangSmith signal and a ticket comment (AD-22)."""

    __slots__ = ("_composition",)

    def __init__(self, composition) -> None:
        self._composition = composition

    async def alert_stale(self, state: PrdState) -> None:
        # Always emit the log/observability signal — a parked run must not be invisible (AD-22).
        logger.warning(
            "STALE RUN: prd_id=%s stage=%s pending_gate=%s updated_at=%s correlation_id=%s",
            state.prd_id,
            state.stage.value,
            state.pending_gate.value,
            state.updated_at.isoformat() if state.updated_at else "?",
            state.correlation_id,
        )
        # Best-effort admin comment on the relevant ticket; a posting failure must not break the sweep.
        tenant = self._composition._registry.by_project_id(state.project_id)
        ticket_key = (
            state.review_ticket_key or state.publishing_ticket_key or state.prd_tracking_ticket_key
        )
        if tenant is None or ticket_key is None:
            return
        try:
            from app.agents.ticket_manager import TicketManager

            jira = self._composition._adapters_for(tenant).jira
            body = build_error_comment(
                admin_account_id=tenant.admin_account_id,
                error=AgentError(
                    message=f"This run has been parked at {state.stage.value} without human action "
                    "past the liveness threshold.",
                    suggested_fix="Check whether the awaited approval/feedback is still expected; a "
                    "dropped webhook is auto-recovered, but a genuinely stuck run needs a human.",
                    operation="reconciler.liveness",
                ),
                correlation_id=state.correlation_id,
            )
            await TicketManager(jira).comment(ticket_key, body)
        except AgentError as exc:
            logger.warning("could not post liveness alert comment for %s: %s", state.prd_id, exc)
