"""Agent B RAG index (S-B6): chunk → embed → store vectors → retrieve (vector + graph + refusal).

Pure retrieval plumbing — no LLM here (the Answerer agent owns that, AD-27) and no raw SQL (the
repository owns that, AD-32). The embedder is injected so the offline suite runs without a model.
"""

from agent_b.rag.chunker import chunk_text, strip_note_scaffolding
from agent_b.rag.embedder import Embedder, FastEmbedEmbedder
from agent_b.rag.index import IndexStats, index_vault
from agent_b.rag.retriever import Hit, Retrieval, retrieve

__all__ = [
    "Embedder",
    "FastEmbedEmbedder",
    "Hit",
    "IndexStats",
    "Retrieval",
    "chunk_text",
    "index_vault",
    "retrieve",
    "strip_note_scaffolding",
]
