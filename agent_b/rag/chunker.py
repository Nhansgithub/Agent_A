"""Split a note's body into embeddable chunks (S-B6).

Deterministic char-window chunking with overlap: a paragraph split across a boundary stays retrievable
because adjacent chunks share `chunk_overlap` characters. Determinism matters for the same reason the
note render is deterministic — the incremental indexer (S-B4 hash-diff) must produce identical chunks
for unchanged content so it can skip re-embedding, and the offline eval must be reproducible.

Frontmatter and the linker's navigation block are stripped before chunking: they are metadata, not the
prose a question is answered from, and embedding them would dilute retrieval.
"""

from __future__ import annotations

import re

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_RELATED_BLOCK = re.compile(
    r"<!-- agent-b:related:start -->.*?<!-- agent-b:related:end -->", re.DOTALL
)


def strip_note_scaffolding(note: str) -> str:
    """Drop YAML frontmatter and the `## Related` navigation block — keep the prose."""
    body = _FRONTMATTER.sub("", note)
    body = _RELATED_BLOCK.sub("", body)
    return body.strip()


def chunk_text(text: str, *, chunk_chars: int, overlap: int) -> list[str]:
    """A stable list of overlapping windows. Empty/short text → 0 or 1 chunk."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]
    step = max(1, chunk_chars - overlap)
    chunks: list[str] = []
    start = 0
    while start < len(text):
        window = text[start : start + chunk_chars].strip()
        if window:
            chunks.append(window)
        if start + chunk_chars >= len(text):
            break
        start += step
    return chunks
