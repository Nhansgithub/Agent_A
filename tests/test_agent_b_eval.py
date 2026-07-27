"""S-B9 — Q&A eval harness: recall + refusal scoring, golden-set loader, the pass bar (offline)."""

from __future__ import annotations

from agent_b.eval import (
    TARGET_REFUSAL_ACCURACY,
    TARGET_SOURCE_RECALL,
    EvalCase,
    evaluate,
    load_golden,
)
from agent_b.qa import QaResult
from agent_b.rag.retriever import Hit


def _hit(page_id: str) -> Hit:
    return Hit(page_id=page_id, title=page_id, source_url="", score=1.0, snippet="", via="vector")


def _answerer(script: dict[str, QaResult]):
    """A fake `answer(case)` that returns a canned QaResult per question."""

    async def answer(case: EvalCase) -> QaResult:
        return script[case.question]

    return answer


async def test_all_correct_passes_the_bar() -> None:
    cases = [
        EvalCase(question="onboard?", expected_page_ids=("P1",)),
        EvalCase(question="vacation?", expect_refusal=True),
    ]
    script = {
        "onboard?": QaResult(
            qa_id=1, answer="a [1]", hits=(_hit("P1"),), refused=False, top_score=0.9
        ),
        "vacation?": QaResult(
            qa_id=2, answer="I don't have a doc on that.", hits=(), refused=True, top_score=0.0
        ),
    }

    report = await evaluate(cases, _answerer(script), model="m")

    assert report.source_recall == 1.0
    assert report.refusal_accuracy == 1.0
    assert report.passed
    assert all(r.passed for r in report.results)


async def test_missing_source_lowers_recall_and_fails() -> None:
    cases = [EvalCase(question="onboard?", expected_page_ids=("P1", "P2"))]
    # Only P1 retrieved; P2 missed → recall 0.5, below the 1.0 per-case pass rule and the 0.8 floor.
    script = {
        "onboard?": QaResult(qa_id=1, answer="a", hits=(_hit("P1"),), refused=False, top_score=0.9)
    }

    report = await evaluate(cases, _answerer(script), model="m")

    assert report.source_recall == 0.5
    assert not report.passed
    assert not report.results[0].passed


async def test_wrong_refusal_is_a_false_negative() -> None:
    cases = [EvalCase(question="onboard?", expected_page_ids=("P1",))]
    # Refused an answerable question → refusal accuracy < 1.0, the Q&A equivalent of a false negative.
    script = {
        "onboard?": QaResult(
            qa_id=1, answer="I don't have a doc on that.", hits=(), refused=True, top_score=0.1
        )
    }

    report = await evaluate(cases, _answerer(script), model="m")

    assert report.refusal_accuracy == 0.0
    assert not report.passed


async def test_fabricating_on_unanswerable_fails() -> None:
    cases = [EvalCase(question="vacation?", expect_refusal=True)]
    # Answered a question the KB has no doc for → refusal incorrect (a fabrication).
    script = {
        "vacation?": QaResult(
            qa_id=1, answer="Two weeks.", hits=(_hit("PX"),), refused=False, top_score=0.6
        )
    }

    report = await evaluate(cases, _answerer(script), model="m")

    assert report.refusal_accuracy == 0.0
    assert not report.passed


def test_load_golden_parses_the_template() -> None:
    cases = load_golden()  # the shipped fixtures/agent_b/golden.example.json
    assert len(cases) == 3
    answerable = [c for c in cases if not c.expect_refusal]
    refusals = [c for c in cases if c.expect_refusal]
    assert len(answerable) == 2 and len(refusals) == 1
    assert all(
        c.expected_page_ids for c in answerable
    )  # answerable cases name their expected sources


def test_target_bar_is_recorded() -> None:
    assert TARGET_REFUSAL_ACCURACY == 1.0
    assert 0.0 < TARGET_SOURCE_RECALL <= 1.0
