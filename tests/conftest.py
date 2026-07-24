"""Shared fixtures.

Nothing here touches the network, a real Atlassian instance, or a real credential. The whole unit
suite must run offline with an empty environment.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config.registry import ConfigRegistry


def tenant_entry(**overrides: Any) -> dict[str, Any]:
    """A valid tenant config entry. Override any field to build the case under test."""
    entry: dict[str, Any] = {
        "confluence_source_folder_id": "folder-source-1",
        "confluence_draft_folder_id": "folder-draft-1",
        "confluence_published_folder_id": "folder-published-1",
        "jira_main_project_key": "TESTMAIN",
        "jira_review_project_key": "TESTREV",
        "pm_account_id": "acct-pm-1",
        "head_of_product_account_id": "acct-hop-1",
        "admin_account_id": "acct-admin-1",
        "md_export_dir": "/tmp/userdocs/test",
        "jira_credentials_ref": "env:TEST_JIRA",
        "confluence_credentials_ref": "env:TEST_CONF",
    }
    entry.update(overrides)
    return entry


def registry_mapping(**tenants: dict[str, Any]) -> dict[str, Any]:
    """A full registry mapping. Defaults to a single tenant named `tenant_one`."""
    return {"system": {}, "tenants": tenants or {"tenant_one": tenant_entry()}}


@pytest.fixture
def registry() -> ConfigRegistry:
    """A single-tenant registry — the common case for most tests."""
    return ConfigRegistry.from_mapping(registry_mapping())


@pytest.fixture
def two_tenant_registry() -> ConfigRegistry:
    """Two fully-isolated tenants — used to prove AD-3 routing never crosses tenants."""
    return ConfigRegistry.from_mapping(
        registry_mapping(
            tenant_one=tenant_entry(),
            tenant_two=tenant_entry(
                confluence_source_folder_id="folder-source-2",
                confluence_draft_folder_id="folder-draft-2",
                confluence_published_folder_id="folder-published-2",
                jira_main_project_key="OTHERMAIN",
                jira_review_project_key="OTHERREV",
                pm_account_id="acct-pm-2",
                head_of_product_account_id="acct-hop-2",
                admin_account_id="acct-admin-2",
                md_export_dir="/tmp/userdocs/two",
                jira_credentials_ref="env:OTHER_JIRA",
                confluence_credentials_ref="env:OTHER_CONF",
            ),
        )
    )


@pytest.fixture
def clean_env() -> dict[str, str]:
    """An explicit, empty environment mapping. Secrets are injected per-test, never from os.environ."""
    return {}
