"""The Classifier agent (FR-03, AD-17).

Reads a title-matching page's content and confirms it is a genuine finalized PRD, per the rubric in
this package's `SKILL.md`. ACCEPT advances the run to `confirmed`; REJECT routes to the FR-02a
rename/confirm path (EH-07) — the same handling as a title mismatch, because in both cases a human
must intervene rather than the agent guessing.

The model id comes from config (`system.models.classifier`, AD-17/AD-4) — never a literal here.
Changing it invalidates the held-out eval, so it must be a visible config change.

The one hard, measurable bar for this agent is **0 FP / 0 FN on the held-out fixture set** (Story
2.4). This module is deliberately thin: parse a page into a decision. All the judgement lives in the
prompt, so tuning is a prompt edit, and the eval harness measures exactly this function.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from app.agents.llm import CallMetadata, LlmClient
from app.agents.skills import load_skill
from app.domain.errors import AgentError

_ROLE = "classifier"

#: Guardrail on the page body sent to the model. A PRD large enough to exceed this is, for the demo,
#: self-evidently substantive — truncating the tail does not change the ACCEPT/REJECT call and keeps
#: one PRD's payload bounded in memory (AD-21).
_MAX_BODY_CHARS = 60_000


class ClassifierDecision(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    decision: ClassifierDecision
    confidence: str
    reason: str

    @property
    def accepted(self) -> bool:
        return self.decision is ClassifierDecision.ACCEPT


class ClassifierAgent:
    """Confirms a page is a genuine finalized PRD."""

    __slots__ = ("_llm", "_model")

    def __init__(self, llm: LlmClient, *, model: str) -> None:
        """
        Args:
            llm: the shared LLM runtime (AD-6). Traces every call (AD-20).
            model: the classifier model id, from `system.models.classifier` (AD-17).
        """
        self._llm = llm
        self._model = model

    async def classify(
        self, *, title: str, body_markdown: str, metadata: CallMetadata
    ) -> ClassificationResult:
        """Classify one page. `body_markdown` is the page converted from storage format."""
        # Determinism comes from the rubric in SKILL.md, not a temperature knob — the pinned models
        # reject sampling params (D-15).
        response = await self._llm.complete(
            model=self._model,
            system=load_skill(_ROLE),
            prompt=self._prompt(title, body_markdown),
            metadata=metadata,
        )
        return self._parse(response.text)

    @staticmethod
    def _prompt(title: str, body_markdown: str) -> str:
        body = body_markdown.strip()
        if len(body) > _MAX_BODY_CHARS:
            body = body[:_MAX_BODY_CHARS] + "\n\n[...truncated for length...]"
        if not body:
            body = "(the page has no body content)"
        return (
            f"Page title: {title}\n\n"
            f"Page content (Markdown):\n---\n{body}\n---\n\n"
            "Classify this page per your rubric. Respond with only the JSON object."
        )

    @staticmethod
    def _parse(text: str) -> ClassificationResult:
        """Parse the model's JSON, tolerating prose or code fences around it.

        A parse failure is treated as an error, not a silent REJECT: it means the classifier is
        misbehaving, which the eval must catch loudly rather than have it masquerade as a legitimate
        rejection and quietly pass the 0-FN bar.
        """
        payload = _extract_json(text)
        if payload is None:
            raise AgentError(
                message=f"Classifier did not return parseable JSON: {text[:200]!r}",
                suggested_fix="Check the classifier SKILL.md output contract and the pinned model id.",
                operation="classifier.parse",
            )
        raw_decision = str(payload.get("decision", "")).strip().upper()
        if raw_decision not in (ClassifierDecision.ACCEPT, ClassifierDecision.REJECT):
            raise AgentError(
                message=f"Classifier returned an unrecognised decision {raw_decision!r}.",
                suggested_fix="The decision must be exactly ACCEPT or REJECT (classifier SKILL.md).",
                operation="classifier.parse",
            )
        return ClassificationResult(
            decision=ClassifierDecision(raw_decision),
            confidence=str(payload.get("confidence", "unknown")).strip().lower(),
            reason=str(payload.get("reason", "")).strip(),
        )


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        # Strip a ```json ... ``` fence.
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.startswith("json"):
            text = text[4:]
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    # Last resort: the first {...} span in the text.
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None
