"""Load each role's `SKILL.md` (AD-6).

Every role-agent is "a per-role system prompt + `SKILL.md` over one shared runtime". The skill file is
the primary quality-tuning surface — it is iterated without touching code — so it is loaded from disk
at use time rather than baked into a Python string.

Skill files must stay grep-clean of project literals (AD-4): they describe a *role*, never a specific
tenant's folders or accounts. The NFR-05 isolation test scans them alongside code.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent


class SkillNotFound(FileNotFoundError):
    """A role has no SKILL.md. A role-agent without its skill file is a build error, not a runtime one."""


@cache
def load_skill(role: str) -> str:
    """Return the `SKILL.md` text for a role directory (e.g. ``"classifier"``)."""
    path = _AGENTS_DIR / role / "SKILL.md"
    if not path.is_file():
        raise SkillNotFound(
            f"no SKILL.md for role {role!r} at {path}. Each role-agent ships its skill file (AD-6)."
        )
    return path.read_text(encoding="utf-8").strip()
