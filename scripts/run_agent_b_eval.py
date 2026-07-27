#!/usr/bin/env python
"""Run the Agent B Q&A eval gate (S-B9) — source recall + refusal correctness over a golden set.

Mirrors scripts/run_classifier_eval.py: pure harness (agent_b.eval) over a real, injected
`answer_question`, so a green result is attributable to a pinned answerer model. Indexes the configured
vault, runs every golden case, prints the report, and exits non-zero if the bar is not met.

    .venv/bin/python scripts/run_agent_b_eval.py --golden fixtures/agent_b/golden.json

API-gated (needs the Anthropic key) and needs the fastembed model + `agent_b` extra installed. The
harness/scoring logic itself is covered offline by tests/test_agent_b_eval.py.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def main(golden_path: str) -> int:
    from agent_b.agents.answerer import AnswererAgent
    from agent_b.config import load_agent_b_config_file
    from agent_b.eval import evaluate, load_golden
    from agent_b.qa import answer_question
    from agent_b.rag import FastEmbedEmbedder, index_vault
    from agent_b.repository import AgentBRepository
    from app.agents.llm import CallMetadata
    from app.composition import Composition
    from app.config.registry import ConfigRegistry

    registry_path = ROOT / "config" / "registry.yaml"
    config = load_agent_b_config_file(registry_path)
    if config is None:
        print("No 'agent_b:' block in config/registry.yaml — Agent B is not configured here.")
        return 1

    registry = ConfigRegistry.from_yaml_file(registry_path)
    repo = AgentBRepository.open(config.database_path)
    embedder = FastEmbedEmbedder(config.embeddings.model)
    index_vault(repo, embedder, config)  # ensure the index is current before scoring
    answerer = AnswererAgent(Composition(registry).llm, model=config.answerer_model)

    async def answer(case):  # noqa: ANN001
        return await answer_question(
            case.question,
            repo=repo,
            embedder=embedder,
            answerer=answerer,
            config=config,
            metadata=CallMetadata(
                correlation_id=f"eval-{uuid.uuid4().hex[:8]}",
                prd_id="agent_b_eval",
                agent_role="answerer",
            ),
        )

    cases = load_golden(golden_path)
    report = await evaluate(cases, answer, model=config.answerer_model)
    repo.close()
    print(report.summary())
    return 0 if report.passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Agent B Q&A eval gate (S-B9).")
    parser.add_argument(
        "--golden",
        default=str(ROOT / "fixtures" / "agent_b" / "golden.json"),
        help="path to the golden set JSON (default fixtures/agent_b/golden.json)",
    )
    args = parser.parse_args()
    load_env()
    raise SystemExit(asyncio.run(main(args.golden)))
