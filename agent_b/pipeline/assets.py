"""Pull Confluence image attachments into the vault's `assets/` (S-B10).

The converter (`render_note`) already rewrites an attachment image ref `![alt](diagram.png)` to the
local path `![alt](../assets/<page_id>/diagram.png)`. This module fetches the actual binaries so those
refs resolve: it lists a page's attachments, downloads the **image** ones (via the shared transport's
binary read, AD-1), and writes them under `assets/<page_id>/`.

Idempotent + incremental: a binary whose bytes are unchanged is left untouched (no vault churn), and the
pull only fetches assets for the pages that changed this run (wired off S-B4's change detection in
`sync_vault`). Tombstoning a page removes its asset directory. The adapter is injected; no socket here.
"""

from __future__ import annotations

import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.adapters.confluence import ConfluenceAdapter

#: `page_id -> number of asset files written/updated`. sync_vault calls this for each changed page.
AssetFetcher = Callable[[str], Awaitable[int]]

_ASSETS_SUBDIR = "assets"


async def fetch_page_assets(confluence: ConfluenceAdapter, vault_dir: str, page_id: str) -> int:
    """Download a page's image attachments into `assets/<page_id>/`; returns the count written."""
    target = Path(vault_dir) / _ASSETS_SUBDIR / page_id
    written = 0
    for attachment in await confluence.list_attachments(page_id):
        if not attachment.is_image or not attachment.filename:
            continue
        data = await confluence.download_attachment(attachment.download_path)
        path = target / attachment.filename
        if path.exists() and path.read_bytes() == data:
            continue  # unchanged binary — idempotent skip, no rewrite
        target.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written += 1
    return written


def remove_page_assets(vault_dir: str, page_id: str) -> None:
    """Delete a tombstoned page's asset directory (a no-op if it never had one)."""
    directory = Path(vault_dir) / _ASSETS_SUBDIR / page_id
    if directory.exists():
        shutil.rmtree(directory)
