"""S-B2 — deterministic linker: hierarchy + restored refs, no false edges, idempotent (AD-30)."""

from __future__ import annotations

from pathlib import Path

from agent_b.pipeline import link_vault, render_note
from agent_b.repository import AgentBRepository


def _seed(repo: AgentBRepository, *, page_id: str, title: str, parent_id: str, body: str) -> None:
    note = render_note(
        page_id=page_id,
        title=title,
        doc_type="prd",
        parent_id=parent_id,
        space_key="PM",
        source_url=f"https://x/wiki/pages/{page_id}",
        markdown=body,
    )
    repo.upsert_document(
        page_id=page_id,
        space_key="PM",
        title=title,
        doc_type="prd",
        vault_path=note.vault_path,
        content_hash=note.content_hash,
        base_content=note.content,
        parent_id=parent_id,
    )


def _corpus(repo: AgentBRepository) -> None:
    _seed(repo, page_id="PA", title="Alpha PRD", parent_id="F", body="Root doc.")
    _seed(repo, page_id="PB", title="Beta Guide", parent_id="PA", body="Child of alpha.")
    _seed(
        repo, page_id="PC", title="Gamma", parent_id="F", body="See the [Beta Guide](Beta Guide)."
    )
    _seed(
        repo,
        page_id="PD",
        title="Delta",
        parent_id="F",
        body="External [docs](https://example.com) and [missing](Nowhere).",
    )


def _links(repo: AgentBRepository) -> set[tuple[str, str, str]]:
    return {
        (str(r["from_page_id"]), str(r["to_page_id"]), str(r["kind"])) for r in repo.all_links()
    }


def test_hierarchy_and_restored_edges(tmp_path: Path) -> None:
    repo = AgentBRepository.open(":memory:")
    _corpus(repo)

    stats = link_vault(repo, str(tmp_path))

    assert stats.hierarchy == 1  # PB -> PA
    assert stats.restored == 1  # PC -> PB
    assert _links(repo) == {("PB", "PA", "hierarchy"), ("PC", "PB", "restored")}

    child = (tmp_path / "notes" / "PB-beta-guide.md").read_text(encoding="utf-8")
    assert "[[PA-alpha-prd|Alpha PRD]]" in child  # parent link
    parent = (tmp_path / "notes" / "PA-alpha-prd.md").read_text(encoding="utf-8")
    assert "[[PB-beta-guide|Beta Guide]]" in parent  # child link
    gamma = (tmp_path / "notes" / "PC-gamma.md").read_text(encoding="utf-8")
    assert "[[PB-beta-guide|Beta Guide]]" in gamma  # restored inline reference
    repo.close()


def test_no_false_edges(tmp_path: Path) -> None:
    repo = AgentBRepository.open(":memory:")
    _corpus(repo)
    link_vault(repo, str(tmp_path))

    delta = (tmp_path / "notes" / "PD-delta.md").read_text(encoding="utf-8")
    # An external URL and an unknown title are left exactly as they were — never invented as links.
    assert "(https://example.com)" in delta
    assert "[missing](Nowhere)" in delta
    assert "[[" not in delta
    # No edge originates from PD.
    assert not any(edge[0] == "PD" for edge in _links(repo))
    repo.close()


def test_linking_is_idempotent(tmp_path: Path) -> None:
    repo = AgentBRepository.open(":memory:")
    _corpus(repo)

    link_vault(repo, str(tmp_path))
    before = {p.name: p.read_bytes() for p in (tmp_path / "notes").glob("*.md")}
    links_before = _links(repo)

    link_vault(repo, str(tmp_path))
    after = {p.name: p.read_bytes() for p in (tmp_path / "notes").glob("*.md")}

    assert after == before  # byte-identical second pass (re-derived from base_content)
    assert _links(repo) == links_before
    repo.close()
