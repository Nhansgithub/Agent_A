"""The agent must never read its own Jira comment back as PM feedback (AD-10, AD-16, AD-9).

Jira echoes every comment as a `comment-created` webhook — including the ones the agent just posted.
Left unguarded, the clarification question the agent asks at `awaiting_clarification` returns as an
event, is interpreted as the PM's reply, and the agent answers its own question. AD-16 is explicit
that the clarification and structure-confirmation loops "block on a human reply and must never
fabricate the answer", so this is a correctness guard, not a tidiness one.

The guard reuses AD-9 rather than adding a second mechanism: `RunContext.post_comment` claims the
new comment's id in `processed_events` at post time, so the echo collides on the UNIQUE constraint
and the ingress drops it as a duplicate.

Deliberately *not* an author-account comparison: an agent whose Atlassian token belongs to the same
account as a human reviewer (the single-account case) could not be told apart that way, and the check
would make the agent deaf to that human's real feedback.
"""

from __future__ import annotations

from app.config.registry import ConfigRegistry
from app.domain import adf
from app.domain.dedupe import DedupeKey
from app.domain.events import EventType
from app.orchestrator.context import RunContext
from app.repository import Repository
from app.repository.database import Database
from tests.conftest import registry_mapping

TENANT = ConfigRegistry.from_mapping(registry_mapping()).tenants["tenant_one"]


class FakeTickets:
    """Stands in for the TicketManager; returns the id Jira would assign."""

    def __init__(self, comment_id: str = "10042") -> None:
        self.comment_id = comment_id
        self.posted: list[tuple[str, dict]] = []

    async def comment(self, issue_key: str, body: dict) -> str:
        self.posted.append((issue_key, body))
        return self.comment_id


def build(comment_id: str = "10042") -> tuple[RunContext, Repository, FakeTickets]:
    repository = Repository(Database(":memory:"))
    tickets = FakeTickets(comment_id)
    context = RunContext(
        prd_id="page-1",
        correlation_id="corr-1",
        tenant=TENANT,
        confluence_base_url="https://example.atlassian.net",
        repository=repository,
        confluence=None,
        detection=None,
        classifier=None,
        author=None,
        feedback_interpreter=None,
        publisher=None,
        ticket_manager=tickets,
        identity=None,
        agent_account_cache={},
    )
    return context, repository, tickets


def key_for(comment_id: str) -> DedupeKey:
    return DedupeKey(TENANT.project_id, EventType.JIRA_COMMENT_CREATED, comment_id)


async def test_posting_a_comment_claims_its_id_so_the_echo_is_a_duplicate() -> None:
    context, repository, tickets = build(comment_id="10042")

    await context.post_comment("UDR-1", adf.doc(adf.paragraph(adf.text("Is this what you mean?"))))

    assert tickets.posted[0][0] == "UDR-1"
    # The webhook echoing this same comment back computes exactly this key and finds it taken.
    assert repository.events.is_processed(key_for("10042"))


async def test_a_genuine_pm_comment_is_not_claimed() -> None:
    """The guard must suppress only the agent's own comments — never a human's."""
    context, repository, _ = build(comment_id="10042")

    await context.post_comment("UDR-1", adf.doc(adf.paragraph(adf.text("question"))))

    assert not repository.events.is_processed(key_for("99999"))


async def test_the_claim_is_scoped_to_the_tenant() -> None:
    """AD-9 keys are tenant-prefixed, so one tenant's claim cannot suppress another's comment."""
    context, repository, _ = build(comment_id="10042")

    await context.post_comment("UDR-1", adf.doc(adf.paragraph(adf.text("question"))))

    other = DedupeKey("tenant_two", EventType.JIRA_COMMENT_CREATED, "10042")
    assert not repository.events.is_processed(other)


async def test_a_comment_jira_gave_no_id_is_tolerated() -> None:
    """A missing id must not crash the run — the comment still posted.

    An empty `entity_id` is not a legal dedupe key (it would collide with every other empty one), so
    the guard skips the claim rather than constructing one. Losing the claim is the safe direction:
    the echo may be re-read, but the run does not fail on a comment that was delivered.
    """
    context, _repository, tickets = build(comment_id="")

    await context.post_comment("UDR-1", adf.doc(adf.paragraph(adf.text("question"))))

    assert tickets.posted  # the comment went out; no ValueError from an empty key
