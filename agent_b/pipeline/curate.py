"""Apply the Librarian's curation to the vault (S-B3): tags, suggested links, MOC notes.

The LLM decision is cached by a corpus hash (`llm_cache`), so an unchanged corpus is never re-curated
— the vault does not churn and the API is not re-billed (AD-20/AD-21). Suggested links are recorded
with source='llm'; the materializer renders them only in the quarantined block (AD-30). The curator +
repo are injected; this is the one pipeline seam that drives an LLM agent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from agent_b.agents.librarian import Curation, DocSummary, LibrarianAgent, Moc
from agent_b.config import AgentBConfig
from agent_b.pipeline.convert import slugify
from agent_b.repository import AgentBRepository
from app.agents.llm import CallMetadata

_CURATION = "curation"


@dataclass(frozen=True, slots=True)
class CurationStats:
    tagged: int = 0
    suggested: int = 0
    mocs: int = 0
    from_cache: bool = False


async def curate_vault(
    librarian: LibrarianAgent,
    repo: AgentBRepository,
    config: AgentBConfig,
    *,
    metadata: CallMetadata,
) -> CurationStats:
    docs = repo.all_documents()
    summaries = [
        DocSummary(
            page_id=str(d["page_id"]),
            title=str(d["title"]),
            doc_type=str(d["doc_type"]),
            snippet=_snippet(str(d["base_content"] or "")),
        )
        for d in docs
    ]

    corpus_hash = _corpus_hash(summaries)
    cached = repo.get_llm_cache(corpus_hash)
    if cached is not None:
        curation = _deserialize(cached)
        from_cache = True
    else:
        curation = await librarian.curate(summaries, metadata=metadata)
        repo.set_llm_cache(corpus_hash, kind=_CURATION, output=_serialize(curation))
        from_cache = False

    repo.clear_links(source="llm")
    for frm, to in curation.suggested:
        repo.add_link(frm, to, kind="suggested", source="llm")
    for page_id, tags in curation.tags.items():
        repo.set_tags(page_id, list(tags))

    titles = {str(d["page_id"]): str(d["title"]) for d in docs}
    paths = {str(d["page_id"]): str(d["vault_path"]) for d in docs}
    _write_mocs(config.vault_dir, curation.mocs, titles, paths)

    return CurationStats(
        tagged=len(curation.tags),
        suggested=len(curation.suggested),
        mocs=len(curation.mocs),
        from_cache=from_cache,
    )


def _snippet(base_content: str, limit: int = 800) -> str:
    body = base_content
    if body.startswith("---"):
        parts = body.split("\n---\n", 1)
        body = parts[1] if len(parts) > 1 else body
    return body.strip()[:limit]


def _corpus_hash(summaries: list[DocSummary]) -> str:
    digest = hashlib.sha256()
    for d in sorted(summaries, key=lambda s: s.page_id):
        digest.update(f"{d.page_id}\x00{d.title}\x00{d.doc_type}\x00{d.snippet}\x00".encode())
    return digest.hexdigest()


def _serialize(curation: Curation) -> str:
    return json.dumps(
        {
            "tags": {k: list(v) for k, v in curation.tags.items()},
            "suggested": [list(s) for s in curation.suggested],
            "mocs": [{"title": m.title, "page_ids": list(m.page_ids)} for m in curation.mocs],
        }
    )


def _deserialize(text: str) -> Curation:
    raw = json.loads(text)
    return Curation(
        tags={k: tuple(v) for k, v in (raw.get("tags") or {}).items()},
        suggested=tuple((str(a), str(b)) for a, b in (raw.get("suggested") or [])),
        mocs=tuple(
            Moc(title=str(m["title"]), page_ids=tuple(str(i) for i in m["page_ids"]))
            for m in (raw.get("mocs") or [])
        ),
    )


def _write_mocs(
    vault_dir: str, mocs: tuple[Moc, ...], titles: dict[str, str], paths: dict[str, str]
) -> None:
    root = Path(vault_dir) / "notes"
    for moc in mocs:
        slug = slugify(moc.title) or "moc"
        lines = [
            "---",
            f'title: "{moc.title}"',
            'doc_type: "moc"',
            "---",
            "",
            f"# {moc.title}",
            "",
            "Documents in this area:",
            "",
        ]
        for pid in moc.page_ids:
            if pid in paths:
                base = Path(paths[pid]).stem
                lines.append(f"- [[{base}|{titles.get(pid, pid)}]]")
        root.mkdir(parents=True, exist_ok=True)
        (root / f"moc-{slug}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
