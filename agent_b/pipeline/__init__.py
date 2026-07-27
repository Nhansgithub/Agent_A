"""Agent B ingestion pipeline (S-B1+): crawl → convert → write → link the vault."""

from agent_b.pipeline.assets import AssetFetcher, fetch_page_assets, remove_page_assets
from agent_b.pipeline.bootstrap import BuildResult, ImportStats, build_vault, import_space
from agent_b.pipeline.convert import RenderedNote, note_vault_path, render_note, slugify
from agent_b.pipeline.crawler import CrawlItem, crawl
from agent_b.pipeline.curate import CurationStats, curate_vault
from agent_b.pipeline.linker import LinkStats, link_vault
from agent_b.pipeline.publish import render_custom_css, render_quartz_config, stage_content
from agent_b.pipeline.sync import PullResult, SyncStats, run_pull, sync_vault
from agent_b.pipeline.vcs import GitVault, VaultVcs
from agent_b.pipeline.writer import VaultWriter

__all__ = [
    "AssetFetcher",
    "BuildResult",
    "CrawlItem",
    "CurationStats",
    "GitVault",
    "ImportStats",
    "LinkStats",
    "PullResult",
    "RenderedNote",
    "SyncStats",
    "VaultVcs",
    "VaultWriter",
    "build_vault",
    "crawl",
    "curate_vault",
    "fetch_page_assets",
    "import_space",
    "link_vault",
    "note_vault_path",
    "render_custom_css",
    "remove_page_assets",
    "render_note",
    "render_quartz_config",
    "run_pull",
    "slugify",
    "stage_content",
    "sync_vault",
]
