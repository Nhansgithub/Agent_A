"""Walk the curated Confluence folders and list the pages to ingest (S-B1).

Read-only and deterministic in order (include-folders in config order, pages in the adapter's tree
order). Each item carries the page's `doc_type` (from `folder_types`) and its immediate `parent_id`,
which the deterministic linker (S-B2) later turns into hierarchy edges. The exclude set is handed to
the adapter so the draft/review folder is never descended into.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_b.config import AgentBConfig
from app.adapters.confluence import ConfluenceAdapter


@dataclass(frozen=True, slots=True)
class CrawlItem:
    page_id: str
    parent_id: str
    doc_type: str


async def crawl(confluence: ConfluenceAdapter, config: AgentBConfig) -> list[CrawlItem]:
    exclude = set(config.exclude_folder_ids)
    seen: set[str] = set()
    items: list[CrawlItem] = []
    for folder_id in config.include_folder_ids:
        doc_type = config.folder_types.get(folder_id, "other")
        for ref in await confluence.list_descendant_pages(folder_id, exclude_folder_ids=exclude):
            if ref.id in seen:
                continue
            seen.add(ref.id)
            items.append(CrawlItem(page_id=ref.id, parent_id=ref.parent_id, doc_type=doc_type))
    return items
