"""Retrieve grounding for a question: vector search + graph expansion, with a refusal floor (S-B6).

Two stages, as AD-30 requires the answer to be grounded and to refuse rather than guess:

  1. **Vector search.** Cosine of the query embedding against every stored chunk (a brute-force scan —
     fine for an internal corpus, D-49). The best-scoring chunk per page ranks the pages; the top page's
     score is the confidence gate. If it is below `rag.min_score`, retrieval **refuses** — no hits go
     forward, so the answerer has nothing to fabricate from.
  2. **Graph expansion.** Around the vector hits, pull in pages a few `[[link]]` hops away along the
     deterministic edges (hierarchy + restored — never the unverified `suggested` edges). A parent PRD
     or a linked design often holds the context a single chunk match misses.

Returns lightweight hits (page id + title + Confluence URL + snippet + score), which the answerer turns
into inline citations. The embedder + repo are injected; this module runs no SQL and loads no model.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from agent_b.config import AgentBConfig
from agent_b.rag.embedder import Embedder
from agent_b.repository import AgentBRepository

_GROUNDING_EDGE_KINDS = frozenset({"hierarchy", "restored"})


@dataclass(frozen=True, slots=True)
class Hit:
    page_id: str
    title: str
    source_url: str
    score: float
    snippet: str
    via: str  # "vector" | "graph"


@dataclass(frozen=True, slots=True)
class Retrieval:
    hits: tuple[Hit, ...]
    refused: bool
    top_score: float


def retrieve(
    repo: AgentBRepository, embedder: Embedder, query: str, config: AgentBConfig
) -> Retrieval:
    chunks = repo.all_chunks()
    if not chunks:
        return Retrieval(hits=(), refused=True, top_score=0.0)

    matrix = np.stack([np.frombuffer(c["embedding"], dtype=np.float32) for c in chunks])
    query_vec = np.asarray(embedder.embed([query])[0], dtype=np.float32)
    scores = _cosine(matrix, query_vec)

    best: dict[str, tuple[float, str]] = {}  # page_id -> (score, snippet)
    for chunk, score in zip(chunks, scores, strict=True):
        page_id = str(chunk["page_id"])
        value = (float(score), str(chunk["text"]))
        if page_id not in best or value[0] > best[page_id][0]:
            best[page_id] = value

    ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)
    top_score = ranked[0][1][0]
    if top_score < config.rag.min_score:
        return Retrieval(hits=(), refused=True, top_score=top_score)

    docs = {str(d["page_id"]): d for d in repo.all_documents()}
    # A page is a vector hit only if its own best chunk clears the floor — a page dragged into the
    # top-k by a weak match is not grounding, it is noise; the graph is the only honest way in for it.
    vector_pages = [
        page_id
        for page_id, (score, _) in ranked[: config.rag.top_k]
        if score >= config.rag.min_score
    ]
    graph_pages = _expand(repo, vector_pages, hops=config.rag.graph_expansion_hops)

    hits: list[Hit] = []
    seen: set[str] = set()
    for page_id in [*vector_pages, *graph_pages]:
        if page_id in seen or page_id not in docs:
            continue
        seen.add(page_id)
        doc = docs[page_id]
        score, snippet = best.get(page_id, (0.0, _lead(str(doc["base_content"] or ""))))
        hits.append(
            Hit(
                page_id=page_id,
                title=str(doc["title"]),
                source_url=str(doc["source_url"] or ""),
                score=round(score, 4),
                snippet=snippet,
                via="vector" if page_id in set(vector_pages) else "graph",
            )
        )
    return Retrieval(hits=tuple(hits), refused=False, top_score=round(top_score, 4))


def _cosine(matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
    matrix_norm = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-8)
    vector_norm = vector / (np.linalg.norm(vector) + 1e-8)
    return matrix_norm @ vector_norm


def _expand(repo: AgentBRepository, seeds: list[str], *, hops: int) -> list[str]:
    """Pages reachable within `hops` deterministic edges of a seed, excluding the seeds themselves."""
    if hops <= 0:
        return []
    adjacency: dict[str, set[str]] = {}
    for edge in repo.all_links():
        if str(edge["kind"]) not in _GROUNDING_EDGE_KINDS:
            continue
        a, b = str(edge["from_page_id"]), str(edge["to_page_id"])
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)  # walk edges in both directions

    frontier = set(seeds)
    visited = set(seeds)
    found: list[str] = []
    for _ in range(hops):
        nxt: set[str] = set()
        for page_id in frontier:
            for neighbour in adjacency.get(page_id, ()):
                if neighbour not in visited:
                    visited.add(neighbour)
                    nxt.add(neighbour)
                    found.append(neighbour)
        frontier = nxt
        if not frontier:
            break
    return found


def _lead(base_content: str, limit: int = 280) -> str:
    """A short lead for a graph-expanded page that had no strong chunk match of its own."""
    from agent_b.rag.chunker import strip_note_scaffolding

    return strip_note_scaffolding(base_content)[:limit].strip()
