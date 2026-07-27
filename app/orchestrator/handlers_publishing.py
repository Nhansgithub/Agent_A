"""Epic 5 stage handlers: `passed` → Publishing ticket, and `publishing` → publish (FR-13…FR-15).

* **`on_passed` (FR-13)** — the PM has passed the draft (a human moved the Review ticket to Done,
  detected by the orchestrator). Post a confirmation on the Review ticket, create the Publishing
  ticket for the Head of Product (find-or-create by marker, AD-11), and **park** at
  `awaiting_publish_approval` — a second human gate (AD-15).
* **`on_publishing` (FR-15)** — the Head of Product has approved (their Done, again detected not
  driven). Run the ordered, per-side-effect-idempotent publish transaction (AD-18) and mark complete.

The `publishing` handler persists a sub-checkpoint after each side-effect (via the context's progress
callback → `repository.update_fields`), so a resume skips what already succeeded. Those are non-`stage`
markers; the orchestrator still owns the final `stage = complete` advance (AD-2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.agents.publisher import Publisher
from app.agents.ticket_manager import PUBLISHING_TICKET_SUMMARY_PREFIX, TicketManager
from app.config.schema import TenantConfig
from app.domain import adf
from app.domain.errors import AgentError
from app.domain.stage import PendingGate, Stage
from app.domain.state import PrdState
from app.orchestrator.stages import Advance, Park, StageOutcome


class PublishContext(Protocol):
    @property
    def prd_id(self) -> str: ...
    @property
    def tenant(self) -> TenantConfig: ...
    @property
    def publisher(self) -> Publisher: ...
    @property
    def ticket_manager(self) -> TicketManager: ...

    def draft_page_url(self, page_id: str) -> str: ...
    async def agent_account_id(self) -> str: ...
    async def space_admin_account_ids(self) -> tuple[str, ...]: ...
    async def post_comment(self, issue_key: str, body: dict) -> None: ...
    async def record_publish_progress(self, **markers: object) -> None: ...
    async def draft_status(self, page_id: str) -> str: ...


@dataclass(frozen=True, slots=True)
class PublishingHandlers:
    # -- FR-13: confirm PASS, create the Publishing ticket -------------------------------------

    async def on_passed(self, context: PublishContext, state: PrdState) -> StageOutcome:
        page_url = context.draft_page_url(state.userdoc_page_id or "")

        # Confirm the pass to the PM (FR-13 step 1).
        if state.review_ticket_key:
            await context.post_comment(
                state.review_ticket_key,
                adf.doc(
                    adf.paragraph(
                        adf.mention(context.tenant.pm_account_id),
                        adf.text(
                            " thanks — the draft has passed. I've sent it to the Head of Product for "
                            "publishing approval."
                        ),
                    )
                ),
            )

        # Create (or adopt) the Publishing ticket for the Head of Product (FR-13 step 2, AD-11).
        # The tracking ticket shares the marker in this same Main project and is older, so the search
        # must be typed to the Publishing ticket — otherwise it adopts the tracking ticket.
        publishing_key = state.publishing_ticket_key
        if publishing_key is None:
            orphan = await context.ticket_manager.find_ticket_by_marker(
                context.tenant.jira_main_project_key,
                context.prd_id,
                summary_prefix=PUBLISHING_TICKET_SUMMARY_PREFIX,
            )
            ticket = orphan or await context.ticket_manager.create_publishing_ticket(
                tenant=context.tenant,
                prd_id=context.prd_id,
                userdoc_title=state.prd_title or "UserDoc",
                draft_page_url=page_url,
            )
            publishing_key = ticket.key

        return Park(
            to_stage=Stage.AWAITING_PUBLISH_APPROVAL,
            gate=PendingGate.HEAD_OF_PRODUCT_APPROVAL,
            recorded={"publishing_ticket_key": publishing_key},
            note=f"awaiting Head of Product approval on {publishing_key}",
        )

    # -- FR-15: the publish transaction --------------------------------------------------------

    async def on_publishing(self, context: PublishContext, state: PrdState) -> StageOutcome:
        # FR-16: never auto-recover a deleted draft. If the page is missing/trashed at publish time,
        # a human deleted it — either they told us to leave it, or the deletion wasn't caught — so we
        # refuse to publish and surface an actionable error rather than silently restoring it. The
        # proactive deletion-audit loop (apply_draft_deleted) is where recovery is *offered* and only
        # done on the PM's confirmation.
        status = await context.draft_status(state.userdoc_page_id or "")
        if status != "current":
            raise AgentError(
                message=f"The UserDoc draft page is {status} and cannot be published.",
                suggested_fix=(
                    "The draft was deleted. Restore it from the Confluence trash (or reply “restore” "
                    "on the Review ticket so I bring it back), then move the Publishing ticket to "
                    "Done again — or reply `@agent resume` on the error ticket."
                ),
                operation="publisher.draft_status",
                context={"page": state.userdoc_page_id or "", "status": status},
            )

        result = await context.publisher.publish(
            tenant=context.tenant,
            page_id=state.userdoc_page_id or "",
            agent_account_id=await context.agent_account_id(),
            space_admin_account_ids=await context.space_admin_account_ids(),
            on_step=context.record_publish_progress,
            restriction_done=state.restriction_applied_at is not None,
            move_done=state.moved_to_published_at is not None,
        )

        # The doc is published but NOT write-protected (FR-15 step 1 opted out). Say so on the
        # ticket: the Head of Product approved on the understanding that publishing locks the page,
        # and a silent skip would leave them believing a protection that is not there.
        if result.restriction_skipped and state.publishing_ticket_key:
            await context.post_comment(
                state.publishing_ticket_key,
                adf.doc(
                    adf.paragraph(
                        adf.mention(context.tenant.head_of_product_account_id),
                        adf.text(" the UserDoc is published and moved — but it is "),
                        adf.strong("not edit-restricted"),
                        adf.text(
                            ". This site's Confluence plan does not support page restrictions, so "
                            "the page stays editable by anyone with space access. Restrict it "
                            "manually if that matters, or upgrade the site and re-publish."
                        ),
                    )
                ),
            )

        # (4) Mark complete — the orchestrator's stage advance, in the same write (AD-11).
        note = "published and moved to the published folder"
        if result.restriction_skipped:
            note += " (edit restriction skipped — tenant opted out)"
        return Advance(to_stage=Stage.COMPLETE, note=note)
