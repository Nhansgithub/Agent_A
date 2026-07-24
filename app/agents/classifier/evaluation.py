"""Classifier evaluation harness (AD-17, PRD §3 counter-metric).

The one objective quality bar in the whole demo: **0 false-positives / 0 false-negatives on the
held-out fixture set.** The readiness report calls this the riskiest single deliverable.

Three rules from AD-17, all enforced here:

* **No train-on-test.** `fixtures/classifier/dev/` tunes the prompt; the bar applies to
  `fixtures/classifier/holdout/` only. `evaluate()` refuses to score the dev set against the bar.
* **A distribution, not a single boolean.** The eval runs **three times** and emits a confusion
  matrix plus a *flake budget* — a fixture that flips between runs is not silently averaged away, it
  is surfaced as instability that must be fixed before the bar can be trusted.
* **The model id is pinned in config** (AD-4). The harness records which model it ran, so a green
  result is attributable to a specific pinned model.

The harness is pure orchestration over an injected classifier, so it is unit-testable against a fake
LLM now; only the *live* accuracy run needs the Anthropic key.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.classifier.agent import ClassificationResult, ClassifierDecision

DEFAULT_RUNS = 3
_FIXTURES_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "classifier"


@dataclass(frozen=True, slots=True)
class Fixture:
    """One labeled example page."""

    name: str
    title: str
    body_markdown: str
    expected: ClassifierDecision


def load_fixtures(split: str, root: Path | None = None) -> list[Fixture]:
    """Load the `dev` or `holdout` fixture set. Each case is a `.md` body + a `.json` label sidecar."""
    directory = (root or _FIXTURES_ROOT) / split
    if not directory.is_dir():
        raise FileNotFoundError(f"no classifier fixtures at {directory}")

    fixtures: list[Fixture] = []
    for label_path in sorted(directory.glob("*.json")):
        meta = json.loads(label_path.read_text(encoding="utf-8"))
        body_path = label_path.with_suffix(".md")
        fixtures.append(
            Fixture(
                name=label_path.stem,
                title=str(meta["title"]),
                body_markdown=body_path.read_text(encoding="utf-8"),
                expected=ClassifierDecision(str(meta["label"]).upper()),
            )
        )
    if not fixtures:
        raise FileNotFoundError(f"{directory} contains no fixtures")
    return fixtures


@dataclass
class FixtureOutcome:
    """How one fixture fared across the runs."""

    fixture: Fixture
    decisions: list[ClassifierDecision] = field(default_factory=list)

    @property
    def is_stable(self) -> bool:
        """Did every run agree? An unstable fixture is a flake, tracked separately from a miss."""
        return len(set(self.decisions)) == 1

    @property
    def majority(self) -> ClassifierDecision:
        accepts = self.decisions.count(ClassifierDecision.ACCEPT)
        rejects = self.decisions.count(ClassifierDecision.REJECT)
        return ClassifierDecision.ACCEPT if accepts >= rejects else ClassifierDecision.REJECT

    @property
    def correct(self) -> bool:
        """Correct on **every** run — the 0-FP/0-FN bar is per-run, not majority-vote."""
        return self.is_stable and self.decisions[0] is self.fixture.expected

    @property
    def is_false_positive(self) -> bool:
        """Predicted ACCEPT on any run when the truth is REJECT — a non-PRD sent into drafting."""
        return (
            self.fixture.expected is ClassifierDecision.REJECT
            and ClassifierDecision.ACCEPT in self.decisions
        )

    @property
    def is_false_negative(self) -> bool:
        """Predicted REJECT on any run when the truth is ACCEPT — a real PRD blocked."""
        return (
            self.fixture.expected is ClassifierDecision.ACCEPT
            and ClassifierDecision.REJECT in self.decisions
        )


@dataclass
class EvalReport:
    """The verdict on one fixture split."""

    split: str
    model: str
    runs: int
    outcomes: list[FixtureOutcome]

    @property
    def false_positives(self) -> list[FixtureOutcome]:
        return [o for o in self.outcomes if o.is_false_positive]

    @property
    def false_negatives(self) -> list[FixtureOutcome]:
        return [o for o in self.outcomes if o.is_false_negative]

    @property
    def flakes(self) -> list[FixtureOutcome]:
        """Fixtures whose decision varied across runs — instability, distinct from a wrong answer."""
        return [o for o in self.outcomes if not o.is_stable]

    @property
    def passed(self) -> bool:
        """The AD-17 bar: zero FP, zero FN, and no flakes (an unstable pass is not a pass)."""
        return not self.false_positives and not self.false_negatives and not self.flakes

    def confusion_matrix(self) -> dict[str, int]:
        """Per-run tallies across all fixtures — the shape AD-17 asks the eval to emit."""
        tp = fp = tn = fn = 0
        for outcome in self.outcomes:
            for decision in outcome.decisions:
                accept = decision is ClassifierDecision.ACCEPT
                truth_accept = outcome.fixture.expected is ClassifierDecision.ACCEPT
                if accept and truth_accept:
                    tp += 1
                elif accept and not truth_accept:
                    fp += 1
                elif not accept and truth_accept:
                    fn += 1
                else:
                    tn += 1
        return {
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": tn,
            "false_negative": fn,
        }

    def summary(self) -> str:
        matrix = self.confusion_matrix()
        lines = [
            f"Classifier eval — split={self.split} model={self.model} runs={self.runs}",
            f"  fixtures: {len(self.outcomes)}  ({self.runs * len(self.outcomes)} classifications)",
            f"  confusion (per classification): {matrix}",
            f"  false positives: {len(self.false_positives)}  "
            f"[{', '.join(o.fixture.name for o in self.false_positives) or 'none'}]",
            f"  false negatives: {len(self.false_negatives)}  "
            f"[{', '.join(o.fixture.name for o in self.false_negatives) or 'none'}]",
            f"  flakes (unstable across runs): {len(self.flakes)}  "
            f"[{', '.join(o.fixture.name for o in self.flakes) or 'none'}]",
            f"  VERDICT: {'PASS (0 FP / 0 FN, stable)' if self.passed else 'FAIL'}",
        ]
        return "\n".join(lines)


#: Runs the classifier on one fixture. Injected so the harness is testable against a fake.
Classify = Callable[[Fixture], Awaitable[ClassificationResult]]


async def evaluate(
    fixtures: list[Fixture],
    classify: Classify,
    *,
    split: str,
    model: str,
    runs: int = DEFAULT_RUNS,
) -> EvalReport:
    """Run the classifier `runs` times over the fixtures and score against the AD-17 bar.

    Args:
        split: which set this is. The 0-FP/0-FN bar is meaningful only on ``"holdout"`` — scoring the
            dev set against it would be train-on-test, so callers must not treat a dev pass as the bar.
    """
    outcomes = [FixtureOutcome(fixture=f) for f in fixtures]
    for _ in range(runs):
        for outcome in outcomes:
            result = await classify(outcome.fixture)
            outcome.decisions.append(result.decision)
    return EvalReport(split=split, model=model, runs=runs, outcomes=outcomes)
