"""Epic 6 operations — content-gating (6.7), config-only modifiability (6.6), deploy artifacts (6.4/6.5)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agents.llm import CallMetadata, LlmClient
from app.config.registry import ConfigRegistry
from tests.conftest import registry_mapping, tenant_entry
from tests.test_llm_client import FakeAnthropic, RecordingTracer

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------------------------
# Story 6.7 — content-gating observability flag (AD-20, NFR-01).
# ---------------------------------------------------------------------------------------------


def test_trace_content_defaults_to_metadata_only() -> None:
    """The demo traces non-confidential test PRDs only; the default keeps document bodies out."""
    tenant = ConfigRegistry.from_mapping(registry_mapping()).by_project_id("tenant_one")
    assert tenant.trace_content is False


async def test_metadata_only_tracing_does_not_egress_content() -> None:
    """AD-20 — with the flag off, prompt/completion text never reaches the trace."""
    tracer = RecordingTracer()
    client = LlmClient(
        "k", tracer=tracer, trace_content=False, client=FakeAnthropic(text="secret doc")
    )

    await client.complete(
        model="claude-sonnet-5",
        system="s",
        prompt="confidential PRD contents",
        metadata=CallMetadata(correlation_id="c", prd_id="p", agent_role="author"),
    )

    serialized = str(tracer.spans[0].inputs) + str(tracer.spans[0].outputs)
    assert "confidential PRD contents" not in serialized
    assert "secret doc" not in serialized
    # But the cost/latency signal NFR-01 requires is still present.
    assert "total_tokens" in tracer.spans[0].outputs


async def test_the_flag_can_opt_in_to_content_tracing() -> None:
    """The seam toward full-content tracing exists; a tenant can turn it on deliberately."""
    tracer = RecordingTracer()
    client = LlmClient(
        "k", tracer=tracer, trace_content=True, client=FakeAnthropic(text="doc body")
    )
    await client.complete(
        model="claude-sonnet-5",
        system="s",
        prompt="the PRD",
        metadata=CallMetadata(correlation_id="c", prd_id="p", agent_role="author"),
    )
    assert tracer.spans[0].inputs["prompt"] == "the PRD"


# ---------------------------------------------------------------------------------------------
# Story 6.6 — config-only modifiability (NFR-02, AD-4).
# ---------------------------------------------------------------------------------------------


def test_adding_a_second_tenant_is_pure_config() -> None:
    """NFR-02 — a second project routes correctly with no code change, just a registry entry."""
    registry = ConfigRegistry.from_mapping(
        registry_mapping(
            project_alpha=tenant_entry(),
            project_beta=tenant_entry(
                confluence_source_folder_id="beta-source",
                confluence_draft_folder_id="beta-draft",
                confluence_published_folder_id="beta-published",
                jira_main_project_key="BETAMAIN",
                jira_review_project_key="BETAREV",
                pm_account_id="acct-beta-pm",
                head_of_product_account_id="acct-beta-hop",
                admin_account_id="acct-beta-admin",
                md_export_dir="/data/userdocs/beta",
                jira_credentials_ref="env:BETA_JIRA",
                confluence_credentials_ref="env:BETA_CONF",
            ),
        )
    )
    # Both tenants resolve independently — the routing indexes handle the second with no new code.
    assert registry.watches_source_folder("beta-source").project_id == "project_beta"
    assert registry.by_jira_project_key("BETAREV").project_id == "project_beta"
    assert registry.watches_source_folder("folder-source-1").project_id == "project_alpha"


def test_swapping_a_reviewer_is_a_single_config_field() -> None:
    """NFR-02 — changing the PM is one field, no code touched."""
    original = ConfigRegistry.from_mapping(registry_mapping()).by_project_id("tenant_one")
    swapped = ConfigRegistry.from_mapping(
        registry_mapping(tenant_one=tenant_entry(pm_account_id="acct-new-pm"))
    ).by_project_id("tenant_one")
    assert original.pm_account_id != swapped.pm_account_id
    assert swapped.pm_account_id == "acct-new-pm"


# ---------------------------------------------------------------------------------------------
# Stories 6.4 / 6.5 — deploy artifacts exist and encode the AD-21 rules.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "artifact",
    [
        "deploy/Dockerfile",
        "deploy/Caddyfile",
        "deploy/litestream.yml",
        "deploy/provision.sh",
        "deploy/reconcile.cron",
        "deploy/README.md",
        ".github/workflows/ci.yml",
        ".github/workflows/build-image.yml",
    ],
)
def test_deploy_artifact_exists(artifact: str) -> None:
    assert (PROJECT_ROOT / artifact).is_file(), f"missing deploy artifact: {artifact}"


def test_dockerfile_uses_the_slim_base_and_a_single_worker() -> None:
    """AD-21 — slim base, single Uvicorn worker."""
    dockerfile = (PROJECT_ROOT / "deploy" / "Dockerfile").read_text()
    assert "python:3.12-slim" in dockerfile
    assert "--workers" in dockerfile and "1" in dockerfile
    assert "USER agent" in dockerfile, "the broadly-privileged agent process must not run as root"


def test_provision_adds_swap_and_restricts_the_firewall() -> None:
    """AD-21 — 1-2 GB swap; firewall opens only 443 + 22."""
    script = (PROJECT_ROOT / "deploy" / "provision.sh").read_text()
    assert "swap" in script.lower()
    assert "ufw allow 443" in script and "ufw allow 22" in script

    caddy = (PROJECT_ROOT / "deploy" / "Caddyfile").read_text()
    # The admin endpoint must not be publicly proxied (AD-22) — no `handle /admin...` block, and
    # only the webhook path is reverse-proxied.
    assert "handle /admin" not in caddy
    assert "handle /webhooks/" in caddy


def test_litestream_targets_off_box_object_storage() -> None:
    """AD-23 — the single store is replicated off-box."""
    config = (PROJECT_ROOT / "deploy" / "litestream.yml").read_text()
    assert "/data/state.db" in config
    assert "digitaloceanspaces.com" in config


def test_reconcile_cron_hits_the_localhost_admin_endpoint() -> None:
    """AD-22 — cron → authenticated localhost admin endpoint, no always-on scheduler."""
    cron = (PROJECT_ROOT / "deploy" / "reconcile.cron").read_text()
    assert "127.0.0.1:8000/admin/reconcile" in cron
    assert "X-Admin-Token" in cron


def test_build_workflow_builds_off_the_box() -> None:
    """AD-21 — the image is built in CI, never on the 1 GB Droplet."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "build-image.yml").read_text()
    assert "docker/build-push-action" in workflow
    assert "deploy/Dockerfile" in workflow


def test_a_restriction_403_names_the_plan_tier_not_the_permissions() -> None:
    """Confluence Cloud Free has no page restrictions and reports it as a permission error.

    The generic 403 advice ("grant the account access to the space") is actively misleading here: the
    account can already hold `restrict_content` and `administer` on the space and still get this 403,
    so an admin following that advice hunts for a permission that is already granted.
    """
    import httpx

    from app.adapters.http import AtlassianClient
    from app.config.secrets import AtlassianCredentials
    from app.domain.errors import AgentError

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "Not enough permissions to alter"})

    credentials = AtlassianCredentials(
        base_url="https://example.atlassian.net", email="svc@example.com", api_token="t"
    )
    client = AtlassianClient(
        credentials,
        product="confluence",
        max_attempts=1,
        backoff_seconds=0,
        client=httpx.AsyncClient(transport=Transport(), base_url=credentials.base_url),
    )

    async def run() -> AgentError:
        try:
            await client.request("PUT", "/x", operation="set_edit_restriction")
        except AgentError as error:
            return error
        raise AssertionError("expected an AgentError")

    error = asyncio.run(run())

    fix = error.suggested_fix.lower()
    assert "free" in fix and "plan" in fix
    assert "edition" in fix  # points at the systemInfo probe that settles it
    assert error.status_code == 403


def test_an_unrelated_403_keeps_the_generic_permission_advice() -> None:
    """The operation-specific override must not swallow ordinary permission failures."""
    import httpx

    from app.adapters.http import AtlassianClient
    from app.config.secrets import AtlassianCredentials
    from app.domain.errors import AgentError

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "nope"})

    credentials = AtlassianCredentials(
        base_url="https://example.atlassian.net", email="svc@example.com", api_token="t"
    )
    client = AtlassianClient(
        credentials,
        product="jira",
        max_attempts=1,
        backoff_seconds=0,
        client=httpx.AsyncClient(transport=Transport(), base_url=credentials.base_url),
    )

    async def run() -> AgentError:
        try:
            await client.request("POST", "/x", operation="create_issue")
        except AgentError as error:
            return error
        raise AssertionError("expected an AgentError")

    error = asyncio.run(run())

    assert "lacks permission" in error.suggested_fix
    assert "free" not in error.suggested_fix.lower()
