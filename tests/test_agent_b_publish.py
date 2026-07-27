"""S-B5 — Quartz publish prep: config generation + content staging, offline (no Node, no network)."""

from __future__ import annotations

from pathlib import Path

from agent_b.config import AgentBConfig, load_agent_b_config
from agent_b.pipeline import (
    link_vault,
    render_custom_css,
    render_note,
    render_quartz_config,
    stage_content,
)
from agent_b.repository import AgentBRepository


def _config(vault_dir: Path, base_url: str = "https://agent.poetroastery.com") -> AgentBConfig:
    cfg = load_agent_b_config(
        {
            "agent_b": {
                "space_key": "PM",
                "confluence_credentials_ref": "env:ALPHA_CONF",
                "include_folder_ids": ["F"],
                "vault_dir": str(vault_dir),
                "publish": {"base_url": base_url},
            }
        }
    )
    assert cfg is not None
    return cfg


def test_quartz_config_injects_base_url_from_config_and_enables_features() -> None:
    ts = render_quartz_config(_config(Path("/tmp/v"), base_url="https://agent.poetroastery.com/"))
    # baseUrl comes from config as the bare host — never a literal in code (AD-4).
    assert 'baseUrl: "agent.poetroastery.com"' in ts
    assert "https://" not in ts.split("baseUrl", 1)[1].split("\n", 1)[0]  # host only, no scheme
    # graph/backlinks/search substrate: SPA + the Obsidian/CrawlLinks transformers + content index.
    assert "enableSPA: true" in ts
    assert "ObsidianFlavoredMarkdown" in ts and "CrawlLinks" in ts
    assert "ContentIndex" in ts


def test_custom_css_targets_the_suggested_callout() -> None:
    css = render_custom_css()
    assert "data-callout='tip'" in css  # the AI-suggested callout the linker emits (AD-30)


def test_stage_content_copies_notes_byte_for_byte_preserving_wikilinks(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    notes = vault / "notes"
    notes.mkdir(parents=True)
    (notes / "P1-a.md").write_text("# A\n\nSee [[P2-b|B]].\n", encoding="utf-8")
    (notes / "P2-b.md").write_text("# B\n", encoding="utf-8")
    content = tmp_path / "quartz" / "content"

    count = stage_content(str(vault), str(content))

    assert count == 2
    staged = (content / "P1-a.md").read_text(encoding="utf-8")
    assert (
        staged == "# A\n\nSee [[P2-b|B]].\n"
    )  # byte-identical; the [[wikilink]] survives for Quartz


def test_stage_content_is_a_faithful_mirror(tmp_path: Path) -> None:
    """A second stage after a note is removed leaves no stale file in the site content."""
    vault = tmp_path / "vault"
    notes = vault / "notes"
    notes.mkdir(parents=True)
    (notes / "keep.md").write_text("# keep\n", encoding="utf-8")
    (notes / "gone.md").write_text("# gone\n", encoding="utf-8")
    content = tmp_path / "quartz" / "content"

    stage_content(str(vault), str(content))
    (notes / "gone.md").unlink()  # deleted from the vault (S-B4 tombstone)
    count = stage_content(str(vault), str(content))

    assert count == 1
    assert {p.name for p in content.glob("*.md")} == {"keep.md"}


def test_suggested_links_render_as_a_distinct_obsidian_callout(tmp_path: Path) -> None:
    """The visual half of AD-30 (S-B5): AI suggestions are a titled callout, never a plain prose line."""
    repo = AgentBRepository.open(":memory:")
    for pid, title in (("P1", "Onboarding"), ("P2", "Billing")):
        note = render_note(
            page_id=pid,
            title=title,
            doc_type="prd",
            parent_id="F",
            space_key="PM",
            source_url=f"https://x/wiki/pages/{pid}",
            markdown="Body.",
        )
        repo.upsert_document(
            page_id=pid,
            space_key="PM",
            title=title,
            doc_type="prd",
            vault_path=note.vault_path,
            content_hash=note.content_hash,
            base_content=note.content,
            parent_id="F",
        )
    repo.add_link("P1", "P2", kind="suggested", source="llm")

    link_vault(repo, str(tmp_path))

    note = (tmp_path / "notes" / "P1-onboarding.md").read_text(encoding="utf-8")
    body, _, related = note.partition("<!-- agent-b:related:start -->")
    assert "> [!tip] Suggested (AI — unverified)" in related  # a callout, not a bullet
    assert "[[P2-billing|Billing]]" in related
    assert "[[P2-billing" not in body  # still never inlined into prose (AD-30)
    repo.close()
