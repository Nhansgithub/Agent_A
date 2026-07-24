"""The per-tenant config registry and its lookup indexes (AD-3, AD-4, PRD §11).

Two jobs:

1. **Load and validate** the registry file into frozen ``TenantConfig`` objects. Cross-tenant
   invariants that no single tenant entry can check (id uniqueness) are enforced here.
2. **Index for route-before-work** (AD-3). Every inbound event must resolve to exactly one tenant
   *before any work happens*, so the registry exposes the two lookups the webhook layer needs:
   Confluence folder id → tenant, and Jira project key → tenant.

Loading requires **no credentials and no network** — the registry holds credential *references*, and
`app.config.secrets` resolves them lazily at the point of use.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.config.schema import SystemConfig, TenantConfig


class ConfigError(ValueError):
    """The config registry is malformed, or violates a cross-tenant invariant."""


class ConfigRegistry:
    """An immutable, indexed view over every configured tenant."""

    __slots__ = ("_by_folder", "_by_project_key", "_system", "_tenants")

    def __init__(self, tenants: dict[str, TenantConfig], system: SystemConfig) -> None:
        if not tenants:
            raise ConfigError("config registry contains no tenants; at least one is required.")
        self._tenants = dict(tenants)
        self._system = system
        self._by_folder = self._index_folders(self._tenants)
        self._by_project_key = self._index_project_keys(self._tenants)

    # -- construction ----------------------------------------------------------------------

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ConfigRegistry:
        if not isinstance(data, dict):
            raise ConfigError("config registry must be a mapping at the top level.")

        raw_tenants = data.get("tenants")
        if not isinstance(raw_tenants, dict) or not raw_tenants:
            raise ConfigError(
                "config registry must contain a non-empty 'tenants:' mapping, keyed by project_id."
            )

        try:
            system = SystemConfig.model_validate(data.get("system") or {})
        except Exception as exc:  # pydantic ValidationError
            raise ConfigError(f"invalid 'system:' block: {exc}") from exc

        tenants: dict[str, TenantConfig] = {}
        for project_id, entry in raw_tenants.items():
            if not isinstance(entry, dict):
                raise ConfigError(f"tenant {project_id!r} must be a mapping of config fields.")
            try:
                tenants[project_id] = TenantConfig.model_validate(
                    {**entry, "project_id": project_id}
                )
            except Exception as exc:  # pydantic ValidationError
                raise ConfigError(f"invalid config for tenant {project_id!r}: {exc}") from exc

        return cls(tenants, system)

    @classmethod
    def from_yaml_file(cls, path: str | Path) -> ConfigRegistry:
        file_path = Path(path)
        if not file_path.is_file():
            raise ConfigError(
                f"config registry not found at {file_path}. Copy config/registry.example.yaml and "
                "fill in this deployment's project ids, folder ids, and account ids."
            )
        try:
            data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(f"config registry at {file_path} is not valid YAML: {exc}") from exc
        return cls.from_mapping(data or {})

    # -- cross-tenant invariants ------------------------------------------------------------

    @staticmethod
    def _index_folders(tenants: dict[str, TenantConfig]) -> dict[str, TenantConfig]:
        """Index all three folder ids so AD-3 routing succeeds for any page event we subscribe to.

        Routing resolves *which tenant*; detection (FR-01, Story 2.1) separately decides whether the
        page is in the watched *source* folder. Keeping those two concerns apart means a page-updated
        on a draft page still routes cleanly and is then declined by the detection guard, rather than
        being dropped as "unknown tenant".
        """
        index: dict[str, TenantConfig] = {}
        for tenant in tenants.values():
            for role, folder_id in (
                ("confluence_source_folder_id", tenant.confluence_source_folder_id),
                ("confluence_draft_folder_id", tenant.confluence_draft_folder_id),
                ("confluence_published_folder_id", tenant.confluence_published_folder_id),
            ):
                existing = index.get(folder_id)
                if existing is not None and existing.project_id != tenant.project_id:
                    raise ConfigError(
                        f"Confluence folder id {folder_id!r} ({role}) is claimed by both tenant "
                        f"{existing.project_id!r} and {tenant.project_id!r}. Tenant resolution would "
                        "be ambiguous, which risks one tenant's flow touching another's resources "
                        "(AD-3)."
                    )
                index[folder_id] = tenant
        return index

    @staticmethod
    def _index_project_keys(tenants: dict[str, TenantConfig]) -> dict[str, TenantConfig]:
        index: dict[str, TenantConfig] = {}
        for tenant in tenants.values():
            for role, key in (
                ("jira_main_project_key", tenant.jira_main_project_key),
                ("jira_review_project_key", tenant.jira_review_project_key),
            ):
                existing = index.get(key)
                if existing is not None and existing.project_id != tenant.project_id:
                    raise ConfigError(
                        f"Jira project key {key!r} ({role}) is claimed by both tenant "
                        f"{existing.project_id!r} and {tenant.project_id!r}. Tenant resolution would "
                        "be ambiguous (AD-3)."
                    )
                index[key] = tenant
        return index

    # -- accessors -------------------------------------------------------------------------

    @property
    def system(self) -> SystemConfig:
        return self._system

    @property
    def tenants(self) -> dict[str, TenantConfig]:
        return dict(self._tenants)

    def by_project_id(self, project_id: str) -> TenantConfig | None:
        return self._tenants.get(project_id)

    def by_confluence_folder(self, folder_id: str | None) -> TenantConfig | None:
        """AD-3 — resolve a Confluence page event to its tenant by containing folder id."""
        return self._by_folder.get(folder_id) if folder_id else None

    def by_jira_project_key(self, project_key: str | None) -> TenantConfig | None:
        """AD-3 — resolve a Jira comment/issue event to its tenant by project key."""
        return self._by_project_key.get(project_key) if project_key else None

    def watches_source_folder(self, folder_id: str | None) -> TenantConfig | None:
        """FR-01 — resolve *only* if the folder is a tenant's WATCHED source folder.

        Distinct from :meth:`by_confluence_folder`, which also matches draft and published folders.
        """
        tenant = self.by_confluence_folder(folder_id)
        if tenant is not None and tenant.confluence_source_folder_id == folder_id:
            return tenant
        return None
