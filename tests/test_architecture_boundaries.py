"""Story 1.1 — AD-1 layered boundaries are enforced by the test suite, not by code review.

The dependency rule is the architecture's single most load-bearing invariant:

    webhooks -> router -> orchestrator -> agents -> {adapters, repository} -> {Atlassian, SQLite}

**Only adapters open an HTTP socket to Atlassian. Only the repository runs SQL.** If an agent could
reach Atlassian directly it would bypass the auth / retry / ADF / API-version rules the adapter layer
owns (AD-7); if it could reach SQLite directly it would bypass the single-durable-store and
stage-ownership rules (AD-2, AD-11).

The contracts themselves live in ``pyproject.toml`` under ``[tool.importlinter]`` — this module runs
them so a violation fails ``pytest``, not just a separate lint step someone forgets.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Contracts declared in pyproject.toml [tool.importlinter]. Bump when a contract is added.
EXPECTED_CONTRACT_COUNT = 5

# The scaffold the Architecture Spine prescribes (Structural Seed -> source tree).
EXPECTED_PACKAGES = [
    "app/webhooks",
    "app/orchestrator",
    "app/agents",
    "app/adapters",
    "app/repository",
    "app/config",
    "app/domain",
    "app/admin",
]

EXPECTED_MODULES = ["app/main.py", "app/router.py"]

EXPECTED_DIRECTORIES = [
    "fixtures/classifier/dev",
    "fixtures/classifier/holdout",
    "deploy",
    "tests",
]


def test_import_linter_contracts_hold() -> None:
    """Run every AD-1 / AD-2 / AD-4 / AD-6 import contract defined in pyproject.toml.

    Invoked through the ``lint-imports`` console script deliberately. ``python -m importlinter.cli``
    exits 0 without running anything, which would make this test pass vacuously — the worst possible
    failure mode for an architecture guard, since it reports green while enforcing nothing.
    """
    executable = Path(sys.executable).parent / "lint-imports"
    assert executable.is_file(), (
        f"{executable} not found — install the dev extras: pip install -e '.[dev]'"
    )

    result = subprocess.run(
        [str(executable)], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, f"Architecture boundary violation.\n\n{output}"

    # Guard against a silent no-op: a config it cannot find would also exit 0.
    summary = re.search(r"Contracts:\s*(\d+)\s*kept,\s*(\d+)\s*broken", output)
    assert summary, (
        f"import-linter ran but printed no contract summary — the guard is enforcing nothing.\n\n{output}"
    )
    kept, broken = int(summary.group(1)), int(summary.group(2))
    assert kept >= EXPECTED_CONTRACT_COUNT, (
        f"expected at least {EXPECTED_CONTRACT_COUNT} contracts, only {kept} ran. "
        "Did a contract get dropped from pyproject.toml?"
    )
    assert broken == 0, f"{broken} contract(s) broken.\n\n{output}"


def test_scaffold_packages_exist() -> None:
    """The cold-start scaffold from the Spine's Structural Seed is present and importable."""
    missing = [
        pkg for pkg in EXPECTED_PACKAGES if not (PROJECT_ROOT / pkg / "__init__.py").is_file()
    ]
    assert not missing, f"Missing scaffold packages: {missing}"


def test_scaffold_modules_exist() -> None:
    missing = [mod for mod in EXPECTED_MODULES if not (PROJECT_ROOT / mod).is_file()]
    assert not missing, f"Missing scaffold modules: {missing}"


def test_scaffold_directories_exist() -> None:
    """fixtures/classifier/{dev,holdout} are build deliverables for the AD-17 accuracy bar."""
    missing = [d for d in EXPECTED_DIRECTORIES if not (PROJECT_ROOT / d).is_dir()]
    assert not missing, f"Missing scaffold directories: {missing}"


def test_app_package_imports_cleanly() -> None:
    """The FastAPI app constructs without any credential, network call, or database file."""
    from app.main import app

    assert app.title == "PRD-to-UserDoc Automation Agent Flow"
