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

    # -- content helpers -----------------------------------------------------------------------

    def page_markdown(self) -> str:
        """The source PRD as Markdown (cached after the first read)."""
        return self._prd_markdown or ""

    async def load_prd_markdown(self) -> str:
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

    def draft_page_url(self, page_id: str) -> str:
        return f"{self._base_url}/wiki/pages/{page_id}" if page_id else ""

    def confluence_space_id(self) -> str:
        # The v2 create-page API needs the space *id*. For the demo the source page's space is used;
        # the composition root resolves it once and passes it in via a subclass/override if needed.
        return self.tenant.confluence_space_key or ""

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
        return await self.feedback_interpreter.interpret(
            comment_text=comment_text,
            awaiting_reply=awaiting_reply,
            draft_markdown=await self.current_draft_markdown(),
            prd_markdown=self.page_markdown() or await self.load_prd_markdown(),
            metadata=metadata,
        )

    async def post_comment(self, issue_key: str, body: dict) -> None:
        await self.ticket_manager.comment(issue_key, body)

    async def record_publish_progress(self, **markers: object) -> None:
        """AD-18 — persist a publish sub-checkpoint. A non-`stage` write (AD-2)."""
        self._repository.state.update_fields(self.prd_id, **markers)
