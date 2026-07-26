"""The Feedback interpreter agent (FR-08, FR-09, FR-10, AD-16).

Reads a PM comment and returns a typed `FeedbackDecision`. It **classifies**; it does not act — the
orchestrator routes on the decision deterministically (AD-16). This split is the whole point: the
routing is unit-testable on hand-built decisions, and only this LLM step is eval-tested.

The model comes from config (`system.models.feedback_interpreter`, AD-17). A parse failure raises
rather than defaulting to a route — silently picking APPLY or CLARIFY on malformed output would be a
worse failure than surfacing it.
"""

from __future__ import annotations

import json

from app.agents.llm import CallMetadata, LlmClient
from app.agents.skills import load_skill
from app.domain.errors import AgentError
from app.domain.feedback import (
    ClarificationTrigger,
    DeletionDecision,
    FeedbackDecision,
    FeedbackRoute,
    ReviewTurn,
)

_ROLE = "feedback_interpreter"


class FeedbackInterpreter:
    """Classifies a PM comment into a typed `FeedbackDecision`."""

    __slots__ = ("_llm", "_model")

    def __init__(self, llm: LlmClient, *, model: str) -> None:
        self._llm = llm
        self._model = model

    async def interpret(
        self,
        *,
        comment_text: str,
        awaiting_reply: bool,
        draft_markdown: str,
        prd_markdown: str,
        conversation: tuple[ReviewTurn, ...] = (),
        pending_restatement: str = "",
        metadata: CallMetadata,
    ) -> FeedbackDecision:
        """Interpret one PM comment **in the context of the review conversation** (FR-10).

        Args:
            awaiting_reply: True if the run was parked awaiting a structure-confirmation or
                clarification answer — biases the model toward the CONFIRMATION route.
            conversation: the recent review-ticket transcript (oldest→newest), so a reply is read
                as part of the discussion, not in isolation. The current comment is the last PM turn.
            pending_restatement: the structured feedback the agent last proposed and is awaiting
                confirmation on (state's `pending_feedback`). Lets the model resolve a "yes",
                "yes but…", or "no, I meant…" against a concrete anchor rather than guessing.
        """
        response = await self._llm.complete(
            model=self._model,
            system=load_skill(_ROLE),
            prompt=self._prompt(
                comment_text,
                awaiting_reply,
                draft_markdown,
                prd_markdown,
                conversation,
                pending_restatement,
            ),
            metadata=metadata,
        )
        return self._parse(response.text)

    @staticmethod
    def _prompt(
        comment: str,
        awaiting_reply: bool,
        draft: str,
        prd: str,
        conversation: tuple[ReviewTurn, ...],
        pending_restatement: str,
    ) -> str:
        context = (
            "The draft is currently WAITING on the PM's reply to a question you already asked "
            "(structure-confirmation or clarification). The newest PM comment below is that reply — "
            "read it against the conversation and the restatement you proposed.\n\n"
            if awaiting_reply
            else "The draft is under review; the newest PM comment below is a fresh comment.\n\n"
        )

        transcript = _format_transcript(conversation)
        transcript_block = (
            f"Conversation so far on the review ticket (oldest first):\n---\n{transcript}\n---\n\n"
            if transcript
            else ""
        )
        restatement_block = (
            "The structured feedback you proposed and are awaiting confirmation on:\n"
            f"---\n{pending_restatement.strip()}\n---\n\n"
            if pending_restatement.strip()
            else ""
        )

        return (
            f"{context}"
            f"{transcript_block}"
            f"{restatement_block}"
            f"Newest PM comment (the one to classify now):\n---\n"
            f"{comment.strip() or '(empty comment)'}\n---\n\n"
            f"Current UserDoc draft (Markdown, truncated):\n---\n{draft[:12000]}\n---\n\n"
            f"Source PRD (Markdown, truncated):\n---\n{prd[:12000]}\n---\n\n"
            "Classify this comment per your rubric. Respond with only the JSON object."
        )

    async def classify_deletion_reply(
        self, *, comment_text: str, metadata: CallMetadata
    ) -> DeletionDecision:
        """Interpret the PM's answer to 'was deleting the draft intentional?' (FR-16).

        Returns a typed decision the orchestrator routes on deterministically (AD-16). Ambiguity is
        never resolved by guessing — an unclear reply returns UNCLEAR and the agent re-asks, because
        both wrong actions (restoring an intentional delete, or abandoning a mistaken one) are bad.
        """
        prompt = (
            "You asked the Reviewer PM whether deleting the UserDoc draft page was intentional. "
            "Read their reply and decide what they want:\n"
            "- RESTORE  — it was a mistake / an accident / they want it back / 'restore it' / 'yes "
            "bring it back' / 'no I didn't mean to'.\n"
            "- LEAVE    — it was intentional / on purpose / they want it to stay deleted / 'yes I "
            "meant to' / 'leave it'.\n"
            "- UNCLEAR  — the reply does not clearly indicate either (a question, off-topic, or "
            "genuinely ambiguous).\n\n"
            f"PM reply:\n---\n{comment_text.strip() or '(empty)'}\n---\n\n"
            "Respond with ONLY one word: RESTORE, LEAVE, or UNCLEAR."
        )
        response = await self._llm.complete(
            model=self._model,
            system=(
                "You classify a short human reply into exactly one of RESTORE, LEAVE, or UNCLEAR. "
                "When in doubt, choose UNCLEAR — never guess between RESTORE and LEAVE."
            ),
            prompt=prompt,
            metadata=metadata,
        )
        token = "".join(ch for ch in response.text.strip().lower() if ch.isalpha())
        for decision in (DeletionDecision.RESTORE, DeletionDecision.LEAVE):
            if decision.value in token:
                return decision
        return DeletionDecision.UNCLEAR

    @staticmethod
    def _parse(text: str) -> FeedbackDecision:
        payload = _extract_json(text)
        if payload is None:
            raise AgentError(
                message=f"Feedback interpreter did not return parseable JSON: {text[:200]!r}",
                suggested_fix="Check the feedback_interpreter SKILL.md output contract and model id.",
                operation="feedback_interpreter.parse",
            )
        try:
            route = FeedbackRoute(str(payload.get("route", "")).strip().lower())
        except ValueError as exc:
            raise AgentError(
                message=f"Feedback interpreter returned an unknown route {payload.get('route')!r}.",
                suggested_fix="Route must be one of apply/confirm_structure/clarify/confirmation.",
                operation="feedback_interpreter.parse",
            ) from exc

        trigger_raw = str(payload.get("trigger", "none")).strip().lower() or "none"
        try:
            trigger = ClarificationTrigger(trigger_raw)
        except ValueError:
            trigger = ClarificationTrigger.NONE

        try:
            return FeedbackDecision(
                route=route,
                structured_feedback=str(payload.get("structured_feedback", "")).strip(),
                trigger=trigger,
                question=str(payload.get("question", "")).strip(),
                assumption=str(payload.get("assumption", "")).strip(),
                confirmed=bool(payload.get("confirmed", False)),
            )
        except ValueError as exc:
            # FeedbackDecision's own invariants (e.g. CLARIFY without a trigger) rejected it.
            raise AgentError(
                message=f"Feedback interpreter produced an invalid decision: {exc}",
                suggested_fix="A CLARIFY route must name one of the four FR-08 triggers (EH-08).",
                operation="feedback_interpreter.parse",
            ) from exc


def _format_transcript(conversation: tuple[ReviewTurn, ...]) -> str:
    """Render the transcript as `PM:`/`Agent:` lines, bounding total size for the prompt budget."""
    lines = [
        f"{turn.speaker.value}: {turn.text.strip()}" for turn in conversation if turn.text.strip()
    ]
    text = "\n".join(lines)
    # Keep the most recent context if the discussion is long — the tail is what a reply refers to.
    return text[-6000:]


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.startswith("json"):
            text = text[4:]
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None
