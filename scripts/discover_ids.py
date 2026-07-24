#!/usr/bin/env python
"""Print every Atlassian ID needed to fill in `config/registry.yaml` (SETUP-GUIDE.md Part 2).

Finding Confluence folder IDs and Atlassian account IDs by hand is the fiddliest part of setup, and
a wrong ID surfaces much later as a confusing 404. This asks Atlassian directly.

Read-only: it performs GETs and changes nothing.

    export ATLASSIAN_SITE_URL="https://yourcompany.atlassian.net"
    export ATLASSIAN_EMAIL="you@example.com"
    export ATLASSIAN_API_TOKEN="..."

    python scripts/discover_ids.py
    python scripts/discover_ids.py --emails pm@example.com head@example.com
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from typing import Any

import httpx

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def fail(message: str) -> None:
    print(f"\n  ERROR  {message}\n", file=sys.stderr)
    sys.exit(1)


def build_client() -> httpx.Client:
    site = os.environ.get("ATLASSIAN_SITE_URL", "").strip().rstrip("/")
    email = os.environ.get("ATLASSIAN_EMAIL", "").strip()
    token = os.environ.get("ATLASSIAN_API_TOKEN", "").strip()

    missing = [
        name
        for name, value in (
            ("ATLASSIAN_SITE_URL", site),
            ("ATLASSIAN_EMAIL", email),
            ("ATLASSIAN_API_TOKEN", token),
        )
        if not value
    ]
    if missing:
        fail(
            f"missing environment variable(s): {', '.join(missing)}.\n"
            "         See SETUP-GUIDE.md Part 1 for how to create an API token."
        )
    if not site.startswith("http"):
        fail(f"ATLASSIAN_SITE_URL must start with https:// (got {site!r})")

    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    return httpx.Client(
        base_url=site,
        timeout=TIMEOUT,
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
    )


def get(client: httpx.Client, path: str, **params: Any) -> Any:
    """GET with human-readable failures — this script is often someone's first API call."""
    try:
        response = client.get(path, params=params or None)
    except httpx.RequestError as exc:
        fail(f"could not reach {client.base_url} ({type(exc).__name__}). Check the site URL.")
    if response.status_code == 401:
        fail(
            "401 Unauthorized — the email/token pair was rejected.\n"
            "         Check ATLASSIAN_EMAIL is the account that OWNS the token (SETUP-GUIDE Part 1)."
        )
    if response.status_code == 403:
        fail("403 Forbidden — the token's account lacks permission for this site.")
    if response.status_code >= 400:
        return None  # optional endpoint (e.g. folders on an older instance); caller degrades
    return response.json()


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n{'─' * len(title)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover Atlassian IDs for config/registry.yaml")
    parser.add_argument(
        "--emails", nargs="*", default=[], help="teammate emails to resolve to account IDs"
    )
    args = parser.parse_args()

    client = build_client()

    # -- the agent's own account (AD-10) -------------------------------------------------------
    section("Your account (the one the agent will act as)")
    me = get(client, "/rest/api/3/myself") or {}
    print(f"  accountId    {me.get('accountId', '(unknown)')}")
    print(f"  displayName  {me.get('displayName', '(unknown)')}")
    print(f"  email        {me.get('emailAddress', '(hidden by site privacy settings)')}")
    print("\n  Use this accountId for pm/head_of_product/admin if you are doing all three roles.")

    # -- Jira projects -------------------------------------------------------------------------
    section("Jira projects  →  jira_main_project_key / jira_review_project_key")
    projects = (get(client, "/rest/api/3/project/search", maxResults=100) or {}).get("values", [])
    if not projects:
        print("  (none found — check the token's account can see your projects)")
    for project in projects:
        print(f"  {project.get('key', '?'):<12} {project.get('name', '')}")
    print("\n  These two must be DIFFERENT projects.")

    # -- Confluence spaces and folders ---------------------------------------------------------
    section("Confluence spaces  →  confluence_space_key")
    spaces = (get(client, "/wiki/api/v2/spaces", limit=100) or {}).get("results", [])
    if not spaces:
        print("  (none found — is Confluence enabled on this site?)")
    for space in spaces:
        print(f"  key={space.get('key', '?'):<12} id={space.get('id', '?'):<12} {space.get('name', '')}")

    section("Confluence folders  →  confluence_source / draft / published _folder_id")
    found_any = False
    for space in spaces:
        folders = (get(client, f"/wiki/api/v2/spaces/{space.get('id')}/folders", limit=100) or {}).get(
            "results", []
        )
        if not folders:
            continue
        found_any = True
        print(f"\n  Space {space.get('key')}:")
        for folder in folders:
            print(f"    id={folder.get('id', '?'):<14} {folder.get('title', '')}")

    if not found_any:
        print(
            "  No folders returned by the API.\n"
            "  Get each ID from the browser instead: open the folder in Confluence and read the URL —\n"
            "    https://<site>/wiki/spaces/<SPACEKEY>/folder/<THIS-IS-THE-ID>"
        )

    print(
        "\n  \033[1mCheck the layout:\033[0m the PUBLISHED folder must be a SIBLING of the source\n"
        "  folder, never inside it. If it is inside, the agent will detect its own published\n"
        "  document as a new PRD and draft forever. See SETUP-GUIDE.md section 2c."
    )

    # -- teammate account ids ------------------------------------------------------------------
    if args.emails:
        section("Teammate account IDs  →  pm / head_of_product / admin _account_id")
        for email in args.emails:
            users = get(client, "/rest/api/3/user/search", query=email) or []
            match = next(
                (u for u in users if str(u.get("emailAddress", "")).lower() == email.lower()),
                users[0] if users else None,
            )
            if match:
                print(f"  {email:<32} {match.get('accountId')}  ({match.get('displayName')})")
            else:
                print(
                    f"  {email:<32} NOT FOUND — the site may hide emails; get the ID from the\n"
                    f"  {'':<32} profile URL instead: /wiki/people/<accountId>"
                )

    print("\nNext: copy these into config/registry.yaml, then run scripts/verify_setup.py\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
