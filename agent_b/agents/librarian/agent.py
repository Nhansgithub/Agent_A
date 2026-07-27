"""The Librarian — Agent B's LLM curator (S-B3, AD-17, AD-20).

Given a summary of the corpus, it proposes a tag taxonomy per document, a few Maps-of-Content (topic
hubs), and *suggested* cross-document links. Suggestions are advisory — the materializer quarantines
them into a labelled block, never inlining them into prose (AD-30). The model id comes from config
(AD-17); every call is traced via the shared `LlmClient` (AD-20). Parsing is defensive: unknown ids,
self-links, and duplicates are dropped, so a hallucinated reference can never enter the graph.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent_b.agents.skills import load_skill
from app.agents.llm import CallMetadata, LlmClient

_ROLE = "librarian"
_MAX_SNIPPET = 800


@dataclass(frozen=True, slots=True)
class DocSummary:
    page_id: str
    title: str
    doc_type: str
    snippet: str


@dataclass(frozen=True, slots=True)
class Moc:
    title: str
    page_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Curation:
    tags: dict[str, tuple[str, ...]] = field(default_factory=dict)
    suggested: tuple[tuple[str, str], ...] = ()  # (from_page_id, to_page_id)
    mocs: tuple[Moc, ...] = ()


class LibrarianAgent:
    __slots__ = ("_llm", "_model")

    def __init__(self, llm: LlmClient, *, model: str) -> None:
        self._llm = llm
        self._model = model

    async def curate(self, corpus: list[DocSummary], *, metadata: CallMetadata) -> Curation:
        if not corpus:
            return Curation()
        response = await self._llm.complete(
            model=self._model,
            system=load_skill(_ROLE),
            prompt=_build_prompt(corpus),
            metadata=metadata,
        )
        return parse_curation(response.text, valid_ids={d.page_id for d in corpus})


def _build_prompt(corpus: list[DocSummary]) -> str:
    lines = ["Corpus (one line per document):", ""]
    for d in corpus:
        lines.append(f"- id={d.page_id} | type={d.doc_type} | title={d.title!r}")
        if d.snippet:
            lines.append(f"    {d.snippet[:_MAX_SNIPPET]}")
    lines.append("")
    lines.append("Return the JSON described in your instructions. Use only the page ids above.")
    return "\n".join(lines)


def parse_curation(text: str, *, valid_ids: set[str]) -> Curation:
    raw = _extract_json(text)
    if raw is None:
        return Curation()

    tags: dict[str, tuple[str, ...]] = {}
    for page_id, values in (raw.get("tags") or {}).items():
        if page_id in valid_ids and isinstance(values, list):
            cleaned = tuple(dict.fromkeys(str(v).strip().lower() for v in values if str(v).strip()))
            if cleaned:
                tags[page_id] = cleaned

    suggested: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for link in raw.get("suggested_links") or []:
        if not isinstance(link, dict):
            continue
        frm, to = str(link.get("from") or ""), str(link.get("to") or "")
        if frm in valid_ids and to in valid_ids and frm != to and (frm, to) not in seen:
            seen.add((frm, to))
            suggested.append((frm, to))

    mocs: list[Moc] = []
    for moc in raw.get("mocs") or []:
        if not isinstance(moc, dict):
            continue
        title = str(moc.get("title") or "").strip()
        ids = tuple(str(i) for i in (moc.get("page_ids") or []) if str(i) in valid_ids)
        if title and ids:
            mocs.append(Moc(title=title, page_ids=ids))

    return Curation(tags=tags, suggested=tuple(suggested), mocs=tuple(mocs))


def _extract_json(text: str) -> dict[str, Any] | None:
    candidates = [text.strip()]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None
