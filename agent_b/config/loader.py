"""Load the `agent_b:` block from the shared config registry (Epic 7).

Agent B reads the *same* `config/registry.yaml` as Agent A but only its own top-level `agent_b:` key.
An absent block yields `None` — Agent B is simply not configured on this deployment, and Agent A is
unaffected (its registry loader ignores unknown top-level keys, `app/config/registry.py`).

A leaf module (AD-27): stdlib + pydantic + PyYAML only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agent_b.config.schema import AgentBConfig


class AgentBConfigError(ValueError):
    """The `agent_b:` config block is missing-but-required, or malformed."""


def load_agent_b_config(data: dict[str, Any] | None) -> AgentBConfig | None:
    """Build an `AgentBConfig` from an already-parsed registry mapping, or `None` if absent."""
    block = (data or {}).get("agent_b")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise AgentBConfigError("the 'agent_b:' block must be a mapping of config fields.")
    try:
        return AgentBConfig.model_validate(block)
    except Exception as exc:  # pydantic ValidationError
        raise AgentBConfigError(f"invalid 'agent_b:' config: {exc}") from exc


def load_agent_b_config_file(path: str | Path) -> AgentBConfig | None:
    """Read `registry.yaml` and return the `AgentBConfig`, or `None` if it has no `agent_b:` block."""
    file_path = Path(path)
    if not file_path.is_file():
        raise AgentBConfigError(
            f"config registry not found at {file_path}. Copy config/registry.example.yaml."
        )
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise AgentBConfigError(f"config registry at {file_path} is not valid YAML: {exc}") from exc
    return load_agent_b_config(data or {})
