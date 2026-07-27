"""`ConfluenceAdapter` — every Confluence call in the system goes through here (AD-7, AD-14).

Defaults to Confluence Cloud REST **v2**, with two deliberate exceptions the architecture already
paid for in research. Do not "simplify" these back to v2:

1. **Placing a page into a folder uses the v1 move endpoint**
   `PUT /wiki/rest/api/content/{id}/move/append/{folderId}`. Setting a folder as `parentId` on the
   v2 create/update endpoint returns **500** (AD-14). This is why `create_page` and `move_page` are
   separate steps rather than one call.
2. **Content restrictions are v1-only Cloud endpoints** (`/wiki/rest/api/content/{id}/restriction/*`).

The third trap is in :meth:`set_edit_restriction`: an edit restriction that omits the agent's own
account **locks the agent out of the page it just published**, and a later re-apply then fails. The
method takes the agent account explicitly and refuses to proceed without it (AD-18).
"""

from __future__ import annotations

from typing import Any

from app.adapters.http import AtlassianClient
from app.adapters.markdown import markdown_to_storage, storage_to_markdown
from app.config.constants import AGENT_GENERATED_LABEL, PRD_CORRELATION_PROPERTY
from app.domain.atlassian import (
    ConfluenceAttachment,
    ConfluencePage,
    ConfluencePageRef,
    InlineComment,
)
from app.domain.errors import AgentError

V2 = "/wiki/api/v2"
V1 = "/wiki/rest/api"


class ConfluenceAdapter:
    """Domain-verb access to one tenant's Confluence."""

    __slots__ = ("_client",)

    def __init__(self, client: AtlassianClient) -> None:
        self._client = client

    # -- identity ----------------------------------------------------------------------------

    async def get_current_user(self) -> str:
        """The agent's own accountId (AD-10). Same account as Jira within one Atlassian org."""
        body = await self._client.request("GET", f"{V1}/user/current", operation="get_current_user")
        return str((body or {}).get("accountId", ""))

    # -- reads -------------------------------------------------------------------------------

    async def get_page(self, page_id: str, *, with_body: bool = True) -> ConfluencePage:
        params: dict[str, Any] = {"include-labels": "true", "include-version": "true"}
        if with_body:
            params["body-format"] = "storage"
        body = await self._client.request(
            "GET",
            f"{V2}/pages/{page_id}",
            operation="get_page",
            params=params,
            context={"page": page_id},
        )
        return self._to_page(body or {})

    async def get_page_ancestors(self, page_id: str) -> tuple[str, ...]:
        """Ancestor ids, nearest first — used by the FR-01 watched-folder check (AD-14).

        The `page-created` webhook payload does not reliably carry the page's container, so detection
        falls back to this rather than guessing.
        """
        body = await self._client.request(
            "GET",
            f"{V2}/pages/{page_id}/ancestors",
            operation="get_page_ancestors",
            context={"page": page_id},
        )
        return tuple(
            str(item.get("id")) for item in (body or {}).get("results", []) if item.get("id")
        )

    async def get_folder(self, folder_id: str) -> dict[str, Any]:
        """AD-14 — folders are first-class and addressable by id in v2."""
        return (
            await self._client.request(
                "GET",
                f"{V2}/folders/{folder_id}",
                operation="get_folder",
                context={"folder": folder_id},
            )
            or {}
        )

    async def get_labels(self, page_id: str) -> tuple[str, ...]:
        """AD-10 defense-in-depth — a page carrying `agent-generated` never enters detection."""
        body = await self._client.request(
            "GET",
            f"{V2}/pages/{page_id}/labels",
            operation="get_labels",
            context={"page": page_id},
        )
        return tuple(
            str(item.get("name")) for item in (body or {}).get("results", []) if item.get("name")
        )

    async def find_page_by_prd_marker(self, folder_id: str, prd_id: str) -> ConfluencePage | None:
        """Find a draft page this run already created, by its AD-11 correlation property.

        Confluence page-create and set-content-property are two calls, so unlike Jira the marker is
        **not** atomic with the create (the open item flagged in the architecture memlog). This
        searches the draft folder's children and checks each page's content property, which covers
        the crash window in practice: the orphan is in the draft folder either way.
        """
        body = await self._client.request(
            "GET",
            # v2 folder children live at /direct-children, NOT /children (verified against the live
            # API — /children 404s to the web UI). This is the AD-11 orphan-adoption read.
            f"{V2}/folders/{folder_id}/direct-children",
            operation="find_page_by_prd_marker",
            params={"limit": 100},
            context={"folder": folder_id},
        )
        for child in (body or {}).get("results", []):
            page_id = str(child.get("id", ""))
            if not page_id:
                continue
            if await self.get_content_property(page_id, PRD_CORRELATION_PROPERTY) == prd_id:
                return await self.get_page(page_id)
        return None

    async def get_inline_comment(self, comment_id: str) -> InlineComment:
        """Read one page comment — the FR-17 inline-feedback channel (AD-14).

        Reads via the **v1** content endpoint as the primary path, for two reasons the architecture
        already knows well: v1 is where the other Confluence exceptions live (move, restriction,
        restore), and the v2 ``/inline-comments/{id}`` endpoint is documented to 404 intermittently.
        One v1 call returns everything needed — the highlighted-passage anchor
        (``extensions.inlineProperties.originalSelection``), whether the comment is inline or a
        page-level footer comment (``extensions.location``), the author, the body, and the containing
        page — so the caller need not trust the Automation smart values for anything but the id.

        Falls back to v2 if v1 is unavailable, and parses tolerantly (either shape's field names), the
        same defensive stance the webhook parser takes toward payload variants.
        """
        try:
            body = await self._client.request(
                "GET",
                f"{V1}/content/{comment_id}",
                operation="get_inline_comment",
                params={
                    "expand": (
                        "body.storage,extensions.inlineProperties,extensions.resolution,"
                        "history,container,version"
                    )
                },
                context={"comment": comment_id},
            )
            return self._inline_comment_from_v1(comment_id, body or {})
        except AgentError:
            body = await self._client.request(
                "GET",
                f"{V2}/inline-comments/{comment_id}",
                operation="get_inline_comment_v2",
                params={"body-format": "storage"},
                context={"comment": comment_id},
            )
            return self._inline_comment_from_v2(comment_id, body or {})

    async def get_content_property(self, page_id: str, key: str) -> str | None:
        body = await self._client.request(
            "GET",
            f"{V2}/pages/{page_id}/properties",
            operation="get_content_property",
            params={"key": key},
            context={"page": page_id, "key": key},
        )
        for item in (body or {}).get("results", []):
            if item.get("key") == key:
                return str(item.get("value"))
        return None

    async def list_descendant_pages(
        self, folder_id: str, *, exclude_folder_ids: set[str] | None = None
    ) -> tuple[ConfluencePageRef, ...]:
        """Every page under a folder, recursively — the Agent B KB crawl (AD-14, Epic 7).

        Walks a folder's direct children (pages and sub-folders) and each page's own child pages,
        following v2 cursor pagination. Sub-folders whose id is in ``exclude_folder_ids`` are not
        descended into — that is how the draft/review folder stays out of the KB. Returns lightweight
        refs; the caller fetches bodies with :meth:`get_page` only for pages it keeps.

        Additive read verb: Agent A does not call it, and it changes no existing behaviour.
        """
        exclude = exclude_folder_ids or set()
        collected: list[ConfluencePageRef] = []
        seen: set[str] = set()
        stack: list[tuple[str, str]] = [(folder_id, "folder")]
        while stack:
            container_id, container_type = stack.pop()
            for child in await self._list_direct_children(container_id, container_type):
                child_id = child["id"]
                child_type = child["type"]
                if child_type == "folder":
                    if child_id not in exclude:
                        stack.append((child_id, "folder"))
                elif child_type == "page" and child_id not in seen:
                    seen.add(child_id)
                    collected.append(
                        ConfluencePageRef(
                            id=child_id,
                            title=child["title"],
                            parent_id=container_id,
                            parent_type=container_type,
                        )
                    )
                    stack.append((child_id, "page"))  # a page may itself have child pages
        return tuple(collected)

    async def list_attachments(self, page_id: str) -> tuple[ConfluenceAttachment, ...]:
        """Every attachment on a page (v2, cursor-paginated) — the Agent B image pull (Epic 7, S-B10).

        Additive read verb: Agent A does not call it. `_links.download` is the API-relative path to the
        binary, handed to :meth:`download_attachment`.
        """
        path: str | None = f"{V2}/pages/{page_id}/attachments"
        params: dict[str, Any] | None = {"limit": 250}
        out: list[ConfluenceAttachment] = []
        while path:
            body = (
                await self._client.request(
                    "GET",
                    path,
                    operation="list_attachments",
                    params=params,
                    context={"page": page_id},
                )
                or {}
            )
            for item in body.get("results") or []:
                download = str(((item.get("_links") or {}).get("download")) or "")
                if not download:
                    continue
                out.append(
                    ConfluenceAttachment(
                        id=str(item.get("id") or ""),
                        filename=str(item.get("title") or ""),
                        media_type=str(item.get("mediaType") or ""),
                        download_path=download,
                    )
                )
            path = str((body.get("_links") or {}).get("next") or "") or None
            params = None
        return tuple(out)

    async def download_attachment(self, download_path: str) -> bytes:
        """Fetch one attachment's binary. `download_path` is the v2 `_links.download` (API-relative).

        Confluence's download links are rooted at `/wiki/...`; the API client's base_url is the site
        root, so a `/wiki`-prefixed path is used as-is and any other is prefixed with `/wiki`.
        """
        path = download_path if download_path.startswith("/wiki") else f"/wiki{download_path}"
        return await self._client.download(path, operation="download_attachment")

    async def _list_direct_children(
        self, container_id: str, container_type: str
    ) -> list[dict[str, str]]:
        """One container's immediate children, following cursor pagination, normalized to id/title/type.

        Folders expose mixed children at ``/folders/{id}/direct-children`` (each item carries a
        ``type``); a page's child pages come from ``/pages/{id}/children`` (all pages). Both are v2.
        """
        if container_type == "folder":
            path = f"{V2}/folders/{container_id}/direct-children"
        else:
            path = f"{V2}/pages/{container_id}/children"
        params: dict[str, Any] | None = {"limit": 250}
        out: list[dict[str, str]] = []
        while path:
            body = (
                await self._client.request(
                    "GET",
                    path,
                    operation="list_direct_children",
                    params=params,
                    context={"container": container_id},
                )
                or {}
            )
            for item in body.get("results") or []:
                item_id = str(item.get("id") or "")
                if not item_id:
                    continue
                out.append(
                    {
                        "id": item_id,
                        "title": str(item.get("title") or ""),
                        "type": str(item.get("type") or "page"),
                    }
                )
            path = str((body.get("_links") or {}).get("next") or "")
            params = None  # the cursor is carried in the next link's query string
        return out

    # -- writes ------------------------------------------------------------------------------

    async def create_page(
        self,
        *,
        space_id: str,
        title: str,
        body_storage: str,
        parent_id: str | None = None,
    ) -> ConfluencePage:
        """Create a page.

        `parent_id` may only be another **page**. To place a page in a *folder*, create it and then
        call :meth:`move_page` — passing a folder id as `parentId` here returns 500 (AD-14).
        """
        payload: dict[str, Any] = {
            "spaceId": space_id,
            "status": "current",
            "title": title,
            "body": {"representation": "storage", "value": body_storage},
        }
        if parent_id:
            payload["parentId"] = parent_id

        body = await self._client.request(
            "POST",
            f"{V2}/pages",
            operation="create_page",
            json=payload,
            context={"space": space_id, "title": title[:80]},
        )
        return self._to_page(body or {})

    async def update_page(
        self, *, page_id: str, title: str, body_storage: str, version: int
    ) -> ConfluencePage:
        """Update a page. Confluence requires the *next* version number for optimistic locking."""
        body = await self._client.request(
            "PUT",
            f"{V2}/pages/{page_id}",
            operation="update_page",
            json={
                "id": page_id,
                "status": "current",
                "title": title,
                "body": {"representation": "storage", "value": body_storage},
                "version": {"number": version + 1, "message": "Revised by the UserDoc agent"},
            },
            context={"page": page_id, "version": str(version)},
        )
        return self._to_page(body or {})

    async def restore_page(self, page_id: str, *, title: str, version: int) -> None:
        """Restore a trashed page (status `trashed` → `current`) for FR-16 recovery.

        Confluence Cloud has **no dedicated untrash endpoint**; the supported workaround is a v1
        content PUT that sets `status: current` with the next version number. A restored page returns
        to the space **root** (restore drops folder placement), so the caller re-`move_page`s it into
        the draft folder. Best-effort: if it fails (e.g. the page was purged, or the plan disallows
        it), the caller recreates the page from the last content instead.
        """
        await self._client.request(
            "PUT",
            f"{V1}/content/{page_id}",
            operation="restore_page",
            json={
                "id": page_id,
                "type": "page",
                "status": "current",
                "title": title,
                "version": {"number": version + 1, "message": "Restored by the UserDoc agent"},
            },
            context={"page": page_id, "version": str(version)},
        )

    async def move_page(self, page_id: str, folder_id: str) -> None:
        """Place a page into a folder via the **v1** move endpoint (AD-14).

        The v2 `parentId` path 500s for folder parents. Used both to put the first draft in the
        draft folder (FR-06) and to move the approved doc to the published folder (FR-15 step 2).
        """
        await self._client.request(
            "PUT",
            f"{V1}/content/{page_id}/move/append/{folder_id}",
            operation="move_page",
            context={"page": page_id, "folder": folder_id},
        )

    async def add_label(self, page_id: str, label: str) -> None:
        await self._client.request(
            "POST",
            f"{V1}/content/{page_id}/label",
            operation="add_label",
            json=[{"prefix": "global", "name": label}],
            context={"page": page_id, "label": label},
        )

    async def stamp_agent_generated(self, page_id: str) -> None:
        """AD-10 — the Publisher stamps this on every page it creates, so detection can exclude it."""
        await self.add_label(page_id, AGENT_GENERATED_LABEL)

    async def set_content_property(self, page_id: str, key: str, value: str) -> None:
        """Stamp the AD-11 correlation marker so a resume can adopt an orphan page."""
        await self._client.request(
            "POST",
            f"{V2}/pages/{page_id}/properties",
            operation="set_content_property",
            json={"key": key, "value": value},
            context={"page": page_id, "key": key},
            expected=(200, 201, 400, 409),  # 400/409 = already set; the marker is immutable
        )

    async def set_edit_restriction(self, page_id: str, *, allowed_account_ids: list[str]) -> None:
        """Restrict **who may edit** the page (FR-15 step 1, AD-18).

        This is an access restriction, **not** a content freeze and **not** version pinning:
        Confluence keeps versioning the page normally, and space admins retain access.

        `allowed_account_ids` **must include the agent's own account** (the one resolved once per
        tenant per AD-10). Omitting it locks the agent out of the page it just published — the API
        rejects the call, and a resume that re-applies the restriction would fail too.
        """
        if not allowed_account_ids:
            raise AgentError(
                message="Refusing to apply an edit restriction with an empty allow-list.",
                suggested_fix=(
                    "Include the agent's own accountId (resolved via get_current_user) and any "
                    "space admins. An empty list would lock the agent out of its own page (AD-18)."
                ),
                operation="confluence.set_edit_restriction",
                context={"page": page_id},
            )

        await self._client.request(
            "PUT",
            f"{V1}/content/{page_id}/restriction",
            operation="set_edit_restriction",
            json=[
                {
                    "operation": "update",
                    "restrictions": {
                        "user": [
                            {"type": "known", "accountId": account_id}
                            for account_id in allowed_account_ids
                        ]
                    },
                }
            ],
            context={"page": page_id, "allowed": str(len(allowed_account_ids))},
        )

    # -- conversion --------------------------------------------------------------------------

    @staticmethod
    def storage_to_markdown(storage_html: str) -> str:
        """Confluence storage format → Markdown for the FR-15 `.md` export."""
        return storage_to_markdown(storage_html)

    @staticmethod
    def markdown_to_storage(markdown: str) -> str:
        """Markdown draft → Confluence storage format, for publishing the Author's draft (FR-06)."""
        return markdown_to_storage(markdown)

    # -- internals ---------------------------------------------------------------------------

    @classmethod
    def _inline_comment_from_v1(cls, comment_id: str, body: dict[str, Any]) -> InlineComment:
        """Parse a v1 ``content`` comment. Inline vs footer is ``extensions.location``."""
        extensions = body.get("extensions") or {}
        inline_props = extensions.get("inlineProperties") or {}
        resolution = extensions.get("resolution") or {}
        history = body.get("history") or {}
        version_by = (body.get("version") or {}).get("by") or {}
        container = body.get("container") or {}
        author = (history.get("createdBy") or {}).get("accountId") or version_by.get("accountId")
        return InlineComment(
            id=str(body.get("id") or comment_id),
            page_id=str(container.get("id") or ""),
            author_account_id=str(author or ""),
            body_text=cls._comment_body_text(body),
            section=str(inline_props.get("originalSelection") or ""),
            is_inline=str(extensions.get("location") or "").lower() == "inline"
            or bool(inline_props),
            resolved=str(resolution.get("status") or "open").lower() == "resolved",
        )

    @classmethod
    def _inline_comment_from_v2(cls, comment_id: str, body: dict[str, Any]) -> InlineComment:
        """Parse a v2 ``inline-comments`` object. The anchor is ``properties.inlineOriginalSelection``."""
        properties = body.get("properties") or {}
        version = body.get("version") or {}
        return InlineComment(
            id=str(body.get("id") or comment_id),
            page_id=str(body.get("pageId") or ""),
            author_account_id=str(version.get("authorId") or ""),
            body_text=cls._comment_body_text(body),
            section=str(properties.get("inlineOriginalSelection") or ""),
            is_inline=True,  # a v2 inline-comments read is inline by construction
            resolved=str(body.get("resolutionStatus") or "open").lower() == "resolved",
        )

    @staticmethod
    def _comment_body_text(body: dict[str, Any]) -> str:
        """A comment body → plain text. Storage is HTML; markdownify then strip is enough for a note."""
        storage = ((body.get("body") or {}).get("storage") or {}).get("value") or ""
        if not storage:
            return ""
        return storage_to_markdown(storage).strip()

    @staticmethod
    def _to_page(body: dict[str, Any]) -> ConfluencePage:
        version = body.get("version") or {}
        page_body = (body.get("body") or {}).get("storage") or {}
        labels = (body.get("labels") or {}).get("results") or []
        return ConfluencePage(
            id=str(body.get("id", "")),
            title=str(body.get("title") or ""),
            version=int(version.get("number") or 1),
            space_id=str(body.get("spaceId")) if body.get("spaceId") else None,
            parent_id=str(body.get("parentId")) if body.get("parentId") else None,
            body_storage=str(page_body.get("value") or ""),
            labels=tuple(str(item.get("name")) for item in labels if item.get("name")),
            author_account_id=body.get("authorId") or (version.get("authorId")),
            status=str(body.get("status") or "current"),
        )
