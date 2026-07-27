"""Load each Agent B role's `SKILL.md` (mirrors `app.agents.skills`, AD-6).

The skill file is the primary quality-tuning surface — iterated without touching code — so it is read
from disk at use time. Skill files stay grep-clean of project literals (AD-4): they describe a *role*.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent


class SkillNotFound(FileNotFoundError):
    """A role has no SKILL.md — a build error, not a runtime one."""


@cache
def load_skill(role: str) -> str:
    path = _AGENTS_DIR / role / "SKILL.md"
    if not path.is_file():
        raise SkillNotFound(
            f"no SKILL.md for agent_b role {role!r} at {path}. Each role ships its skill file (AD-6)."
        )
    return path.read_text(encoding="utf-8").strip()
