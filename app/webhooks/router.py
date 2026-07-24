"""The FastAPI webhook endpoint — the one public door (AD-8).

Turns an authenticated, deduped, routed event into the right orchestrator call:

* a Confluence page event admits a new PRD (or re-enters a renamed one) → `advance`;
* a Jira comment on the Review ticket → `apply_pm_comment`, or on an error ticket → resume;
* a Jira issue-updated that moved a gate ticket to Done → `apply_gate_done`.

The endpoint always returns 2xx for an authenticated request it understood — even when the event is
dropped as a duplicate or as unroutable — because a non-2xx tells Atlassian to redeliver, and a
*handled* drop is not a delivery failure. Only a failed signature check returns 4xx.

This module maps events to intents; it does not contain flow logic. The orchestrator owns that.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

from app.agents.error_handler import ErrorHandler, is_resume_request
from app.composition import Composition
from app.domain.events import (
    ConfluencePageEvent,
    JiraCommentEvent,
    JiraIssueUpdatedEvent,
)
from app.domain.stage import Stage
from app.webhooks.ingress import IngressOutcome, WebhookIngress

logger = logging.getLogger(__name__)


def build_webhook_router(composition: Composition) -> APIRouter:
    router = APIRouter()
    ingress = WebhookIngress(composition._registry, composition.repository, env=composition._env)

    @router.post("/webhooks/atlassian")
    async def receive(request: Request) -> Response:
        body = await request.body()
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        result = ingress.handle(body=body, headers=dict(request.headers), payload=payload)

        if result.outcome is IngressOutcome.REJECTED_SIGNATURE:
            logger.warning("dropped unauthenticated webhook: %s", result.detail)
            return Response(status_code=401)

        if not result.accepted:
            # A handled drop (duplicate, unroutable, unsupported) — ack so Atlassian does not retry.
            logger.info("webhook dropped: %s (%s)", result.outcome.value, result.detail)
            return Response(status_code=200)

        await _dispatch(composition, result)
        return Response(status_code=200)

    return router


async def _dispatch(composition: Composition, result) -> None:
    """Route an accepted event to the orchestrator, then surface any resulting error (EH-01)."""
    event = result.event
    tenant = result.tenant
    repository = composition.repository

    run_result = None

    if isinstance(event, ConfluencePageEvent):
        # A page event that admits a *new* PRD records its dedupe key inside `admit` (AD-9), so it is
        # not recorded again here. A re-entry (renamed page) records below like any follow-up event.
        run_result = await _dispatch_page(composition, event, tenant)
    elif isinstance(event, JiraCommentEvent):
        run_result = await _dispatch_comment(composition, event, tenant)
    elif isinstance(event, JiraIssueUpdatedEvent):
        run_result = await _dispatch_transition(composition, event, tenant)

    # Record the follow-up event's key so a duplicate delivery — or an AD-22 reconcile-poll of the
    # same transition — collides on the UNIQUE constraint (AD-9). Idempotent: a page-created key
    # already recorded by `admit` returns False here and is a no-op.
    if result.dedupe_key is not None and run_result is not None:
        repository.record_event_for(result.dedupe_key, run_result.prd_id)

    # EH-01 — if a stage failed, post the one escalation comment on the relevant ticket.
    if run_result is not None and run_result.error is not None:
        state = repository.state.get(run_result.prd_id)
        if state is not None:
            adapters = composition._adapters_for(tenant)
            from app.agents.ticket_manager import TicketManager

            await ErrorHandler(TicketManager(adapters.jira)).surface(
                state=state, error=run_result.error, tenant=tenant
            )


async def _dispatch_page(composition: Composition, event: ConfluencePageEvent, tenant):
    """A page event admits a new PRD (or re-enters a renamed one)."""
    repository = composition.repository
    existing = repository.state.get(event.page_id)
    if existing is None:
        from app.domain.dedupe import dedupe_key_for
        from app.domain.state import PrdState

        # Admit the new PRD atomically with its dedupe key (AD-9).
        key = dedupe_key_for(tenant.project_id, event)
        admitted = repository.admit(
            key, PrdState(prd_id=event.page_id, project_id=tenant.project_id, prd_title=event.title)
        )
        if admitted is None:
            return None  # lost the admission race to a concurrent duplicate
    # Hand the parsed event to the context so detection sees the true creator/labels/container
    # without a live re-fetch (and correctly authored-by the real uploader, not the agent).
    composition.stash_event(event.page_id, event)
    return await composition.orchestrator.advance(event.page_id)


async def _dispatch_comment(composition: Composition, event: JiraCommentEvent, tenant):
    """A Jira comment: a PM feedback comment, or an admin resume reply on an error ticket."""
    repository = composition.repository
    prd_id = _find_prd_by_ticket(repository, event.issue_key)
    if prd_id is None:
        return None
    state = repository.state.get(prd_id)
    if state is None:
        return None

    # An admin resume reply on an errored run (EH-02).
    if state.stage is Stage.ERROR and event.author_account_id == tenant.admin_account_id:
        if is_resume_request(event.body_text):
            return await composition.orchestrator.apply_admin_resume(prd_id)
        return None

    # Otherwise treat it as PM feedback in the review loop.
    return await composition.orchestrator.apply_pm_comment(prd_id, comment_text=event.body_text)


async def _dispatch_transition(composition: Composition, event: JiraIssueUpdatedEvent, tenant):
    """A gate ticket moved to Done → detect the human approval (FR-12 / FR-14, AD-15)."""
    if not event.moved_to_done:
        return None
    prd_id = _find_prd_by_ticket(composition.repository, event.issue_key)
    if prd_id is None:
        return None
    return await composition.orchestrator.apply_gate_done(prd_id, issue_key=event.issue_key)


def _find_prd_by_ticket(repository, issue_key: str) -> str | None:
    """Resolve which run a Jira ticket belongs to.

    A linear scan over active runs is fine at demo volume (one PRD at a time, AD-5). A production
    build would index ticket → prd_id; noted rather than built, since the serial queue bounds it.
    """
    for status in _ACTIVE_QUEUE_STATUSES:
        for state in repository.state.list_by_queue_status(status):
            if issue_key in _ticket_keys(state):
                return state.prd_id
    # Also check parked/errored runs, which are IDLE.
    from app.domain.stage import LIVENESS_WATCHED_STAGES, PARKED_STAGES

    for state in repository.state.list_by_stage(*(PARKED_STAGES | LIVENESS_WATCHED_STAGES)):
        if issue_key in _ticket_keys(state):
            return state.prd_id
    return None


def _ticket_keys(state) -> set[str]:
    return {
        k
        for k in (
            state.review_ticket_key,
            state.publishing_ticket_key,
            state.prd_tracking_ticket_key,
            state.rename_request_ticket_key,
        )
        if k
    }


from app.domain.stage import QueueStatus  # noqa: E402

_ACTIVE_QUEUE_STATUSES = (QueueStatus.IN_PROGRESS, QueueStatus.QUEUED, QueueStatus.IDLE)
