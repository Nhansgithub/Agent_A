"""The production run-context — the composition root's per-run object (AD-1, AD-3).

Every stage handler and the review/publish coordination take a *context* by injection; this is the
real one. It bundles, for a single PRD run: the resolved tenant config, the role-agents, the two
Atlassian adapters, and the small helpers the handlers call (`page_markdown`, `draft_page_url`,
`interpret_comment`, `record_publish_progress`, …).

It exists so the handlers stay pure of construction — they never build an adapter or open a socket
(AD-1). One context is built per invocation from the resolved tenant (AD-3); nothing about a run
outlives its state record, and no cross-PRD object is shared except the orchestrator's serializer.

The agent account id (AD-10) is resolved once per tenant and cached on a shared dict, so detection
and the publish restriction use the one source.
"""

from __future__ import annotations

from app.adapters.confluence import ConfluenceAdapter
from app.agents.author.agent import AuthorAgent
from app.agents.classifier.agent import ClassifierAgent
from app.agents.detection import DetectionAgent
from app.agents.feedback_interpreter.agent import FeedbackInterpreter
from app.agents.identity import IdentityResolver
from app.agents.llm import CallMetadata
from app.agents.publisher import Publisher
from app.agents.ticket_manager import TicketManager
from app.config.schema import TenantConfig
from app.domain.events import ConfluencePageEvent
from app.domain.feedback import FeedbackDecision


class RunContext:
    """Everything one PRD run needs, resolved for its tenant."""

    def __init__(
        self,
        *,
        prd_id: str,
        correlation_id: str,
        tenant: TenantConfig,
        confluence_base_url: str,
        repository,
        confluence: ConfluenceAdapter,
        detection: DetectionAgent,
        classifier: ClassifierAgent,
        author: AuthorAgent,
        feedback_interpreter: FeedbackInterpreter,
        publisher: Publisher,
        ticket_manager: TicketManager,
        identity: IdentityResolver,
        agent_account_cache: dict[str, str],
        page_event: ConfluencePageEvent | None = None,
    ) -> None:
        self.prd_id = prd_id
        self.correlation_id = correlation_id
        self.tenant = tenant
        self.page_event = page_event
        self.confluence = confluence
        self.detection = detection
        self.classifier = classifier
        self.author = author
        self.feedback_interpreter = feedback_interpreter
        self.publisher = publisher
        self.ticket_manager = ticket_manager
        self.identity = identity
        self._base_url = confluence_base_url.rstrip("/")
        self._repository = repository
        self._agent_account_cache = agent_account_cache
        self._prd_markdown: str | None = None
        self._space_id: str | None = None

    # -- content helpers -----------------------------------------------------------------------

    async def get_page_event(self):
        """The triggering page as a `ConfluencePageEvent`.

        Uses the webhook event if one was threaded in; otherwise builds it from a **live** `get_page`
        + ancestors lookup. Fetching live rather than depending on the ephemeral event is what makes
        detection work identically on a webhook, a resume, and a reconcile (AD-11/AD-22).
        """
        if self.page_event is not None:
            return self.page_event
        from app.domain.events import ConfluencePageEvent, EventType

        page = await self.confluence.get_page(self.prd_id)
        container = page.parent_id
        if not container:
            ancestors = await self.confluence.get_page_ancestors(self.prd_id)
            container = ancestors[0] if ancestors else None
        return ConfluencePageEvent(
            event_type=EventType.CONFLUENCE_PAGE_CREATED,
            page_id=page.id,
            version_number=page.version,
            title=page.title,
            creator_account_id=page.author_account_id,
            container_id=container,
            labels=page.labels,
        )

    async def page_markdown(self) -> str:
        """The source PRD as Markdown (fetched live and cached after the first read)."""
        if self._prd_markdown is None:
            page = await self.confluence.get_page(self.prd_id)
            self._prd_markdown = self.confluence.storage_to_markdown(page.body_storage)
        return self._prd_markdown

    async def current_draft_markdown(self) -> str:
        """The current draft page body as Markdown, for the FR-11 revise diff."""
        page_id = self._repository.state.require(self.prd_id).userdoc_page_id
        if not page_id:
            return ""
        page = await self.confluence.get_page(page_id)
        return self.confluence.storage_to_markdown(page.body_storage)

    def page_url(self) -> str:
        """The source PRD page's URL (for the tracking-ticket / rename-task links)."""
        return f"{self._base_url}/wiki/pages/viewpage.action?pageId={self.prd_id}"

    def draft_page_url(self, page_id: str) -> str:
        return f"{self._base_url}/wiki/pages/viewpage.action?pageId={page_id}" if page_id else ""

    async def confluence_space_id(self) -> str:
        """The numeric space **id** the v2 create-page API needs (config stores the space *key*).

        Resolved from the draft folder's `spaceId` and cached — the draft page is created in the
        draft folder's space, so that folder is the authoritative source.
        """
        if self._space_id is None:
            folder = await self.confluence.get_folder(self.tenant.confluence_draft_folder_id)
            self._space_id = str(folder.get("spaceId") or "")
        return self._space_id

    # -- agent-account resolution (AD-10, one source) ------------------------------------------

    async def agent_account_id(self) -> str:
        cached = self._agent_account_cache.get(self.tenant.project_id)
        if cached is None:
            cached = await self.confluence.get_current_user()
            self._agent_account_cache[self.tenant.project_id] = cached
        return cached

    async def space_admin_account_ids(self) -> tuple[str, ...]:
        # Space admins retain edit access after the publish restriction (AD-18). Resolving the real
        # admin set is instance-specific; the demo relies on the agent account being included.
        return ()

    # -- review-loop delegation ----------------------------------------------------------------

    async def interpret_comment(
        self, *, comment_text: str, awaiting_reply: bool, metadata: CallMetadata
    ) -> FeedbackDecision:
        """Interpret a PM comment with the review conversation as context (FR-10, AD-16).

        Reads the transcript and the pending restatement from the state + ticket so the interpreter
        judges a reply in-conversation. Purely enriches the interpreter's *input*; the typed decision
        and the deterministic routing (AD-16) are unchanged.
        """
        state = self._repository.state.require(self.prd_id)
        conversation = await self._review_conversation(state.review_ticket_key)
        return await self.feedback_interpreter.interpret(
            comment_text=comment_text,
            awaiting_reply=awaiting_reply,
            draft_markdown=await self.current_draft_markdown(),
            prd_markdown=await self.page_markdown(),
            conversation=conversation,
            pending_restatement=state.pending_feedback or "",
            metadata=metadata,
        )

    async def _review_conversation(self, review_ticket_key: str | None) -> tuple:
        """The review-ticket comment thread, labelled PM vs Agent (AD-10 account), oldest first.

        The transcript is what gives the loop memory. Best-effort: a read failure degrades to no
        transcript (the interpreter still has the current comment, draft, and PRD) rather than
        failing the round.
        """
        from app.domain.feedback import ReviewTurn, Speaker

        if not review_ticket_key:
            return ()
        try:
            comments = await self.ticket_manager.discussion(review_ticket_key)
        except Exception:  # noqa: BLE001 — a transcript is an enhancement, never load-bearing
            return ()
        agent_account = await self.agent_account_id()
        return tuple(
            ReviewTurn(
                speaker=Speaker.AGENT if c.author_account_id == agent_account else Speaker.PM,
                text=c.body_text,
            )
            for c in comments
            if c.body_text.strip()
        )

    async def recover_draft(self):
        """Restore or recreate the run's deleted UserDoc draft (FR-16). Idempotent."""
        page_id = self._repository.state.require(self.prd_id).userdoc_page_id
        if not page_id:
            from app.agents.publisher import DraftRecovery

            return DraftRecovery(action="healthy", page_id=None)
        return await self.publisher.recover_draft(
            tenant=self.tenant, prd_id=self.prd_id, page_id=page_id
        )

    async def post_comment(self, issue_key: str, body: dict) -> None:
        """Post an agent comment and immediately claim its id in `processed_events` (AD-9, AD-10).

        Jira echoes every comment back as a `comment-created` webhook — including the agent's own.
        Without this, the clarification question the agent just asked would return as an event, be
        read as the PM's reply, and *fabricate the answer to its own question* — precisely what AD-16
        forbids. Recording the id at post time makes that echo collide on the dedupe UNIQUE
        constraint and be dropped as a duplicate, which is the AD-10 self-ingestion guard applied to
        Jira comments rather than a second mechanism. It also keeps the AD-22 poll path honest: a
        reconciler reading comments back skips the ones the agent wrote itself.
        """
        comment_id = await self.ticket_manager.comment(issue_key, body)
        if comment_id:
            from app.domain.dedupe import DedupeKey
            from app.domain.events import EventType

            self._repository.record_event_for(
                DedupeKey(self.tenant.project_id, EventType.JIRA_COMMENT_CREATED, comment_id),
                self.prd_id,
            )

    async def record_publish_progress(self, **markers: object) -> None:
        """AD-18 — persist a publish sub-checkpoint. A non-`stage` write (AD-2)."""
        self._repository.state.update_fields(self.prd_id, **markers)
