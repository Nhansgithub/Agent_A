"""The Answerer — Agent B's grounded, citing, refusing Q&A agent (S-B6, AD-17, AD-20, AD-30).

Given a question and the retrieved passages, it answers **only** from those passages, with inline `[n]`
citations, and **refuses** (a fixed sentinel) when they do not contain the answer — the same 0-fabrication
discipline the Agent A classifier holds. The model id comes from config (AD-17); the call is traced via
the shared `LlmClient` (AD-20). This layer owns the LLM (AD-6/AD-27 mirror); retrieval and logging live
outside it (`agent_b.rag`, `agent_b.qa`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_b.agents.skills import load_skill
from app.agents.llm import CallMetadata, LlmClient

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agent_b.rag.retriever import Hit

_ROLE = "answerer"

#: The exact phrase the agent emits when the context cannot answer the question. The Q&A service keys
#: refusal off this, and the eval (S-B9) checks unanswerable questions produce it.
REFUSAL = "I don't have a doc on that."


@dataclass(frozen=True, slots=True)
class AnswerResult:
    text: str
    refused: bool


class AnswererAgent:
    __slots__ = ("_llm", "_model")

    def __init__(self, llm: LlmClient, *, model: str) -> None:
        self._llm = llm
        self._model = model

    async def answer(
        self, question: str, hits: Sequence[Hit], *, metadata: CallMetadata
    ) -> AnswerResult:
        if not hits:
            return AnswerResult(text=REFUSAL, refused=True)  # nothing retrieved → never fabricate
        response = await self._llm.complete(
            model=self._model,
            system=load_skill(_ROLE),
            prompt=_build_prompt(question, hits),
            metadata=metadata,
        )
        text = response.text.strip()
        refused = not text or REFUSAL.lower() in text.lower()
        return AnswerResult(text=text or REFUSAL, refused=refused)


def _build_prompt(question: str, hits: Sequence[Hit]) -> str:
    lines = ["Context passages — cite them inline by their [n] number:", ""]
    for index, hit in enumerate(hits, start=1):
        lines.append(f"[{index}] {hit.title} — {hit.source_url}")
        lines.append(hit.snippet.strip())
        lines.append("")
    lines.append(f"Question: {question}")
    lines.append(
        "Answer using ONLY the passages above, citing each claim with its [n]. If the passages do "
        f"not contain the answer, reply with exactly: {REFUSAL}"
    )
    return "\n".join(lines)
