"""FR-17 — the Confluence inline-comment feedback channel.

A reviewer highlights a passage on the UserDoc draft and leaves an inline comment. The agent detects
it, reads it (author + highlighted-passage anchor + body), restates it as Section / Issue / Suggested
change — proposing a fix if the reviewer gave none — and posts that on the Jira Review ticket,
@-mentioning the **exact commenter** (never the config PM). From there the conversation-aware loop
finalizes the change.

The whole chain is covered offline: the adapter read (v1 primary, v2 fallback), the webhook parse, the
tenant routing, the interpreter restatement, and the orchestrator pickup + hand-off.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.agents.llm import CallMetadata
from app.config.schema import TenantConfig
from app.domain.feedback import FeedbackDecision, FeedbackRoute, InlineRestatement
from app.domain.stage import PendingGate, Stage
from app.domain.state import PrdState
from app.repository import Repository
from app.repository.database import Database
from tests.conftest import registry_mapping, tenant_entry
from tests.test_jira_adapter import json_response

TENANT = TenantConfig.model_validate({**tenant_entry(), "project_id": "tenant_one"})

META = CallMetadata(correlation_id="c", prd_id="page-1", agent_role="feedback_interpreter")


# ==============================================================================================
# The adapter read — v1 primary, v2 fallback, footer detection.
# ==============================================================================================


def _confluence(*responses):
    from tests.test_confluence_adapter import build

    return build(*responses)


async def test_get_inline_comment_parses_the_v1_shape() -> None:
    adapter, _ = _confluence(
        json_response(
            200,
            {
                "id": "ic-1",
                "type": "comment",
                "body": {"storage": {"value": "<p>This is too vague</p>"}},
                "extensions": {
                    "location": "inline",
                    "inlineProperties": {"originalSelection": "Click the widget button"},
                    "resolution": {"status": "open"},
                },
                "history": {"createdBy": {"accountId": "acct-designer"}},
                "container": {"id": "draft-1"},
            },
        )
    )

    comment = await adapter.get_inline_comment("ic-1")

    assert comment.id == "ic-1"
    assert comment.page_id == "draft-1"
    assert comment.author_account_id == "acct-designer"
    assert comment.section == "Click the widget button"
    assert comment.body_text == "This is too vague"
    assert comment.is_inline is True
    assert comment.resolved is False


async def test_get_inline_comment_falls_back_to_v2_when_v1_404s() -> None:
    """The v2 inline-comments endpoint is documented to 404 intermittently — but so can v1 lag. If the
    primary read is unavailable, the other shape must still yield the comment."""
    adapter, transport = _confluence(
        json_response(404, {"message": "not found"}),
        json_response(
            200,
            {
                "id": "ic-2",
                "pageId": "draft-1",
                "version": {"authorId": "acct-designer"},
                "resolutionStatus": "open",
                "body": {"storage": {"value": "<p>Reword this</p>"}},
                "properties": {"inlineOriginalSelection": "the onboarding flow"},
            },
        ),
    )

    comment = await adapter.get_inline_comment("ic-2")

    assert comment.page_id == "draft-1"
    assert comment.author_account_id == "acct-designer"
    assert comment.section == "the onboarding flow"
    assert comment.body_text == "Reword this"
    assert comment.is_inline is True
    assert len(transport.requests) == 2, "v1 was tried first, then v2"


async def test_get_inline_comment_flags_a_footer_comment_as_not_inline() -> None:
    """The 'Page commented' trigger fires for page-level (footer) comments too; only an inline comment
    carries a highlighted-passage anchor."""
    adapter, _ = _confluence(
        json_response(
            200,
            {
                "id": "fc-1",
                "type": "comment",
                "body": {"storage": {"value": "<p>general note</p>"}},
                "extensions": {"location": "footer", "resolution": {"status": "open"}},
                "history": {"createdBy": {"accountId": "acct-pm-1"}},
                "container": {"id": "draft-1"},
            },
        )
    )

    comment = await adapter.get_inline_comment("fc-1")

    assert comment.is_inline is False
    assert comment.section == ""


# ==============================================================================================
# The webhook parse.
# ==============================================================================================


def test_parse_confluence_comment_event_reads_the_automation_payload() -> None:
    from app.domain.events import EventType
    from app.webhooks.events import parse_confluence_comment_event

    event = parse_confluence_comment_event(
        {
            "webhookEvent": "page_commented",
            "comment": {
                "id": "ic-1",
                "author": {"accountId": "acct-designer"},
                "body": "too vague",
            },
            "page": {"id": "draft-1", "title": "Widget Guide", "spaceKey": "DOCS"},
        }
    )

    assert event.event_type is EventType.CONFLUENCE_INLINE_COMMENT_CREATED
    assert event.comment_id == "ic-1"
    assert event.page_id == "draft-1"
    assert event.author_account_id == "acct-designer"
    assert event.space_key == "DOCS"
    assert event.entity_id == "ic-1" and event.version_marker == ""


def test_parse_event_routes_page_commented_to_the_confluence_comment_parser() -> None:
    from app.domain.events import ConfluenceCommentEvent
    from app.webhooks.events import parse_event

    event = parse_event(
        {
            "webhookEvent": "page_commented",
            "comment": {"id": "ic-9"},
            "page": {"id": "draft-1"},
        }
    )
    assert isinstance(event, ConfluenceCommentEvent)


def test_parse_event_structurally_distinguishes_a_confluence_comment_from_a_jira_one() -> None:
    """A payload with no `webhookEvent`: `comment`+`page` is Confluence; `comment`+`issue` is Jira."""
    from app.domain.events import ConfluenceCommentEvent, JiraCommentEvent
    from app.webhooks.events import parse_event

    confluence = parse_event({"comment": {"id": "ic-1"}, "page": {"id": "draft-1"}})
    jira = parse_event({"comment": {"id": "jc-1"}, "issue": {"key": "TESTREV-1"}})
    assert isinstance(confluence, ConfluenceCommentEvent)
    assert isinstance(jira, JiraCommentEvent)


# ==============================================================================================
# Tenant routing.
# ==============================================================================================


def _comment_event(space_key=None):
    from app.domain.events import ConfluenceCommentEvent, EventType

    return ConfluenceCommentEvent(
        event_type=EventType.CONFLUENCE_INLINE_COMMENT_CREATED,
        comment_id="ic-1",
        page_id="draft-1",
        author_account_id="acct-designer",
        space_key=space_key,
    )


def test_a_comment_event_routes_to_the_single_configured_tenant() -> None:
    from app.config.registry import ConfigRegistry
    from app.router import TenantRouter

    registry = ConfigRegistry.from_mapping(registry_mapping())
    decision = TenantRouter(registry).resolve(_comment_event())
    assert decision.resolved and decision.tenant.project_id == "tenant_one"


def test_a_comment_event_is_unrouted_when_two_tenants_and_no_space_key() -> None:
    from app.config.registry import ConfigRegistry
    from app.router import TenantRouter

    registry = ConfigRegistry.from_mapping(
        registry_mapping(
            tenant_one=tenant_entry(),
            tenant_two=tenant_entry(
                confluence_source_folder_id="folder-source-2",
                confluence_draft_folder_id="folder-draft-2",
                confluence_published_folder_id="folder-published-2",
                jira_main_project_key="OTHERMAIN",
                jira_review_project_key="OTHERREV",
                jira_credentials_ref="env:OTHER_JIRA",
                confluence_credentials_ref="env:OTHER_CONF",
            ),
        )
    )
    decision = TenantRouter(registry).resolve(_comment_event())
    assert not decision.resolved, "ambiguous without a space key — must not guess a tenant"


# ==============================================================================================
# The interpreter restatement (FR-17 #5 — propose a solution when none was given).
# ==============================================================================================


async def test_restate_inline_comment_structures_the_note() -> None:
    from app.agents.feedback_interpreter.agent import FeedbackInterpreter
    from app.agents.llm import LlmClient
    from tests.test_llm_client import FakeAnthropic

    reply = (
        '{"section": "Getting started", "issue": "the button name is wrong", '
        '"suggested_change": "call it \'Start\'", "solution_proposed": false}'
    )
    interp = FeedbackInterpreter(LlmClient("k", client=FakeAnthropic(text=reply)), model="m")

    restatement = await interp.restate_inline_comment(
        section="Click the widget button",
        comment_text="this button name is wrong",
        draft_markdown="# Guide",
        prd_markdown="# PRD",
        metadata=META,
    )

    assert isinstance(restatement, InlineRestatement)
    assert restatement.section == "Getting started"
    assert "Section: Getting started" in restatement.structured_feedback
    assert "Issue: the button name is wrong" in restatement.structured_feedback
    assert "Suggested change: call it 'Start'" in restatement.structured_feedback
    assert restatement.solution_proposed is False


async def test_restate_inline_comment_reports_a_proposed_solution() -> None:
    """When the reviewer names a problem but no fix, the agent proposes one and flags that it did."""
    from app.agents.feedback_interpreter.agent import FeedbackInterpreter
    from app.agents.llm import LlmClient
    from tests.test_llm_client import FakeAnthropic

    reply = (
        '{"section": "Onboarding", "issue": "this step is confusing", '
        '"suggested_change": "add a screenshot and a one-line summary", "solution_proposed": true}'
    )
    interp = FeedbackInterpreter(LlmClient("k", client=FakeAnthropic(text=reply)), model="m")

    restatement = await interp.restate_inline_comment(
        section="the onboarding flow",
        comment_text="confusing",
        draft_markdown="",
        prd_markdown="",
        metadata=META,
    )
    assert restatement.solution_proposed is True


async def test_restate_inline_comment_raises_on_unparseable_output() -> None:
    from app.agents.feedback_interpreter.agent import FeedbackInterpreter
    from app.agents.llm import LlmClient
    from app.domain.errors import AgentError
    from tests.test_llm_client import FakeAnthropic

    interp = FeedbackInterpreter(LlmClient("k", client=FakeAnthropic(text="not json")), model="m")
    with pytest.raises(AgentError, match="restatement"):
        await interp.restate_inline_comment(
            section="s", comment_text="c", draft_markdown="", prd_markdown="", metadata=META
        )


# ==============================================================================================
# The orchestrator pickup (apply_inline_comment) and the hand-off.
# ==============================================================================================


@dataclass
class FakeInlineContext:
    """A context for `apply_inline_comment`: reads a scripted comment, restates it, records posts."""

    comment: object
    restatement: InlineRestatement
    tenant: TenantConfig = TENANT
    comments: list[tuple[str, dict]] = field(default_factory=list)
    restate_calls: list[str] = field(default_factory=list)

    async def read_inline_comment(self, comment_id: str):
        return self.comment

    async def restate_inline_comment(self, *, section, comment_text, metadata):
        self.restate_calls.append(section)
        return self.restatement

    async def post_comment(self, issue_key: str, body: dict) -> None:
        self.comments.append((issue_key, body))


def _inline_comment(author="acct-designer", section="Click the widget button", body="too vague"):
    from app.domain.atlassian import InlineComment

    return InlineComment(
        id="ic-1",
        page_id="draft-1",
        author_account_id=author,
        body_text=body,
        section=section,
        is_inline=True,
    )


def _build_orchestrator(context, *, stage=Stage.AWAITING_REVIEW, **state_kwargs):
    from app.orchestrator.runner import Orchestrator
    from app.orchestrator.stages import HandlerRegistry

    repository = Repository(Database(":memory:"))
    repository.state.create(
        PrdState(
            prd_id="page-1",
            project_id="tenant_one",
            stage=stage,
            pending_gate=PendingGate.PM_REVIEW,
            review_ticket_key="TESTREV-1",
            userdoc_page_id="draft-1",
            **state_kwargs,
        )
    )
    orchestrator = Orchestrator(repository, HandlerRegistry({}), context_factory=lambda _s: context)
    return orchestrator, repository


async def test_apply_inline_comment_posts_and_parks_mentioning_the_exact_commenter() -> None:
    restatement = InlineRestatement(
        section="Getting started",
        structured_feedback="Section: Getting started\nIssue: x\nSuggested change: y",
        solution_proposed=False,
    )
    context = FakeInlineContext(
        comment=_inline_comment(author="acct-designer"), restatement=restatement
    )
    orchestrator, repository = _build_orchestrator(context)

    result = await orchestrator.apply_inline_comment(
        "page-1", comment_id="ic-1", commenter_account_id="acct-designer"
    )

    assert result.final_stage is Stage.AWAITING_STRUCTURE_CONFIRM
    final = repository.state.require("page-1")
    assert final.pending_gate is PendingGate.PM_STRUCTURE_CONFIRM
    assert final.pending_feedback == restatement.structured_feedback
    assert final.active_reviewer_account_id == "acct-designer"
    # The posted comment @-mentions the EXACT commenter, not the configured PM.
    assert context.comments and context.comments[-1][0] == "TESTREV-1"
    body = str(context.comments[-1][1])
    assert "acct-designer" in body, "must tag the person who left the inline comment"
    assert "acct-pm-1" not in body, "must NOT tag the config PM instead"


async def test_apply_inline_comment_wording_differs_when_the_agent_proposed_the_fix() -> None:
    from app.domain import adf

    restatement = InlineRestatement(
        section="Onboarding",
        structured_feedback="Section: Onboarding\nIssue: confusing\nSuggested change: add a screenshot",
        solution_proposed=True,
    )
    context = FakeInlineContext(comment=_inline_comment(), restatement=restatement)
    orchestrator, _ = _build_orchestrator(context)

    await orchestrator.apply_inline_comment("page-1", comment_id="ic-1")

    text = adf.extract_text(context.comments[-1][1]).lower()
    assert "proposed one" in text, "when the agent supplies the fix, it says so"


async def test_apply_inline_comment_is_ignored_outside_review() -> None:
    context = FakeInlineContext(
        comment=_inline_comment(),
        restatement=InlineRestatement(section="s", structured_feedback="f"),
    )
    orchestrator, repository = _build_orchestrator(context, stage=Stage.PUBLISHING)

    result = await orchestrator.apply_inline_comment("page-1", comment_id="ic-1")

    assert "not in review" in result.stopped_reason
    assert context.comments == [], "no ticket comment when the run isn't under review"
    assert repository.state.require("page-1").stage is Stage.PUBLISHING


async def test_apply_inline_comment_defers_to_a_pending_deletion_decision() -> None:
    context = FakeInlineContext(
        comment=_inline_comment(),
        restatement=InlineRestatement(section="s", structured_feedback="f"),
    )
    orchestrator, _ = _build_orchestrator(context, pending_deletion_page_id="draft-1")

    result = await orchestrator.apply_inline_comment("page-1", comment_id="ic-1")

    assert "deletion pending" in result.stopped_reason
    assert context.comments == []


# -- the hand-off: the confirmation sub-conversation addresses the inline commenter ---------------


async def test_the_follow_up_confirmation_still_mentions_the_inline_commenter() -> None:
    """After an inline comment set the active reviewer, a re-restatement in the confirmation sub-loop
    must @-mention that person, not the config PM (FR-17 — the whole thread addresses the commenter)."""
    from tests.test_review_loop import build

    decision = FeedbackDecision(
        route=FeedbackRoute.CONFIRM_STRUCTURE,
        structured_feedback="Section: Intro\nIssue: x\nSuggested change: y",
        question="is this what you mean?",
    )
    orchestrator, repository, context = build(
        decision,
        stage=Stage.AWAITING_STRUCTURE_CONFIRM,
        pending_feedback="Section: Intro\nIssue: x\nSuggested change: y",
        active_reviewer_account_id="acct-designer",
    )

    await orchestrator.apply_pm_comment("page-1", comment_text="no, I meant the header")

    body = str(context.comments[-1][1])
    assert "acct-designer" in body, "the sub-conversation addresses the inline commenter"
    assert "acct-pm-1" not in body


async def test_applying_the_feedback_clears_the_active_reviewer() -> None:
    """Once the inline thread's feedback is applied, the active reviewer is cleared so a later,
    unrelated Jira thread addresses the config PM again."""
    from tests.test_review_loop import build

    decision = FeedbackDecision(route=FeedbackRoute.CONFIRMATION, confirmed=True)
    orchestrator, repository, _ = build(
        decision,
        stage=Stage.AWAITING_STRUCTURE_CONFIRM,
        pending_feedback="Section: Intro\nIssue: x\nSuggested change: y",
        active_reviewer_account_id="acct-designer",
    )

    await orchestrator.apply_pm_comment("page-1", comment_text="yes")

    assert repository.state.require("page-1").active_reviewer_account_id is None


# ==============================================================================================
# State round-trips the new column (additive migration, D-38 pattern).
# ==============================================================================================


def test_active_reviewer_account_id_round_trips_through_the_store() -> None:
    repository = Repository(Database(":memory:"))
    repository.state.create(
        PrdState(
            prd_id="page-1", project_id="tenant_one", active_reviewer_account_id="acct-designer"
        )
    )
    assert repository.state.require("page-1").active_reviewer_account_id == "acct-designer"

    repository.state.update_fields("page-1", active_reviewer_account_id=None)
    assert repository.state.require("page-1").active_reviewer_account_id is None
