"""Q&A eval harness (S-B9) — a measurable answer-quality gate, mirroring the classifier's discipline.

The internal KB is only trustworthy if we can measure two things objectively:

  * **Source recall** — for an answerable question, did retrieval surface the note(s) that actually hold
    the answer? (top-k recall over the cited/retrieved pages.)
  * **Refusal correctness** — did the bot refuse the questions it *should* refuse (nothing in the KB),
    and answer the ones it should? This is the S-B6/AD-30 anti-fabrication guarantee, measured.

The bar (`TARGET`): **100 % refusal correctness** (never fabricate on an unanswerable question, never
refuse an answerable one) and **source recall ≥ 0.8**. This mirrors the classifier's 0-FP/0-FN stance —
a wrong refusal is the Q&A equivalent of a false negative.

Like the classifier harness, this is pure orchestration over an injected `answer_question` seam, so it
is unit-testable against a fake embedder + fake LLM now; only the *live* accuracy run needs an API key
and a real indexed vault (`scripts/run_agent_b_eval.py`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "agent_b"

#: The pass bar, recorded here so a green result is attributable to a specific threshold.
TARGET_REFUSAL_ACCURACY = 1.0
TARGET_SOURCE_RECALL = 0.8


@dataclass(frozen=True, slots=True)
class EvalCase:
    question: str
    expected_page_ids: tuple[str, ...] = ()
    expect_refusal: bool = False


def load_golden(path: str | Path | None = None) -> list[EvalCase]:
    """Load the golden set (`question` → expected sources / expected-refusal) from JSON."""
    file_path = Path(path) if path is not None else _FIXTURES_ROOT / "golden.example.json"
    raw = json.loads(Path(file_path).read_text(encoding="utf-8"))
    cases = raw["cases"] if isinstance(raw, dict) else raw
    return [
        EvalCase(
            question=str(c["question"]),
            expected_page_ids=tuple(str(p) for p in c.get("expected_page_ids", [])),
            expect_refusal=bool(c.get("expect_refusal", False)),
        )
        for c in cases
    ]


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: EvalCase
    refused: bool
    retrieved_page_ids: tuple[str, ...]
    recall: float
    refusal_correct: bool

    @property
    def passed(self) -> bool:
        if self.case.expect_refusal:
            return self.refused
        return not self.refused and self.recall >= 1.0  # answerable: answered AND all sources found


@dataclass(frozen=True, slots=True)
class QaEvalReport:
    model: str
    results: tuple[CaseResult, ...]

    @property
    def answerable(self) -> tuple[CaseResult, ...]:
        return tuple(r for r in self.results if not r.case.expect_refusal)

    @property
    def source_recall(self) -> float:
        """Mean recall over answerable cases (1.0 if there are none)."""
        answerable = self.answerable
        return sum(r.recall for r in answerable) / len(answerable) if answerable else 1.0

    @property
    def refusal_accuracy(self) -> float:
        """Fraction of all cases whose refuse/answer decision matched the label."""
        return (
            sum(1 for r in self.results if r.refusal_correct) / len(self.results)
            if self.results
            else 1.0
        )

    @property
    def passed(self) -> bool:
        """The S-B9 bar: perfect refusal behaviour + recall at or above the floor."""
        return (
            self.refusal_accuracy >= TARGET_REFUSAL_ACCURACY
            and self.source_recall >= TARGET_SOURCE_RECALL
        )

    def summary(self) -> str:
        misses = [r for r in self.results if not r.passed]
        lines = [
            f"Agent B Q&A eval — model={self.model} cases={len(self.results)}",
            f"  source recall (answerable): {self.source_recall:.2f} (bar {TARGET_SOURCE_RECALL})",
            f"  refusal accuracy: {self.refusal_accuracy:.2f} (bar {TARGET_REFUSAL_ACCURACY})",
            f"  failing cases: {len(misses)} "
            f"[{', '.join(repr(r.case.question) for r in misses) or 'none'}]",
            f"  VERDICT: {'PASS' if self.passed else 'FAIL'}",
        ]
        return "\n".join(lines)


async def evaluate(cases, answer, *, model: str) -> QaEvalReport:  # noqa: ANN001
    """Run each case through `answer(case) -> QaResult` and score it against the labels.

    `answer` is injected (partial over `agent_b.qa.answer_question`) so the harness is testable with a
    fake embedder + LLM and never couples to a transport.
    """
    results: list[CaseResult] = []
    for case in cases:
        outcome = await answer(case)
        retrieved = tuple(h.page_id for h in outcome.hits)
        if case.expect_refusal:
            recall = 1.0
            refusal_correct = outcome.refused
        else:
            expected = case.expected_page_ids
            found = sum(1 for p in expected if p in retrieved)
            recall = (found / len(expected)) if expected else (0.0 if outcome.refused else 1.0)
            refusal_correct = not outcome.refused
        results.append(
            CaseResult(
                case=case,
                refused=outcome.refused,
                retrieved_page_ids=retrieved,
                recall=recall,
                refusal_correct=refusal_correct,
            )
        )
    return QaEvalReport(model=model, results=tuple(results))
