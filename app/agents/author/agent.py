"""The Author agent (FR-05, FR-11, AD-17).

Drafts the first UserDoc from a PRD, runs one self-critique pass, and later revises on PM feedback.
Structure is decided by the `SKILL.md` rubric, not a hardcoded template (FR-05).

Two things this module is careful about:

* **The self-critique is exactly one pass** (FR-05): draft → critique against the skill → single
  revision. It is a *drafting aid*, never an acceptance gate — the human PM PASS is the sole
  content-quality gate (AD-17). The method returns the revised draft; it does not decide "done".
* **The model comes from config** (`system.models.author`, AD-17/AD-4), never a literal here.

Output is Markdown. Publication (create the Confluence page, convert, stamp, move) is the Publisher's
job in Story 3.3 — the Author only writes text.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.llm import CallMetadata, LlmClient
from app.agents.skills import load_skill
from app.domain.errors import AgentError

_ROLE = "author"
_MAX_PRD_CHARS = 80_000


@dataclass(frozen=True, slots=True)
class Draft:
    """A UserDoc draft plus the numbers that let LangSmith show per-round cost (NFR-09)."""

    title: str
    markdown: str
    self_critique_applied: bool
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class AuthorAgent:
    """Drafts and revises the UserDoc."""

    __slots__ = ("_llm", "_model")

    def __init__(self, llm: LlmClient, *, model: str) -> None:
        self._llm = llm
        self._model = model

    # -- FR-05: first draft + one self-critique pass -------------------------------------------

    async def draft(self, *, prd_title: str, prd_markdown: str, metadata: CallMetadata) -> Draft:
        """Produce the first UserDoc draft, then run exactly one self-critique-and-revise pass."""
        skill = load_skill(_ROLE)
        prd = self._trim(prd_markdown)

        first = await self._llm.complete(
            model=self._model,
            system=skill,
            prompt=(
                f"Write the first draft of the end-user UserDoc for this PRD.\n\n"
                f"PRD title: {prd_title}\n\nPRD content (Markdown):\n---\n{prd}\n---"
            ),
            metadata=metadata,
        )

        # The single self-critique pass (FR-05). One round: critique THEN revise, in one call, so it
        # cannot become an unbounded loop — and so it is unmistakably a drafting aid, not a gate.
        revised = await self._llm.complete(
            model=self._model,
            system=skill,
            prompt=(
                "Here is your first draft of the UserDoc:\n---\n"
                f"{first.text}\n---\n\n"
                "Critique it once against your skill (opening in the user's language; every procedure "
                "followable; nothing about internals or out-of-scope features; skimmable), then output "
                "the single revised UserDoc. Output only the revised document."
            ),
            metadata=metadata,
        )

        return Draft(
            title=self._extract_title(revised.text, fallback=prd_title),
            markdown=revised.text.strip(),
            self_critique_applied=True,
            input_tokens=first.input_tokens + revised.input_tokens,
            output_tokens=first.output_tokens + revised.output_tokens,
        )

    # -- FR-11: revise on confirmed structured feedback ----------------------------------------

    async def revise(
        self,
        *,
        current_markdown: str,
        structured_feedback: str,
        metadata: CallMetadata,
    ) -> Draft:
        """Apply confirmed structured feedback to produce a new draft (FR-11).

        No self-critique pass here — the human is already in the loop, so a second machine opinion
        adds cost without adding a gate (NFR-09). The redraft loop's guardrail is `review_round` +
        per-round cost in LangSmith, carried on `metadata`.
        """
        response = await self._llm.complete(
            model=self._model,
            system=load_skill(_ROLE),
            prompt=(
                "Revise the UserDoc below by applying the reviewer's structured feedback. Apply each "
                "point precisely, preserve everything not mentioned, and keep the document coherent. "
                "Output only the revised UserDoc.\n\n"
                f"Current UserDoc:\n---\n{current_markdown}\n---\n\n"
                f"Reviewer feedback:\n---\n{structured_feedback}\n---"
            ),
            metadata=metadata,
        )
        return Draft(
            title=self._extract_title(response.text, fallback="UserDoc"),
            markdown=response.text.strip(),
            self_critique_applied=False,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

    async def summarize_changes(
        self, *, before: str, after: str, feedback: str, metadata: CallMetadata
    ) -> str:
        """A short, specific summary of what changed, for the FR-11 re-request comment."""
        response = await self._llm.complete(
            model=self._model,
            system="You summarize document revisions in 2-4 short bullet points. Be specific and "
            "concrete about what changed. Output only the bullets.",
            prompt=(
                f"Reviewer feedback:\n{feedback}\n\n"
                f"Summarize what changed between these two versions.\n\n"
                f"BEFORE:\n{before[:8000]}\n\nAFTER:\n{after[:8000]}"
            ),
            metadata=metadata,
        )
        return response.text.strip()

    # -- helpers -------------------------------------------------------------------------------

    @staticmethod
    def _trim(prd_markdown: str) -> str:
        prd = prd_markdown.strip()
        if not prd:
            raise AgentError(
                message="The PRD page has no content to draft from.",
                suggested_fix="Confirm the source page actually contains the PRD text.",
                operation="author.draft",
            )
        if len(prd) > _MAX_PRD_CHARS:
            prd = prd[:_MAX_PRD_CHARS] + "\n\n[...truncated for length...]"
        return prd

    @staticmethod
    def _extract_title(markdown: str, *, fallback: str) -> str:
        for line in markdown.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
            if stripped:
                break
        return fallback
