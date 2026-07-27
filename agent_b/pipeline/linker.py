"""Deterministic linker + note materializer — tiers 1–2, plus the curation overlay (S-B2 / S-B3).

`link_vault` is the single place that writes the *final* vault note. It composes, per document:

  * **Tier 1 (hierarchy).** A page whose stored parent is a known page gets a `[[parent]]` link and is
    listed under the parent's Children.
  * **Tier 2 (restored references).** A Confluence internal link survives conversion as
    `[label](Page Title)`. Where the title matches exactly one document it becomes `[[note|label]]`;
    external (URL) and ambiguous (shared-title) links are left untouched — **no false edges** (AD-30).
  * **Curation overlay (S-B3).** The LLM curator's `tags` go into frontmatter; its *suggested* links go
    into a labelled "Suggested (AI — unverified)" **Obsidian callout** in the Related block — a distinct
    titled box in Obsidian and Quartz (S-B5), **never inlined** into prose (AD-30). These are read from
    the store; this module never calls the LLM.

Deterministic + idempotent: every note is re-derived from its stored `base_content` (never from the
already-linked file), so re-running is byte-identical. The pass rebuilds only `deterministic` edges,
leaving the curator's `llm` edges intact.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from agent_b.repository import AgentBRepository

_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_RELATED_START = "<!-- agent-b:related:start -->"
_RELATED_END = "<!-- agent-b:related:end -->"


@dataclass(frozen=True, slots=True)
class LinkStats:
    hierarchy: int = 0
    restored: int = 0


@dataclass(frozen=True, slots=True)
class _Doc:
    page_id: str
    title: str
    parent_id: str | None
    vault_path: str
    base_content: str
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def basename(self) -> str:
        return Path(self.vault_path).stem


class _Counter:
    __slots__ = ("hierarchy", "restored")

    def __init__(self) -> None:
        self.hierarchy = 0
        self.restored = 0


def link_vault(repo: AgentBRepository, vault_dir: str) -> LinkStats:
    docs = [
        _Doc(
            page_id=str(row["page_id"]),
            title=str(row["title"]),
            parent_id=(str(row["parent_id"]) if row["parent_id"] else None),
            vault_path=str(row["vault_path"]),
            base_content=str(row["base_content"] or ""),
            tags=tuple(json.loads(str(row["tags"] or "[]"))),
        )
        for row in repo.all_documents()
    ]
    by_id = {d.page_id: d for d in docs}

    counts: dict[str, int] = {}
    for d in docs:
        counts[d.title.strip().lower()] = counts.get(d.title.strip().lower(), 0) + 1
    by_title = {d.title.strip().lower(): d for d in docs if counts[d.title.strip().lower()] == 1}

    children: dict[str, list[_Doc]] = {}
    for d in docs:
        if d.parent_id and d.parent_id in by_id:
            children.setdefault(d.parent_id, []).append(d)

    repo.clear_links(source="deterministic")
    stats = _Counter()
    root = Path(vault_dir)
    for d in docs:
        suggested = [
            by_id[str(link["to_page_id"])]
            for link in repo.links_from(d.page_id, kind="suggested")
            if str(link["to_page_id"]) in by_id
        ]
        content = _render(d, by_id, by_title, children, suggested, repo, stats)
        path = root / d.vault_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return LinkStats(hierarchy=stats.hierarchy, restored=stats.restored)


def _with_tags(base_content: str, tags: tuple[str, ...]) -> str:
    if not tags:
        return base_content
    lines = base_content.split("\n")
    fences = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if len(fences) < 2:
        return base_content
    lines.insert(fences[1], "tags: [" + ", ".join(f'"{t}"' for t in tags) + "]")
    return "\n".join(lines)


def _render(
    doc: _Doc,
    by_id: dict[str, _Doc],
    by_title: dict[str, _Doc],
    children: dict[str, list[_Doc]],
    suggested: list[_Doc],
    repo: AgentBRepository,
    stats: _Counter,
) -> str:
    def _restore(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2).strip()
        hit = by_title.get(target.lower())
        if hit is None or hit.page_id == doc.page_id:
            return match.group(0)  # external, unknown, or self → untouched (no false edge)
        stats.restored += 1
        repo.add_link(doc.page_id, hit.page_id, kind="restored", source="deterministic")
        return f"[[{hit.basename}|{label}]]"

    body = _MD_LINK.sub(_restore, _with_tags(doc.base_content, doc.tags)).rstrip() + "\n"

    related: list[str] = []
    parent = by_id.get(doc.parent_id) if doc.parent_id else None
    if parent is not None:
        stats.hierarchy += 1
        repo.add_link(doc.page_id, parent.page_id, kind="hierarchy", source="deterministic")
        related.append(f"- **Parent:** [[{parent.basename}|{parent.title}]]")
    kids = sorted(children.get(doc.page_id, []), key=lambda c: c.page_id)
    if kids:
        related.append("- **Children:** " + ", ".join(f"[[{c.basename}|{c.title}]]" for c in kids))

    if not related and not suggested:
        return body

    lines = [_RELATED_START, "", "## Related", "", *related]
    if suggested:
        # An Obsidian callout, not a plain list item: Obsidian *and* Quartz (S-B5) render it as a
        # distinct titled box, which is exactly the "unverified — treat with care" signal AD-30 wants,
        # without inlining the links into prose. A blank line makes it start its own block.
        if related:
            lines.append("")
        lines.append("> [!tip] Suggested (AI — unverified)")
        lines.append("> " + ", ".join(f"[[{s.basename}|{s.title}]]" for s in suggested))
    lines += ["", _RELATED_END]
    return f"{body}\n" + "\n".join(lines) + "\n"
