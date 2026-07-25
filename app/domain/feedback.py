"""The typed `FeedbackDecision` (AD-16, FR-08/09/10).

AD-16's core move: the Feedback interpreter (an LLM) returns a **typed decision**, and the
orchestrator's stage routing is a **deterministic function of that decision** — unit-tested on
hand-built `FeedbackDecision` objects, with only the decision-*producing* LLM eval-tested. This keeps
"which stage does this feedback go to" out of untestable prose.

The four routes correspond to the four things a PM comment can mean while a draft is under review:

* `APPLY` — the comment is (or was converted to) structured §6.2 feedback ready to apply (FR-11).
* `CONFIRM_STRUCTURE` — plain-language feedback; the agent restated it and must confirm before acting
  (FR-10). Blocks on the human (EH-08).
* `CLARIFY` — a blocking FR-08 trigger holds; the agent asks a question and waits (EH-08).
* `CONFIRMATION` — the comment is the PM's reply to a pending confirmation/clarification question, not
  new feedback. Lets the orchestrator resume the loop rather than re-interpret from scratch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FeedbackRoute(StrEnum):
    APPLY = "apply"
    CONFIRM_STRUCTURE = "confirm_structure"
    CLARIFY = "clarify"
    CONFIRMATION = "confirmation"


class Speaker(StrEnum):
    """Who wrote a review-ticket comment, as the interpreter sees the conversation."""

    PM = "PM"
    AGENT = "Agent"


@dataclass(frozen=True, slots=True)
class ReviewTurn:
    """One comment in the review-ticket discussion, for the interpreter's conversation memory (FR-10).

    The review loop is a back-and-forth, but each comment used to be interpreted in isolation — so a
    reply like "yes, but drop the last point" or a bare "no" had no anchor. Feeding the recent
    transcript (this type, oldest→newest) gives the Feedback interpreter the context to read a reply
    as part of a conversation rather than a standalone instruction.
    """

    speaker: Speaker
    text: str


class ClarificationTrigger(StrEnum):
    """The FR-08 enumerated triggers — the ONLY cases where the agent blocks to ask (EH-08).

    Outside these, the agent proceeds with a stated assumption. Encoding them as an enum keeps the
    list closed: a decision citing anything else is not a legal reason to block.
    """

    NONE = "none"
    UNDEFINED_TERM = "undefined_term"  # (1) a term/acronym defined nowhere that changes the doc
    PRD_CONTRADICTION = "prd_contradiction"  # (2) two parts contradict on user-facing behaviour
    INCOMPLETE_FLOW = "incomplete_flow"  # (3) a required user-facing step/outcome is missing
    FEEDBACK_INCOHERENT = "feedback_incoherent"  # (4) PM feedback contradicts itself / dangling ref


@dataclass(frozen=True, slots=True)
class FeedbackDecision:
    """A typed decision the orchestrator routes on deterministically (AD-16)."""

    route: FeedbackRoute
    structured_feedback: str = ""
    """The §6.2-formatted feedback, for APPLY (verbatim or normalized) and CONFIRM_STRUCTURE (the
    agent's restatement to confirm)."""

    trigger: ClarificationTrigger = ClarificationTrigger.NONE
    """Which FR-08 trigger fired, when `route == CLARIFY`."""

    question: str = ""
    """The clarifying/confirmation question to post to the PM, for CLARIFY and CONFIRM_STRUCTURE."""

    assumption: str = ""
    """A stated assumption when proceeding without asking (FR-08 'outside these cases')."""

    confirmed: bool = False
    """For CONFIRMATION: whether the PM's reply affirmed the restatement/answer."""

    def __post_init__(self) -> None:
        if self.route is FeedbackRoute.CLARIFY and self.trigger is ClarificationTrigger.NONE:
            raise ValueError(
                "a CLARIFY decision must name one of the four FR-08 triggers — the agent may not "
                "block to ask outside the enumerated cases (EH-08)."
            )
        if self.route is FeedbackRoute.APPLY and not self.structured_feedback.strip():
            raise ValueError("an APPLY decision must carry the structured feedback to apply.")
