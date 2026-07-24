"""Reconciliation & liveness sweep (AD-22).

Atlassian webhook delivery is best-effort. A dropped `issue-updated` event could otherwise strand a
run that a human already approved — with zero signal that anything is wrong. This sweep closes that
gap **without introducing a timeout**: the park stays indefinite; what changes is that a stuck run
becomes *visible* and a missed gate-Done gets *recovered*.

Two jobs per sweep:

* **(a) Liveness** — find runs parked in `awaiting_review` / `awaiting_publish_approval` / `error`
  whose `updated_at` is older than the threshold, and alert the admin **once per threshold crossing**
  (tracked by a `liveness_alerted_at` marker, so a stuck run is not re-alerted every sweep).
* **(b) Reconcile-poll** — re-poll the two gate tickets; if a gate is now `done`-category, feed that
  to the orchestrator as an **input**, identical to the missed webhook.

Three things keep this within AD-22's guarantees. The reconciler writes **only non-`stage` markers**
through the repository — a found gate-Done is fed as an input (`apply_gate_done`), never written as a
transition, so the orchestrator remains the sole `stage` writer (AD-2) and the agent still never
transitions a gate (AD-15). And a gate-Done cannot double-advance: the input goes through the same
serial-queue path, derives the same AD-9 dedupe key as a racing webhook, and hits an idempotent
stage advance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

from app.config.registry import ConfigRegistry
from app.domain.atlassian import DONE_CATEGORY
from app.domain.stage import LIVENESS_WATCHED_STAGES, Stage
from app.domain.state import PrdState, utc_now

logger = logging.getLogger(__name__)


class GatePoller(Protocol):
    """Reads a gate ticket's status for a tenant. Backed by the Jira adapter in production."""

    async def is_gate_done(self, tenant_project_id: str, issue_key: str) -> bool: ...


class GateInput(Protocol):
    """Feeds a recovered gate-Done to the orchestrator (its `apply_gate_done`)."""

    async def apply_gate_done(self, prd_id: str, *, issue_key: str) -> object: ...


class AdminAlerter(Protocol):
    """Alerts the admin about a stale run. Backed by the Error handler / a log+LangSmith signal."""

    async def alert_stale(self, state: PrdState) -> None: ...


@dataclass(frozen=True, slots=True)
class SweepResult:
    alerted: tuple[str, ...] = ()
    recovered: tuple[str, ...] = ()
    checked: int = 0


@dataclass
class Reconciler:
    """One sweep of liveness alerting + gate reconcile-polling."""

    repository: object
    registry: ConfigRegistry
    poller: GatePoller
    gate_input: GateInput
    alerter: AdminAlerter
    threshold: timedelta = field(default_factory=lambda: timedelta(minutes=1440))

    async def sweep(self) -> SweepResult:
        stale = self.repository.state.find_stale(LIVENESS_WATCHED_STAGES, self.threshold)
        alerted: list[str] = []
        recovered: list[str] = []

        for state in stale:
            # (b) reconcile-poll the relevant gate ticket first — recovery beats alerting, since a
            # run that can be recovered is not actually stuck.
            if await self._try_recover_gate(state):
                recovered.append(state.prd_id)
                continue

            # (a) liveness alert, once per threshold crossing (AD-22).
            if state.liveness_alerted_at is None:
                await self.alerter.alert_stale(state)
                self.repository.state.update_fields(state.prd_id, liveness_alerted_at=utc_now())
                alerted.append(state.prd_id)

        return SweepResult(tuple(alerted), tuple(recovered), checked=len(stale))

    async def _try_recover_gate(self, state: PrdState) -> bool:
        """If the run is parked at a gate whose ticket is now Done, feed that as an input."""
        gate_ticket = _gate_ticket_for(state)
        if gate_ticket is None:
            return False
        if not await self.poller.is_gate_done(state.project_id, gate_ticket):
            return False
        # Feed as an input — NOT a stage write. The orchestrator advances the stage; if a webhook
        # already did, this collides on the dedupe key / is a no-op (AD-22).
        await self.gate_input.apply_gate_done(state.prd_id, issue_key=gate_ticket)
        logger.info(
            "reconciler recovered a missed gate-Done: prd_id=%s ticket=%s",
            state.prd_id,
            gate_ticket,
        )
        return True


def _gate_ticket_for(state: PrdState) -> str | None:
    if state.stage is Stage.AWAITING_REVIEW:
        return state.review_ticket_key
    if state.stage is Stage.AWAITING_PUBLISH_APPROVAL:
        return state.publishing_ticket_key
    return None  # `error` runs are alerted, not gate-recovered


# Re-exported so callers importing the reconciler also see what "done" means (AD-13 consistency).
__all__ = ["DONE_CATEGORY", "GateInput", "GatePoller", "Reconciler", "SweepResult", "AdminAlerter"]
