#!/usr/bin/env python
"""Verify this deployment's configuration end to end (SETUP-GUIDE.md Part 6c).

Checks that `config/registry.yaml` parses, every secret it references resolves, the credentials work
against the real Atlassian site, the configured projects/folders/accounts actually exist, and the
Anthropic and LangSmith keys are accepted.

**Read-only.** It creates no ticket, page, or comment — safe to run against a live site any time.

    python scripts/verify_setup.py
    python scripts/verify_setup.py --tenant project_alpha
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.registry import ConfigError, ConfigRegistry  # noqa: E402
from app.config.schema import TenantConfig  # noqa: E402
from app.config.secrets import (  # noqa: E402
    AtlassianCredentials,
    SecretResolutionError,
    resolve_atlassian_credentials,
    resolve_secret,
)

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

PASS, FAIL, WARN, SKIP = (
    "\033[32m PASS \033[0m",
    "\033[31m FAIL \033[0m",
    "\033[33m WARN \033[0m",
    " SKIP ",
)

_results: list[tuple[str, str]] = []


def record(status: str, message: str, hint: str = "") -> None:
    _results.append((status, message))
    print(f"[{status}] {message}")
    if hint and status in (FAIL, WARN):
        print(f"         → {hint}")


def load_dotenv(path: Path) -> dict[str, str]:
    """Read `.env` without requiring it to be exported into the shell first."""
    env = dict(os.environ)
    if not path.is_file():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env


def atlassian_client(credentials: AtlassianCredentials) -> httpx.Client:
    auth = base64.b64encode(f"{credentials.email}:{credentials.api_token}".encode()).decode()
    return httpx.Client(
        base_url=credentials.base_url,
        timeout=TIMEOUT,
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
    )


def check_atlassian(tenant: TenantConfig, env: dict[str, str]) -> None:
    try:
        jira_creds = resolve_atlassian_credentials(tenant.jira_credentials_ref, env)
        conf_creds = resolve_atlassian_credentials(tenant.confluence_credentials_ref, env)
    except SecretResolutionError as exc:
        record(FAIL, "Atlassian credentials", str(exc))
        return

    record(PASS, f"credential references resolve ({jira_creds.base_url})")

    # -- Jira ---------------------------------------------------------------------------------
    with atlassian_client(jira_creds) as client:
        try:
            response = client.get("/rest/api/3/myself")
        except httpx.RequestError as exc:
            record(FAIL, "Jira reachable", f"{type(exc).__name__} — check ATLASSIAN base URL")
            return

        if response.status_code == 401:
            record(
                FAIL,
                "Jira authentication",
                "401 — the email must be the account that OWNS the token (SETUP-GUIDE Part 1)",
            )
            return
        if response.status_code >= 400:
            record(FAIL, "Jira authentication", f"HTTP {response.status_code}")
            return

        me = response.json()
        record(PASS, f"Jira authenticated as {me.get('displayName')} ({me.get('accountId')})")

        for label, key in (
            ("main", tenant.jira_main_project_key),
            ("review", tenant.jira_review_project_key),
        ):
            result = client.get(f"/rest/api/3/project/{key}")
            if result.status_code == 200:
                record(PASS, f"Jira {label} project {key} — {result.json().get('name')}")
            else:
                record(
                    FAIL,
                    f"Jira {label} project {key} (HTTP {result.status_code})",
                    "Check the key in config/registry.yaml, or grant this account access.",
                )

        for role, account_id in (
            ("pm_account_id", tenant.pm_account_id),
            ("head_of_product_account_id", tenant.head_of_product_account_id),
            ("admin_account_id", tenant.admin_account_id),
        ):
            result = client.get("/rest/api/3/user", params={"accountId": account_id})
            if result.status_code == 200:
                record(PASS, f"{role} → {result.json().get('displayName')}")
            else:
                record(
                    FAIL,
                    f"{role} {account_id!r} not found (HTTP {result.status_code})",
                    "An accountId is NOT an email. See SETUP-GUIDE.md section 2d.",
                )

    # -- Confluence ----------------------------------------------------------------------------
    with atlassian_client(conf_creds) as client:
        folders = {
            "source (watched)": tenant.confluence_source_folder_id,
            "draft": tenant.confluence_draft_folder_id,
            "published": tenant.confluence_published_folder_id,
        }
        for label, folder_id in folders.items():
            result = client.get(f"/wiki/api/v2/folders/{folder_id}")
            if result.status_code == 200:
                record(
                    PASS, f"Confluence {label} folder {folder_id} — {result.json().get('title')}"
                )
            else:
                record(
                    FAIL,
                    f"Confluence {label} folder {folder_id} (HTTP {result.status_code})",
                    "Get the id from the folder's URL: /wiki/spaces/<KEY>/folder/<ID>",
                )

        # The config loader already rejects published == source. This catches the case it cannot
        # see: published nested INSIDE source, which would make the agent re-ingest its own output.
        published = client.get(f"/wiki/api/v2/folders/{tenant.confluence_published_folder_id}")
        if published.status_code == 200:
            parent = str(published.json().get("parentId") or "")
            if parent == tenant.confluence_source_folder_id:
                record(
                    FAIL,
                    "published folder is INSIDE the source folder",
                    "Move it to be a SIBLING of source. Otherwise the agent detects its own "
                    "published document as a new PRD and drafts forever (SETUP-GUIDE 2c).",
                )
            else:
                record(PASS, "published folder is not nested inside the watched source folder")


def check_key(name: str, ref: str, env: dict[str, str], probe: str | None = None) -> None:
    try:
        value = resolve_secret(ref, env)
    except SecretResolutionError as exc:
        record(FAIL, name, str(exc))
        return
    if not probe:
        record(PASS, f"{name} is set")
        return

    try:
        if probe == "anthropic":
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                timeout=TIMEOUT,
                headers={
                    "x-api-key": value,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            ok = response.status_code < 400 or response.status_code == 429
        else:  # langsmith
            response = httpx.get(
                "https://api.smith.langchain.com/api/v1/sessions",
                timeout=TIMEOUT,
                headers={"x-api-key": value},
                params={"limit": 1},
            )
            ok = response.status_code < 400
    except httpx.RequestError as exc:
        record(WARN, f"{name} set, but could not be checked ({type(exc).__name__})")
        return

    if ok:
        record(PASS, f"{name} accepted by the API")
    else:
        record(FAIL, f"{name} rejected (HTTP {response.status_code})", "Regenerate the key.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify this deployment's configuration")
    parser.add_argument("--tenant", help="verify only this tenant")
    args = parser.parse_args()

    print("\n\033[1mLeapXpert_AgentA — setup verification\033[0m")
    print("Read-only: no ticket, page, or comment is created.\n")

    env = load_dotenv(ROOT / ".env")
    record(
        PASS if (ROOT / ".env").is_file() else WARN,
        ".env present" if (ROOT / ".env").is_file() else ".env not found (using shell environment)",
    )

    registry_path = ROOT / "config" / "registry.yaml"
    if not registry_path.is_file():
        record(
            FAIL,
            "config/registry.yaml not found",
            "cp config/registry.example.yaml config/registry.yaml",
        )
        return 1
    try:
        registry = ConfigRegistry.from_yaml_file(registry_path)
    except ConfigError as exc:
        record(FAIL, "config/registry.yaml is invalid", str(exc))
        return 1
    record(PASS, f"config/registry.yaml parsed ({len(registry.tenants)} tenant(s))")

    for project_id, tenant in registry.tenants.items():
        if args.tenant and project_id != args.tenant:
            continue
        print(f"\n\033[1mTenant: {project_id}\033[0m")
        if "REPLACE" in tenant.confluence_source_folder_id:
            record(
                FAIL,
                "config still contains REPLACE_ placeholders",
                "Fill in SETUP-GUIDE Part 2 values.",
            )
            continue
        check_atlassian(tenant, env)

    print("\n\033[1mShared services\033[0m")
    check_key("webhook shared secret", registry.system.webhook_secret_ref, env)
    check_key("admin API token", registry.system.admin_token_ref, env)
    check_key("Anthropic API key", registry.system.anthropic_api_key_ref, env, probe="anthropic")
    if registry.system.observability.langsmith_enabled:
        check_key(
            "LangSmith API key",
            registry.system.observability.langsmith_api_key_ref,
            env,
            probe="langsmith",
        )
    else:
        record(SKIP, "LangSmith (set system.observability.langsmith_enabled: true to enable)")

    failures = sum(1 for status, _ in _results if status == FAIL)
    print(
        f"\n\033[1m{len(_results) - failures} passed, {failures} failed.\033[0m"
        + ("  Setup looks good.\n" if not failures else "  Fix the FAIL lines above and re-run.\n")
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
