"""The authenticated localhost admin endpoint (AD-22).

The AD-22 reconciler runs on a fixed interval. The chosen mechanism is **system `cron` on the Droplet
calling this endpoint over localhost** — no extra always-on Python process competing for RAM on the
1 GB box (AD-21). This module exposes that endpoint.

It is bound to localhost behind Caddy (Caddy proxies only the public webhook path), so it is not
reachable from the internet, and it additionally requires the admin token — defence in depth for the
same reason the webhook endpoint requires a signature.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException

from app.admin.reconciler import Reconciler
from app.composition import Composition
from app.config.secrets import resolve_secret


def build_admin_router(composition: Composition, *, reconciler_factory) -> APIRouter:
    """Build the admin router.

    Args:
        reconciler_factory: builds a `Reconciler` on demand. Injected so the endpoint does not itself
            construct the poller/alerter wiring (kept in the composition root, AD-1).
    """
    router = APIRouter()
    expected_token = _resolve_admin_token(composition)

    def _authorize(token: str | None) -> None:
        if not expected_token or not token or not hmac.compare_digest(token, expected_token):
            raise HTTPException(status_code=401, detail="admin token required")

    @router.post("/admin/reconcile")
    async def reconcile(x_admin_token: str | None = Header(default=None)) -> dict:
        """Run one AD-22 sweep. Called by cron; returns what it alerted/recovered."""
        _authorize(x_admin_token)
        reconciler: Reconciler = reconciler_factory()
        result = await reconciler.sweep()
        return {
            "checked": result.checked,
            "alerted": list(result.alerted),
            "recovered": list(result.recovered),
        }

    @router.get("/admin/health")
    async def health(x_admin_token: str | None = Header(default=None)) -> dict:
        _authorize(x_admin_token)
        return {"status": "ok"}

    return router


def _resolve_admin_token(composition: Composition) -> str | None:
    try:
        return resolve_secret(composition._registry.system.admin_token_ref, composition._env)
    except Exception:
        return None
