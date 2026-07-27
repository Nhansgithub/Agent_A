"""The Answerer — Agent B's grounded, citing, conversational Q&A agent (S-B6, AD-17, AD-20, AD-30).

Given the user's message, the retrieved passages, and the KB catalog, it replies like a warm, helpful
teammate: it answers real questions **only** from the passages (inline `[n]` citations); greets and
makes small talk; lists/suggests documents from the catalog; and, when nothing answers a real question,
says so kindly and points at related docs — but it **never invents document facts** (AD-30). The persona
lives in `SKILL.md` (the tuning surface). The model id comes from config (AD-17); every call is traced
via the shared `LlmClient` (AD-20). This layer owns the LLM (AD-6/AD-27 mirror); retrieval, the catalog,
and logging live outside it (`agent_b.rag`, `agent_b.qa`).
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
_MAX_CATALOG = 100  # cap the doc list in the prompt; a small internal KB stays well under this

#: Shown only if the model returns nothing at all (an API hiccup) — never the normal "no doc" path,
#: which the SKILL handles conversationally.
_FALLBACK = "Sorry, I hit a snag just now — could you try asking again?"


@dataclass(frozen=True, slots=True)
class AnswerResult:
    text: str


class AnswererAgent:
    __slots__ = ("_llm", "_model")

    def __init__(self, llm: LlmClient, *, model: str) -> None:
        self._llm = llm
        self._model = model

    async def answer(
        self,
        question: str,
        hits: Sequence[Hit],
        catalog: Sequence[tuple[str, str]],
        *,
        metadata: CallMetadata,
        history: Sequence[tuple[str, str]] = (),
    ) -> AnswerResult:
        """Craft a reply. Always calls the model so greetings/refusals are warm; the SKILL keeps it
        grounded (it may only state doc facts that appear in `hits`). `catalog` = (title, doc_type) of
        every live document, for listing and 'closest related' suggestions. `history` = recent
        (question, answer) turns of this conversation, so follow-ups like 'why?' / 'the second one'
        resolve — memory helps it *understand* the question; it never becomes a source of doc facts."""
        response = await self._llm.complete(
            model=self._model,
            system=load_skill(_ROLE),
            prompt=_build_prompt(question, hits, catalog, history),
            metadata=metadata,
        )
        return AnswerResult(text=response.text.strip() or _FALLBACK)


def _build_prompt(
    question: str,
    hits: Sequence[Hit],
    catalog: Sequence[tuple[str, str]],
    history: Sequence[tuple[str, str]] = (),
) -> str:
    lines: list[str] = []
    if history:
        lines.append("Recent conversation (oldest first) — use it to resolve follow-ups:")
        for prev_q, prev_a in history:
            lines.append(f"User: {prev_q}")
            lines.append(f"You: {prev_a}")
        lines.append("")
    if catalog:
        lines.append("Catalog — the documents currently in the knowledge base:")
        for title, doc_type in list(catalog)[:_MAX_CATALOG]:
            lines.append(f"- {title} ({doc_type})")
        lines.append("")
    if hits:
        lines.append("Passages retrieved for this message — cite each claim inline by its [n]:")
        lines.append("")
        for index, hit in enumerate(hits, start=1):
            lines.append(f"[{index}] {hit.title} — {hit.source_url}")
            lines.append(hit.snippet.strip())
            lines.append("")
    else:
        lines.append("Passages retrieved for this message: (none matched)")
        lines.append("")
    lines.append(f"User message: {question}")
    return "\n".join(lines)
