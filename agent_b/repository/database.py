"""Agent B's own SQLite store — separate from Agent A's `state.db` (AD-2 boundary, AD-32).

This module (and its siblings under `agent_b/repository/`) are the ONLY place in `agent_b` that runs
SQL — an import-linter contract forbids `sqlite3` in every other `agent_b` module, exactly as AD-2 does
for Agent A. The store holds the projection's *bookkeeping*, never the note bodies (those live in the
git vault): document rows + content hashes (idempotent re-pull), the link graph, an LLM-decision cache
(idempotent curation), the Q&A log, and one row per scheduled pull run.

WAL journal mode mirrors Agent A (AD-23 litestream-friendly). The store is small and single-writer —
the nightly pull is a short-lived job, and the Slack query path only reads and appends to `qa_log`.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
-- One row per ingested Confluence page. `content_hash` drives idempotent re-pull (S-B4); a tombstone
-- (`deleted_at`) records a page that vanished from Confluence since the last run.
CREATE TABLE IF NOT EXISTS documents (
    page_id       TEXT PRIMARY KEY,
    space_key     TEXT NOT NULL,
    title         TEXT NOT NULL,
    doc_type      TEXT NOT NULL,          -- prd | userdoc | design | other
    parent_id     TEXT,
    vault_path    TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    source_url    TEXT,
    pulled_at     TEXT NOT NULL,
    base_content  TEXT NOT NULL DEFAULT '',   -- the pre-link note; the linker re-derives from this
    tags          TEXT NOT NULL DEFAULT '[]', -- JSON list, set by the LLM curator (S-B3)
    deleted_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_parent ON documents (parent_id);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents (doc_type);

-- The link graph AND the linker cache. `kind`: hierarchy | restored | suggested. `source`:
-- deterministic | llm. Suggested/llm edges are quarantined in the vault (AD-30), but every edge is
-- recorded here so the graph is queryable and re-runs are stable.
CREATE TABLE IF NOT EXISTS links (
    from_page_id  TEXT NOT NULL,
    to_page_id    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    source        TEXT NOT NULL,
    confidence    REAL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (from_page_id, to_page_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_links_from ON links (from_page_id);
CREATE INDEX IF NOT EXISTS idx_links_to ON links (to_page_id);

-- Idempotent LLM curation (S-B3): keyed by a hash of the input, so unchanged docs are never
-- re-curated and the vault does not churn.
CREATE TABLE IF NOT EXISTS llm_cache (
    input_hash    TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    output        TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

-- Every Slack answer (AD-20 traceability + the S-B9 eval): the question, the cited notes, whether the
-- bot refused, and the human's thumbs up/down.
CREATE TABLE IF NOT EXISTS qa_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id  TEXT NOT NULL,
    channel         TEXT,
    user_id         TEXT,
    question        TEXT NOT NULL,
    answer          TEXT,
    cited_page_ids  TEXT,                 -- JSON array of page_ids
    refused         INTEGER NOT NULL DEFAULT 0,
    feedback        TEXT,                 -- up | down | (null)
    created_at      TEXT NOT NULL
);

-- One row per scheduled pull (S-B4), for observability of the nightly job.
CREATE TABLE IF NOT EXISTS pull_runs (
    run_id       TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    added        INTEGER NOT NULL DEFAULT 0,
    changed      INTEGER NOT NULL DEFAULT 0,
    deleted      INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL            -- running | ok | error
);

-- The RAG vector index (S-B6): one row per embedded chunk of a note. The embedding is a float32
-- BLOB (numpy `tobytes()`) rather than a sqlite-vec vector column — this runtime has no loadable
-- extension support, and a brute-force cosine over a small internal corpus is fast enough (D-49).
-- `page_content_hash` mirrors the document's hash so the index re-embeds only changed pages (S-B4).
CREATE TABLE IF NOT EXISTS chunks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id            TEXT NOT NULL,
    chunk_index        INTEGER NOT NULL,
    text               TEXT NOT NULL,
    embedding          BLOB NOT NULL,
    dim                INTEGER NOT NULL,
    page_content_hash  TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    UNIQUE (page_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks (page_id);
"""


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(value: datetime | None) -> str | None:
    """Persist timestamps as ISO-8601 UTC (Spine → Consistency Conventions)."""
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


class AgentBDatabase:
    """Owns the connection to Agent B's store and applies the schema."""

    __slots__ = ("_connection", "_lock", "_path")

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = threading.RLock()
        self._connection = self._connect()
        self._apply_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        connection.row_factory = sqlite3.Row
        if self._path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _apply_schema(self) -> None:
        with self._lock:
            self._connection.executescript(SCHEMA)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """An atomic unit of work — a pull upsert and its link/cache writes commit together."""
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """A read-only borrow of the connection."""
        with self._lock:
            yield self._connection

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @property
    def path(self) -> str:
        return self._path
