"""Build/refresh the vector index over the vault (S-B6).

Incremental by the same content hash the pull uses (S-B4): a page whose `content_hash` matches the
hash its chunks were embedded from is skipped, so a nightly re-index only embeds what changed. Vectors
are stored as float32 BLOBs in Agent B's own SQLite store (D-49) — no sqlite-vec extension, which this
runtime cannot load. The embedder is injected (a fake in tests); this module never loads a model itself.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from agent_b.config import AgentBConfig
from agent_b.rag.chunker import chunk_text, strip_note_scaffolding
from agent_b.rag.embedder import Embedder
from agent_b.repository import AgentBRepository


@dataclass(frozen=True, slots=True)
class IndexStats:
    embedded: int = 0
    skipped: int = 0
    chunks: int = 0


def index_vault(repo: AgentBRepository, embedder: Embedder, config: AgentBConfig) -> IndexStats:
    """Embed changed pages' chunks; leave unchanged pages untouched. Returns per-run counts."""
    embedded = skipped = 0
    for doc in repo.all_documents():  # live docs only (tombstoned pages' chunks are already gone)
        page_id = str(doc["page_id"])
        content_hash = str(doc["content_hash"])
        if repo.chunk_page_hash(page_id) == content_hash:
            skipped += 1
            continue
        body = strip_note_scaffolding(str(doc["base_content"] or ""))
        texts = chunk_text(
            body,
            chunk_chars=config.embeddings.chunk_chars,
            overlap=config.embeddings.chunk_overlap,
        )
        if not texts:
            repo.delete_chunks(page_id)
            continue
        vectors = embedder.embed(texts)
        rows = [
            (text, np.asarray(vec, dtype=np.float32).tobytes(), len(vec))
            for text, vec in zip(texts, vectors, strict=True)
        ]
        repo.replace_chunks(page_id, page_content_hash=content_hash, chunks=rows)
        embedded += 1
    return IndexStats(embedded=embedded, skipped=skipped, chunks=repo.chunk_count())
