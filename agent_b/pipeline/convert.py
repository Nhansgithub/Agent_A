"""Confluence page → an Obsidian vault note (S-B1).

Pure string work: given a page's already-converted Markdown body and its metadata, produce the note
file content (deterministic YAML frontmatter + body) and a content hash. Determinism matters — the
note must be byte-identical across runs for unchanged content (idempotency, D-41), so **no timestamp
or run-specific value goes into the note**; `pulled_at` lives only in the SQLite bookkeeping row.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_MAX_SLUG = 60

#: A Markdown image `![alt](src)`. Confluence attachment images convert to a **bare filename** src; an
#: external image keeps its URL. S-B10 rewrites only the bare ones to the local asset path.
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_HAS_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.IGNORECASE)


def _localize_image_refs(markdown: str, page_id: str) -> str:
    """Point attachment-image refs at the vault's local assets (S-B10), deterministically.

    `![alt](diagram.png)` → `![alt](../assets/<page_id>/diagram.png)` (notes live in `notes/`, assets in
    `assets/<page_id>/`). External URLs, data URIs, and already-localized paths are left untouched — the
    rewrite is pure and stable, so it does not disturb note idempotency (the binary fetch is separate)."""

    def _rewrite(match: re.Match[str]) -> str:
        alt, src = match.group(1), match.group(2)
        if _HAS_SCHEME.match(src) or src.startswith(("../assets/", "assets/", "#", "/")):
            return match.group(0)
        return f"![{alt}](../assets/{page_id}/{src})"

    return _MD_IMAGE.sub(_rewrite, markdown)


@dataclass(frozen=True, slots=True)
class RenderedNote:
    """A note ready to write to the vault and record in the store."""

    page_id: str
    title: str
    doc_type: str
    parent_id: str
    space_key: str
    source_url: str
    vault_path: str
    content: str
    content_hash: str


def slugify(title: str) -> str:
    return _SLUG_STRIP.sub("-", title.lower()).strip("-")[:_MAX_SLUG].strip("-")


def note_vault_path(page_id: str, title: str) -> str:
    slug = slugify(title)
    name = f"{page_id}-{slug}" if slug else page_id
    return f"notes/{name}.md"


def page_source_url(base_url: str, space_key: str, page_id: str) -> str:
    """The canonical Confluence Cloud page URL: `/wiki/spaces/<KEY>/pages/<id>`.

    Confluence redirects that to the full title-slug URL. The bare `/wiki/pages/<id>` form does **not**
    resolve for a viewer, which is why the note's `source_url` must carry the space key.
    """
    return f"{base_url.rstrip('/')}/wiki/spaces/{space_key}/pages/{page_id}"


def _yaml_str(value: str) -> str:
    """A deterministic, safe double-quoted YAML scalar."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_note(
    *,
    page_id: str,
    title: str,
    doc_type: str,
    parent_id: str,
    space_key: str,
    source_url: str,
    markdown: str,
) -> RenderedNote:
    """Build the note content + hash. Pure and deterministic in its inputs."""
    frontmatter = "\n".join(
        [
            "---",
            f"title: {_yaml_str(title)}",
            f"page_id: {_yaml_str(page_id)}",
            f"space: {_yaml_str(space_key)}",
            f"doc_type: {_yaml_str(doc_type)}",
            f"parent_id: {_yaml_str(parent_id)}",
            f"source_url: {_yaml_str(source_url)}",
            "---",
        ]
    )
    body = _localize_image_refs(markdown.strip(), page_id)
    content = f"{frontmatter}\n\n# {title}\n\n{body}\n" if body else f"{frontmatter}\n\n# {title}\n"
    return RenderedNote(
        page_id=page_id,
        title=title,
        doc_type=doc_type,
        parent_id=parent_id,
        space_key=space_key,
        source_url=source_url,
        vault_path=note_vault_path(page_id, title),
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
