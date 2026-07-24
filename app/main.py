"""FastAPI application + the single public webhook entrypoint (AD-8, AD-21).

The composition root (`app.composition.Composition`) builds everything; this module wires it to
FastAPI: the public `/webhooks/atlassian` endpoint and the localhost `/admin/*` endpoints. In
production a single Uvicorn worker serves this behind Caddy, which terminates TLS and proxies only the
public webhook path (AD-21).

`create_app()` is the factory. The default `app` builds from `config/registry.yaml`; if that file is
absent (e.g. a bare checkout before setup), the app still starts and serves `/health`, so a container
smoke test does not require credentials — the webhook routes simply aren't mounted until config exists.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "registry.yaml"


def create_app(registry_path: Path | None = None) -> FastAPI:
    path = registry_path or _REGISTRY_PATH
    composition = None
    if path.is_file():
        from app.composition import Composition
        from app.config.registry import ConfigRegistry

        composition = Composition(ConfigRegistry.from_yaml_file(path))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if composition is not None:
            composition.close()

    app = FastAPI(title="PRD-to-UserDoc Automation Agent Flow", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe (Story 6.4 deploy smoke test). Works with or without config."""
        return {"status": "ok"}

    if composition is None:
        logger.warning(
            "no config registry at %s — webhook routes not mounted. Copy "
            "config/registry.example.yaml and fill it in (SETUP-GUIDE.md).",
            path,
        )
        return app

    from app.admin.endpoint import build_admin_router
    from app.webhooks.router import build_webhook_router

    app.state.composition = composition
    app.include_router(build_webhook_router(composition))
    app.include_router(
        build_admin_router(composition, reconciler_factory=lambda: _reconciler(composition))
    )
    return app


def _reconciler(composition):
    """Build the AD-22 reconciler with the production poller/alerter wiring."""
    from app.admin.reconciler import Reconciler
    from app.admin.wiring import JiraGatePoller, LogAndTicketAlerter

    system = composition._registry.system
    return Reconciler(
        repository=composition.repository,
        registry=composition._registry,
        poller=JiraGatePoller(composition),
        gate_input=composition.orchestrator,
        alerter=LogAndTicketAlerter(composition),
        threshold=timedelta(minutes=system.reconciler.liveness_threshold_minutes),
    )


app = create_app()
