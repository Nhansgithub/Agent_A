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
from app.domain.feedback import ClarificationTrigger, FeedbackDecision, FeedbackRoute

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
        metadata: CallMetadata,
    ) -> FeedbackDecision:
        """Interpret one PM comment.

        Args:
            awaiting_reply: True if the run was parked awaiting a structure-confirmation or
                clarification answer — biases the model toward the CONFIRMATION route.
        """
        response = await self._llm.complete(
            model=self._model,
            system=load_skill(_ROLE),
            prompt=self._prompt(comment_text, awaiting_reply, draft_markdown, prd_markdown),
            metadata=metadata,
        )
        return self._parse(response.text)

    @staticmethod
    def _prompt(comment: str, awaiting_reply: bool, draft: str, prd: str) -> str:
        context = (
            "The draft is currently WAITING on the PM's reply to a question you already asked "
            "(structure-confirmation or clarification). This comment is most likely that reply.\n\n"
            if awaiting_reply
            else "The draft is under review; this is a fresh comment from the PM.\n\n"
        )
        return (
            f"{context}"
            f"PM comment:\n---\n{comment.strip() or '(empty comment)'}\n---\n\n"
            f"Current UserDoc draft (Markdown, truncated):\n---\n{draft[:12000]}\n---\n\n"
            f"Source PRD (Markdown, truncated):\n---\n{prd[:12000]}\n---\n\n"
            "Classify this comment per your rubric. Respond with only the JSON object."
        )

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
