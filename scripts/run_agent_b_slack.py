#!/usr/bin/env python
"""Run the Agent B Slack Q&A bot (S-B7) — the live demo surface. Socket Mode, no public endpoint.

Wires the transport-agnostic `SlackQaHandler` (retrieve → grounded/citing/refusing answer → `qa_log`)
to a bolt Socket-Mode app, and blocks serving events. Reuses Agent B's config + own store (AD-32) and
the shared traced `LlmClient` (AD-27). Tokens come from `env:` refs (AD-4).

    .venv/bin/python scripts/run_agent_b_slack.py

Live-gated on B-9 (a Slack app + its bot/app tokens) and needs `slack-bolt` installed (the `agent_b`
extra) plus the fastembed model available. The offline behaviour is covered by tests/test_agent_b_slack.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    import os

    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def main() -> int:
    from agent_b.agents.answerer import AnswererAgent
    from agent_b.config import load_agent_b_config_file
    from agent_b.rag import FastEmbedEmbedder
    from agent_b.repository import AgentBRepository
    from agent_b.slack import build_socket_mode_app
    from app.composition import Composition
    from app.config.registry import ConfigRegistry
    from app.config.secrets import resolve_secret

    registry_path = ROOT / "config" / "registry.yaml"
    config = load_agent_b_config_file(registry_path)
    if config is None:
        print("No 'agent_b:' block in config/registry.yaml — Agent B is not configured here.")
        return 1

    registry = ConfigRegistry.from_yaml_file(registry_path)
    repo = AgentBRepository.open(config.database_path)
    answerer = AnswererAgent(Composition(registry).llm, model=config.answerer_model)
    embedder = FastEmbedEmbedder(config.embeddings.model)

    from agent_b.slack import SlackQaHandler

    handler = SlackQaHandler(repo=repo, embedder=embedder, answerer=answerer, config=config)
    _, socket = build_socket_mode_app(
        handler=handler,
        bot_token=resolve_secret(config.slack.bot_token_ref),
        app_token=resolve_secret(config.slack.app_token_ref),
    )
    print("Agent B Slack bot starting (Socket Mode). Ctrl-C to stop.")
    await socket.start_async()
    return 0


if __name__ == "__main__":
    import asyncio

    load_env()
    raise SystemExit(asyncio.run(main()))
