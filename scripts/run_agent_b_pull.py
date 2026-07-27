#!/usr/bin/env python
"""Run one Agent B maintained pull (S-B4) — the body of the nightly cron job.

Re-pulls the curated Confluence space into the Obsidian vault: change-detect (only changed pages are
rewritten), reconcile deletions (tombstone vanished pages), apply the LLM curation overlay, link, and
commit the result to the vault's git history (AD-28). Every run appends a row to `pull_runs`.

It reuses Agent A's building blocks by injection (AD-27): the Confluence adapter (built from Agent B's
own `confluence_credentials_ref`, a read-only pull of the same site) and the shared traced `LlmClient`
for the Librarian. It touches Agent B's OWN SQLite store (AD-32), never Agent A's `state.db`.

    .venv/bin/python scripts/run_agent_b_pull.py              # full pull: sync + curate + link + commit
    .venv/bin/python scripts/run_agent_b_pull.py --no-curate  # skip the LLM overlay (no API key needed)
    .venv/bin/python scripts/run_agent_b_pull.py --no-commit  # don't git-commit the vault this run

Deployed via deploy/agent_b_pull.cron. Live activation needs the tenant creds already in /opt/agent/.env
(the same ALPHA_CONF the Agent A demo uses) and, for curation, the Anthropic key.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def main(curate: bool, commit: bool, index: bool, assets: bool) -> int:
    from agent_b.agents.librarian import LibrarianAgent
    from agent_b.config import load_agent_b_config_file
    from agent_b.pipeline import GitVault, fetch_page_assets, run_pull
    from agent_b.rag import FastEmbedEmbedder, index_vault
    from agent_b.repository import AgentBRepository
    from app.adapters.confluence import ConfluenceAdapter
    from app.adapters.http import AtlassianClient
    from app.agents.llm import CallMetadata
    from app.composition import Composition
    from app.config.registry import ConfigRegistry
    from app.config.secrets import resolve_atlassian_credentials

    registry_path = ROOT / "config" / "registry.yaml"
    config = load_agent_b_config_file(registry_path)
    if config is None:
        print("No 'agent_b:' block in config/registry.yaml — Agent B is not configured here.")
        return 1

    registry = ConfigRegistry.from_yaml_file(registry_path)
    system = registry.system
    conf_creds = resolve_atlassian_credentials(config.confluence_credentials_ref, None)
    confluence = ConfluenceAdapter(
        AtlassianClient(
            conf_creds,
            product="confluence",
            max_attempts=system.retry_max_attempts,
            backoff_seconds=system.retry_backoff_seconds,
        )
    )

    repo = AgentBRepository.open(config.database_path)
    librarian = metadata = None
    if curate:
        librarian = LibrarianAgent(Composition(registry).llm, model=config.curator_model)
        metadata = CallMetadata(
            correlation_id=uuid.uuid4().hex, prd_id="agent_b_kb", agent_role="librarian"
        )
    vcs = GitVault(config.vault_dir) if commit else None

    async def _fetch_assets(page_id: str) -> int:
        return await fetch_page_assets(confluence, config.vault_dir, page_id)

    index_stats = None
    try:
        result = await run_pull(
            confluence,
            repo,
            config,
            base_url=conf_creds.base_url,
            librarian=librarian,
            metadata=metadata,
            vcs=vcs,
            fetch_assets=_fetch_assets if assets else None,
        )
        if index:
            # Refresh the RAG vector index over the just-synced vault — incremental by the same
            # content hash the pull uses, so only changed pages are re-embedded (S-B6).
            index_stats = index_vault(repo, FastEmbedEmbedder(config.embeddings.model), config)
    finally:
        await confluence._client.aclose()
        repo.close()

    s = result.sync
    print(
        f"pull {result.run_id[:8]}: +{s.added} ~{s.changed} ={s.unchanged} -{s.deleted} "
        f"| links h{result.links.hierarchy}/r{result.links.restored} "
        f"| commit {result.committed or '(none)'}"
    )
    if index_stats is not None:
        print(
            f"index: embedded {index_stats.embedded}, skipped {index_stats.skipped}, "
            f"{index_stats.chunks} chunks total"
        )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run one Agent B maintained pull (S-B4/S-B6).")
    parser.add_argument("--no-curate", action="store_true", help="skip the LLM curation overlay")
    parser.add_argument("--no-commit", action="store_true", help="do not git-commit the vault")
    parser.add_argument("--no-index", action="store_true", help="skip the RAG vector re-index")
    parser.add_argument("--no-assets", action="store_true", help="skip pulling image binaries")
    args = parser.parse_args()
    load_env()
    raise SystemExit(
        asyncio.run(
            main(
                curate=not args.no_curate,
                commit=not args.no_commit,
                index=not args.no_index,
                assets=not args.no_assets,
            )
        )
    )
