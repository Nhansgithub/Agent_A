"""Publisher — Confluence page lifecycle for the UserDoc (FR-06 draft, FR-15 publish; AD-10/11/14/18).

Two responsibilities, both idempotent so a resume never double-creates or re-applies:

* **`publish_draft` (FR-06)** — create the draft page from the Author's Markdown, place it in the
  draft folder (v1 move, AD-14), and stamp it as agent output (`agent-generated` label + `prd_id`
  content property, AD-10/AD-11). If a draft for this run already exists it is *adopted*, not
  recreated (AD-11 find-or-create by the correlation marker).
* **`update_draft`** — revise the existing page in place for the FR-11 redraft loop.

The FR-15 publish transaction (restrict/move/export) is added in Epic 5; this module owns only the
draft-side page lifecycle so far.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.confluence import ConfluenceAdapter
from app.config.constants import PRD_CORRELATION_PROPERTY
from app.config.schema import TenantConfig
from app.domain.atlassian import ConfluencePage


@dataclass(frozen=True, slots=True)
class PublishedDraft:
    page: ConfluencePage
    created: bool
    """False when an existing draft for this run was adopted rather than created (AD-11)."""


class Publisher:
    """Owns the UserDoc's Confluence page lifecycle."""

    __slots__ = ("_confluence",)

    def __init__(self, confluence: ConfluenceAdapter) -> None:
        self._confluence = confluence

    async def publish_draft(
        self,
        *,
        tenant: TenantConfig,
        prd_id: str,
        title: str,
        markdown: str,
        space_id: str,
        existing_page_id: str | None = None,
    ) -> PublishedDraft:
        """Create (or adopt) the draft page in the draft folder, stamped as agent output (FR-06).

        AD-11 idempotency, in order:
          1. if the state record already has a page id, reuse it (update in place);
          2. else search the draft folder for a page carrying this run's `prd_id` marker — adopt an
             orphan created in a crash window before its id was persisted;
          3. else create a new page.
        """
        storage = self._confluence.markdown_to_storage(markdown)

        # 1. Known id — update in place.
        if existing_page_id:
            page = await self._confluence.get_page(existing_page_id)
            updated = await self._confluence.update_page(
                page_id=existing_page_id, title=title, body_storage=storage, version=page.version
            )
            return PublishedDraft(page=updated, created=False)

        # 2. Adopt an orphan created by a previous crashed attempt.
        orphan = await self._confluence.find_page_by_prd_marker(
            tenant.confluence_draft_folder_id, prd_id
        )
        if orphan is not None:
            updated = await self._confluence.update_page(
                page_id=orphan.id, title=title, body_storage=storage, version=orphan.version
            )
            return PublishedDraft(page=updated, created=False)

        # 3. Genuinely new — create, place in the draft folder, and stamp.
        page = await self._confluence.create_page(
            space_id=space_id, title=title, body_storage=storage
        )
        await self._stamp(page.id, prd_id)
        await self._confluence.move_page(page.id, tenant.confluence_draft_folder_id)
        return PublishedDraft(page=page, created=True)

    async def update_draft(self, *, page_id: str, title: str, markdown: str) -> ConfluencePage:
        """Revise the draft in place for the FR-11 redraft loop."""
        storage = self._confluence.markdown_to_storage(markdown)
        page = await self._confluence.get_page(page_id, with_body=False)
        return await self._confluence.update_page(
            page_id=page_id, title=title, body_storage=storage, version=page.version
        )

    async def _stamp(self, page_id: str, prd_id: str) -> None:
        """AD-10 + AD-11 — mark the page as agent output and carry the correlation marker.

        Both must be set so detection never re-ingests it (AD-10) and a resume can adopt it (AD-11).
        """
        await self._confluence.stamp_agent_generated(page_id)
        await self._confluence.set_content_property(page_id, PRD_CORRELATION_PROPERTY, prd_id)
