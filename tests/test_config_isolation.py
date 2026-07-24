"""Story 1.2 / Story 6.6 — NFR-05: the source tree is grep-clean of project literals (AD-4).

The "swap a reviewer or onboard a project by editing config only" promise (NFR-02) is only true if
no project-specific literal has leaked into code, a prompt, or a `SKILL.md`. A leaked folder id or
account id is invisible until a second tenant is added and silently routes to the wrong place.

This test does the grep automatically, against whatever registry is actually configured — so it
keeps protecting the invariant as the codebase grows, rather than being a one-off manual check.

The only value allowed to be cross-tenant-constant in the source tree is the reserved
`agent-generated` label (Story 1.2 AC, AD-10).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config.registry import ConfigRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Where project literals are *allowed* to appear.
ALLOWED_PATHS = {
    PROJECT_ROOT / "config",  # the registry itself — its whole purpose
    PROJECT_ROOT / "tests",  # tests legitimately name their own fixture values
    PROJECT_ROOT / "planning-artifacts",  # read-only source-of-truth documents
    PROJECT_ROOT / "implementation-state",
    PROJECT_ROOT / ".venv",
}

# Searched for leaks: application code, agent prompts, and skill files.
SEARCHED_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".j2"}
SEARCHED_ROOTS = ["app", "fixtures", "deploy"]

# Short values produce false positives against ordinary English; a real project key is >= 3 chars.
MIN_LITERAL_LENGTH = 3


def _configured_registry() -> ConfigRegistry:
    """Prefer this deployment's real registry; fall back to the shipped example."""
    real = PROJECT_ROOT / "config" / "registry.yaml"
    example = PROJECT_ROOT / "config" / "registry.example.yaml"
    return ConfigRegistry.from_yaml_file(real if real.is_file() else example)


def _project_literals() -> dict[str, str]:
    """Every project-specific literal in the registry, mapped to the field it came from."""
    literals: dict[str, str] = {}
    for tenant in _configured_registry().tenants.values():
        for field in (
            "confluence_source_folder_id",
            "confluence_draft_folder_id",
            "confluence_published_folder_id",
            "jira_main_project_key",
            "jira_review_project_key",
            "pm_account_id",
            "head_of_product_account_id",
            "admin_account_id",
            "md_export_dir",
        ):
            value = getattr(tenant, field)
            if isinstance(value, str) and len(value) >= MIN_LITERAL_LENGTH:
                literals[value] = f"{tenant.project_id}.{field}"
    return literals


def _searchable_files() -> list[Path]:
    files: list[Path] = []
    for root_name in SEARCHED_ROOTS:
        root = PROJECT_ROOT / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in SEARCHED_SUFFIXES:
                continue
            if any(allowed in path.parents for allowed in ALLOWED_PATHS):
                continue
            if "__pycache__" in path.parts:
                continue
            files.append(path)
    return files


def test_no_project_literal_appears_outside_the_config_registry() -> None:
    """NFR-05 — grep-clean. A hit here means a literal must move into config/registry.yaml."""
    literals = _project_literals()
    assert literals, "no literals extracted — the grep test would vacuously pass"

    leaks: list[str] = []
    for path in _searchable_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for literal, origin in literals.items():
            # Word-boundary match so a short project key does not match inside a longer word.
            if re.search(rf"(?<![\w-]){re.escape(literal)}(?![\w-])", text):
                relative = path.relative_to(PROJECT_ROOT)
                leaks.append(f"{relative}: contains {literal!r} (config field {origin})")

    assert not leaks, (
        "NFR-05 violation — project-specific literals leaked outside the config registry.\n"
        "Move each value into config/registry.yaml and inject the tenant config instead:\n  "
        + "\n  ".join(leaks)
    )


def test_the_reserved_label_is_the_only_hard_coded_cross_tenant_constant() -> None:
    """AD-10 / AD-4 — `agent-generated` is identical for every tenant, so it is not a project literal."""
    from app.config.constants import AGENT_GENERATED_LABEL

    assert AGENT_GENERATED_LABEL == "agent-generated"
    assert AGENT_GENERATED_LABEL not in _project_literals(), (
        "the reserved system label must not also be a configurable per-tenant value"
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("final_PRD_Widget Manager", True),
        ("final_PRD_x", True),
        ("  final_PRD_Padded  ", True),
        ("final_PRD_", False),
        ("Final_PRD_Widget", False),  # case-sensitive per the demo-agreed convention
        ("draft_PRD_Widget", False),
        ("PRD_Widget", False),
        ("final PRD Widget", False),
        ("my final_PRD_Widget", False),  # must match from the start
    ],
)
def test_title_gate_pattern(title: str, expected: bool) -> None:
    """FR-02 — the title gate is a cross-tenant constant, correctly hard-coded (Story 2.2 uses it)."""
    from app.config.constants import matches_prd_title

    assert matches_prd_title(title) is expected


def test_prd_name_is_extracted_from_the_title() -> None:
    from app.config.constants import prd_name_from_title

    assert prd_name_from_title("final_PRD_Widget Manager") == "Widget Manager"
    assert prd_name_from_title("not a prd") is None
