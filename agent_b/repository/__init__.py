"""The only `agent_b` layer that runs SQL (AD-2 mirror, AD-32).

`AgentBRepository` is the injection point for the ingestion pipeline, the RAG index, and the Slack
surface. B0 ships the store + the document round-trip the later stories build on; the link / Q&A /
pull-run APIs land with the stories that use them.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from agent_b.repository.database import AgentBDatabase, to_iso, utc_now

__all__ = ["AgentBDatabase", "AgentBRepository"]


class AgentBRepository:
    """Facade over Agent B's SQLite store."""

    __slots__ = ("_db",)

    def __init__(self, database: AgentBDatabase) -> None:
        self._db = database

    @classmethod
    def open(cls, path: str | Path) -> AgentBRepository:
        return cls(AgentBDatabase(path))

    # -- documents (the idempotency backbone, S-B1 / S-B4) -------------------------------------

    def upsert_document(
        self,
        *,
        page_id: str,
        space_key: str,
        title: str,
        doc_type: str,
        vault_path: str,
        content_hash: str,
        base_content: str = "",
        parent_id: str | None = None,
        source_url: str | None = None,
    ) -> None:
        """Insert or update one document row, clearing any prior tombstone."""
        params = {
            "page_id": page_id,
            "space_key": space_key,
            "title": title,
            "doc_type": doc_type,
            "parent_id": parent_id,
            "vault_path": vault_path,
            "content_hash": content_hash,
            "base_content": base_content,
            "source_url": source_url,
            "pulled_at": to_iso(utc_now()),
        }
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO documents
                    (page_id, space_key, title, doc_type, parent_id, vault_path,
                     content_hash, base_content, source_url, pulled_at, deleted_at)
                VALUES
                    (:page_id, :space_key, :title, :doc_type, :parent_id, :vault_path,
                     :content_hash, :base_content, :source_url, :pulled_at, NULL)
                ON CONFLICT(page_id) DO UPDATE SET
                    space_key    = excluded.space_key,
                    title        = excluded.title,
                    doc_type     = excluded.doc_type,
                    parent_id    = excluded.parent_id,
                    vault_path   = excluded.vault_path,
                    content_hash = excluded.content_hash,
                    base_content = excluded.base_content,
                    source_url   = excluded.source_url,
                    pulled_at    = excluded.pulled_at,
                    deleted_at   = NULL
                """,
                params,
            )

    def get_document(self, page_id: str) -> dict[str, object] | None:
        with self._db.read() as conn:
            row = conn.execute("SELECT * FROM documents WHERE page_id = ?", (page_id,)).fetchone()
        return dict(row) if row is not None else None

    def all_documents(self, *, include_deleted: bool = False) -> list[dict[str, object]]:
        query = "SELECT * FROM documents"
        if not include_deleted:
            query += " WHERE deleted_at IS NULL"
        query += " ORDER BY page_id"
        with self._db.read() as conn:
            return [dict(row) for row in conn.execute(query)]

    def document_ids(self, *, include_deleted: bool = False) -> list[str]:
        query = "SELECT page_id FROM documents"
        if not include_deleted:
            query += " WHERE deleted_at IS NULL"
        query += " ORDER BY page_id"
        with self._db.read() as conn:
            return [row["page_id"] for row in conn.execute(query)]

    def tombstone_documents(self, page_ids: list[str]) -> int:
        """Mark pages that vanished from Confluence as deleted (S-B4 deletion reconcile).

        Sets `deleted_at` (so the row survives for audit and can un-tombstone on a re-add) and drops
        every edge touching the page from `links` — a link to a tombstoned note would render a dead
        `[[wikilink]]`. Returns the count actually tombstoned (skips ids already tombstoned)."""
        if not page_ids:
            return 0
        now = to_iso(utc_now())
        tombstoned = 0
        with self._db.transaction() as conn:
            for page_id in page_ids:
                cursor = conn.execute(
                    "UPDATE documents SET deleted_at = ? WHERE page_id = ? AND deleted_at IS NULL",
                    (now, page_id),
                )
                if cursor.rowcount:
                    tombstoned += 1
                    conn.execute(
                        "DELETE FROM links WHERE from_page_id = ? OR to_page_id = ?",
                        (page_id, page_id),
                    )
                    conn.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))
        return tombstoned

    # -- links (the graph, S-B2/S-B3) ----------------------------------------------------------

    def clear_links(self, *, source: str | None = None) -> None:
        """Drop edges. `source=None` clears all; the materializer clears only `deterministic` so the
        LLM-suggested edges (S-B3) survive its rebuild, and vice-versa."""
        with self._db.transaction() as conn:
            if source is None:
                conn.execute("DELETE FROM links")
            else:
                conn.execute("DELETE FROM links WHERE source = ?", (source,))

    def add_link(
        self,
        from_page_id: str,
        to_page_id: str,
        *,
        kind: str,
        source: str,
        confidence: float | None = None,
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO links
                    (from_page_id, to_page_id, kind, source, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (from_page_id, to_page_id, kind, source, confidence, to_iso(utc_now())),
            )

    def all_links(self) -> list[dict[str, object]]:
        with self._db.read() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM links ORDER BY from_page_id, to_page_id, kind"
                )
            ]

    def links_from(self, page_id: str, *, kind: str | None = None) -> list[dict[str, object]]:
        query = "SELECT * FROM links WHERE from_page_id = ?"
        params: list[object] = [page_id]
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        query += " ORDER BY to_page_id"
        with self._db.read() as conn:
            return [dict(row) for row in conn.execute(query, params)]

    # -- curation (S-B3) -----------------------------------------------------------------------

    def set_tags(self, page_id: str, tags: list[str]) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE documents SET tags = ? WHERE page_id = ?", (json.dumps(list(tags)), page_id)
            )

    def get_llm_cache(self, input_hash: str) -> str | None:
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT output FROM llm_cache WHERE input_hash = ?", (input_hash,)
            ).fetchone()
        return str(row["output"]) if row is not None else None

    def set_llm_cache(self, input_hash: str, *, kind: str, output: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (input_hash, kind, output, created_at) "
                "VALUES (?, ?, ?, ?)",
                (input_hash, kind, output, to_iso(utc_now())),
            )

    # -- pull runs (the nightly job's ledger, S-B4) --------------------------------------------

    def begin_pull_run(self) -> str:
        """Open a `running` row for one scheduled pull; returns its id (correlates the run's trace)."""
        run_id = uuid.uuid4().hex
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO pull_runs (run_id, started_at, status) VALUES (?, ?, 'running')",
                (run_id, to_iso(utc_now())),
            )
        return run_id

    def finish_pull_run(
        self,
        run_id: str,
        *,
        status: str,
        added: int = 0,
        changed: int = 0,
        deleted: int = 0,
    ) -> None:
        """Close a pull run with its outcome + counts (`ok` | `error`), for observability of the job."""
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE pull_runs SET finished_at = ?, status = ?, added = ?, changed = ?, "
                "deleted = ? WHERE run_id = ?",
                (to_iso(utc_now()), status, added, changed, deleted, run_id),
            )

    def get_pull_run(self, run_id: str) -> dict[str, object] | None:
        with self._db.read() as conn:
            row = conn.execute("SELECT * FROM pull_runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row is not None else None

    def all_pull_runs(self) -> list[dict[str, object]]:
        with self._db.read() as conn:
            return [
                dict(row)
                for row in conn.execute("SELECT * FROM pull_runs ORDER BY started_at, run_id")
            ]

    # -- chunks (the RAG vector index, S-B6) ---------------------------------------------------

    def chunk_page_hash(self, page_id: str) -> str | None:
        """The `page_content_hash` the page's chunks were embedded from — lets the indexer skip a
        page whose content is unchanged since it was last embedded (incremental, S-B4)."""
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT page_content_hash FROM chunks WHERE page_id = ? LIMIT 1", (page_id,)
            ).fetchone()
        return str(row["page_content_hash"]) if row is not None else None

    def replace_chunks(
        self,
        page_id: str,
        *,
        page_content_hash: str,
        chunks: list[tuple[str, bytes, int]],
    ) -> None:
        """Atomically swap a page's chunks. Each chunk is `(text, embedding_bytes, dim)`."""
        now = to_iso(utc_now())
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))
            conn.executemany(
                "INSERT INTO chunks "
                "(page_id, chunk_index, text, embedding, dim, page_content_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (page_id, index, text, embedding, dim, page_content_hash, now)
                    for index, (text, embedding, dim) in enumerate(chunks)
                ],
            )

    def delete_chunks(self, page_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))

    def all_chunks(self) -> list[dict[str, object]]:
        """Every chunk of every live document, for the brute-force cosine scan (D-49). Tombstoned
        pages have had their chunks deleted, so this is already scoped to the live corpus."""
        with self._db.read() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT id, page_id, chunk_index, text, embedding, dim FROM chunks "
                    "ORDER BY page_id, chunk_index"
                )
            ]

    def chunk_count(self) -> int:
        with self._db.read() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"])

    # -- Q&A log (every Slack answer, AD-20 + the S-B9 eval) -----------------------------------

    def log_qa(
        self,
        *,
        correlation_id: str,
        question: str,
        answer: str | None,
        cited_page_ids: list[str],
        refused: bool,
        channel: str | None = None,
        user_id: str | None = None,
        conversation_key: str | None = None,
    ) -> int:
        """Record one answered (or refused) question; returns the row id for a later feedback update.

        `conversation_key` groups a DM / channel-thread so `recent_qa` can reconstruct memory."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO qa_log "
                "(correlation_id, channel, user_id, question, answer, cited_page_ids, refused, "
                "conversation_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    correlation_id,
                    channel,
                    user_id,
                    question,
                    answer,
                    json.dumps(list(cited_page_ids)),
                    1 if refused else 0,
                    conversation_key,
                    to_iso(utc_now()),
                ),
            )
            return int(cursor.lastrowid or 0)

    def recent_qa(self, conversation_key: str, *, limit: int = 6) -> list[tuple[str, str]]:
        """The last `limit` (question, answer) turns of a conversation, oldest-first — the memory the
        answerer uses to resolve follow-ups. Skips rows with no answer text."""
        if not conversation_key:
            return []
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT question, answer FROM qa_log WHERE conversation_key = ? "
                "ORDER BY id DESC LIMIT ?",
                (conversation_key, limit),
            ).fetchall()
        turns = [(str(r["question"]), str(r["answer"] or "")) for r in rows if r["answer"]]
        turns.reverse()  # chronological
        return turns

    def set_qa_feedback(self, qa_id: int, feedback: str) -> None:
        """Record a 👍/👎 (`up`/`down`) against a logged answer (S-B7)."""
        with self._db.transaction() as conn:
            conn.execute("UPDATE qa_log SET feedback = ? WHERE id = ?", (feedback, qa_id))

    def get_qa(self, qa_id: int) -> dict[str, object] | None:
        with self._db.read() as conn:
            row = conn.execute("SELECT * FROM qa_log WHERE id = ?", (qa_id,)).fetchone()
        return dict(row) if row is not None else None

    def close(self) -> None:
        self._db.close()
