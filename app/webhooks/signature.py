"""Webhook authenticity check — AD-8 step 1, PRD §15.4.

The public endpoint triggers **real Jira and Confluence writes**, so validating it is a security Must,
not a nice-to-have. A request that fails here is dropped with no side effects and no state write.

Two mechanisms are accepted, because the demo's webhook registration path is not yet confirmed
against the real tenant (BLOCKERS B-3, PRD §13 Q3):

1. **HMAC-SHA256 over the raw body** compared against ``X-Hub-Signature: sha256=<hex>`` — what a Jira
   webhook configured with a secret sends.
2. **A shared-secret header** (``X-Webhook-Secret``) — what an Automation rule can send, since
   Automation cannot compute an HMAC.

Both comparisons use :func:`hmac.compare_digest`, so a wrong secret takes the same time as a right
one and cannot be recovered byte-by-byte by timing the endpoint.

The HMAC path is strictly stronger: it authenticates the *body*, so a captured request cannot be
replayed with modified content. Prefer it when the tenant's registration mechanism supports it.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping

HMAC_HEADER = "x-hub-signature"
SHARED_SECRET_HEADER = "x-webhook-secret"
_HMAC_PREFIX = "sha256="


class InvalidSignature(Exception):
    """The request could not be authenticated. Drop it — do not process, do not escalate."""


def _normalize(headers: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


def compute_hmac(secret: str, body: bytes) -> str:
    """The value a sender puts in ``X-Hub-Signature`` (without the ``sha256=`` prefix)."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(*, secret: str, body: bytes, headers: Mapping[str, str]) -> None:
    """Raise :class:`InvalidSignature` unless the request proves knowledge of the shared secret.

    Args:
        secret: resolved from ``system.webhook_secret_ref`` (AD-4 — never inline).
        body: the **raw** request bytes. Re-serializing parsed JSON would change whitespace and
            break the HMAC, so the caller must pass what arrived on the wire.
        headers: the request headers, matched case-insensitively.
    """
    if not secret:
        raise InvalidSignature(
            "no webhook secret is configured. Set the variable named by "
            "system.webhook_secret_ref — an unauthenticated endpoint that triggers Atlassian "
            "writes must never be served (PRD §15.4)."
        )

    normalized = _normalize(headers)

    provided_hmac = normalized.get(HMAC_HEADER)
    if provided_hmac:
        expected = compute_hmac(secret, body)
        candidate = (
            provided_hmac[len(_HMAC_PREFIX) :]
            if provided_hmac.startswith(_HMAC_PREFIX)
            else provided_hmac
        )
        if hmac.compare_digest(candidate.lower(), expected.lower()):
            return
        raise InvalidSignature("X-Hub-Signature did not match the HMAC of the request body")

    provided_secret = normalized.get(SHARED_SECRET_HEADER)
    if provided_secret:
        if hmac.compare_digest(provided_secret, secret):
            return
        raise InvalidSignature("X-Webhook-Secret did not match the configured shared secret")

    raise InvalidSignature(
        f"request carried neither {HMAC_HEADER} nor {SHARED_SECRET_HEADER}; "
        "it cannot be authenticated and is dropped"
    )
