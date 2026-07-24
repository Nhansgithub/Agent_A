"""Composition root — assemble the whole service from config + secrets (AD-1, AD-3, AD-6).

This is the one place that *constructs* things: it reads the config registry, resolves credentials,
builds the adapters and the shared LLM client, wires the six role-agents, registers every stage
handler, and produces the `Orchestrator`. Everything below it receives its collaborators by
injection, which is what keeps the layering rule enforceable (AD-1) and the unit suite offline.

Per-tenant adapters are built lazily and cached: each tenant has its own Atlassian credentials, so it
gets its own `JiraAdapter` / `ConfluenceAdapter`, but they are built once and reused across that
tenant's runs. The agent-account cache (AD-10) is shared here so the "one source" rule holds.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.confluence import ConfluenceAdapter
from app.adapters.http import AtlassianClient
from app.adapters.jira import JiraAdapter
from app.agents.author.agent import AuthorAgent
from app.agents.classifier.agent import ClassifierAgent
from app.agents.detection import DetectionAgent
from app.agents.feedback_interpreter.agent import FeedbackInterpreter
from app.agents.identity import IdentityResolver
from app.agents.llm import LlmClient
from app.agents.publisher import Publisher
from app.agents.ticket_manager import TicketManager
from app.agents.tracing import build_tracer
from app.config.registry import ConfigRegistry
from app.config.schema import TenantConfig
from app.config.secrets import resolve_atlassian_credentials, resolve_secret
from app.domain.stage import Stage
from app.orchestrator.context import RunContext
from app.orchestrator.handlers_authoring import AuthoringHandlers
from app.orchestrator.handlers_detection import DetectionHandlers
from app.orchestrator.handlers_publishing import PublishingHandlers
from app.orchestrator.handlers_review import ReviewHandlers
from app.orchestrator.runner import Orchestrator
from app.orchestrator.stages import HandlerRegistry


@dataclass
class _TenantAdapters:
    jira: JiraAdapter
    confluence: ConfluenceAdapter
    base_url: str


class Composition:
    """Builds and holds the running service's collaborators."""

    def __init__(self, registry: ConfigRegistry, *, env: dict[str, str] | None = None) -> None:
        self._registry = registry
        self._env = env
        self._repository = None
        self._llm: LlmClient | None = None
        self._adapters: dict[str, _TenantAdapters] = {}
        self._agent_account_cache: dict[str, str] = {}
        self._orchestrator: Orchestrator | None = None

    # -- lazily-built singletons ---------------------------------------------------------------

    @property
    def repository(self):
        if self._repository is None:
            from app.repository import Repository

            self._repository = Repository.open(self._registry.system.database_path)
        return self._repository

    @property
    def llm(self) -> LlmClient:
        if self._llm is None:
            system = self._registry.system
            tracer = build_tracer(
                enabled=system.observability.langsmith_enabled,
                project=system.observability.langsmith_project,
                api_key=self._maybe_secret(system.observability.langsmith_api_key_ref),
                env=self._env,
            )
            self._llm = LlmClient(
                resolve_secret(system.anthropic_api_key_ref, self._env),
                tracer=tracer,
                # trace_content is per-tenant; the demo default (metadata-only) is applied per call
                # site via the tenant flag, so the shared client stays conservative here.
                trace_content=False,
            )
        return self._llm

    @property
    def orchestrator(self) -> Orchestrator:
        if self._orchestrator is None:
            self._orchestrator = Orchestrator(
                self.repository, self._build_handler_registry(), context_factory=self._context_for
            )
        return self._orchestrator

    # -- per-tenant adapters -------------------------------------------------------------------

    def _adapters_for(self, tenant: TenantConfig) -> _TenantAdapters:
        cached = self._adapters.get(tenant.project_id)
        if cached is not None:
            return cached
        system = self._registry.system
        jira_creds = resolve_atlassian_credentials(tenant.jira_credentials_ref, self._env)
        conf_creds = resolve_atlassian_credentials(tenant.confluence_credentials_ref, self._env)
        adapters = _TenantAdapters(
            jira=JiraAdapter(
                AtlassianClient(
                    jira_creds,
                    product="jira",
                    max_attempts=system.retry_max_attempts,
                    backoff_seconds=system.retry_backoff_seconds,
                )
            ),
            confluence=ConfluenceAdapter(
                AtlassianClient(
                    conf_creds,
                    product="confluence",
                    max_attempts=system.retry_max_attempts,
                    backoff_seconds=system.retry_backoff_seconds,
                )
            ),
            base_url=conf_creds.base_url,
        )
        self._adapters[tenant.project_id] = adapters
        return adapters

    # -- context factory (one per run) ---------------------------------------------------------

    def _context_for(self, state) -> RunContext:
        tenant = self._registry.by_project_id(state.project_id)
        if tenant is None:
            raise ValueError(f"no tenant configured for project_id={state.project_id!r}")
        adapters = self._adapters_for(tenant)
        models = self._registry.system.models
        ticket_manager = TicketManager(adapters.jira)
        return RunContext(
            prd_id=state.prd_id,
            correlation_id=state.correlation_id,
            tenant=tenant,
            confluence_base_url=adapters.base_url,
            repository=self.repository,
            confluence=adapters.confluence,
            detection=DetectionAgent(adapters.confluence),
            classifier=ClassifierAgent(self.llm, model=models.classifier),
            author=AuthorAgent(self.llm, model=models.author),
            feedback_interpreter=FeedbackInterpreter(self.llm, model=models.feedback_interpreter),
            publisher=Publisher(adapters.confluence),
            ticket_manager=ticket_manager,
            identity=IdentityResolver(adapters.jira),
            agent_account_cache=self._agent_account_cache,
        )

    @staticmethod
    def _build_handler_registry() -> HandlerRegistry:
        detection = DetectionHandlers()
        authoring = AuthoringHandlers()
        review = ReviewHandlers()
        publishing = PublishingHandlers()
        return HandlerRegistry(
            {
                Stage.DETECTED: detection.on_detected,
                Stage.CONFIRMED: detection.on_confirmed,
                Stage.PRD_TICKET_DONE: detection.on_prd_ticket_done,
                Stage.DRAFTED: authoring.on_drafted,
                Stage.REVISING: review.on_revising,
                Stage.PASSED: publishing.on_passed,
                Stage.PUBLISHING: publishing.on_publishing,
            }
        )

    def _maybe_secret(self, ref: str) -> str | None:
        try:
            return resolve_secret(ref, self._env)
        except Exception:
            return None

    def close(self) -> None:
        if self._repository is not None:
            self._repository.close()
