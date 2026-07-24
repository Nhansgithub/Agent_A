"""Detection — decide whether a page event should enter the flow (FR-01, FR-02, AD-10).

Runs after ingress has authenticated, parsed, routed to a tenant, and dedupe-checked the event. Its
job is the *admission* decision: is this page a candidate PRD for this tenant, and is it safe to
process?

Three questions, in order, and every one must pass:

1. **Is it in the watched source folder?** (FR-01 / AD-14) The published and draft folders are
   different, adjacent folders, so the agent's own output is never in the watched set — this is the
   *primary* self-ingestion guard.
2. **Is it the agent's own output anyway?** (AD-10 defense-in-depth) Reject a page carrying the
   `agent-generated` label or authored by the agent's own account, in case the structural guard is
   ever bypassed.
3. **Does the title match `final_PRD_<name>`?** (FR-02) A non-match is not a rejection — it routes to
   the FR-02a rename-request path, because a human may simply have named it wrong.

This is a plain rule, not an LLM call — the *content* confirmation is the Classifier's job (FR-03).
Detection only decides admission, so it stays deterministic and cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.adapters.confluence import ConfluenceAdapter
from app.config.constants import AGENT_GENERATED_LABEL, matches_prd_title
from app.config.schema import TenantConfig
from app.domain.events import ConfluencePageEvent


class DetectionVerdict(StrEnum):
    """Why a page was admitted or not. Every non-admit is a normal outcome, not an error."""

    ADMIT = "admit"
    """A title-matching page in the watched folder — proceed to the Classifier (FR-03)."""

    TITLE_MISMATCH = "title_mismatch"
    """In the watched folder but wrongly named — route to the FR-02a rename request."""

    NOT_IN_SOURCE_FOLDER = "not_in_source_folder"
    """Not in the watched folder — drop. Draft/published pages and edits elsewhere land here."""

    AGENT_GENERATED = "agent_generated"
    """The agent's own output (label or author) — drop, never re-ingest (AD-10)."""


@dataclass(frozen=True, slots=True)
class DetectionResult:
    verdict: DetectionVerdict
    reason: str

    @property
    def admitted(self) -> bool:
        return self.verdict is DetectionVerdict.ADMIT

    @property
    def needs_rename_request(self) -> bool:
        return self.verdict is DetectionVerdict.TITLE_MISMATCH


class DetectionAgent:
    """Applies the AD-10 admission guard to a Confluence page event.

    The agent's own account id is resolved once per tenant via the adapter and cached — AD-10 is
    explicit that this id has ONE source, reused by the AD-18 publish restriction, never guessed
    independently by two units.
    """

    __slots__ = ("_agent_account_cache", "_confluence")

    def __init__(self, confluence: ConfluenceAdapter) -> None:
        self._confluence = confluence
        self._agent_account_cache: dict[str, str] = {}

    async def evaluate(self, event: ConfluencePageEvent, tenant: TenantConfig) -> DetectionResult:
        # 1. Watched-folder check (FR-01, AD-14) — the primary self-ingestion guard.
        if not await self._in_source_folder(event, tenant):
            return DetectionResult(
                DetectionVerdict.NOT_IN_SOURCE_FOLDER,
                f"page {event.page_id} is not in the watched source folder "
                f"{tenant.confluence_source_folder_id}",
            )

        # 2. Defense-in-depth: never re-ingest the agent's own output (AD-10 b, c).
        if await self._is_agent_generated(event, tenant):
            return DetectionResult(
                DetectionVerdict.AGENT_GENERATED,
                f"page {event.page_id} is agent-generated output; not re-ingested",
            )

        # 3. Title gate (FR-02). A mismatch is handled by a human, not dropped.
        if not matches_prd_title(event.title):
            return DetectionResult(
                DetectionVerdict.TITLE_MISMATCH,
                f"page {event.page_id} title {event.title!r} does not match final_PRD_<name>",
            )

        return DetectionResult(
            DetectionVerdict.ADMIT, f"page {event.page_id} is a candidate PRD in the source folder"
        )

    async def _in_source_folder(self, event: ConfluencePageEvent, tenant: TenantConfig) -> bool:
        source = tenant.confluence_source_folder_id

        # Fast path: the webhook payload carried the container.
        if event.container_id:
            return event.container_id == source

        # The page-created payload does not reliably carry the container (PRD §13 Q3/Q4), so fall
        # back to the ancestors lookup rather than guessing (AD-14).
        ancestors = await self._confluence.get_page_ancestors(event.page_id)
        return source in ancestors

    async def _is_agent_generated(self, event: ConfluencePageEvent, tenant: TenantConfig) -> bool:
        # (b) the reserved label. Prefer the labels already on the event; fall back to a read.
        labels = event.labels or await self._confluence.get_labels(event.page_id)
        if AGENT_GENERATED_LABEL in labels:
            return True

        # (c) the agent's own account authored it.
        if event.creator_account_id:
            return event.creator_account_id == await self._agent_account_id(tenant)
        return False

    async def _agent_account_id(self, tenant: TenantConfig) -> str:
        """Resolve once per tenant and cache (AD-10 — one source for this id)."""
        cached = self._agent_account_cache.get(tenant.project_id)
        if cached is None:
            cached = await self._confluence.get_current_user()
            self._agent_account_cache[tenant.project_id] = cached
        return cached
