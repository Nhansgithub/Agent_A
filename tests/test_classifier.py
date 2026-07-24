"""Stories 2.3 / 2.4 — Classifier agent + held-out eval harness (FR-03, AD-17, PRD §3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.classifier.agent import ClassifierAgent, ClassifierDecision
from app.agents.classifier.evaluation import (
    Fixture,
    evaluate,
    load_fixtures,
)
from app.agents.llm import CallMetadata, LlmClient
from app.domain.errors import AgentError
from tests.test_llm_client import FakeAnthropic

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def classifier_with_reply(reply: str) -> ClassifierAgent:
    fake = FakeAnthropic(text=reply)
    return ClassifierAgent(LlmClient("k", client=fake), model="claude-sonnet-5")


def metadata() -> CallMetadata:
    return CallMetadata(correlation_id="c", prd_id="page-1", agent_role="classifier")


async def classify(agent: ClassifierAgent, *, title="final_PRD_X", body="Some PRD body."):
    return await agent.classify(title=title, body_markdown=body, metadata=metadata())


# ---------------------------------------------------------------------------------------------
# Story 2.3 — the Classifier parses a decision from the model.
# ---------------------------------------------------------------------------------------------


async def test_accept_is_parsed() -> None:
    agent = classifier_with_reply(
        '{"decision": "ACCEPT", "confidence": "high", "reason": "complete"}'
    )
    result = await classify(agent)
    assert result.decision is ClassifierDecision.ACCEPT
    assert result.accepted
    assert result.reason == "complete"


async def test_reject_is_parsed() -> None:
    agent = classifier_with_reply(
        '{"decision": "REJECT", "confidence": "high", "reason": "template"}'
    )
    result = await classify(agent)
    assert result.decision is ClassifierDecision.REJECT
    assert not result.accepted


async def test_json_wrapped_in_a_code_fence_is_parsed() -> None:
    """Models often fence their JSON; the parser must tolerate it."""
    agent = classifier_with_reply(
        '```json\n{"decision": "ACCEPT", "confidence": "medium", "reason": "ok"}\n```'
    )
    assert (await classify(agent)).accepted


async def test_json_with_surrounding_prose_is_parsed() -> None:
    agent = classifier_with_reply(
        'Here is my analysis:\n{"decision": "REJECT", "reason": "empty"}\nDone.'
    )
    assert not (await classify(agent)).accepted


async def test_unparseable_output_raises_rather_than_silently_rejecting() -> None:
    """A misbehaving classifier must fail loudly, not masquerade as a legitimate REJECT."""
    agent = classifier_with_reply("I think this is probably fine?")
    with pytest.raises(AgentError, match="parseable JSON"):
        await classify(agent)


async def test_an_unrecognised_decision_value_raises() -> None:
    agent = classifier_with_reply('{"decision": "MAYBE", "reason": "unsure"}')
    with pytest.raises(AgentError, match="unrecognised decision"):
        await classify(agent)


async def test_the_classifier_uses_its_pinned_model() -> None:
    """AD-17 — the model comes from config, and a call is traced (AD-20)."""
    fake = FakeAnthropic(text='{"decision": "ACCEPT", "reason": "x"}')
    agent = ClassifierAgent(LlmClient("k", client=fake), model="claude-sonnet-5")
    await classify(agent)
    assert fake.calls[0]["model"] == "claude-sonnet-5"
    # No `temperature`: the pinned models reject sampling params (D-15). Determinism is steered by
    # the rubric in SKILL.md, not a knob.
    assert "temperature" not in fake.calls[0]


async def test_the_page_body_is_included_in_the_prompt() -> None:
    fake = FakeAnthropic(text='{"decision": "ACCEPT", "reason": "x"}')
    agent = ClassifierAgent(LlmClient("k", client=fake), model="claude-sonnet-5")
    await agent.classify(
        title="final_PRD_Widget", body_markdown="Distinctive body text.", metadata=metadata()
    )
    assert "Distinctive body text." in fake.calls[0]["messages"][0]["content"]


async def test_the_skill_file_is_the_system_prompt() -> None:
    fake = FakeAnthropic(text='{"decision": "ACCEPT", "reason": "x"}')
    agent = ClassifierAgent(LlmClient("k", client=fake), model="claude-sonnet-5")
    await classify(agent)
    assert "Classifier" in fake.calls[0]["system"]
    assert "ACCEPT" in fake.calls[0]["system"]


# ---------------------------------------------------------------------------------------------
# Story 2.4 — the fixture sets are build deliverables.
# ---------------------------------------------------------------------------------------------


def test_both_fixture_splits_exist_and_are_labeled() -> None:
    dev = load_fixtures("dev")
    holdout = load_fixtures("holdout")
    assert 3 <= len(dev) <= 8
    assert 3 <= len(holdout) <= 8


@pytest.mark.parametrize("split", ["dev", "holdout"])
def test_each_split_has_both_accept_and_reject_examples(split: str) -> None:
    """AD-17 — a real PRD, an empty page, a bare template, a mislabeled non-PRD."""
    labels = {f.expected for f in load_fixtures(split)}
    assert ClassifierDecision.ACCEPT in labels
    assert ClassifierDecision.REJECT in labels


def test_dev_and_holdout_fixtures_are_disjoint() -> None:
    """No train-on-test — a dev fixture reappearing in holdout would rig the bar."""
    dev_bodies = {f.body_markdown for f in load_fixtures("dev")}
    holdout_bodies = {f.body_markdown for f in load_fixtures("holdout")}
    assert dev_bodies.isdisjoint(holdout_bodies)


# ---------------------------------------------------------------------------------------------
# Story 2.4 — the ×3 eval harness, confusion matrix, and 0-FP/0-FN bar.
# ---------------------------------------------------------------------------------------------


def scripted(answers: dict[str, ClassifierDecision]):
    """A classify() that returns a fixed decision per fixture name."""

    async def classify_fn(fixture: Fixture):
        from app.agents.classifier.agent import ClassificationResult

        return ClassificationResult(
            decision=answers[fixture.name], confidence="high", reason="scripted"
        )

    return classify_fn


async def test_a_perfect_run_passes_the_bar() -> None:
    fixtures = load_fixtures("holdout")
    answers = {f.name: f.expected for f in fixtures}

    report = await evaluate(
        fixtures, scripted(answers), split="holdout", model="claude-sonnet-5", runs=3
    )

    assert report.passed
    assert not report.false_positives and not report.false_negatives
    matrix = report.confusion_matrix()
    assert matrix["false_positive"] == 0 and matrix["false_negative"] == 0


async def test_a_false_positive_fails_the_bar() -> None:
    """A non-PRD classified ACCEPT — the expensive mistake, sends junk into drafting."""
    fixtures = load_fixtures("holdout")
    answers = {f.name: f.expected for f in fixtures}
    reject_fixture = next(f for f in fixtures if f.expected is ClassifierDecision.REJECT)
    answers[reject_fixture.name] = ClassifierDecision.ACCEPT

    report = await evaluate(fixtures, scripted(answers), split="holdout", model="m", runs=3)

    assert not report.passed
    assert reject_fixture.name in {o.fixture.name for o in report.false_positives}


async def test_a_false_negative_fails_the_bar() -> None:
    fixtures = load_fixtures("holdout")
    answers = {f.name: f.expected for f in fixtures}
    accept_fixture = next(f for f in fixtures if f.expected is ClassifierDecision.ACCEPT)
    answers[accept_fixture.name] = ClassifierDecision.REJECT

    report = await evaluate(fixtures, scripted(answers), split="holdout", model="m", runs=3)

    assert not report.passed
    assert accept_fixture.name in {o.fixture.name for o in report.false_negatives}


async def test_an_unstable_fixture_is_a_flake_and_fails_the_bar() -> None:
    """AD-17 — acceptance is a distribution: a fixture that flips between runs is not a pass."""
    fixtures = load_fixtures("holdout")
    accept_fixture = next(f for f in fixtures if f.expected is ClassifierDecision.ACCEPT)
    flips = iter([ClassifierDecision.ACCEPT, ClassifierDecision.REJECT, ClassifierDecision.ACCEPT])

    async def flaky(fixture: Fixture):
        from app.agents.classifier.agent import ClassificationResult

        decision = next(flips) if fixture.name == accept_fixture.name else fixture.expected
        return ClassificationResult(decision=decision, confidence="low", reason="flaky")

    report = await evaluate(fixtures, flaky, split="holdout", model="m", runs=3)

    assert not report.passed
    assert accept_fixture.name in {o.fixture.name for o in report.flakes}


async def test_the_eval_runs_the_configured_number_of_times() -> None:
    fixtures = load_fixtures("holdout")
    calls: list[str] = []

    async def counting(fixture: Fixture):
        from app.agents.classifier.agent import ClassificationResult

        calls.append(fixture.name)
        return ClassificationResult(decision=fixture.expected, confidence="high", reason="")

    await evaluate(fixtures, counting, split="holdout", model="m", runs=3)

    assert len(calls) == 3 * len(fixtures)


async def test_the_report_summary_names_the_model_and_the_verdict() -> None:
    fixtures = load_fixtures("holdout")
    answers = {f.name: f.expected for f in fixtures}
    report = await evaluate(
        fixtures, scripted(answers), split="holdout", model="claude-sonnet-5", runs=3
    )

    summary = report.summary()
    assert "claude-sonnet-5" in summary
    assert "PASS" in summary


async def test_the_agent_and_the_harness_compose() -> None:
    """End-to-end with the real agent parsing over a fake LLM — the shape the live eval will run."""
    fixtures = load_fixtures("holdout")

    async def classify_fn(fixture: Fixture):
        reply = f'{{"decision": "{fixture.expected.value}", "confidence": "high", "reason": "ok"}}'
        agent = classifier_with_reply(reply)
        return await agent.classify(
            title=fixture.title, body_markdown=fixture.body_markdown, metadata=metadata()
        )

    report = await evaluate(fixtures, classify_fn, split="holdout", model="claude-sonnet-5", runs=3)
    assert report.passed
