"""Incremental sync + deletion reconcile — the maintained pull (S-B4).

S-B1 established the full-pull baseline; this turns it into a vault kept current on a schedule:

  * **Change detection.** Each crawled page is classified against its stored row — *added* (new or a
    resurrected tombstone), *changed* (content hash differs), or *unchanged*. Only added/changed pages
    are rewritten; the byte-identical idempotency of S-B1 means an unchanged corpus produces no churn.
  * **Deletion reconcile.** A page that was live last run but is absent from this crawl is tombstoned:
    its note file is removed, its edges dropped, and its index row flagged `deleted_at` (kept, not
    purged, so a later re-add un-tombstones cleanly).
  * **The maintained run** (`run_pull`) wraps the above with the nightly job's bookkeeping: a
    `pull_runs` ledger row (counts + status), the LLM curation overlay (optional), the final link
    materialization, and one git commit versioning the result (AD-28).

`sync_vault` is the pure core (repo + files only — trivially testable); `run_pull` composes it with
curation, linking, the ledger, and VCS, exactly as `build_vault` composed the S-B1 baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_b.config import AgentBConfig
from agent_b.pipeline.assets import AssetFetcher, remove_page_assets
from agent_b.pipeline.convert import render_note
from agent_b.pipeline.crawler import crawl
from agent_b.pipeline.curate import CurationStats, curate_vault
from agent_b.pipeline.linker import LinkStats, link_vault
from agent_b.pipeline.vcs import VaultVcs
from agent_b.pipeline.writer import VaultWriter
from agent_b.repository import AgentBRepository
from app.adapters.confluence import ConfluenceAdapter
from app.agents.llm import CallMetadata


@dataclass(frozen=True, slots=True)
class SyncStats:
    added: int = 0
    changed: int = 0
    unchanged: int = 0
    deleted: int = 0

    @property
    def touched(self) -> int:
        """Pages written or removed this run — drives whether a git commit is worth making."""
        return self.added + self.changed + self.deleted


@dataclass(frozen=True, slots=True)
class PullResult:
    run_id: str
    sync: SyncStats
    links: LinkStats
    curation: CurationStats | None = None
    committed: str | None = None


async def sync_vault(
    confluence: ConfluenceAdapter,
    repo: AgentBRepository,
    config: AgentBConfig,
    *,
    base_url: str,
    fetch_assets: AssetFetcher | None = None,
) -> SyncStats:
    """Re-pull the curated space, writing only changed pages and tombstoning vanished ones.

    When `fetch_assets` is provided (S-B10), it is invoked for each added/changed page to pull that
    page's image binaries — incremental by construction, since unchanged pages are never visited.
    """
    previously_live = set(repo.document_ids())  # live (non-tombstoned) before this run
    writer = VaultWriter(config.vault_dir, repo)
    seen: set[str] = set()
    added = changed = unchanged = 0
    for item in await crawl(confluence, config):
        page = await confluence.get_page(item.page_id)
        markdown = confluence.storage_to_markdown(page.body_storage)
        note = render_note(
            page_id=page.id,
            title=page.title,
            doc_type=item.doc_type,
            parent_id=item.parent_id,
            space_key=config.space_key,
            source_url=f"{base_url.rstrip('/')}/wiki/pages/{page.id}",
            markdown=markdown,
        )
        existing = repo.get_document(page.id)
        is_new = existing is None or existing.get("deleted_at") is not None
        is_changed = not is_new and existing.get("content_hash") != note.content_hash
        if is_new:
            added += 1  # brand-new, or a tombstone coming back to life
        elif is_changed:
            changed += 1
        else:
            unchanged += 1
        writer.write(note)  # clears any tombstone via the upsert
        seen.add(page.id)
        if fetch_assets is not None and (is_new or is_changed):
            await fetch_assets(page.id)  # pull image binaries for the pages that changed (S-B10)

    deleted_ids = sorted(previously_live - seen)
    for page_id in deleted_ids:
        row = repo.get_document(page_id)  # vault_path survives the tombstone below
        if row is not None:
            (Path(config.vault_dir) / str(row["vault_path"])).unlink(missing_ok=True)
        remove_page_assets(config.vault_dir, page_id)  # drop the tombstoned page's images (S-B10)
    deleted = repo.tombstone_documents(deleted_ids)
    return SyncStats(added=added, changed=changed, unchanged=unchanged, deleted=deleted)


async def run_pull(
    confluence: ConfluenceAdapter,
    repo: AgentBRepository,
    config: AgentBConfig,
    *,
    base_url: str,
    librarian: object | None = None,
    metadata: CallMetadata | None = None,
    vcs: VaultVcs | None = None,
    fetch_assets: AssetFetcher | None = None,
) -> PullResult:
    """One scheduled maintained pull: sync → curate (optional) → link → commit, ledgered end-to-end.

    A failure anywhere marks the `pull_runs` row `error` (so a dead nightly job is visible) and
    re-raises. `librarian` is duck-typed to avoid coupling this module to the agents layer's import.
    """
    run_id = repo.begin_pull_run()
    try:
        sync = await sync_vault(
            confluence, repo, config, base_url=base_url, fetch_assets=fetch_assets
        )
        curation: CurationStats | None = None
        if librarian is not None and metadata is not None:
            curation = await curate_vault(librarian, repo, config, metadata=metadata)  # type: ignore[arg-type]
        links = link_vault(repo, config.vault_dir)
        committed = None
        if vcs is not None and (sync.touched or curation is not None):
            committed = vcs.commit(
                f"agent-b pull {run_id[:8]}: +{sync.added} ~{sync.changed} -{sync.deleted}"
            )
    except BaseException:
        repo.finish_pull_run(run_id, status="error")
        raise
    repo.finish_pull_run(
        run_id,
        status="ok",
        added=sync.added,
        changed=sync.changed,
        deleted=sync.deleted,
    )
    return PullResult(run_id=run_id, sync=sync, links=links, curation=curation, committed=committed)
