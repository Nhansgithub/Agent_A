"""Error handler — surface a failure to Jira + admin, and drive resume (EH-01, EH-02, AD-19).

The orchestrator owns the *state* side of an error: on any `AgentError` after the adapters' retries,
it sets `stage = error`, preserves `last_good_checkpoint` and `pending_gate`, and stops. This module
owns the *human* side: posting exactly one structured comment so the admin knows what broke, how to
fix it, and how to resume.

EH-01 requires the comment to contain: a plain-language error, a suggested fix, an `@admin` mention,
the **literal** resume instruction, and a correlation id (for LangSmith). AD-19 requires **exactly
one** such comment — not one per retry — which is why it is posted once from the errored result, not
from inside the retry loop.

Resume (EH-02) is the inverse: an admin comment containing `@agent resume` or `fixed` re-runs the
failed stage from `last_good_checkpoint`, never the whole flow. The dedupe guard (AD-9) at the webhook
layer stops a duplicate delivery from double-resuming.
"""

from __future__ import annotations

from app.agents.ticket_manager import TicketManager
from app.config.constants import RESUME_KEYWORDS
from app.config.schema import TenantConfig
from app.domain import adf
from app.domain.errors import AgentError
from app.domain.stage import Stage
from app.domain.state import PrdState

_RESUME_INSTRUCTION = "Reply `@agent resume` or `fixed` on this comment once fixed and I'll retry from where I stopped."


def is_resume_request(comment_text: str) -> bool:
    """EH-02 — does this admin comment ask the agent to resume?"""
    lowered = comment_text.lower()
    return any(keyword in lowered for keyword in RESUME_KEYWORDS)


def relevant_ticket_key(state: PrdState) -> str | None:
    """The ticket EH-01 should post the error on — the one closest to where the run is.

    A failure during the review loop belongs on the Review ticket the PM is watching; during
    publishing, on the Publishing ticket; otherwise on the PRD-tracking ticket. This puts the
    escalation where the relevant human will actually see it.
    """
    review_stages = {
        Stage.DRAFTED,
        Stage.AWAITING_REVIEW,
        Stage.AWAITING_STRUCTURE_CONFIRM,
        Stage.AWAITING_CLARIFICATION,
        Stage.REVISING,
    }
    checkpoint = state.last_good_checkpoint or state.stage
    if checkpoint in {Stage.PASSED, Stage.AWAITING_PUBLISH_APPROVAL, Stage.PUBLISHING}:
        return state.publishing_ticket_key or state.prd_tracking_ticket_key
    if checkpoint in review_stages:
        return state.review_ticket_key or state.prd_tracking_ticket_key
    return state.prd_tracking_ticket_key or state.review_ticket_key


def build_error_comment(*, admin_account_id: str, error: AgentError, correlation_id: str) -> dict:
    """The single EH-01 escalation comment (ADF)."""
    return adf.doc(
        adf.paragraph(
            adf.mention(admin_account_id),
            adf.text(" the automation hit a problem and has paused this run."),
        ),
        adf.paragraph(adf.strong("What happened: "), adf.text(str(error))),
        adf.paragraph(adf.strong("Suggested fix: "), adf.text(error.suggested_fix)),
        adf.paragraph(adf.strong("To resume: "), adf.text(_RESUME_INSTRUCTION.replace("`", ""))),
        adf.paragraph(
            adf.text("Correlation id: "),
            adf.code(correlation_id),
            adf.text(f"  ·  failed step: {error.operation}"),
        ),
    )


class ErrorHandler:
    """Posts the EH-01 escalation comment for an errored run."""

    __slots__ = ("_on_comment", "_ticket_manager")

    def __init__(self, ticket_manager: TicketManager, *, on_comment=None) -> None:
        """
        Args:
            ticket_manager: the Jira write path.
            on_comment: optional `(comment_id) -> None` hook used to claim the posted comment's id
                in `processed_events`. The escalation comment quotes the literal `@agent resume`
                instruction, so Jira's echo of it is a comment event that `is_resume_request` would
                match — claiming the id makes that echo a dedupe duplicate instead of a run
                resuming itself. The author check in the router covers the same ground whenever the
                agent and admin are separate accounts; this covers it when they are not.
        """
        self._ticket_manager = ticket_manager
        self._on_comment = on_comment

    async def surface(
        self, *, state: PrdState, error: AgentError, tenant: TenantConfig
    ) -> str | None:
        """Post the one escalation comment. Returns the ticket it landed on, or None if there is none.

        Called once, from the errored `RunResult` — not from inside a retry loop — so AD-19's
        "exactly one comment" holds.
        """
        ticket_key = relevant_ticket_key(state)
        if ticket_key is None:
            # No ticket exists yet (failure before the tracking ticket). Nothing to post on; the
            # error is still in the state record and the logs for the admin to find.
            return None
        comment_id = await self._ticket_manager.comment(
            ticket_key,
            build_error_comment(
                admin_account_id=tenant.admin_account_id,
                error=error,
                correlation_id=state.correlation_id,
            ),
        )
        if comment_id and self._on_comment is not None:
            self._on_comment(comment_id)
        return ticket_key
