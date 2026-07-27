"""One-shot / scheduled import of the curated space into the vault (S-B1).

Ties the three steps together: crawl the curated folders, convert each page to a note, write it. The
incremental/deletion logic (S-B4) layers on top of this; B1 is the full-pull baseline. The
`ConfluenceAdapter` is injected (AD-1/AD-27) — this module never opens a socket itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_b.config import AgentBConfig
from agent_b.pipeline.convert import page_source_url, render_note
from agent_b.pipeline.crawler import crawl
from agent_b.pipeline.linker import LinkStats, link_vault
from agent_b.pipeline.writer import VaultWriter
from agent_b.repository import AgentBRepository
from app.adapters.confluence import ConfluenceAdapter


@dataclass(frozen=True, slots=True)
class ImportStats:
    pages: int = 0
    written: int = 0
    unchanged: int = 0


@dataclass(frozen=True, slots=True)
class BuildResult:
    imported: ImportStats
    links: LinkStats


async def import_space(
    confluence: ConfluenceAdapter,
    repo: AgentBRepository,
    config: AgentBConfig,
    *,
    base_url: str,
) -> ImportStats:
    writer = VaultWriter(config.vault_dir, repo)
    pages = written = unchanged = 0
    for item in await crawl(confluence, config):
        page = await confluence.get_page(item.page_id)
        markdown = confluence.storage_to_markdown(page.body_storage)
        note = render_note(
            page_id=page.id,
            title=page.title,
            doc_type=item.doc_type,
            parent_id=item.parent_id,
            space_key=config.space_key,
            source_url=page_source_url(base_url, config.space_key, page.id),
            markdown=markdown,
        )
        pages += 1
        if writer.write(note):
            written += 1
        else:
            unchanged += 1
    return ImportStats(pages=pages, written=written, unchanged=unchanged)


async def build_vault(
    confluence: ConfluenceAdapter,
    repo: AgentBRepository,
    config: AgentBConfig,
    *,
    base_url: str,
) -> BuildResult:
    """The full vault pass: import the curated pages (base notes), then link them (S-B1 + S-B2)."""
    imported = await import_space(confluence, repo, config, base_url=base_url)
    links = link_vault(repo, config.vault_dir)
    return BuildResult(imported=imported, links=links)
