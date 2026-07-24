#!/usr/bin/env python
"""Run the classifier accuracy eval against the held-out fixture set (AD-17, PRD §3).

This is the demo's one objective quality gate: **0 false-positives / 0 false-negatives on the
holdout set**, measured across three runs with a confusion matrix and a flake budget.

Needs a live Anthropic key (BLOCKERS B-1) — it makes real Claude calls. The harness itself is
unit-tested offline against a fake in tests/test_classifier.py; this script is the live measurement.

    export ANTHROPIC_API_KEY=sk-ant-...
    python scripts/run_classifier_eval.py                 # holdout, 3 runs, the acceptance bar
    python scripts/run_classifier_eval.py --split dev      # dev set, for prompt tuning
    python scripts/run_classifier_eval.py --runs 5

Exit code is non-zero if the bar is not met, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.classifier.agent import ClassifierAgent  # noqa: E402
from app.agents.classifier.evaluation import Fixture, evaluate, load_fixtures  # noqa: E402
from app.agents.llm import CallMetadata, LlmClient  # noqa: E402
from app.config.registry import ConfigRegistry  # noqa: E402
from app.config.secrets import resolve_secret  # noqa: E402


def load_env(path: Path) -> dict[str, str]:
    env = dict(os.environ)
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run the classifier accuracy eval")
    parser.add_argument("--split", default="holdout", choices=["holdout", "dev"])
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    env = load_env(root / ".env")

    registry_path = root / "config" / "registry.yaml"
    model = "claude-sonnet-5"
    if registry_path.is_file():
        model = ConfigRegistry.from_yaml_file(registry_path).system.models.classifier

    try:
        api_key = resolve_secret("env:ANTHROPIC_API_KEY", env)
    except Exception as exc:
        print(f"ERROR: {exc}\nSee SETUP-GUIDE.md Part 3.", file=sys.stderr)
        return 2

    agent = ClassifierAgent(LlmClient(api_key), model=model)

    async def classify(fixture: Fixture):
        return await agent.classify(
            title=fixture.title,
            body_markdown=fixture.body_markdown,
            metadata=CallMetadata(
                correlation_id=f"eval-{fixture.name}",
                prd_id=fixture.name,
                agent_role="classifier",
            ),
        )

    fixtures = load_fixtures(args.split)
    print(
        f"Running classifier eval: {len(fixtures)} {args.split} fixtures × {args.runs} runs "
        f"(model={model})...\n"
    )
    report = await evaluate(fixtures, classify, split=args.split, model=model, runs=args.runs)
    print(report.summary())

    if args.split == "dev":
        print("\nNote: the dev set tunes the prompt; the acceptance bar applies to holdout only.")
        return 0
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
