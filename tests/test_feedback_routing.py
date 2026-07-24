"""Story 4.1 — deterministic feedback routing on hand-built decisions (AD-16).

AD-16's testability contract: the orchestrator's routing off a `FeedbackDecision` is deterministic
and unit-tested here on hand-built decisions, with **no LLM involved**. Only the decision-*producing*
step (the interpreter) is eval-tested. So these tests build decisions directly and assert the route.
"""

from __future__ import annotations

import pytest

from app.domain.feedback import ClarificationTrigger, FeedbackDecision, FeedbackRoute
from app.domain.stage import PendingGate, Stage
from app.orchestrator.feedback_routing import FeedbackAction, route_feedback


def test_structured_feedback_routes_to_revising() -> None:
    decision = FeedbackDecision(
        route=FeedbackRoute.APPLY,
        structured_feedback="Section: Intro\nIssue: x\nSuggested change: y",
    )
    outcome = route_feedback(decision, current_stage=Stage.AWAITING_REVIEW)
    assert outcome.action is FeedbackAction.APPLY_FEEDBACK
    assert outcome.target_stage is Stage.REVISING


def test_plain_language_routes_to_structure_confirmation() -> None:
    """FR-10 — restate and confirm before applying; blocks on the human (EH-08)."""
    decision = FeedbackDecision(
        route=FeedbackRoute.CONFIRM_STRUCTURE,
        structured_feedback="Section: Intro\nIssue: unclear\nSuggested change: add example",
        question="is this what you mean?",
    )
    outcome = route_feedback(decision, current_stage=Stage.AWAITING_REVIEW)
    assert outcome.action is FeedbackAction.ASK_STRUCTURE_CONFIRM
    assert outcome.target_stage is Stage.AWAITING_STRUCTURE_CONFIRM
    assert outcome.gate is PendingGate.PM_STRUCTURE_CONFIRM


@pytest.mark.parametrize(
    "trigger",
    [
        ClarificationTrigger.UNDEFINED_TERM,
        ClarificationTrigger.PRD_CONTRADICTION,
        ClarificationTrigger.INCOMPLETE_FLOW,
        ClarificationTrigger.FEEDBACK_INCOHERENT,
    ],
)
def test_each_clarification_trigger_routes_to_awaiting_clarification(trigger) -> None:
    """FR-08 — only the four enumerated triggers may block."""
    decision = FeedbackDecision(
        route=FeedbackRoute.CLARIFY, trigger=trigger, question="which one did you mean?"
    )
    outcome = route_feedback(decision, current_stage=Stage.AWAITING_REVIEW)
    assert outcome.action is FeedbackAction.ASK_CLARIFICATION
    assert outcome.target_stage is Stage.AWAITING_CLARIFICATION
    assert outcome.gate is PendingGate.PM_CLARIFICATION


def test_a_clarify_decision_without_a_trigger_is_rejected_at_construction() -> None:
    """EH-08 — the agent may not block outside the four enumerated triggers."""
    with pytest.raises(ValueError, match="four FR-08 triggers"):
        FeedbackDecision(route=FeedbackRoute.CLARIFY, trigger=ClarificationTrigger.NONE)


def test_an_apply_decision_without_feedback_is_rejected() -> None:
    with pytest.raises(ValueError, match="structured feedback"):
        FeedbackDecision(route=FeedbackRoute.APPLY, structured_feedback="  ")


def test_confirmation_that_is_affirmed_routes_to_revising() -> None:
    decision = FeedbackDecision(
        route=FeedbackRoute.CONFIRMATION,
        confirmed=True,
        structured_feedback="Section: Intro\nIssue: x\nSuggested change: y",
    )
    outcome = route_feedback(decision, current_stage=Stage.AWAITING_STRUCTURE_CONFIRM)
    assert outcome.action is FeedbackAction.APPLY_FEEDBACK
    assert outcome.target_stage is Stage.REVISING


def test_confirmation_that_is_declined_returns_to_open_review() -> None:
    decision = FeedbackDecision(route=FeedbackRoute.CONFIRMATION, confirmed=False)
    outcome = route_feedback(decision, current_stage=Stage.AWAITING_STRUCTURE_CONFIRM)
    assert outcome.action is FeedbackAction.IGNORE
    assert outcome.target_stage is Stage.AWAITING_REVIEW


@pytest.mark.parametrize(
    "stage", [Stage.PASSED, Stage.PUBLISHING, Stage.COMPLETE, Stage.DRAFTED, Stage.DETECTED]
)
def test_a_comment_outside_the_review_loop_is_ignored(stage) -> None:
    """EH-06 — feedback after Done (or before review) is not processed."""
    decision = FeedbackDecision(
        route=FeedbackRoute.APPLY, structured_feedback="Section: x\nIssue: y\nSuggested change: z"
    )
    outcome = route_feedback(decision, current_stage=stage)
    assert outcome.action is FeedbackAction.IGNORE
    assert "not in the review loop" in outcome.reason


def test_routing_is_a_total_function_over_every_route_and_review_stage() -> None:
    """Deterministic and total — every (route, review-stage) pair yields an outcome, no exceptions."""
    review_stages = [
        Stage.AWAITING_REVIEW,
        Stage.AWAITING_STRUCTURE_CONFIRM,
        Stage.AWAITING_CLARIFICATION,
    ]
    decisions = [
        FeedbackDecision(
            route=FeedbackRoute.APPLY, structured_feedback="S: a\nI: b\nSuggested change: c"
        ),
        FeedbackDecision(route=FeedbackRoute.CONFIRM_STRUCTURE, structured_feedback="x"),
        FeedbackDecision(route=FeedbackRoute.CLARIFY, trigger=ClarificationTrigger.UNDEFINED_TERM),
        FeedbackDecision(route=FeedbackRoute.CONFIRMATION, confirmed=True),
        FeedbackDecision(route=FeedbackRoute.CONFIRMATION, confirmed=False),
    ]
    for stage in review_stages:
        for decision in decisions:
            outcome = route_feedback(decision, current_stage=stage)
            assert outcome.action in FeedbackAction
            assert outcome.target_stage in Stage
