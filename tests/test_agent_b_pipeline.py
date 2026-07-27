"""S-B1 — curated space import: crawl → convert → write the vault (offline, fake adapter)."""

from __future__ import annotations

from pathlib import Path

from agent_b.config import load_agent_b_config
from agent_b.pipeline import import_space, render_note, slugify
from agent_b.repository import AgentBRepository
from app.adapters.markdown import storage_to_markdown
from app.domain.atlassian import ConfluencePage, ConfluencePageRef


class FakeConfluence:
    """A stand-in for the injected ConfluenceAdapter — no HTTP, no credentials."""

    def __init__(
        self,
        tree: dict[str, list[ConfluencePageRef]],
        pages: dict[str, ConfluencePage],
    ) -> None:
        self._tree = tree
        self._pages = pages
        self.exclude_seen: set[str] | None = None

    async def list_descendant_pages(
        self, folder_id: str, *, exclude_folder_ids: set[str] | None = None
    ) -> tuple[ConfluencePageRef, ...]:
        self.exclude_seen = exclude_folder_ids
        return tuple(self._tree.get(folder_id, ()))

    async def get_page(self, page_id: str, *, with_body: bool = True) -> ConfluencePage:
        return self._pages[page_id]

    @staticmethod
    def storage_to_markdown(storage_html: str) -> str:
        return storage_to_markdown(storage_html)


def _config(vault_dir: Path):
    return load_agent_b_config(
        {
            "agent_b": {
                "space_key": "PM",
                "confluence_credentials_ref": "env:ALPHA_CONF",
                "include_folder_ids": ["F_prd", "F_ud"],
                "exclude_folder_ids": ["F_excl"],
                "folder_types": {"F_prd": "prd", "F_ud": "userdoc"},
                "vault_dir": str(vault_dir),
            }
        }
    )


def _fake() -> FakeConfluence:
    tree = {
        "F_prd": [
            ConfluencePageRef(
                id="P1", title="Onboarding PRD", parent_id="F_prd", parent_type="folder"
            ),
            ConfluencePageRef(
                id="P2", title="Billing PRD", parent_id="F_prd", parent_type="folder"
            ),
        ],
        "F_ud": [
            ConfluencePageRef(
                id="P3", title="How to onboard", parent_id="F_ud", parent_type="folder"
            ),
        ],
    }
    pages = {
        "P1": ConfluencePage(
            id="P1", title="Onboarding PRD", body_storage="<p>Welcome aboard.</p>"
        ),
        "P2": ConfluencePage(
            id="P2", title="Billing PRD", body_storage="<p>How billing works.</p>"
        ),
        "P3": ConfluencePage(id="P3", title="How to onboard", body_storage="<p>Step one.</p>"),
    }
    return FakeConfluence(tree, pages)


async def test_import_writes_notes_with_frontmatter(tmp_path: Path) -> None:
    fake = _fake()
    repo = AgentBRepository.open(":memory:")

    stats = await import_space(fake, repo, _config(tmp_path), base_url="https://x.atlassian.net")

    assert stats.pages == 3
    assert stats.written == 3
    assert fake.exclude_seen == {"F_excl"}  # exclude set passed through to the adapter

    prd_note = (tmp_path / "notes" / "P1-onboarding-prd.md").read_text(encoding="utf-8")
    assert 'doc_type: "prd"' in prd_note
    assert 'page_id: "P1"' in prd_note
    # Canonical Confluence URL — /wiki/spaces/<KEY>/pages/<id> (the bare /wiki/pages/<id> doesn't resolve).
    assert 'source_url: "https://x.atlassian.net/wiki/spaces/PM/pages/P1"' in prd_note
    assert "Welcome aboard." in prd_note

    ud_note = (tmp_path / "notes" / "P3-how-to-onboard.md").read_text(encoding="utf-8")
    assert 'doc_type: "userdoc"' in ud_note

    assert repo.document_ids() == ["P1", "P2", "P3"]
    repo.close()


async def test_import_is_idempotent(tmp_path: Path) -> None:
    fake = _fake()
    repo = AgentBRepository.open(":memory:")
    config = _config(tmp_path)

    await import_space(fake, repo, config, base_url="https://x.atlassian.net")
    before = {p.name: p.read_bytes() for p in (tmp_path / "notes").glob("*.md")}

    stats = await import_space(fake, repo, config, base_url="https://x.atlassian.net")
    after = {p.name: p.read_bytes() for p in (tmp_path / "notes").glob("*.md")}

    assert stats.written == 0
    assert stats.unchanged == 3
    assert before == after  # byte-identical second run
    assert repo.document_ids() == ["P1", "P2", "P3"]
    repo.close()


def test_slugify() -> None:
    assert slugify("How to Onboard!") == "how-to-onboard"
    assert slugify("  Spaces & Symbols  ") == "spaces-symbols"


def test_render_note_is_deterministic() -> None:
    kwargs = {
        "page_id": "P1",
        "title": "Onboarding PRD",
        "doc_type": "prd",
        "parent_id": "F_prd",
        "space_key": "PM",
        "source_url": "https://x/wiki/pages/P1",
        "markdown": "Welcome aboard.",
    }
    a = render_note(**kwargs)
    b = render_note(**kwargs)
    assert a.content == b.content
    assert a.content_hash == b.content_hash
    assert a.vault_path == "notes/P1-onboarding-prd.md"
