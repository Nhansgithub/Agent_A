"""S-B0 — Agent B config schema + loader (AD-4 mirror, AD-27).

Offline: no network, no credentials — config loading resolves nothing, it only validates shape.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_b.config import AgentBConfig, AgentBConfigError, load_agent_b_config
from app.config.registry import ConfigRegistry


def _valid_block(**overrides: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "space_key": "PM",
        "confluence_credentials_ref": "env:ALPHA_CONF",
        "include_folder_ids": ["65871", "1441796"],
        "exclude_folder_ids": ["1474562"],
    }
    block.update(overrides)
    return block


def test_loads_a_valid_block() -> None:
    cfg = load_agent_b_config({"agent_b": _valid_block()})
    assert isinstance(cfg, AgentBConfig)
    assert cfg.space_key == "PM"
    assert cfg.include_folder_ids == ("65871", "1441796")
    assert cfg.exclude_folder_ids == ("1474562",)
    # Defaults are sane and do not require the block to spell them out.
    assert cfg.embeddings.runtime == "fastembed"
    assert cfg.rag.min_score == 0.35
    assert cfg.schedule_cron == "0 3 * * *"


def test_absent_block_is_none() -> None:
    assert load_agent_b_config({"system": {}, "tenants": {}}) is None
    assert load_agent_b_config(None) is None


def test_agent_b_key_does_not_disturb_agent_a_registry() -> None:
    # Agent A's loader ignores unknown top-level keys, so an `agent_b:` block never breaks it.
    from tests.conftest import registry_mapping

    data = registry_mapping()
    data["agent_b"] = _valid_block()
    registry = ConfigRegistry.from_mapping(data)
    assert registry.tenants  # Agent A still loads


def test_inline_credential_rejected() -> None:
    with pytest.raises(AgentBConfigError):
        load_agent_b_config({"agent_b": _valid_block(confluence_credentials_ref="a-real-token")})


def test_slack_ref_must_be_env() -> None:
    with pytest.raises(AgentBConfigError):
        load_agent_b_config({"agent_b": _valid_block(slack={"bot_token_ref": "xoxb-literal"})})


def test_include_exclude_overlap_rejected() -> None:
    with pytest.raises(AgentBConfigError):
        load_agent_b_config(
            {"agent_b": _valid_block(include_folder_ids=["A", "B"], exclude_folder_ids=["B"])}
        )


def test_empty_include_rejected() -> None:
    with pytest.raises(AgentBConfigError):
        load_agent_b_config({"agent_b": _valid_block(include_folder_ids=[])})


def test_unknown_field_rejected() -> None:
    # extra="forbid" — a typo in the config surfaces loudly instead of being silently ignored.
    with pytest.raises(AgentBConfigError):
        load_agent_b_config({"agent_b": _valid_block(scheduel_cron="0 0 * * *")})
