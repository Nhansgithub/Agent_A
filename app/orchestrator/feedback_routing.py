"""Deterministic routing of a `FeedbackDecision` to a stage transition (AD-16).

This is the half of AD-16 that must be **testable without an LLM**: given a `FeedbackDecision` and the
stage the run is parked at, decide exactly what happens to `stage`. Pure function, no I/O, no model —
so the "which stage does this feedback go to" logic lives in unit tests on hand-built decisions, never
in prose or inside a prompt.

The orchestrator calls the Feedback interpreter (the LLM half) to *produce* the decision, then calls
this to *act* on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.feedback import FeedbackDecision, FeedbackRoute
from app.domain.stage import PendingGate, Stage


class FeedbackAction(StrEnum):
    """What the orchestrator should do with the decision, beyond moving the stage."""

    APPLY_FEEDBACK = "apply_feedback"
    """Store the structured feedback and revise (→ revising)."""

    ASK_STRUCTURE_CONFIRM = "ask_structure_confirm"
    """Post the restated feedback and park for confirmation (→ awaiting_structure_confirm)."""

    ASK_CLARIFICATION = "ask_clarification"
    """Post the clarifying question and park (→ awaiting_clarification)."""

    IGNORE = "ignore"
    """Do nothing — a comment that is not actionable, or arrives in the wrong state (EH-06)."""


@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    action: FeedbackAction
    target_stage: Stage
    gate: PendingGate
    reason: str = ""


def route_feedback(decision: FeedbackDecision, *, current_stage: Stage) -> RoutingOutcome:
    """Map a decision + current stage to a stage transition. Deterministic and total.

    Legal current stages for a PM comment are the review-loop waits: `awaiting_review`,
    `awaiting_structure_confirm`, `awaiting_clarification`. A comment in any other stage is ignored
    (e.g. EH-06 — feedback after the ticket is already Done never reaches here, but if it did, it is a
    no-op).
    """
    review_stages = {
        Stage.AWAITING_REVIEW,
        Stage.AWAITING_STRUCTURE_CONFIRM,
        Stage.AWAITING_CLARIFICATION,
    }
    if current_stage not in review_stages:
        return RoutingOutcome(
            FeedbackAction.IGNORE,
            current_stage,
            PendingGate.NONE,
            f"comment ignored: run is at {current_stage.value}, not in the review loop",
        )

    if decision.route is FeedbackRoute.APPLY:
        return RoutingOutcome(
            FeedbackAction.APPLY_FEEDBACK,
            Stage.REVISING,
            PendingGate.NONE,
            "structured feedback → revise",
        )

    if decision.route is FeedbackRoute.CONFIRM_STRUCTURE:
        return RoutingOutcome(
            FeedbackAction.ASK_STRUCTURE_CONFIRM,
            Stage.AWAITING_STRUCTURE_CONFIRM,
            PendingGate.PM_STRUCTURE_CONFIRM,
            "plain-language feedback → restate and confirm (FR-10)",
        )

    if decision.route is FeedbackRoute.CLARIFY:
        return RoutingOutcome(
            FeedbackAction.ASK_CLARIFICATION,
            Stage.AWAITING_CLARIFICATION,
            PendingGate.PM_CLARIFICATION,
            f"clarification needed ({decision.trigger.value}) → ask and wait (FR-08)",
        )

    # CONFIRMATION — the PM answered a pending question.
    if decision.confirmed:
        return RoutingOutcome(
            FeedbackAction.APPLY_FEEDBACK,
            Stage.REVISING,
            PendingGate.NONE,
            "PM confirmed → revise",
        )
    # Not confirmed: they want changes to the restatement, or answered a clarification without
    # agreeing to a concrete change. Return to open review for a fresh feedback round.
    return RoutingOutcome(
        FeedbackAction.IGNORE,
        Stage.AWAITING_REVIEW,
        PendingGate.PM_REVIEW,
        "PM did not confirm → back to awaiting_review for another round",
    )
