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
    ConfluenceCommentEvent,
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
        if event.is_trashed_event:
            # A page was deleted (FR-16). If it is a run's UserDoc draft, recover it and alert the PM.
            run_result = await _dispatch_page_trashed(composition, event, tenant)
        else:
            # A page event that admits a *new* PRD records its dedupe key inside `admit` (AD-9), so it
            # is not recorded again here. A re-entry (renamed page) records after its work, below.
            run_result = await _dispatch_page(composition, event, tenant)
    elif isinstance(event, ConfluenceCommentEvent):
        run_result = await _dispatch_confluence_comment(composition, event, tenant)
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
            from app.domain.dedupe import DedupeKey
            from app.domain.events import EventType

            def claim(comment_id: str) -> None:
                repository.record_event_for(
                    DedupeKey(tenant.project_id, EventType.JIRA_COMMENT_CREATED, comment_id),
                    run_result.prd_id,
                )

            await ErrorHandler(TicketManager(adapters.jira), on_comment=claim).surface(
                state=state, error=run_result.error, tenant=tenant
            )


async def _dispatch_page(composition: Composition, event: ConfluencePageEvent, tenant):
    """A page event admits a new PRD (or re-enters a renamed one)."""
    repository = composition.repository
    from app.domain.dedupe import dedupe_key_for
    from app.domain.stage import PendingGate
    from app.domain.state import PrdState

    # FR-16 robust deletion detection. A page event whose id is a run's OWN draft (userdoc_page_id)
    # is never a new PRD — it is the agent's own page, which the AD-10 guard below would drop. But if
    # that draft is now *trashed*, the event is a deletion signal we must act on. Crucially this
    # catches a deletion even when the Automation rule fired a generic "page updated" (not
    # "page trashed") — the common misconfiguration — because we check the page's real status rather
    # than trusting the event label. A non-deletion event about the agent's own draft is ignored.
    draft_owner = _find_prd_by_userdoc_page(repository, event.page_id)
    if draft_owner is not None:
        if event.is_trashed_event or await _page_is_trashed(composition, event.page_id, tenant):
            logger.info(
                "draft %s (run %s) is trashed; asking the PM (FR-16)", event.page_id, draft_owner
            )
            return await composition.orchestrator.apply_draft_deleted(draft_owner, event.page_id)
        logger.info("page %s is the agent's own draft (not deleted); ignored", event.page_id)
        return None

    # Rename-churn guard (FR-01a). A page's id is stable across renames, so an already-admitted PRD
    # keeps hitting this path every time its source page is renamed — and each rename is a new
    # Confluence version, i.e. a new dedupe key, so version-dedup alone would not stop it. The ONLY
    # existing-run state where a source-page event is actionable is a run parked awaiting a corrected
    # re-upload: a title mismatch (FR-02a, at `detected`) or a Classifier REJECT (EH-07, at
    # `confirmed`), both marked `UPLOADING_PM_RENAME`. For any other existing state — drafted, in
    # review, publishing, errored, complete — the PRD was already taken from its finalized version and
    # re-drafting on a later source edit is out of scope, so the event is dropped here, cheaply,
    # BEFORE the version-resolving GET. Toggling the name back and forth after drafting is now a no-op.
    existing = repository.state.get(event.page_id)
    if existing is not None and existing.pending_gate is not PendingGate.UPLOADING_PM_RENAME:
        logger.info(
            "page %s already admitted (stage=%s gate=%s); source-page change ignored "
            "(rename-churn guard)",
            event.page_id,
            existing.stage.value,
            existing.pending_gate.value,
        )
        return None

    event = await _resolve_version(composition, event, tenant)

    if _is_agent_output(event, tenant):
        # AD-10 at the door. A Confluence Automation trigger is space-wide, so it also fires for the
        # agent's OWN draft and published pages. Detection would decline them a moment later, but
        # only after `admit` had already written a state row — leaving a permanent `detected` row per
        # agent page. Refusing before admission keeps the single durable store free of runs that can
        # never advance. Cheap: it reads what the version fetch already returned.
        logger.info("page %s is the agent's own output; not admitted (AD-10)", event.page_id)
        return None

    if existing is None and not await _in_source_folder(composition, event, tenant):
        # Source-folder admission gate (AD-2/AD-14). The space-wide trigger fires for pages created
        # anywhere in the space; admitting one that is not in the watched source folder would write a
        # `detected` row that detection then declines (NOT_IN_SOURCE_FOLDER → Stay), leaving a
        # permanent dead run that still costs a scan on every ticket lookup. Detection re-checks this
        # (defense in depth); refusing here keeps the single durable store clean.
        logger.info("page %s is not in the watched source folder; not admitted", event.page_id)
        return None

    key = dedupe_key_for(tenant.project_id, event)

    if existing is None:
        # Admit the new PRD atomically with its dedupe key (AD-9).
        admitted = repository.admit(
            key, PrdState(prd_id=event.page_id, project_id=tenant.project_id, prd_title=event.title)
        )
        if admitted is None:
            return None  # lost the admission race to a concurrent duplicate
    elif repository.events.is_processed(key):
        # This exact page version was already handled — a redelivery of the same rename while still
        # awaiting the corrected upload. (Past-detection runs are already dropped by the guard above.)
        logger.info("page event %s already processed; dropped as duplicate", key.value)
        return None

    # Hand the parsed event to the context so detection sees the true creator/labels/container
    # without a live re-fetch (and correctly authored-by the real uploader, not the agent).
    composition.stash_event(event.page_id, event)
    result = await composition.orchestrator.advance(event.page_id)

    # Record a re-entry event's dedupe key AFTER the work, never before (AD-9 crash-safety). A new
    # admission already recorded its key atomically inside `admit`; for a re-entry (a rename
    # correction), recording before `advance` would — if the process died mid-advance — leave the key
    # committed with no stage change, so the redelivery is dropped as a duplicate and the run strands
    # at `detected`/`confirmed` forever (that stage is not liveness-watched). Recording after means a
    # crash simply lets the redelivery re-advance, which is idempotent.
    if existing is not None:
        repository.record_event_for(key, event.page_id)
    return result


async def _dispatch_page_trashed(composition: Composition, event: ConfluencePageEvent, tenant):
    """A page was moved to trash (FR-16). Recover it only if it is a tracked UserDoc draft.

    No admission, no version resolution, no dedupe key: recovery is idempotent (a redelivery finds
    the page already restored → a no-op), so the ingress accepts trashed events without a key and this
    handler leans on `apply_draft_deleted` for safety. A trashed page that is not any run's draft
    (a source PRD, or an unrelated page) is ignored.
    """
    prd_id = _find_prd_by_userdoc_page(composition.repository, event.page_id)
    if prd_id is None:
        logger.info("trashed page %s is not a tracked UserDoc draft; ignored", event.page_id)
        return None
    logger.info(
        "draft page %s for run %s was trashed; asking the PM (FR-16)", event.page_id, prd_id
    )
    return await composition.orchestrator.apply_draft_deleted(prd_id, event.page_id)


async def _dispatch_confluence_comment(
    composition: Composition, event: ConfluenceCommentEvent, tenant
):
    """A comment on a Confluence page (FR-17). Act on it only if it is on a tracked UserDoc draft.

    The "Page commented" Automation trigger is page-wide and fires for every comment in the space —
    footer or inline, on any page. The single admission gate is ownership: the comment's page must be a
    run's current `userdoc_page_id`. A comment on a source PRD, a published doc, or an unrelated page
    resolves to no run and is ignored. Whether the comment is genuinely *inline* (vs a page-level
    footer comment) is decided later by the orchestrator, which reads the comment through the adapter.
    """
    prd_id = _find_prd_by_userdoc_page(composition.repository, event.page_id)
    if prd_id is None:
        logger.info(
            "comment %s is on page %s, not a tracked UserDoc draft; ignored",
            event.comment_id,
            event.page_id,
        )
        return None
    logger.info(
        "comment %s on draft %s (run %s); picking it up as feedback (FR-17)",
        event.comment_id,
        event.page_id,
        prd_id,
    )
    return await composition.orchestrator.apply_inline_comment(
        prd_id,
        comment_id=event.comment_id,
        commenter_account_id=event.author_account_id,
    )


async def _page_is_trashed(composition: Composition, page_id: str, tenant) -> bool:
    """Is the page actually in the trash right now (FR-16)? — read the real status, don't trust labels.

    A hard 404 means the page is gone (purged) → treat as deleted. A transient read error is **not**
    a deletion, so we do not badger the PM over a blip (the adapter has already retried transients).
    """
    from app.domain.errors import AgentError

    try:
        page = await composition._adapters_for(tenant).confluence.get_page(page_id, with_body=False)
    except AgentError as exc:
        return exc.status_code == 404
    return page.is_trashed


def _find_prd_by_userdoc_page(repository, page_id: str) -> str | None:
    """Resolve which run owns a (now-trashed) draft page, by `userdoc_page_id`.

    Mirrors `_find_prd_by_ticket`: a linear scan over active + parked/errored runs, fine at demo
    volume (AD-5). A deleted draft belongs to a run parked or errored between drafting and publish.
    """
    from app.domain.stage import LIVENESS_WATCHED_STAGES, PARKED_STAGES

    for status in _ACTIVE_QUEUE_STATUSES:
        for state in repository.state.list_by_queue_status(status):
            if state.userdoc_page_id == page_id:
                return state.prd_id
    for state in repository.state.list_by_stage(*(PARKED_STAGES | LIVENESS_WATCHED_STAGES)):
        if state.userdoc_page_id == page_id:
            return state.prd_id
    return None


async def _resolve_version(composition: Composition, event: ConfluencePageEvent, tenant):
    """Fill in a page version the trigger could not supply (AD-9).

    Confluence Cloud Automation exposes **no page-version smart value**, and an Automation rule is
    the only way to trigger on a page event without a Connect app — so in practice every real page
    event arrives unversioned. One cheap `GET page` recovers the authoritative value, which is
    strictly better than trusting a payload field anyway: it is the version Confluence actually
    holds, so a redelivery and the AD-22 reconciler agree on the same key.

    Runs only after the signature check has passed (AD-8), so an unauthenticated request still costs
    nothing.
    """
    if not event.needs_version_resolution:
        return event
    from dataclasses import replace

    page = await composition._adapters_for(tenant).confluence.get_page(
        event.page_id, with_body=False
    )
    return replace(
        event,
        version_number=page.version,
        title=event.title or page.title,
        container_id=event.container_id or page.parent_id,
        # `get_page` already asks for labels, so carrying them costs nothing and lets the AD-10
        # guard below decide without a second call.
        labels=event.labels or tuple(page.labels),
    )


def _is_agent_output(event: ConfluencePageEvent, tenant) -> bool:
    """Positively identify a page the agent itself produced (AD-10 b).

    Two signals, both certain — never a heuristic that could refuse a real PRD:

    * the reserved ``agent-generated`` label, stamped on every page the Publisher creates;
    * the page sits directly in this tenant's *draft* or *published* folder, which only the agent
      writes to.

    A page whose container is unknown, or which is nested under something else, is **not** judged
    here — it is admitted and detection makes the full call with an ancestors lookup.
    """
    from app.config.constants import AGENT_GENERATED_LABEL

    if AGENT_GENERATED_LABEL in event.labels:
        return True
    agent_folders = {tenant.confluence_draft_folder_id, tenant.confluence_published_folder_id}
    return bool(event.container_id) and event.container_id in agent_folders


async def _in_source_folder(composition: Composition, event: ConfluencePageEvent, tenant) -> bool:
    """Is this page in the watched source folder (FR-01 / AD-14)? — the positive admission gate.

    Fast path: the resolved `container_id` is the source folder directly (a PRD dropped straight into
    it, the normal case), so no extra call. Otherwise fall back to an ancestors lookup, which also
    covers a PRD nested under a page inside the source folder. Mirrors detection's own check so the
    door-guard and detection agree; detection re-runs it as defense in depth.
    """
    source = tenant.confluence_source_folder_id
    if event.container_id == source:
        return True
    ancestors = await composition._adapters_for(tenant).confluence.get_page_ancestors(event.page_id)
    return source in ancestors


async def _dispatch_comment(composition: Composition, event: JiraCommentEvent, tenant):
    """A Jira comment: a PM feedback comment, or an admin resume reply on an error ticket."""
    repository = composition.repository
    prd_id = _find_prd_by_ticket(repository, event.issue_key)
    if prd_id is None:
        return None
    state = repository.state.get(prd_id)
    if state is None:
        return None

    # A pending draft-deletion decision takes precedence over the feedback loop (FR-16): while the
    # agent is waiting to hear whether a deletion was intentional, the PM's next comment is that
    # answer, not draft feedback.
    if state.pending_deletion_page_id:
        return await composition.orchestrator.apply_deletion_decision(
            prd_id, comment_text=event.body_text
        )

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
