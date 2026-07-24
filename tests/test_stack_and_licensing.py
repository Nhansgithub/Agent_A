"""Story 1.1 — the Architecture Spine's Stack table is an executable contract.

Two acceptance criteria are enforced here:

* **NFR-10 / AD-6 licensing hygiene** — only the MIT-licensed ``langgraph`` core library is used.
  The ``langgraph-api`` server product (what ``langgraph dev|build`` runs) is Elastic-licensed;
  depending on it would cost money and require a server the 1 GB box cannot afford (AD-21).
  This test fails the build if it ever appears in the resolved dependency tree.
* **Stack-table pins** — the versions the architecture web-verified are the versions installed.
  Drift here is how "works locally, fails on the Droplet" starts.
"""

from __future__ import annotations

import importlib.metadata as md

import pytest

# Architecture Spine -> "Stack" table (r2, re-verified 2026-07-24).
# Package name -> exact version the architecture pinned.
STACK_TABLE_PINS = {
    "fastapi": "0.136.3",
    "uvicorn": "0.51.0",
    "langgraph": "1.2.9",
    "anthropic": "0.117.0",
    "langsmith": "0.10.9",
    "markdownify": "1.2.3",
}

# Pinned as a range rather than an exact version. name -> (floor, exclusive ceiling)
STACK_TABLE_RANGES = {
    # langgraph core pins langgraph-checkpoint >=3.0.1,<5; 4.1.1 provides InMemorySaver (AD-11).
    "langgraph-checkpoint": ("4.1.1", "5"),
}

# AD-6 / NFR-10: these must never enter the dependency tree.
ELASTIC_LICENSED_FORBIDDEN = ["langgraph-api", "langgraph-cli", "langgraph-runtime-inmem"]


def _parse(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


@pytest.mark.parametrize(("package", "expected"), sorted(STACK_TABLE_PINS.items()))
def test_stack_table_version_is_installed(package: str, expected: str) -> None:
    assert md.version(package) == expected, (
        f"{package} drifted from the Architecture Spine Stack table pin {expected}. "
        "Either restore the pin or update the Stack table and record the change in "
        "implementation-state/DECISION-LOG.md."
    )


@pytest.mark.parametrize(("package", "bounds"), sorted(STACK_TABLE_RANGES.items()))
def test_stack_table_range_is_satisfied(package: str, bounds: tuple[str, str]) -> None:
    floor, ceiling = bounds
    installed = _parse(md.version(package))
    assert _parse(floor) <= installed < _parse(ceiling), (
        f"{package} {md.version(package)} is outside the architecture's range [{floor}, {ceiling})."
    )


@pytest.mark.parametrize("package", ELASTIC_LICENSED_FORBIDDEN)
def test_elastic_licensed_langgraph_server_is_not_installed(package: str) -> None:
    """NFR-10 — the licensed server product must not be a dependency."""
    with pytest.raises(md.PackageNotFoundError):
        md.version(package)


def test_langgraph_core_is_mit_licensed() -> None:
    """AD-6 — the orchestration library we *do* depend on is the MIT core."""
    metadata = md.metadata("langgraph")
    declared = " ".join(
        filter(None, [metadata.get("License-Expression", ""), metadata.get("License", "")])
    )
    classifiers = " ".join(metadata.get_all("Classifier") or [])
    assert "MIT" in declared or "MIT" in classifiers, (
        f"langgraph no longer declares an MIT license (got: {declared!r}). "
        "NFR-10 requires the MIT core only — stop and re-verify before shipping."
    )


def test_python_runtime_matches_the_container_base() -> None:
    """AD-21 — local dev runs the same minor version as the python:3.12-slim base image."""
    import sys

    assert sys.version_info[:2] == (3, 12), (
        f"Running Python {sys.version_info.major}.{sys.version_info.minor}, but the deploy base "
        "image is python:3.12-slim. Use the project's .python-version / .venv."
    )
