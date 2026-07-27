"""S-B0 — Agent B SQLite store skeleton (AD-2 mirror, AD-32).

Offline: an in-memory database, no disk, no network.
"""

from __future__ import annotations

from agent_b.repository import AgentBRepository
from agent_b.repository.database import AgentBDatabase


def test_schema_has_all_tables() -> None:
    db = AgentBDatabase(":memory:")
    with db.read() as conn:
        names = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    db.close()
    assert {"documents", "links", "llm_cache", "qa_log", "pull_runs"} <= names


def test_document_upsert_round_trip() -> None:
    repo = AgentBRepository.open(":memory:")
    repo.upsert_document(
        page_id="123",
        space_key="PM",
        title="Onboarding PRD",
        doc_type="prd",
        vault_path="notes/123-onboarding-prd.md",
        content_hash="hash-a",
        source_url="https://example.atlassian.net/wiki/pages/123",
    )

    doc = repo.get_document("123")
    assert doc is not None
    assert doc["title"] == "Onboarding PRD"
    assert doc["doc_type"] == "prd"
    assert doc["deleted_at"] is None
    assert repo.document_ids() == ["123"]
    repo.close()


def test_upsert_is_idempotent_and_updates_in_place() -> None:
    repo = AgentBRepository.open(":memory:")
    for content_hash, title in (("hash-a", "Onboarding PRD"), ("hash-b", "Onboarding PRD v2")):
        repo.upsert_document(
            page_id="123",
            space_key="PM",
            title=title,
            doc_type="prd",
            vault_path="notes/123-onboarding-prd.md",
            content_hash=content_hash,
        )

    # Two upserts of the same page_id → one row, latest values.
    assert repo.document_ids() == ["123"]
    doc = repo.get_document("123")
    assert doc is not None
    assert doc["title"] == "Onboarding PRD v2"
    assert doc["content_hash"] == "hash-b"
    repo.close()


def test_missing_document_is_none() -> None:
    repo = AgentBRepository.open(":memory:")
    assert repo.get_document("nope") is None
    assert repo.document_ids() == []
    repo.close()


def test_recent_qa_returns_conversation_turns_in_order() -> None:
    repo = AgentBRepository.open(":memory:")
    # Two conversations interleaved; recent_qa must isolate one and return it oldest-first.
    repo.log_qa(
        correlation_id="c1",
        question="how do I onboard?",
        answer="Press the button.",
        cited_page_ids=["P1"],
        refused=False,
        conversation_key="dmA",
    )
    repo.log_qa(
        correlation_id="c2",
        question="unrelated",
        answer="Elsewhere.",
        cited_page_ids=[],
        refused=False,
        conversation_key="dmB",
    )
    repo.log_qa(
        correlation_id="c3",
        question="why?",
        answer="Because it syncs.",
        cited_page_ids=["P1"],
        refused=False,
        conversation_key="dmA",
    )

    turns = repo.recent_qa("dmA", limit=6)
    assert turns == [
        ("how do I onboard?", "Press the button."),
        ("why?", "Because it syncs."),
    ]
    assert repo.recent_qa("dmB") == [("unrelated", "Elsewhere.")]
    assert repo.recent_qa("never-seen") == []
    repo.close()


def test_recent_qa_windows_to_the_limit() -> None:
    repo = AgentBRepository.open(":memory:")
    for i in range(10):
        repo.log_qa(
            correlation_id=f"c{i}",
            question=f"q{i}",
            answer=f"a{i}",
            cited_page_ids=[],
            refused=False,
            conversation_key="k",
        )
    turns = repo.recent_qa("k", limit=3)
    assert turns == [("q7", "a7"), ("q8", "a8"), ("q9", "a9")]  # the 3 most recent, chronological
    repo.close()


def test_migration_adds_conversation_key_to_a_pre_existing_qa_log(tmp_path) -> None:
    import sqlite3

    # A store created before the column existed: the OLD qa_log schema, with one row already in it.
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE qa_log (id INTEGER PRIMARY KEY AUTOINCREMENT, correlation_id TEXT NOT NULL, "
        "channel TEXT, user_id TEXT, question TEXT NOT NULL, answer TEXT, cited_page_ids TEXT, "
        "refused INTEGER NOT NULL DEFAULT 0, feedback TEXT, created_at TEXT NOT NULL)"
    )
    raw.execute(
        "INSERT INTO qa_log (correlation_id, question, answer, created_at) VALUES (?, ?, ?, ?)",
        ("c0", "old question", "old answer", "2026-01-01T00:00:00+00:00"),
    )
    raw.commit()
    raw.close()

    # Opening it the normal way runs the migration: the column is added, old data survives, and the
    # new memory path works — no rebuild of the live DB needed.
    repo = AgentBRepository.open(str(path))
    repo.log_qa(
        correlation_id="c1",
        question="new",
        answer="new a",
        cited_page_ids=[],
        refused=False,
        conversation_key="dmA",
    )
    assert repo.recent_qa("dmA") == [("new", "new a")]
    with repo._db.read() as conn:  # the pre-existing row is still there
        assert conn.execute("SELECT COUNT(*) AS n FROM qa_log").fetchone()["n"] == 2
    repo.close()
