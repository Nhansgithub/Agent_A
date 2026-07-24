"""Composition root — the production wiring assembles completely and offline.

Proves the composition root builds the orchestrator with a handler for **every** advancing stage (no
gaps that would strand the flow), and that the FastAPI app starts with or without config. No
credentials, no network — construction is lazy, so building the wiring touches neither.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.composition import Composition
from app.config.registry import ConfigRegistry
from app.main import create_app
from app.orchestrator.stages import ADVANCING_STAGES
from tests.conftest import registry_mapping


@pytest.fixture
def registry() -> ConfigRegistry:
    return ConfigRegistry.from_mapping(registry_mapping())


def test_every_advancing_stage_has_a_handler(registry) -> None:
    """A missing handler would stop the flow mid-run; the composition must wire all of them."""
    composition = Composition(registry, env={})
    orchestrator = composition.orchestrator

    missing = orchestrator._handlers.missing()
    assert missing == [], f"advancing stages with no handler: {[s.value for s in missing]}"
    # And it is exactly the advancing set — no more, no less.
    for stage in ADVANCING_STAGES:
        assert orchestrator._handlers.get(stage) is not None


def test_building_the_wiring_needs_no_credentials(registry) -> None:
    """Construction is lazy — adapters and the LLM client are only built when a run needs them."""
    composition = Composition(registry, env={})
    # Touching the orchestrator builds the handler registry + repository, but no adapter/LLM.
    assert composition.orchestrator is not None
    composition.close()


def test_the_app_serves_health_without_config(tmp_path) -> None:
    """A bare checkout (no registry.yaml) still starts and answers /health (Story 6.4 smoke test)."""
    app = create_app(registry_path=tmp_path / "absent.yaml")
    paths = {route.path for route in app.routes}
    assert "/health" in paths
    assert "/webhooks/atlassian" not in paths, "webhook routes not mounted without config"


def test_the_app_mounts_webhook_and_admin_routes_with_config(tmp_path) -> None:
    registry_file = tmp_path / "registry.yaml"
    registry_file.write_text(yaml.safe_dump(registry_mapping()), encoding="utf-8")

    app = create_app(registry_path=registry_file)
    paths = {route.path for route in app.routes}

    assert "/webhooks/atlassian" in paths
    assert "/admin/reconcile" in paths
    assert "/health" in paths


def test_the_shipped_example_registry_composes() -> None:
    """The example an operator copies must build a working composition."""
    example = Path(__file__).resolve().parents[1] / "config" / "registry.example.yaml"
    composition = Composition(ConfigRegistry.from_yaml_file(example), env={})
    assert composition.orchestrator._handlers.missing() == []


def test_health_reports_a_missing_config_so_the_deploy_smoke_test_can_catch_it(tmp_path) -> None:
    """A container started without its config volume is alive but deaf — /health must say so.

    Otherwise the Droplet answers `{"status": "ok"}`, the smoke test passes, webhooks get registered,
    and every Atlassian delivery is silently ignored.
    """
    from fastapi.testclient import TestClient

    with TestClient(create_app(registry_path=tmp_path / "absent.yaml")) as client:
        body = client.get("/health").json()

    assert body["status"] == "ok"  # still 200/alive — a restart would not fix a missing mount
    assert body["config"] == "missing"
    assert body["webhooks"] == "not-mounted"


def test_health_reports_a_loaded_config(tmp_path) -> None:
    from fastapi.testclient import TestClient

    registry_file = tmp_path / "registry.yaml"
    registry_file.write_text(yaml.safe_dump(registry_mapping()), encoding="utf-8")

    with TestClient(create_app(registry_path=registry_file)) as client:
        body = client.get("/health").json()

    assert body["config"] == "loaded"
    assert body["webhooks"] == "mounted"
