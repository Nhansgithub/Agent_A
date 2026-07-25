"""The §9 lifecycle stage machine (AD-2, AD-11).

``stage`` is the business cursor for a run. Three rules govern it, and they are the reason this is an
explicit enum rather than something inferred:

* It is **authoritative over any Atlassian status** — never derived from a Jira field.
* It is written **only by the orchestrator**, at a stage boundary. Role-agents return results; the
  orchestrator persists them (AD-2).
* It is the **resumable unit** (AD-11). Resume re-enters the graph at the recorded stage, and
  converges because every externally-visible create is idempotent.

The legal-transition map below encodes the state diagram from the solution design (§4). It exists so
an orchestrator bug surfaces as a loud rejected transition instead of a run silently skipping a human
gate — the failure mode AD-15 exists to prevent.
"""

from __future__ import annotations

from enum import StrEnum


class Stage(StrEnum):
    """PRD §9 — the explicit lifecycle stages, in flow order."""

    DETECTED = "detected"
    CONFIRMED = "confirmed"
    PRD_TICKET_DONE = "prd_ticket_done"
    DRAFTED = "drafted"
    AWAITING_REVIEW = "awaiting_review"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    AWAITING_STRUCTURE_CONFIRM = "awaiting_structure_confirm"
    REVISING = "revising"
    PASSED = "passed"
    AWAITING_PUBLISH_APPROVAL = "awaiting_publish_approval"
    PUBLISHING = "publishing"
    COMPLETE = "complete"
    ERROR = "error"


class PendingGate(StrEnum):
    """What human action a parked run is waiting for (§10 `pending_gate`).

    Distinct from ``stage``: the stage says where the flow is, the gate says whose move it is. Used by
    the AD-22 liveness sweep and by the EH-01 error comment to address the right human.
    """

    NONE = "none"
    UPLOADING_PM_RENAME = "uploading_pm_rename"
    """FR-02a — a mislabeled or REJECTed page awaits the page creator renaming it."""

    PM_REVIEW = "pm_review"
    """FR-12 — the Reviewer PM must comment or transition the Review ticket to Done."""

    PM_CLARIFICATION = "pm_clarification"
    """FR-08 — a clarifying question is outstanding; the agent must not proceed (EH-08)."""

    PM_STRUCTURE_CONFIRM = "pm_structure_confirm"
    """FR-10 — plain-language feedback was restated; awaiting the PM's confirmation (EH-08)."""

    HEAD_OF_PRODUCT_APPROVAL = "head_of_product_approval"
    """FR-14 — the publish gate."""

    ADMIN_RESUME = "admin_resume"
    """EH-02 — an error is surfaced; awaiting `@agent resume` / `fixed` from the admin."""


class QueueStatus(StrEnum):
    """AD-5 / NFR-06 — the serial queue's view of a run.

    The demo processes exactly one PRD at a time. This is also a memory-safety measure (AD-21): large
    PRD payloads are the main RAM consumer on the 1 GB box, so only one is ever resident.
    """

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    IDLE = "idle"
    """Not being worked: parked at a human gate, complete, or in error."""


#: Terminal stages — no further automatic advance is possible.
TERMINAL_STAGES = frozenset({Stage.COMPLETE})

#: Stages that park indefinitely awaiting a human (FR-12, FR-14, EH-09). No timeout by design.
PARKED_STAGES = frozenset(
    {
        Stage.AWAITING_REVIEW,
        Stage.AWAITING_CLARIFICATION,
        Stage.AWAITING_STRUCTURE_CONFIRM,
        Stage.AWAITING_PUBLISH_APPROVAL,
    }
)

#: Stages the AD-22 liveness sweep watches for staleness.
LIVENESS_WATCHED_STAGES = frozenset(
    {Stage.AWAITING_REVIEW, Stage.AWAITING_PUBLISH_APPROVAL, Stage.ERROR}
)

#: The solution-design §4 state diagram, as data. ANY stage may additionally go to ERROR, and ERROR
#: may return to any stage (EH-02 resume re-enters at `last_good_checkpoint`).
_LEGAL_TRANSITIONS: dict[Stage, frozenset[Stage]] = {
    Stage.DETECTED: frozenset({Stage.CONFIRMED}),
    Stage.CONFIRMED: frozenset({Stage.PRD_TICKET_DONE}),
    # A clarification may be needed before the first draft as well as during a redraft (FR-08 applies
    # to "drafting/redrafting"), so drafting stages may park on the clarification gate.
    Stage.PRD_TICKET_DONE: frozenset({Stage.DRAFTED, Stage.AWAITING_CLARIFICATION}),
    Stage.DRAFTED: frozenset({Stage.AWAITING_REVIEW}),
    Stage.AWAITING_REVIEW: frozenset(
        {
            Stage.AWAITING_CLARIFICATION,
            Stage.AWAITING_STRUCTURE_CONFIRM,
            Stage.REVISING,
            Stage.PASSED,
        }
    ),
    # Where a clarification returns to depends on what asked for it; the orchestrator restores the
    # stage recorded in `last_good_checkpoint`.
    Stage.AWAITING_CLARIFICATION: frozenset(
        {Stage.PRD_TICKET_DONE, Stage.AWAITING_REVIEW, Stage.REVISING}
    ),
    # REVISING when the PM confirms; AWAITING_REVIEW when they reject the restatement — a rejection
    # returns to open review for another round (FR-10, amendment 2026-07-25). Without this edge a
    # "no" raised IllegalStageTransition and 500'd the webhook.
    Stage.AWAITING_STRUCTURE_CONFIRM: frozenset({Stage.REVISING, Stage.AWAITING_REVIEW}),
    Stage.REVISING: frozenset({Stage.AWAITING_REVIEW, Stage.AWAITING_CLARIFICATION}),
    Stage.PASSED: frozenset({Stage.AWAITING_PUBLISH_APPROVAL}),
    Stage.AWAITING_PUBLISH_APPROVAL: frozenset({Stage.PUBLISHING}),
    Stage.PUBLISHING: frozenset({Stage.COMPLETE}),
    Stage.COMPLETE: frozenset(),
    Stage.ERROR: frozenset(Stage),  # EH-02 resume returns to the failed stage
}


class IllegalStageTransition(ValueError):
    """An attempt to advance `stage` along an edge the state machine does not have."""


def is_legal_transition(current: Stage, target: Stage) -> bool:
    """Is advancing `current -> target` allowed by the §9 state machine?

    Self-transitions are legal: replaying a stage is exactly how AD-11 resume works, and it converges
    because every externally-visible create is idempotent.
    """
    if target is Stage.ERROR or current is target:
        return True
    return target in _LEGAL_TRANSITIONS.get(current, frozenset())


def assert_legal_transition(current: Stage, target: Stage) -> None:
    """Raise if the transition would skip a stage — most dangerously, a human gate (AD-15)."""
    if not is_legal_transition(current, target):
        legal = sorted(s.value for s in _LEGAL_TRANSITIONS.get(current, frozenset()))
        raise IllegalStageTransition(
            f"illegal stage transition {current.value!r} -> {target.value!r}. "
            f"Legal from {current.value!r}: {legal or ['(terminal)']} (plus 'error'). "
            "Advancing along a non-existent edge risks skipping a human gate (AD-15)."
        )


def gate_for_stage(stage: Stage) -> PendingGate:
    """The human whose move a parked stage is waiting on."""
    return {
        Stage.AWAITING_REVIEW: PendingGate.PM_REVIEW,
        Stage.AWAITING_CLARIFICATION: PendingGate.PM_CLARIFICATION,
        Stage.AWAITING_STRUCTURE_CONFIRM: PendingGate.PM_STRUCTURE_CONFIRM,
        Stage.AWAITING_PUBLISH_APPROVAL: PendingGate.HEAD_OF_PRODUCT_APPROVAL,
        Stage.ERROR: PendingGate.ADMIN_RESUME,
    }.get(stage, PendingGate.NONE)
