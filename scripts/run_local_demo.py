#!/usr/bin/env python
"""Drive the full flow against the live tenant WITHOUT a deployed webhook endpoint (Story 6.4).

The production trigger is a Confluence/Jira webhook (SETUP-GUIDE Part 7), which needs a public HTTPS
endpoint (the Droplet, Part 8). Until that's deployed, this script stands in for the webhook layer:
it creates a real PRD page, calls the orchestrator directly, and — for the two human gates — polls
Jira exactly as the AD-22 reconciler does, so you drive the gates from the Atlassian UI.

It creates REAL artifacts in your tenant (one Confluence page, Jira tickets, comments). Everything it
creates is printed with its URL. Safe to re-run: the AD-11 idempotency guards adopt existing artifacts
rather than duplicating them.

    .venv/bin/python scripts/run_local_demo.py                 # phase 1: create PRD -> park at review
    .venv/bin/python scripts/run_local_demo.py --resume        # after you act on a gate: continue
    .venv/bin/python scripts/run_local_demo.py --status        # where is the run now?
    .venv/bin/python scripts/run_local_demo.py --baseline      # ignore comments already on a ticket
    .venv/bin/python scripts/run_local_demo.py --admin-resume  # EH-02: retry an errored run
    .venv/bin/python scripts/run_local_demo.py --cleanup       # delete the demo page + state row

Between phases, act in the Atlassian UI, then re-run with --resume:
  1. At `awaiting_review`, either
       * leave feedback on the Review ticket (the `Section: / Issue: / Suggested change:` format)
         → --resume interprets it and revises the draft; or
       * move the Review ticket to Done → --resume detects the PASS (FR-12).
  2. At `awaiting_publish_approval`: move the Publishing ticket to Done (FR-14).

**Nothing happens the moment you comment or transition.** In production those are webhooks; with no
public endpoint deployed there is no listener, so --resume polls Jira for both signals instead. That
polling is the AD-22 reconciler's mechanism, not a shortcut around a gate — a human still has to act,
and the agent still never transitions a gate ticket itself (AD-15).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]

DEMO_TITLE = "final_PRD_Quick Notes"
DEMO_PRD_MARKDOWN = """# final_PRD_Quick Notes

## Problem
People capture quick thoughts across scattered apps — a note here, a reminder there — and lose them.
There is no single fast place to jot something and find it again.

## Solution
Quick Notes: a one-keystroke capture box that saves a note instantly and makes every note findable by
full-text search. Notes sync across the user's devices.

## Requirements
- Press a global shortcut to open a capture box from anywhere and save a note in one step.
- Search all notes by their text; results update as the user types.
- Pin a note to keep it at the top of the list.
- Notes sync automatically across the user's signed-in devices.

## Scope
In scope: capture, search, pin, sync. Out of scope for v1: sharing notes with other people, rich
formatting, and attachments.
"""


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def banner(text: str) -> None:
    print(f"\n\033[1m{text}\033[0m")


async def _build():
    from app.composition import Composition
    from app.config.registry import ConfigRegistry

    registry = ConfigRegistry.from_yaml_file(ROOT / "config" / "registry.yaml")
    composition = Composition(registry)
    tenant = next(iter(registry.tenants.values()))
    return composition, tenant


async def _space_id(confluence, folder_id: str) -> str:
    folder = await confluence.get_folder(folder_id)
    return str(folder.get("spaceId") or folder.get("space", {}).get("id") or "")


async def _find_demo_page(confluence, tenant) -> str | None:
    body = await confluence._client.request(
        "GET",
        f"/wiki/api/v2/folders/{tenant.confluence_source_folder_id}/direct-children",
        operation="list_children",
        params={"limit": 100},
    )
    for child in (body or {}).get("results", []):
        if child.get("title") == DEMO_TITLE:
            return str(child.get("id"))
    return None


def _print_state(state, base_url: str) -> None:
    print(f"  prd_id (page)         {state.prd_id}")
    print(f"  stage                 \033[1m{state.stage.value}\033[0m")
    print(f"  pending_gate          {state.pending_gate.value}")
    print(f"  review_round          {state.review_round}")
    if state.prd_tracking_ticket_key:
        print(f"  tracking ticket       {base_url}/browse/{state.prd_tracking_ticket_key}")
    if state.userdoc_page_id:
        print(
            f"  UserDoc draft         {base_url}/wiki/pages/viewpage.action?pageId={state.userdoc_page_id}"
        )
    if state.review_ticket_key:
        print(f"  Review ticket         {base_url}/browse/{state.review_ticket_key}")
    if state.publishing_ticket_key:
        print(f"  Publishing ticket     {base_url}/browse/{state.publishing_ticket_key}")
    if state.md_export_path:
        print(f"  exported .md          {state.md_export_path}")


async def create_and_start() -> None:
    composition, tenant = await _build()
    confluence = composition._adapters_for(tenant).confluence
    base_url = composition._adapters_for(tenant).base_url
    repo = composition.repository

    banner("Phase 1 — create the PRD page and start the flow")

    page_id = await _find_demo_page(confluence, tenant)
    if page_id is None:
        space_id = await _space_id(confluence, tenant.confluence_source_folder_id)
        storage = confluence.markdown_to_storage(DEMO_PRD_MARKDOWN)
        page = await confluence.create_page(
            space_id=space_id, title=DEMO_TITLE, body_storage=storage
        )
        await confluence.move_page(page.id, tenant.confluence_source_folder_id)
        page_id = page.id
        print(f"  created PRD page {page_id} in the source folder ({DEMO_TITLE!r})")
    else:
        print(f"  reusing existing demo PRD page {page_id}")

    # Admit the PRD to the flow (what the webhook layer's admission step does).
    from app.domain.dedupe import DedupeKey
    from app.domain.events import ConfluencePageEvent, EventType
    from app.domain.state import PrdState

    if repo.state.get(page_id) is None:
        key = DedupeKey(tenant.project_id, EventType.CONFLUENCE_PAGE_CREATED, page_id, "1")
        repo.admit(
            key, PrdState(prd_id=page_id, project_id=tenant.project_id, prd_title=DEMO_TITLE)
        )

    # Stand in for the webhook event. In production a human PM uploads the PRD with their own
    # account; this demo's PRD page was physically created with the agent's own token (only one is
    # available), which detection's AD-10 self-author guard would (correctly) decline. So we present
    # the event as authored by the configured PM — the real "a human uploaded this" scenario — which
    # is exactly what a live webhook payload carries. Detection then runs its real folder/label/
    # author checks against a faithful input.
    composition.stash_event(
        page_id,
        ConfluencePageEvent(
            event_type=EventType.CONFLUENCE_PAGE_CREATED,
            page_id=page_id,
            version_number=1,
            title=DEMO_TITLE,
            creator_account_id=tenant.pm_account_id,
            container_id=tenant.confluence_source_folder_id,
        ),
    )

    result = await composition.orchestrator.advance(page_id)

    banner("Result")
    print(f"  advanced through: {[s.value for s in result.advanced]}")
    if result.error:
        print(f"  \033[31mERROR:\033[0m {result.error}")
    _print_state(repo.state.require(page_id), base_url)

    banner("Your move")
    print("  Open the Review ticket above, then either:")
    print("    a) leave feedback in the `Section: / Issue: / Suggested change:` format, or")
    print("    b) move it to \033[1mDone\033[0m to PASS it.")
    print("  Either way, nothing runs until you then run:")
    print("      .venv/bin/python scripts/run_local_demo.py --resume")
    composition.close()


def _comment_key(tenant, comment_id: str):
    from app.domain.dedupe import DedupeKey
    from app.domain.events import EventType

    return DedupeKey(tenant.project_id, EventType.JIRA_COMMENT_CREATED, comment_id)


async def _ingest_feedback(composition, tenant, repo, jira, prd_id: str, issue_key: str) -> bool:
    """Feed the newest unseen comment on the gate ticket to the orchestrator (FR-09).

    This stands in for the webhook layer's `_dispatch_comment`. Without a deployed endpoint no
    `comment-created` event ever arrives, so a PM's feedback sits in Jira and nothing runs — polling
    it back is the only way to reach the review loop locally.

    Comments the agent posted itself are skipped because their ids were claimed in `processed_events`
    at post time (the same AD-9 record the webhook path dedupes on), **not** by comparing author
    accounts — which matters here, since this demo's agent token and the human reviewer are the same
    Atlassian account and an author check could not tell them apart.

    Rule: only the *newest* unseen comment is interpreted; anything older is marked seen without
    being read. A poll sees a whole backlog at once and the latest word supersedes — and it keeps a
    comment written before ids were claimed from being replayed as fresh feedback.
    """
    comments = await jira.get_comments(issue_key)
    unseen = [c for c in comments if not repo.events.is_processed(_comment_key(tenant, c.id))]
    if not unseen:
        print(f"  no new comments on {issue_key}")
        return False

    *history, latest = unseen
    for old in history:
        repo.record_event_for(_comment_key(tenant, old.id), prd_id)
    if history:
        print(f"  marked {len(history)} earlier comment(s) on {issue_key} as history")

    preview = " ".join(latest.body_text.split())[:120]
    print(f"  → interpreting comment {latest.id}: {preview!r}")
    result = await composition.orchestrator.apply_pm_comment(prd_id, comment_text=latest.body_text)
    repo.record_event_for(_comment_key(tenant, latest.id), prd_id)

    if result.error:
        print(f"    \033[31mERROR:\033[0m {result.error}")
    else:
        print(f"    → stage now \033[1m{result.final_stage.value}\033[0m")
        if result.stopped_reason:
            print(f"      ({result.stopped_reason})")
    return True


async def baseline() -> None:
    """Mark every comment currently on the gate tickets as already seen.

    An escape hatch: use it when a ticket carries comments from before this run that should not be
    read as feedback. After it, only genuinely new comments are interpreted.
    """
    composition, tenant = await _build()
    repo = composition.repository
    jira = composition._adapters_for(tenant).jira
    page_id = await _find_demo_page(composition._adapters_for(tenant).confluence, tenant)
    state = repo.state.get(page_id) if page_id else None
    if state is None:
        print("No demo run found.")
        composition.close()
        return

    banner("Baselining existing comments")
    for issue_key in (state.review_ticket_key, state.publishing_ticket_key):
        if not issue_key:
            continue
        claimed = 0
        for comment in await jira.get_comments(issue_key):
            if repo.record_event_for(_comment_key(tenant, comment.id), page_id):
                claimed += 1
        print(f"  {issue_key}: marked {claimed} comment(s) as seen")
    composition.close()


async def admin_resume() -> None:
    """EH-02 — re-run an errored run from its `last_good_checkpoint`.

    In production an admin types `@agent resume` on the error ticket and the webhook layer routes it
    to `apply_admin_resume`. With no endpoint deployed that path is unreachable, so this exposes the
    same call. It is *not* a bypass: a run in `error` is deliberately inert (it must never quietly
    restart on the next unrelated event), and this re-enters at the failed stage only — the publish
    transaction is ordered and idempotent (AD-18), so completed steps are adopted, not repeated.
    """
    composition, tenant = await _build()
    base_url = composition._adapters_for(tenant).base_url
    repo = composition.repository
    page_id = await _find_demo_page(composition._adapters_for(tenant).confluence, tenant)
    state = repo.state.get(page_id) if page_id else None
    if state is None:
        print("No demo run found.")
        composition.close()
        return

    from app.domain.stage import Stage

    if state.stage is not Stage.ERROR:
        print(f"  Run is at {state.stage.value}, not error — nothing to resume.")
    else:
        print(f"  Resuming from last good checkpoint: {state.last_good_checkpoint}")
        if state.last_error:
            print(f"  (previous failure: {state.last_error})")
        result = await composition.orchestrator.apply_admin_resume(page_id)
        if result.error:
            print(f"\n  \033[31mSTILL FAILING:\033[0m {result.error}")
            print(f"  \033[33mSuggested fix:\033[0m {result.error.suggested_fix}")

    banner("Current state")
    final = repo.state.require(page_id)
    _print_state(final, base_url)
    if final.stage.value == "complete":
        # Don't claim a restriction the tenant opted out of — the whole point of the notice on the
        # Publishing ticket is that an unprotected page must not look like a protected one.
        steps = "moved, and exported"
        if final.restriction_applied_at is not None:
            steps = "restricted, moved, and exported"
        banner(f"🎉 Complete — the UserDoc is published, {steps}.")
        if final.restriction_applied_at is None:
            print("  Note: the edit restriction was skipped (require_edit_restriction: false).")
            print("  The published page is still editable — see BLOCKERS.md → B-7.")
    composition.close()


async def resume() -> None:
    composition, tenant = await _build()
    base_url = composition._adapters_for(tenant).base_url
    repo = composition.repository
    jira = composition._adapters_for(tenant).jira

    page_id = await _find_demo_page(composition._adapters_for(tenant).confluence, tenant)
    state = repo.state.get(page_id) if page_id else None
    if state is None:
        print("No demo run found. Run without --resume first.")
        composition.close()
        return

    banner(f"Resuming from stage: {state.stage.value}")

    from app.domain.stage import Stage

    # 1. Feedback first, then the gate — the order a webhook would have delivered them in. A PM who
    #    comments and *then* moves the ticket to Done gets the revision applied before the PASS.
    review_stages = {
        Stage.AWAITING_REVIEW,
        Stage.AWAITING_STRUCTURE_CONFIRM,
        Stage.AWAITING_CLARIFICATION,
    }
    if state.stage in review_stages and state.review_ticket_key:
        await _ingest_feedback(composition, tenant, repo, jira, page_id, state.review_ticket_key)
        state = repo.state.require(page_id)  # the comment may have moved the run

    # 2. Poll the relevant gate ticket (what the AD-22 reconciler does) and feed a found Done as input.
    if state.stage is Stage.AWAITING_REVIEW and state.review_ticket_key:
        issue = await jira.get_issue(state.review_ticket_key)
        if issue.is_done:
            print(f"  Review ticket {issue.key} is Done → PASS")
            await composition.orchestrator.apply_gate_done(page_id, issue_key=issue.key)
        else:
            print(
                f"  Review ticket {issue.key} is still {issue.status_name!r} — leave feedback and "
                "re-run --resume, or move it to Done to PASS."
            )
    elif state.stage is Stage.AWAITING_PUBLISH_APPROVAL and state.publishing_ticket_key:
        issue = await jira.get_issue(state.publishing_ticket_key)
        if issue.is_done:
            print(f"  Publishing ticket {issue.key} is Done → publishing")
            await composition.orchestrator.apply_gate_done(page_id, issue_key=issue.key)
        else:
            print(
                f"  Publishing ticket {issue.key} is still {issue.status_name!r} — move it to Done first."
            )
    elif state.stage in review_stages:
        # The feedback loop parked on a reply (FR-08 / FR-10). AD-16: it must block on a human, so
        # there is nothing to poll — answer the agent's question on the ticket and re-run --resume.
        print(f"  Parked at {state.stage.value} — reply to the agent's question on the ticket.")
    else:
        await composition.orchestrator.advance(page_id)

    banner("Current state")
    final = repo.state.require(page_id)
    _print_state(final, base_url)
    if final.stage.value == "complete":
        # Don't claim a restriction the tenant opted out of — the whole point of the notice on the
        # Publishing ticket is that an unprotected page must not look like a protected one.
        steps = "moved, and exported"
        if final.restriction_applied_at is not None:
            steps = "restricted, moved, and exported"
        banner(f"🎉 Complete — the UserDoc is published, {steps}.")
        if final.restriction_applied_at is None:
            print("  Note: the edit restriction was skipped (require_edit_restriction: false).")
            print("  The published page is still editable — see BLOCKERS.md → B-7.")
    composition.close()


async def status() -> None:
    composition, tenant = await _build()
    base_url = composition._adapters_for(tenant).base_url
    page_id = await _find_demo_page(composition._adapters_for(tenant).confluence, tenant)
    state = composition.repository.state.get(page_id) if page_id else None
    if state is None:
        print("No demo run found.")
    else:
        banner("Demo run status")
        _print_state(state, base_url)
    composition.close()


async def cleanup() -> None:
    composition, tenant = await _build()
    confluence = composition._adapters_for(tenant).confluence
    page_id = await _find_demo_page(confluence, tenant)
    if page_id:
        await confluence._client.request(
            "DELETE", f"/wiki/api/v2/pages/{page_id}", operation="delete_page", expected=(204, 200)
        )
        print(f"  deleted demo PRD page {page_id}")
    # The state row + tickets are left for inspection; delete the state file manually if desired.
    print("  (Jira tickets and the draft page are left in place for inspection.)")
    composition.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive the flow against the live tenant")
    parser.add_argument("--resume", action="store_true", help="continue after acting on a gate")
    parser.add_argument("--status", action="store_true", help="show where the run is")
    parser.add_argument("--cleanup", action="store_true", help="delete the demo PRD page")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="mark all existing ticket comments as seen (do not read them as feedback)",
    )
    parser.add_argument(
        "--admin-resume",
        action="store_true",
        help="EH-02: re-run an errored run from its last good checkpoint (`@agent resume`)",
    )
    args = parser.parse_args()

    load_env()
    if args.status:
        asyncio.run(status())
    elif args.cleanup:
        asyncio.run(cleanup())
    elif args.baseline:
        asyncio.run(baseline())
    elif args.admin_resume:
        asyncio.run(admin_resume())
    elif args.resume:
        asyncio.run(resume())
    else:
        asyncio.run(create_and_start())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
