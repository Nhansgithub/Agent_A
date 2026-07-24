"""Story 1.2 — per-tenant config registry, tenant-config schema, env-ref secrets (AD-4)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config.registry import ConfigError, ConfigRegistry
from app.config.secrets import (
    SecretResolutionError,
    resolve_atlassian_credentials,
    resolve_secret,
)
from tests.conftest import registry_mapping, tenant_entry

# ---------------------------------------------------------------------------------------------
# AC 1: the loader produces a validated tenant-config object exposing every PRD §11 field.
# ---------------------------------------------------------------------------------------------

PRD_SECTION_11_FIELDS = [
    "confluence_source_folder_id",
    "confluence_draft_folder_id",
    "confluence_published_folder_id",
    "jira_main_project_key",
    "jira_review_project_key",
    "pm_account_id",
    "head_of_product_account_id",
    "admin_account_id",
    "md_export_dir",
    "jira_credentials_ref",
    "confluence_credentials_ref",
]


@pytest.mark.parametrize("field", PRD_SECTION_11_FIELDS)
def test_tenant_config_exposes_every_prd_section_11_field(registry, field: str) -> None:
    tenant = registry.by_project_id("tenant_one")
    assert tenant is not None
    assert getattr(tenant, field), f"tenant config is missing PRD §11 field {field!r}"


def test_mapping_key_becomes_the_project_id(registry) -> None:
    """`project_id` is the tenant component of the AD-9 dedupe key, so it must be stable."""
    assert registry.by_project_id("tenant_one").project_id == "tenant_one"


def test_tenant_config_is_immutable(registry) -> None:
    """A frozen config cannot be mutated mid-flow, so every step sees the same tenant (AD-3)."""
    tenant = registry.by_project_id("tenant_one")
    with pytest.raises(ValidationError):
        tenant.pm_account_id = "someone-else"  # type: ignore[misc]


def test_unknown_field_is_rejected_rather_than_silently_ignored() -> None:
    """A typo'd key must fail loudly — a silently-ignored `pm_acount_id` would route to nobody."""
    with pytest.raises(ConfigError, match="pm_acount_id"):
        ConfigRegistry.from_mapping(registry_mapping(tenant_one=tenant_entry(pm_acount_id="typo")))


def test_hardening_fields_have_safe_defaults(registry) -> None:
    """Full-hardening features (AD-12, AD-13, AD-20) default to the conservative behaviour."""
    tenant = registry.by_project_id("tenant_one")
    assert tenant.identity_overrides == {}, "same-org accountId sharing is the default (AD-12)"
    assert tenant.preferred_transition_path == [], "direct-only; escalate rather than guess (AD-13)"
    assert tenant.trace_content is False, "metadata-only tracing by default (AD-20)"


# ---------------------------------------------------------------------------------------------
# AC 1 (cont.): credentials resolved from environment references, never read inline.
# ---------------------------------------------------------------------------------------------


def test_loading_the_registry_requires_no_credentials(registry) -> None:
    """The registry holds references only, so config loads offline with an empty environment."""
    assert registry.by_project_id("tenant_one").jira_credentials_ref == "env:TEST_JIRA"


def test_inline_secret_is_rejected_at_load_time() -> None:
    with pytest.raises(ConfigError, match="environment reference"):
        ConfigRegistry.from_mapping(
            registry_mapping(
                tenant_one=tenant_entry(jira_credentials_ref="ATATT-a-real-looking-token")
            )
        )


def test_env_reference_resolves_the_credential_triple() -> None:
    creds = resolve_atlassian_credentials(
        "env:TEST_JIRA",
        env={
            "TEST_JIRA_BASE_URL": "https://example.atlassian.net/",
            "TEST_JIRA_EMAIL": "svc@example.com",
            "TEST_JIRA_API_TOKEN": "token-value",
        },
    )
    assert creds.base_url == "https://example.atlassian.net"  # trailing slash normalized
    assert creds.email == "svc@example.com"
    assert creds.api_token == "token-value"


def test_missing_env_var_names_the_variable_to_set() -> None:
    """A missing credential must fail with an actionable message, not a downstream 401."""
    with pytest.raises(SecretResolutionError, match="TEST_JIRA_API_TOKEN"):
        resolve_atlassian_credentials(
            "env:TEST_JIRA",
            env={"TEST_JIRA_BASE_URL": "https://x.atlassian.net", "TEST_JIRA_EMAIL": "a@b.c"},
        )


def test_credentials_repr_does_not_leak_the_token() -> None:
    """Tokens must not reach logs or tracebacks."""
    creds = resolve_atlassian_credentials(
        "env:TEST_JIRA",
        env={
            "TEST_JIRA_BASE_URL": "https://x.atlassian.net",
            "TEST_JIRA_EMAIL": "a@b.c",
            "TEST_JIRA_API_TOKEN": "super-secret-value",
        },
    )
    assert "super-secret-value" not in repr(creds)


def test_single_value_secret_reference_resolves() -> None:
    assert (
        resolve_secret("env:WEBHOOK_SHARED_SECRET", env={"WEBHOOK_SHARED_SECRET": "s3cr3t"})
        == "s3cr3t"
    )


# ---------------------------------------------------------------------------------------------
# AD-10 primary self-ingestion guard — enforced at config load, not discovered in production.
# ---------------------------------------------------------------------------------------------


def test_published_folder_inside_source_folder_is_rejected() -> None:
    """Publishing into the watched folder would loop the agent on its own output forever."""
    with pytest.raises(ConfigError, match="ADJACENT"):
        ConfigRegistry.from_mapping(
            registry_mapping(
                tenant_one=tenant_entry(confluence_published_folder_id="folder-source-1")
            )
        )


def test_draft_folder_equal_to_source_folder_is_rejected() -> None:
    with pytest.raises(ConfigError, match="confluence_draft_folder_id"):
        ConfigRegistry.from_mapping(
            registry_mapping(tenant_one=tenant_entry(confluence_draft_folder_id="folder-source-1"))
        )


def test_main_and_review_projects_must_differ() -> None:
    with pytest.raises(ConfigError, match="must differ"):
        ConfigRegistry.from_mapping(
            registry_mapping(tenant_one=tenant_entry(jira_review_project_key="TESTMAIN"))
        )


# ---------------------------------------------------------------------------------------------
# AD-3 route-before-work: the indexes the webhook layer resolves tenants with.
# ---------------------------------------------------------------------------------------------


def test_source_folder_resolves_to_its_tenant(two_tenant_registry) -> None:
    assert two_tenant_registry.by_confluence_folder("folder-source-2").project_id == "tenant_two"


def test_jira_project_key_resolves_to_its_tenant(two_tenant_registry) -> None:
    assert two_tenant_registry.by_jira_project_key("OTHERREV").project_id == "tenant_two"


def test_unknown_folder_and_project_resolve_to_nothing(two_tenant_registry) -> None:
    """An event that resolves to no tenant is dropped without side effects (AD-3)."""
    assert two_tenant_registry.by_confluence_folder("folder-nobody-owns") is None
    assert two_tenant_registry.by_jira_project_key("NOPE") is None
    assert two_tenant_registry.by_confluence_folder(None) is None


def test_watches_source_folder_distinguishes_watched_from_merely_owned(two_tenant_registry) -> None:
    """FR-01 watches only the source folder; draft and published folders route but are not watched."""
    assert two_tenant_registry.watches_source_folder("folder-source-1").project_id == "tenant_one"
    assert two_tenant_registry.watches_source_folder("folder-draft-1") is None
    assert two_tenant_registry.watches_source_folder("folder-published-1") is None


def test_duplicate_folder_id_across_tenants_is_rejected() -> None:
    """Ambiguous routing risks one tenant's flow touching another's Atlassian resources."""
    with pytest.raises(ConfigError, match="claimed by both tenant"):
        ConfigRegistry.from_mapping(
            registry_mapping(
                tenant_one=tenant_entry(),
                tenant_two=tenant_entry(
                    confluence_source_folder_id="folder-source-2",
                    # collides with tenant_one's SOURCE folder -> ambiguous tenant resolution
                    confluence_draft_folder_id="folder-source-1",
                    confluence_published_folder_id="folder-published-2",
                    jira_main_project_key="OTHERMAIN",
                    jira_review_project_key="OTHERREV",
                ),
            )
        )


def test_duplicate_jira_project_key_across_tenants_is_rejected() -> None:
    with pytest.raises(ConfigError, match="claimed by both tenant"):
        ConfigRegistry.from_mapping(
            registry_mapping(
                tenant_one=tenant_entry(),
                tenant_two=tenant_entry(
                    confluence_source_folder_id="folder-source-2",
                    confluence_draft_folder_id="folder-draft-2",
                    confluence_published_folder_id="folder-published-2",
                    jira_main_project_key="TESTMAIN",  # collides with tenant_one
                    jira_review_project_key="OTHERREV",
                ),
            )
        )


# ---------------------------------------------------------------------------------------------
# Loader plumbing.
# ---------------------------------------------------------------------------------------------


def test_empty_registry_is_rejected() -> None:
    with pytest.raises(ConfigError, match="non-empty 'tenants:' mapping"):
        ConfigRegistry.from_mapping({"system": {}, "tenants": {}})


def test_missing_registry_file_message_points_at_the_example(tmp_path) -> None:
    with pytest.raises(ConfigError, match="registry.example.yaml"):
        ConfigRegistry.from_yaml_file(tmp_path / "nope.yaml")


def test_shipped_example_registry_is_valid() -> None:
    """The example must stay loadable — it is what an operator copies to onboard a project."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "config" / "registry.example.yaml"
    loaded = ConfigRegistry.from_yaml_file(example)
    assert loaded.by_project_id("project_alpha") is not None
    assert loaded.system.models.classifier, (
        "AD-17 requires the classifier model id pinned in config"
    )
