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

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.adapters.confluence import ConfluenceAdapter
from app.config.constants import PRD_CORRELATION_PROPERTY
from app.config.schema import TenantConfig
from app.domain.atlassian import ConfluencePage
from app.domain.errors import AgentError
from app.domain.state import utc_now


@dataclass(frozen=True, slots=True)
class PublishedDraft:
    page: ConfluencePage
    created: bool
    """False when an existing draft for this run was adopted rather than created (AD-11)."""


@dataclass(frozen=True, slots=True)
class DraftRecovery:
    """The outcome of an FR-16 draft-deletion recovery attempt."""

    action: str
    """``healthy`` (the page was fine — a stale/duplicate event or already restored), ``restored``
    (untrashed in place, same id, the ticket link still works), ``recreated`` (a new page holds the
    last content — `page_id` changed), or ``unrecoverable`` (purged/unreadable — a human must act)."""

    page_id: str | None
    """The live page id after recovery — a NEW id when ``recreated``, else the original."""


@dataclass(frozen=True, slots=True)
class PublishResult:
    """The outcome of the FR-15 publish transaction."""

    md_export_path: str | None
    restriction_applied: bool
    moved: bool
    exported: bool
    restriction_skipped: bool = False
    """True when the tenant opted out of FR-15 step 1 (`require_edit_restriction: false`). The caller
    surfaces this to the humans — a published page that is still editable must not look identical to
    a protected one."""


#: Called after each publish side-effect to persist its sub-checkpoint (AD-18). Non-`stage` markers,
#: so it goes through `repository.update_fields` — the orchestrator still owns `stage` (AD-2).
ProgressCallback = Callable[..., Awaitable[None]]


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

    async def recover_draft(
        self, *, tenant: TenantConfig, prd_id: str, page_id: str
    ) -> DraftRecovery:
        """Restore or recreate a UserDoc draft that was deleted mid-flow (FR-16).

        Fully idempotent, so it is safe to call on every deletion event, on a `publishing` resume, or
        twice on a redelivery:

        1. read the page — if it is already ``current``, the deletion was undone or the event is
           stale → ``healthy``, no change;
        2. if ``trashed``, try restore-from-trash (same id → the review-ticket link still works), then
           re-move it into the draft folder (restore drops folder placement);
        3. if the restore fails, recreate a new page from the content read in step 1 (trashed pages
           are still readable) — the ``exact same content as the latest version`` — stamp it and place
           it in the draft folder; the page id changes, so the caller repoints the state;
        4. if the page cannot be read at all (purged), it is ``unrecoverable`` — a human must restore
           it or the run must be re-drafted.
        """
        try:
            page = await self._confluence.get_page(page_id)
        except AgentError:
            return DraftRecovery(action="unrecoverable", page_id=None)

        if not page.is_trashed:
            return DraftRecovery(action="healthy", page_id=page_id)

        try:
            await self._confluence.restore_page(page_id, title=page.title, version=page.version)
            await self._confluence.move_page(page_id, tenant.confluence_draft_folder_id)
            return DraftRecovery(action="restored", page_id=page_id)
        except AgentError:
            # Restore failed (purge, plan limit, race). Recreate from the content we just read.
            space_id = page.space_id or await self._draft_space_id(tenant)
            recreated = await self._confluence.create_page(
                space_id=space_id, title=page.title, body_storage=page.body_storage
            )
            await self._stamp(recreated.id, prd_id)
            await self._confluence.move_page(recreated.id, tenant.confluence_draft_folder_id)
            return DraftRecovery(action="recreated", page_id=recreated.id)

    async def _draft_space_id(self, tenant: TenantConfig) -> str:
        folder = await self._confluence.get_folder(tenant.confluence_draft_folder_id)
        return str(folder.get("spaceId") or "")

    async def _stamp(self, page_id: str, prd_id: str) -> None:
        """AD-10 + AD-11 — mark the page as agent output and carry the correlation marker.

        Both must be set so detection never re-ingests it (AD-10) and a resume can adopt it (AD-11).
        """
        await self._confluence.stamp_agent_generated(page_id)
        await self._confluence.set_content_property(page_id, PRD_CORRELATION_PROPERTY, prd_id)

    # -- FR-15: the publish transaction (AD-18, AD-14, AD-10) -----------------------------------

    async def publish(
        self,
        *,
        tenant: TenantConfig,
        prd_id: str,
        page_id: str,
        page_title: str,
        agent_account_id: str,
        space_admin_account_ids: tuple[str, ...] = (),
        on_step: ProgressCallback,
        restriction_done: bool = False,
        move_done: bool = False,
        export_done: bool = False,
        existing_md_path: str | None = None,
    ) -> PublishResult:
        """Run the four ordered publish side-effects, each idempotent and sub-checkpointed (AD-18).

        A resume of the `publishing` stage passes the `*_done` flags from the state record, so a
        side-effect that already completed is skipped and never re-applied. Order is fixed:
        restrict → move → export → (caller marks complete).

        Args:
            agent_account_id: the AD-10 cached agent account. **Must** be in the restriction allow-list
                or the agent locks itself out of the page it just published; the adapter enforces a
                non-empty list, and this method always includes it.
            on_step: persists each sub-checkpoint as it completes.
        """
        # (1) Confluence edit restriction — restricts *who may edit*, not a content freeze (AD-18).
        # The agent account and any space admins are always included, so a re-apply cannot lock out.
        # Skipped only when the tenant explicitly opted out (Confluence Free has no restrictions —
        # schema note on `require_edit_restriction`, BLOCKERS.md → B-7). A skip records nothing: the
        # checkpoint must never claim a restriction that was not applied, or a later resume would
        # believe the page is protected when it is editable by anyone.
        restriction_skipped = not tenant.require_edit_restriction
        if not restriction_done and not restriction_skipped:
            allowed = [agent_account_id, *space_admin_account_ids]
            await self._confluence.set_edit_restriction(page_id, allowed_account_ids=allowed)
            await on_step(restriction_applied_at=utc_now())

        # (2) Move into the published folder (adjacent to source, so never re-ingested — AD-10/AD-14).
        # A no-op if the page is already placed there, which is what makes a resume safe.
        if not move_done:
            await self._confluence.move_page(page_id, tenant.confluence_published_folder_id)
            await on_step(moved_to_published_at=utc_now())

        # (3) Export storage → Markdown to server disk for the later SSG step (FR-15 step 3).
        md_path = existing_md_path
        if not export_done:
            page = await self._confluence.get_page(page_id)
            markdown = self._confluence.storage_to_markdown(page.body_storage)
            md_path = self._write_export(tenant.md_export_dir, prd_id, page_title, markdown)
            await on_step(md_exported_at=utc_now(), md_export_path=md_path)

        return PublishResult(
            md_export_path=md_path,
            restriction_applied=not restriction_done and not restriction_skipped,
            moved=not move_done,
            exported=not export_done,
            restriction_skipped=restriction_skipped,
        )

    @staticmethod
    def _write_export(md_export_dir: str, prd_id: str, title: str, markdown: str) -> str:
        """Write the `.md` to the tenant's export dir. Overwrite-safe, so a resume re-export is fine."""
        directory = Path(md_export_dir)
        directory.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "userdoc"
        path = directory / f"{prd_id}-{slug}.md"
        path.write_text(markdown, encoding="utf-8")
        return str(path)
