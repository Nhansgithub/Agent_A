"""Route-before-work tenant resolution (AD-3).

Every inbound event maps to **exactly one** tenant before any work happens, and the resolved
tenant-config object is threaded through the whole flow — no step ever accesses a resource for a
tenant it was not handed. An event that resolves to no tenant is dropped with no side effects.

Resolution is a pure in-memory lookup against the config registry indexes. It opens no socket and
writes no state, which is what makes it safe to run before the dedupe check (see the note in
`app/webhooks/ingress.py` on the AD-8 / AD-9 ordering).

Confluence page events resolve by containing folder id. Two fallbacks exist because the
`page-created` payload is not guaranteed to carry the container (PRD §13 Q3/Q4, BLOCKERS B-3):
space key, then — only when a single tenant is configured — that tenant. Both fallbacks are
*routing* conveniences, not admission decisions: FR-01 detection still independently checks the page
is in the watched source folder (Story 2.1), so a fallback can never smuggle a page into the flow.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.registry import ConfigRegistry
from app.config.schema import TenantConfig
from app.domain.events import (
    ConfluencePageEvent,
    JiraCommentEvent,
    JiraIssueUpdatedEvent,
    WebhookEvent,
)


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """The outcome of resolving one event to a tenant."""

    tenant: TenantConfig | None
    reason: str
    """How the tenant was found, or why it was not. Surfaced in logs so an unrouted event in
    production is diagnosable without reproducing it."""

    @property
    def resolved(self) -> bool:
        return self.tenant is not None


class TenantRouter:
    """Resolves an event to exactly one tenant (AD-3)."""

    __slots__ = ("_registry",)

    def __init__(self, registry: ConfigRegistry) -> None:
        self._registry = registry

    def resolve(self, event: WebhookEvent) -> RoutingDecision:
        if isinstance(event, ConfluencePageEvent):
            return self._resolve_confluence(event)
        if isinstance(event, JiraCommentEvent | JiraIssueUpdatedEvent):
            return self._resolve_jira(event)
        return RoutingDecision(None, f"unroutable event type {type(event).__name__}")

    # -- Confluence --------------------------------------------------------------------------

    def _resolve_confluence(self, event: ConfluencePageEvent) -> RoutingDecision:
        if event.container_id:
            tenant = self._registry.by_confluence_folder(event.container_id)
            if tenant is not None:
                return RoutingDecision(tenant, f"container folder {event.container_id}")
            return RoutingDecision(
                None, f"container folder {event.container_id} belongs to no configured tenant"
            )

        if event.space_key:
            matches = [
                tenant
                for tenant in self._registry.tenants.values()
                if tenant.confluence_space_key and tenant.confluence_space_key == event.space_key
            ]
            if len(matches) == 1:
                return RoutingDecision(matches[0], f"space key {event.space_key}")
            if len(matches) > 1:
                return RoutingDecision(
                    None, f"space key {event.space_key} is claimed by more than one tenant"
                )

        tenants = list(self._registry.tenants.values())
        if len(tenants) == 1:
            # Unambiguous by construction. Detection still applies the watched-folder check.
            return RoutingDecision(tenants[0], "single configured tenant (no container in payload)")

        return RoutingDecision(
            None,
            "page event carried no container folder id and no unambiguous space key; "
            "set confluence_space_key on the tenant, or confirm the webhook payload includes the "
            "page container (PRD §13 Q3)",
        )

    # -- Jira ---------------------------------------------------------------------------------

    def _resolve_jira(self, event: JiraCommentEvent | JiraIssueUpdatedEvent) -> RoutingDecision:
        tenant = self._registry.by_jira_project_key(event.project_key)
        if tenant is not None:
            return RoutingDecision(tenant, f"Jira project {event.project_key}")
        return RoutingDecision(
            None, f"Jira project {event.project_key} belongs to no configured tenant"
        )
