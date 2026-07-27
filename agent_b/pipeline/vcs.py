"""Version the generated vault with one git commit per scheduled pull (S-B4).

The vault is a generated **projection** (AD-28): humans never edit it, so its git history is not a
collaboration log but the audit trail of *what the KB looked like after each nightly run* — and the
substrate Quartz (S-B5) builds the published site from. A commit is made only when the pull actually
changed a file, so an unchanged run adds no empty commit (idempotency, D-41).

`VaultVcs` is the seam the pull orchestrator depends on; the unit suite injects a fake so it neither
shells out nor needs a real repo. `GitVault` is the real implementation used on the Droplet.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol


class VaultVcs(Protocol):
    """Commit the current vault state. Returns the new commit id, or None when nothing changed."""

    def commit(self, message: str) -> str | None: ...


class GitVault:
    """A thin `git` wrapper scoped to the vault directory (no network, local tool only)."""

    __slots__ = ("_dir",)

    def __init__(self, vault_dir: str) -> None:
        self._dir = Path(vault_dir)

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self._dir), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def commit(self, message: str) -> str | None:
        self._dir.mkdir(parents=True, exist_ok=True)
        if not (self._dir / ".git").exists():
            self._git("init", "-q")
        self._git("add", "-A")
        if not self._git("status", "--porcelain"):
            return None  # nothing staged — an unchanged pull makes no empty commit
        # Identity is passed per-invocation so the job never depends on a configured global git user.
        self._git(
            "-c",
            "user.email=agent-b@localhost",
            "-c",
            "user.name=Agent B",
            "commit",
            "-q",
            "-m",
            message,
        )
        return self._git("rev-parse", "HEAD")
