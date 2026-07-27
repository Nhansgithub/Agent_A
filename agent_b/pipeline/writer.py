"""Write rendered notes into the vault and record them in the store (S-B1).

The vault file is the human/Obsidian artifact; the SQLite row is the bookkeeping that makes the next
run idempotent (content hash) and the graph queryable. A note whose on-disk content already matches is
left untouched, so an unchanged re-pull does not rewrite the vault (D-41 / AD-28).
"""

from __future__ import annotations

from pathlib import Path

from agent_b.pipeline.convert import RenderedNote
from agent_b.repository import AgentBRepository


class VaultWriter:
    __slots__ = ("_repo", "_vault_dir")

    def __init__(self, vault_dir: str, repo: AgentBRepository) -> None:
        self._vault_dir = Path(vault_dir)
        self._repo = repo

    def write(self, note: RenderedNote) -> bool:
        """Write the base note if its source changed, then upsert its row. Returns whether it changed.

        Change is judged by the stored `content_hash` (a signature of the source page), **not** the
        file bytes — the linker (S-B2) rewrites the file to add `[[links]]`, so comparing file bytes
        would make every unchanged page look changed on the next pull.
        """
        existing = self._repo.get_document(note.page_id)
        path = self._vault_dir / note.vault_path
        changed = existing is None or existing.get("content_hash") != note.content_hash
        if changed or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(note.content, encoding="utf-8")
        # A retitled page gets a new slug → a new vault_path; drop the stale file at the old path so a
        # rename does not leave an orphan note behind (S-B4).
        old_path = str(existing["vault_path"]) if existing else None
        if old_path and old_path != note.vault_path:
            (self._vault_dir / old_path).unlink(missing_ok=True)
        self._repo.upsert_document(
            page_id=note.page_id,
            space_key=note.space_key,
            title=note.title,
            doc_type=note.doc_type,
            vault_path=note.vault_path,
            content_hash=note.content_hash,
            base_content=note.content,
            parent_id=note.parent_id or None,
            source_url=note.source_url,
        )
        return changed
