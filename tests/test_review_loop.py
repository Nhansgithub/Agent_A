"""Epic 4 — the review loop through the orchestrator (FR-08…FR-12, EH-06, EH-08, AD-15, AD-16).

Exercises the webhook-driven re-entry: a parked run reacting to a PM comment or a gate transition.
The Feedback interpreter is faked (its decision is scripted); the routing that acts on the decision is
the real deterministic code — which is exactly the AD-16 split (fake the LLM, test the routing).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.agents.author.agent import Draft
from app.agents.feedback_interpreter.agent import FeedbackInterpreter
from app.agents.llm import CallMetadata
from app.domain.feedback import ClarificationTrigger, FeedbackDecision, FeedbackRoute
from app.domain.stage import PendingGate, Stage
from app.domain.state import PrdState
from app.orchestrator.handlers_review import ReviewHandlers
from app.orchestrator.runner import Orchestrator
from app.orchestrator.stages import HandlerRegistry
from app.repository import Repository
from app.repository.database import Database
from tests.conftest import tenant_entry

from app.config.schema import TenantConfig  # isort: skip

TENANT = TenantConfig.model_validate({**tenant_entry(), "project_id": "tenant_one"})

STRUCTURED = "Section: Intro\nIssue: unclear\nSuggested change: add an example"


class FakeAuthor:
    def __init__(self) -> None:
        self.revise_calls = 0
        self.revised_with = ""

    async def revise(self, *, current_markdown, structured_feedback, metadata) -> Draft:
        self.revise_calls += 1
        self.revised_with = structured_feedback
        return Draft(
            title="Widget Guide", markdown="# Widget Guide\n\nRevised.", self_critique_applied=False
        )

    async def summarize_changes(self, *, before, after, feedback, metadata) -> str:
        return "- Added an example to the intro"


class FakePublisher:
    def __init__(self) -> None:
        self.updates = 0

    async def update_draft(self, *, page_id, title, markdown):
        self.updates += 1
        from app.domain.atlassian import ConfluencePage

        return ConfluencePage(id=page_id, title=title, version=2)


@dataclass
class FakeContext:
    decision: FeedbackDecision
    prd_id: str = "page-1"
    correlation_id: str = "corr-1"
    tenant: TenantConfig = TENANT
    author: FakeAuthor = field(default_factory=FakeAuthor)
    publisher: FakePublisher = field(default_factory=FakePublisher)
    comments: list[tuple[str, dict]] = field(default_factory=list)
    interpret_calls: list[bool] = field(default_factory=list)

    def page_markdown(self) -> str:
        return "The PRD."

    async def current_draft_markdown(self) -> str:
        return "# Widget Guide\n\nOld draft."

    def draft_page_url(self, page_id: str) -> str:
        return f"https://x/{page_id}"

    async def interpret_comment(
        self, *, comment_text, awaiting_reply, metadata
    ) -> FeedbackDecision:
        self.interpret_calls.append(awaiting_reply)
        return self.decision

    async def post_comment(self, issue_key: str, body: dict) -> None:
        self.comments.append((issue_key, body))


def build(decision: FeedbackDecision, *, stage=Stage.AWAITING_REVIEW, **state_kwargs):
    repository = Repository(Database(":memory:"))
    repository.state.create(
        PrdState(
            prd_id="page-1",
            project_id="tenant_one",
            stage=stage,
            pending_gate=PendingGate.PM_REVIEW,
            userdoc_page_id="draft-1",
            review_ticket_key="TESTREV-1",
            **state_kwargs,
        )
    )
    context = FakeContext(decision=decision)
    registry = HandlerRegistry({Stage.REVISING: ReviewHandlers().on_revising})
    orchestrator = Orchestrator(repository, registry, context_factory=lambda _s: context)
    return orchestrator, repository, context


def apply_decision():
    return FeedbackDecision(route=FeedbackRoute.APPLY, structured_feedback=STRUCTURED)


# ---------------------------------------------------------------------------------------------
# Story 4.2 — structured feedback → revise → change summary → re-request → park again.
# ---------------------------------------------------------------------------------------------


async def test_structured_feedback_revises_and_re_requests() -> None:
    orchestrator, repository, context = build(apply_decision())

    result = await orchestrator.apply_pm_comment("page-1", comment_text=STRUCTURED)

    assert result.final_stage is Stage.AWAITING_REVIEW, "back to the gate after revising"
    assert context.author.revise_calls == 1
    assert context.publisher.updates == 1
    # A change-summary comment was posted (the re-request).
    assert context.comments and context.comments[-1][0] == "TESTREV-1"
    final = repository.state.require("page-1")
    assert final.review_round == 1, "NFR-09 — one increment per applied round"
    assert final.pending_feedback is None, "cleared so a future round can't re-apply it"


async def test_the_loop_is_uncapped_but_needs_a_fresh_comment_each_round() -> None:
    """FR-11 / NFR-09 — uncapped, but cannot self-spin: each round requires a new PM comment."""
    orchestrator, repository, context = build(apply_decision())

    for expected_round in (1, 2, 3):
        await orchestrator.apply_pm_comment("page-1", comment_text=STRUCTURED)
        assert repository.state.require("page-1").review_round == expected_round
        assert repository.state.require("page-1").stage is Stage.AWAITING_REVIEW

    # Without a fresh comment, nothing advances — the loop cannot spin on its own.
    idle = await orchestrator.advance("page-1")
    assert not idle.progressed
    assert context.author.revise_calls == 3


# ---------------------------------------------------------------------------------------------
# Story 4.4 — plain-language feedback: restate, ask, block until confirmed (EH-08).
# ---------------------------------------------------------------------------------------------


async def test_plain_feedback_asks_for_confirmation_and_blocks() -> None:
    decision = FeedbackDecision(
        route=FeedbackRoute.CONFIRM_STRUCTURE,
        structured_feedback=STRUCTURED,
        question="is this what you mean?",
    )
    orchestrator, repository, context = build(decision)

    result = await orchestrator.apply_pm_comment("page-1", comment_text="the intro is confusing")

    assert result.final_stage is Stage.AWAITING_STRUCTURE_CONFIRM
    assert repository.state.require("page-1").pending_gate is PendingGate.PM_STRUCTURE_CONFIRM
    assert context.author.revise_calls == 0, "nothing is applied until the PM confirms (EH-08)"
    # The restated feedback was posted for confirmation.
    from app.domain import adf

    assert "is this what you mean" in adf.extract_text(context.comments[-1][1])


async def test_confirming_the_restatement_then_applies_it() -> None:
    orchestrator, repository, context = build(
        FeedbackDecision(
            route=FeedbackRoute.CONFIRMATION, confirmed=True, structured_feedback=STRUCTURED
        ),
        stage=Stage.AWAITING_STRUCTURE_CONFIRM,
        pending_feedback=STRUCTURED,
    )

    result = await orchestrator.apply_pm_comment("page-1", comment_text="yes exactly")

    assert result.final_stage is Stage.AWAITING_REVIEW
    assert context.author.revise_calls == 1, "confirmed → the revision now runs"
    assert context.interpret_calls == [True], "the interpreter was told a reply was awaited"


async def test_confirming_applies_the_stored_feedback_even_if_the_decision_omits_it() -> None:
    """Production reality: the interpreter returns CONFIRMATION with EMPTY structured_feedback for a
    bare 'yes'. The orchestrator must fall back to the stored restatement, not revise on nothing."""
    orchestrator, repository, context = build(
        FeedbackDecision(
            route=FeedbackRoute.CONFIRMATION, confirmed=True
        ),  # no structured_feedback
        stage=Stage.AWAITING_STRUCTURE_CONFIRM,
        pending_feedback=STRUCTURED,
    )

    await orchestrator.apply_pm_comment("page-1", comment_text="yes")

    assert context.author.revise_calls == 1
    assert STRUCTURED in context.author.revised_with, "applied the stored restatement, not nothing"


async def test_a_bare_no_asks_what_to_change_instead_of_silently_dead_ending() -> None:
    """FR-10: a rejection must open a dialogue, not vanish. Previously this posted nothing and the PM
    was left with no signal they needed to re-explain."""
    from app.domain import adf

    orchestrator, repository, context = build(
        FeedbackDecision(route=FeedbackRoute.CONFIRMATION, confirmed=False),
        stage=Stage.AWAITING_STRUCTURE_CONFIRM,
        pending_feedback=STRUCTURED,
    )

    result = await orchestrator.apply_pm_comment("page-1", comment_text="no")

    assert result.final_stage is Stage.AWAITING_REVIEW, "back to open review for another round"
    assert context.comments, "the agent must acknowledge the 'no', not go silent"
    text = adf.extract_text(context.comments[-1][1]).lower()
    assert "what you'd like changed" in text or "changed instead" in text
    assert "mention" in str(context.comments[-1][1]), (
        "must @-mention the PM, or they aren't notified"
    )
    # The rejected restatement must not linger as pending feedback.
    assert repository.state.require("page-1").pending_feedback is None


# ---------------------------------------------------------------------------------------------
# Story 4.5 — bounded clarification: ask only on a trigger, block on the answer (EH-08).
# ---------------------------------------------------------------------------------------------


async def test_a_clarification_trigger_asks_and_blocks() -> None:
    decision = FeedbackDecision(
        route=FeedbackRoute.CLARIFY,
        trigger=ClarificationTrigger.UNDEFINED_TERM,
        question="What does 'quick-add' refer to?",
    )
    orchestrator, repository, context = build(decision)

    result = await orchestrator.apply_pm_comment("page-1", comment_text="make quick-add clearer")

    assert result.final_stage is Stage.AWAITING_CLARIFICATION
    assert repository.state.require("page-1").pending_gate is PendingGate.PM_CLARIFICATION
    assert context.author.revise_calls == 0, "blocks on the answer; never fabricates it (EH-08)"
    from app.domain import adf

    assert "quick-add" in adf.extract_text(context.comments[-1][1])


# ---------------------------------------------------------------------------------------------
# Story 4.3 — PASS detection on the PM's Done transition (FR-12, AD-15).
# ---------------------------------------------------------------------------------------------


async def test_the_pm_done_transition_is_detected_as_pass() -> None:
    orchestrator, repository, _ = build(apply_decision())

    await orchestrator.apply_gate_done("page-1", issue_key="TESTREV-1")

    # No `passed` handler yet (Epic 5), so it advances to passed and stops there.
    assert repository.state.require("page-1").stage is Stage.PASSED


async def test_a_done_on_a_different_ticket_is_ignored() -> None:
    """Only the PM moving the *Review* ticket is PASS — an unrelated ticket's Done must not advance."""
    orchestrator, repository, _ = build(apply_decision())

    result = await orchestrator.apply_gate_done("page-1", issue_key="SOME-OTHER-1")

    assert repository.state.require("page-1").stage is Stage.AWAITING_REVIEW
    assert "ignored" in result.stopped_reason


# ---------------------------------------------------------------------------------------------
# Story 4.6 — late feedback after Done is ignored (EH-06); the agent never self-advances.
# ---------------------------------------------------------------------------------------------


async def test_feedback_after_pass_is_not_processed() -> None:
    """EH-06 — the pass is final at the Done transition."""
    orchestrator, repository, context = build(apply_decision(), stage=Stage.PASSED)

    result = await orchestrator.apply_pm_comment("page-1", comment_text=STRUCTURED)

    assert context.author.revise_calls == 0
    assert repository.state.require("page-1").stage is Stage.PASSED
    assert "not in review" in result.stopped_reason


async def test_a_parked_review_never_advances_without_a_human() -> None:
    """FR-12 — with no comment and no Done, the run parks at awaiting_review indefinitely."""
    orchestrator, repository, _ = build(apply_decision())
    result = await orchestrator.advance("page-1")
    assert not result.progressed
    assert repository.state.require("page-1").stage is Stage.AWAITING_REVIEW


# ---------------------------------------------------------------------------------------------
# Story 4.1 — the interpreter parses a decision (its own boundary; routing is tested above).
# ---------------------------------------------------------------------------------------------


async def test_interpreter_parses_a_structured_decision() -> None:
    from app.agents.llm import LlmClient
    from tests.test_llm_client import FakeAnthropic

    reply = '{"route": "apply", "structured_feedback": "Section: Intro\\nIssue: x\\nSuggested change: y"}'
    interpreter = FeedbackInterpreter(
        LlmClient("k", client=FakeAnthropic(text=reply)), model="claude-sonnet-5"
    )

    decision = await interpreter.interpret(
        comment_text="Section: Intro\nIssue: x\nSuggested change: y",
        awaiting_reply=False,
        draft_markdown="# Guide",
        prd_markdown="# PRD",
        metadata=CallMetadata(
            correlation_id="c", prd_id="page-1", agent_role="feedback_interpreter"
        ),
    )
    assert decision.route is FeedbackRoute.APPLY


async def test_interpreter_rejects_a_clarify_without_a_trigger() -> None:
    """EH-08 — a CLARIFY with trigger 'none' is not a legal reason to block; surface it."""
    from app.agents.llm import LlmClient
    from app.domain.errors import AgentError
    from tests.test_llm_client import FakeAnthropic

    reply = '{"route": "clarify", "trigger": "none", "question": "?"}'
    interpreter = FeedbackInterpreter(LlmClient("k", client=FakeAnthropic(text=reply)), model="m")

    with pytest.raises(AgentError, match="four FR-08 triggers"):
        await interpreter.interpret(
            comment_text="huh?",
            awaiting_reply=False,
            draft_markdown="d",
            prd_markdown="p",
            metadata=CallMetadata(
                correlation_id="c", prd_id="page-1", agent_role="feedback_interpreter"
            ),
        )


# ---------------------------------------------------------------------------------------------
# FR-10 (amendment 2026-07-25) — the interpreter reasons with conversation memory.
# ---------------------------------------------------------------------------------------------


async def test_interpreter_prompt_carries_the_transcript_and_the_restatement() -> None:
    """The model must SEE the discussion and the restatement it's confirming — otherwise a reply like
    'yes but drop the last point' has no anchor. Assert both reach the prompt."""
    from app.agents.llm import LlmClient
    from app.domain.feedback import ReviewTurn, Speaker
    from tests.test_llm_client import FakeAnthropic

    fake = FakeAnthropic(text='{"route": "confirmation", "confirmed": true}')
    interpreter = FeedbackInterpreter(LlmClient("k", client=fake), model="m")

    await interpreter.interpret(
        comment_text="yes but drop the last point",
        awaiting_reply=True,
        draft_markdown="# Guide",
        prd_markdown="# PRD",
        conversation=(
            ReviewTurn(Speaker.PM, "the intro is too long"),
            ReviewTurn(Speaker.AGENT, "I curated it like this — is this what you mean?"),
            ReviewTurn(Speaker.PM, "yes but drop the last point"),
        ),
        pending_restatement="Section: Intro\nIssue: too long\nSuggested change: trim it",
        metadata=CallMetadata(
            correlation_id="c", prd_id="page-1", agent_role="feedback_interpreter"
        ),
    )

    prompt = fake.calls[-1]["messages"][0]["content"]
    assert "PM: the intro is too long" in prompt, "the transcript must reach the model"
    assert "Agent: I curated it" in prompt, "the agent's own turn must be labelled"
    assert "Section: Intro" in prompt, "the restatement being confirmed must be shown"


async def test_run_context_labels_the_agents_own_turns_and_the_pms() -> None:
    """The transcript must distinguish who said what, by AD-10 account — not guesswork."""
    from app.domain.atlassian import JiraComment
    from app.domain.feedback import Speaker
    from app.orchestrator.context import RunContext

    class FakeTickets:
        async def discussion(self, issue_key, *, limit=30):
            return [
                JiraComment(id="1", author_account_id="acct-pm", body_text="fix the intro"),
                JiraComment(id="2", author_account_id="agent-acct", body_text="is this right?"),
            ]

    ctx = RunContext(
        prd_id="page-1",
        correlation_id="c",
        tenant=TENANT,
        confluence_base_url="https://x",
        repository=None,
        confluence=None,
        detection=None,
        classifier=None,
        author=None,
        feedback_interpreter=None,
        publisher=None,
        ticket_manager=FakeTickets(),
        identity=None,
        agent_account_cache={TENANT.project_id: "agent-acct"},
    )

    turns = await ctx._review_conversation("TESTREV-1")

    assert [(t.speaker, t.text) for t in turns] == [
        (Speaker.PM, "fix the intro"),
        (Speaker.AGENT, "is this right?"),
    ]


async def test_run_context_transcript_degrades_to_empty_on_a_read_failure() -> None:
    """A transcript is an enhancement — a Jira hiccup must not fail the whole feedback round."""
    from app.orchestrator.context import RunContext

    class BrokenTickets:
        async def discussion(self, issue_key, *, limit=30):
            raise RuntimeError("jira down")

    ctx = RunContext(
        prd_id="page-1",
        correlation_id="c",
        tenant=TENANT,
        confluence_base_url="https://x",
        repository=None,
        confluence=None,
        detection=None,
        classifier=None,
        author=None,
        feedback_interpreter=None,
        publisher=None,
        ticket_manager=BrokenTickets(),
        identity=None,
        agent_account_cache={},
    )

    assert await ctx._review_conversation("TESTREV-1") == ()


# ---------------------------------------------------------------------------------------------
# State-machine cross-edges between the two review-loop waits (audit 2026-07-25, D-31 class).
# A conversation-aware reply can turn a structure-confirm into a clarification and vice-versa;
# the missing edges used to throw IllegalStageTransition and 500 the webhook.
# ---------------------------------------------------------------------------------------------


async def test_a_clarify_raised_while_awaiting_structure_confirm_does_not_500() -> None:
    orchestrator, repository, context = build(
        FeedbackDecision(
            route=FeedbackRoute.CLARIFY,
            trigger=ClarificationTrigger.UNDEFINED_TERM,
            question="What does 'quick-add' mean here?",
        ),
        stage=Stage.AWAITING_STRUCTURE_CONFIRM,
        pending_feedback=STRUCTURED,
    )

    result = await orchestrator.apply_pm_comment("page-1", comment_text="wait, what's quick-add?")

    assert result.final_stage is Stage.AWAITING_CLARIFICATION, "structure-confirm → clarification"
    assert result.error is None


async def test_plain_feedback_while_awaiting_clarification_re_restates() -> None:
    orchestrator, repository, context = build(
        FeedbackDecision(
            route=FeedbackRoute.CONFIRM_STRUCTURE,
            structured_feedback=STRUCTURED,
            question="is this what you mean?",
        ),
        stage=Stage.AWAITING_CLARIFICATION,
    )

    result = await orchestrator.apply_pm_comment("page-1", comment_text="oh, and shorten the intro")

    assert result.final_stage is Stage.AWAITING_STRUCTURE_CONFIRM, (
        "clarification → structure-confirm"
    )
    assert result.error is None
