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
    .venv/bin/python scripts/run_local_demo.py --cleanup       # delete the demo page + state row

Between phases, act in the Atlassian UI:
  1. At `awaiting_review`: open the Review ticket, leave feedback (optional), then move it to Done.
  2. At `awaiting_publish_approval`: open the Publishing ticket and move it to Done.
Re-run with --resume after each.
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
    from app.domain.events import EventType
    from app.domain.state import PrdState

    if repo.state.get(page_id) is None:
        key = DedupeKey(tenant.project_id, EventType.CONFLUENCE_PAGE_CREATED, page_id, "1")
        repo.admit(
            key, PrdState(prd_id=page_id, project_id=tenant.project_id, prd_title=DEMO_TITLE)
        )

    result = await composition.orchestrator.advance(page_id)

    banner("Result")
    print(f"  advanced through: {[s.value for s in result.advanced]}")
    if result.error:
        print(f"  \033[31mERROR:\033[0m {result.error}")
    _print_state(repo.state.require(page_id), base_url)

    banner("Your move")
    print("  Open the Review ticket above, optionally leave feedback in the")
    print("  `Section: / Issue: / Suggested change:` format, then move it to \033[1mDone\033[0m.")
    print("  Then run:  .venv/bin/python scripts/run_local_demo.py --resume")
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

    # Poll the relevant gate ticket (what the AD-22 reconciler does) and feed a found Done as input.
    from app.domain.stage import Stage

    if state.stage is Stage.AWAITING_REVIEW and state.review_ticket_key:
        issue = await jira.get_issue(state.review_ticket_key)
        if issue.is_done:
            print(f"  Review ticket {issue.key} is Done → PASS")
            await composition.orchestrator.apply_gate_done(page_id, issue_key=issue.key)
        else:
            print(
                f"  Review ticket {issue.key} is still {issue.status_name!r} — move it to Done first."
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
    else:
        await composition.orchestrator.advance(page_id)

    banner("Current state")
    final = repo.state.require(page_id)
    _print_state(final, base_url)
    if final.stage.value == "complete":
        banner("🎉 Complete — the UserDoc is published, restricted, moved, and exported.")
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
    args = parser.parse_args()

    load_env()
    if args.status:
        asyncio.run(status())
    elif args.cleanup:
        asyncio.run(cleanup())
    elif args.resume:
        asyncio.run(resume())
    else:
        asyncio.run(create_and_start())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
