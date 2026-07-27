"""S-B5 — Quartz publish prep: config generation + content staging, offline (no Node, no network)."""

from __future__ import annotations

from pathlib import Path

from agent_b.pipeline import (
    link_vault,
    render_custom_css,
    render_index_md,
    render_note,
    set_quartz_base_url,
    set_quartz_page_title,
    stage_content,
)
from agent_b.repository import AgentBRepository


def test_set_quartz_base_url_patches_only_the_url() -> None:
    # A stand-in for Quartz's own config: we replace only the baseUrl, preserving everything else.
    default = (
        "const config = {\n"
        "  configuration: {\n"
        '    pageTitle: "🪴 Quartz 4",\n'
        '    baseUrl: "quartz.jzhao.xyz",\n'
        "    theme: { colors: { lightMode: {} } },\n"
        "  },\n"
        "}\n"
    )
    out = set_quartz_base_url(default, "https://agent.poetroastery.com/")
    assert 'baseUrl: "agent.poetroastery.com"' in out  # bare host, no scheme/slash (AD-4)
    assert "quartz.jzhao.xyz" not in out  # the placeholder is gone
    assert "theme: { colors" in out and 'pageTitle: "🪴 Quartz 4"' in out  # everything else intact


def test_set_quartz_page_title_replaces_the_site_name() -> None:
    default = '    pageTitle: "🪴 Quartz 4",\n    baseUrl: "quartz.jzhao.xyz",\n'
    out = set_quartz_page_title(default, "Knowledge Base")
    assert 'pageTitle: "Knowledge Base"' in out
    assert "Quartz 4" not in out
    assert 'baseUrl: "quartz.jzhao.xyz"' in out  # only the title changed


def test_index_md_uses_the_configured_title() -> None:
    index = render_index_md("Knowledge Base")
    assert 'title: "Knowledge Base"' in index and "# Knowledge Base" in index


def test_custom_css_targets_the_suggested_callout() -> None:
    css = render_custom_css()
    assert "data-callout='tip'" in css  # the AI-suggested callout the linker emits (AD-30)


def test_index_md_is_a_valid_landing_page() -> None:
    index = render_index_md()
    # Quartz needs a content/index.md or the site root 404s (the bug this fixes).
    assert index.startswith("---\n")  # frontmatter present
    assert 'title: "Internal Knowledge Base"' in index
    assert "# Internal Knowledge Base" in index


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
