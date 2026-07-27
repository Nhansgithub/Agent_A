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
