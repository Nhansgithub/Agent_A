"""The ordered webhook ingress pipeline (AD-8).

    authenticate  →  parse  →  resolve tenant  →  dedupe-check  →  admit

A request that fails any step is dropped **with no side effects and no state write**.

---

**A note on the AD-8 / AD-9 ordering.** AD-8 states the order as "validate → dedupe → route". Taken
literally that is unbuildable, because AD-9's dedupe key *begins with* the tenant id
(``<tenant_id>:<event_type>:<entity_id>:<version_marker>``) — the key cannot be computed before the
tenant is known.

The resolution is that tenant resolution is a **pure in-memory config lookup**: it opens no socket,
writes no state, and calls no agent. It is not "work" in the sense AD-8 is protecting against. Both
decisions' actual guarantees are preserved exactly:

* nothing happens at all before the signature is validated (AD-8's real point);
* no *work* begins until both a tenant is resolved and the dedupe check has passed;
* the dedupe key is the AD-9 composite, including the tenant component.

This is recorded as D-07 in implementation-state/DECISION-LOG.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.config.registry import ConfigRegistry
from app.config.schema import TenantConfig
from app.config.secrets import SecretResolutionError, resolve_secret
from app.domain.dedupe import DedupeKey, dedupe_key_for
from app.domain.events import UnsupportedEvent, WebhookEvent
from app.router import TenantRouter
from app.webhooks.events import parse_event
from app.webhooks.signature import InvalidSignature, verify_signature


class IngressOutcome(StrEnum):
    """Why a request was accepted or dropped. Every drop is a normal, expected occurrence."""

    ACCEPTED = "accepted"
    REJECTED_SIGNATURE = "rejected_signature"
    """Authentication failed. The only outcome that should surface as a non-2xx response."""

    DROPPED_UNSUPPORTED = "dropped_unsupported"
    """Atlassian sends more event types than we subscribe to."""

    DROPPED_UNROUTED = "dropped_unrouted"
    """No configured tenant owns this event (AD-3)."""

    DROPPED_DUPLICATE = "dropped_duplicate"
    """Already admitted. Jira and Confluence redeliver routinely (NFR-04)."""


@dataclass(frozen=True, slots=True)
class IngressResult:
    """The pipeline's verdict, plus everything the orchestrator needs to start work."""

    outcome: IngressOutcome
    detail: str
    event: WebhookEvent | None = None
    tenant: TenantConfig | None = None
    dedupe_key: DedupeKey | None = None

    @property
    def accepted(self) -> bool:
        return self.outcome is IngressOutcome.ACCEPTED


class WebhookIngress:
    """Authenticates, parses, routes, and dedupe-checks inbound Atlassian webhooks."""

    __slots__ = ("_env", "_registry", "_repository", "_router")

    def __init__(
        self,
        registry: ConfigRegistry,
        repository: Any,
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        """
        Args:
            registry: the config registry (AD-4).
            repository: the `Repository` facade. Typed loosely to keep the webhook layer from
                importing the repository package at module scope — the layering rule is enforced
                by import-linter, and this keeps the seam honest.
            env: environment mapping for secret resolution. Injected in tests; defaults to the
                real process environment.
        """
        self._registry = registry
        self._repository = repository
        self._router = TenantRouter(registry)
        self._env = env

    def handle(
        self, *, body: bytes, headers: dict[str, str], payload: dict[str, Any]
    ) -> IngressResult:
        """Run the pipeline. Never raises for an ordinary drop — the outcome carries the reason.

        Args:
            body: the **raw** request bytes, needed for the HMAC check. Re-serializing `payload`
                would change whitespace and invalidate the signature.
            headers: request headers (matched case-insensitively).
            payload: the parsed JSON body.
        """
        # 1. Authenticate. Nothing whatsoever happens before this succeeds (AD-8, PRD §15.4).
        try:
            secret = resolve_secret(self._registry.system.webhook_secret_ref, self._env)
            verify_signature(secret=secret, body=body, headers=headers)
        except (InvalidSignature, SecretResolutionError) as exc:
            return IngressResult(IngressOutcome.REJECTED_SIGNATURE, str(exc))

        # 2. Parse into one of the four subscribed event shapes.
        try:
            event = parse_event(payload)
        except UnsupportedEvent as exc:
            return IngressResult(IngressOutcome.DROPPED_UNSUPPORTED, str(exc))

        # 3. Resolve exactly one tenant (AD-3). Pure config lookup — no socket, no state write.
        routing = self._router.resolve(event)
        if routing.tenant is None:
            return IngressResult(IngressOutcome.DROPPED_UNROUTED, routing.reason, event=event)

        # 4. Dedupe-check (AD-9). Advisory: the authoritative guard is the UNIQUE constraint at
        #    admission, since another delivery may arrive between this check and that write.
        key = dedupe_key_for(routing.tenant.project_id, event)
        if self._repository.events.is_processed(key):
            return IngressResult(
                IngressOutcome.DROPPED_DUPLICATE,
                f"event {key.value} was already admitted",
                event=event,
                tenant=routing.tenant,
                dedupe_key=key,
            )

        return IngressResult(
            IngressOutcome.ACCEPTED,
            routing.reason,
            event=event,
            tenant=routing.tenant,
            dedupe_key=key,
        )
