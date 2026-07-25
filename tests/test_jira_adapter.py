"""Story 1.7 — `JiraAdapter`: domain verbs, ADF bodies, retry, error normalization (AD-7, NFR-08)."""

from __future__ import annotations

import httpx
import pytest

from app.adapters.http import AtlassianClient
from app.adapters.jira import JiraAdapter
from app.config.secrets import AtlassianCredentials
from app.domain import adf
from app.domain.errors import AgentError

CREDENTIALS = AtlassianCredentials(
    base_url="https://example.atlassian.net", email="svc@example.com", api_token="token"
)


class FakeTransport(httpx.AsyncBaseTransport):
    """Records requests and replays scripted responses. No network, no credentials."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"unexpected extra request: {request.method} {request.url}")
        return self.responses.pop(0)


def build(*responses: httpx.Response, max_attempts: int = 3) -> tuple[JiraAdapter, FakeTransport]:
    transport = FakeTransport(*responses)
    client = AtlassianClient(
        CREDENTIALS,
        product="jira",
        max_attempts=max_attempts,
        backoff_seconds=0,
        client=httpx.AsyncClient(transport=transport, base_url=CREDENTIALS.base_url),
        sleep=_no_sleep,
    )
    return JiraAdapter(client), transport


async def _no_sleep(_seconds: float) -> None:
    """Backoff must not actually delay the test suite."""


def json_response(status: int, payload: object) -> httpx.Response:
    return httpx.Response(status, json=payload)


def issue_payload(
    *,
    key: str = "MAIN-1",
    category: str = "new",
    labels: list[str] | None = None,
    summary: str = "Track the PRD",
) -> dict:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": "To Do", "statusCategory": {"key": category}},
            "assignee": {"accountId": "acct-pm-1"},
            "reporter": {"accountId": "acct-admin-1"},
            "labels": labels or [],
            "issuetype": {"name": "Task"},
        },
    }


# ---------------------------------------------------------------------------------------------
# AC 1: the domain-verb surface, on Jira REST v3, with token auth from an env reference.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb",
    [
        "search_issues",
        "get_transitions",
        "transition_issue",
        "add_comment",
        "create_issue",
        "get_current_user",
    ],
)
def test_adapter_exposes_the_required_domain_verbs(verb: str) -> None:
    assert callable(getattr(JiraAdapter, verb, None)), f"AD-7 requires a `{verb}` domain verb"


async def test_calls_target_jira_rest_v3() -> None:
    adapter, transport = build(json_response(200, {"accountId": "acct-agent"}))
    await adapter.get_current_user()
    assert transport.requests[0].url.path == "/rest/api/3/myself"


async def test_requests_carry_basic_auth_from_the_credential_triple() -> None:
    adapter, transport = build(json_response(200, {"accountId": "acct-agent"}))
    await adapter.get_current_user()
    assert transport.requests[0].headers["Authorization"].startswith("Basic ")


async def test_get_current_user_returns_the_agent_account_id() -> None:
    """AD-10 — this id has ONE source; the detection guard and publish restriction both reuse it."""
    adapter, _ = build(json_response(200, {"accountId": "acct-agent-99"}))
    assert await adapter.get_current_user() == "acct-agent-99"


async def test_get_issue_maps_status_category_not_status_name() -> None:
    """AD-13 — done-ness is judged by category so it holds across differently-named workflows."""
    adapter, _ = build(json_response(200, issue_payload(category="done")))
    issue = await adapter.get_issue("MAIN-1")
    assert issue.is_done
    assert issue.status_name == "To Do", "the literal name is irrelevant to done-ness"


async def test_an_in_progress_issue_is_not_done() -> None:
    adapter, _ = build(json_response(200, issue_payload(category="indeterminate")))
    assert not (await adapter.get_issue("MAIN-1")).is_done


async def test_get_transitions_returns_only_currently_legal_ones() -> None:
    adapter, _ = build(
        json_response(
            200,
            {
                "transitions": [
                    {
                        "id": "31",
                        "name": "Done",
                        "to": {"name": "Done", "statusCategory": {"key": "done"}},
                    },
                    {
                        "id": "21",
                        "name": "Start",
                        "to": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
                    },
                ]
            },
        )
    )
    transitions = await adapter.get_transitions("MAIN-1")
    assert [t.id for t in transitions] == ["31", "21"]
    assert transitions[0].leads_to_done
    assert not transitions[1].leads_to_done


async def test_transition_issue_posts_the_transition_id() -> None:
    adapter, transport = build(httpx.Response(204))
    await adapter.transition_issue("MAIN-1", "31")
    request = transport.requests[0]
    assert request.url.path == "/rest/api/3/issue/MAIN-1/transitions"
    assert b'"id": "31"' in request.content or b'"id":"31"' in request.content


# ---------------------------------------------------------------------------------------------
# AC 1 (cont.): every comment/description body is ADF — a plain string is rejected by Jira v3.
# ---------------------------------------------------------------------------------------------


async def test_add_comment_sends_an_adf_document() -> None:
    adapter, transport = build(json_response(201, {"id": "10001"}))
    body = adf.doc(adf.paragraph(adf.text("Please review the draft.")))

    comment_id = await adapter.add_comment("REV-1", body)

    assert comment_id == "10001"
    assert b'"type":"doc"' in transport.requests[0].content.replace(b" ", b"")


async def test_add_comment_refuses_a_plain_string() -> None:
    """Caught here rather than surfacing as an opaque 400 mid-run."""
    adapter, _ = build()
    with pytest.raises(AgentError, match="not an ADF document"):
        await adapter.add_comment("REV-1", "just a string")  # type: ignore[arg-type]


async def test_create_issue_refuses_a_plain_string_description() -> None:
    adapter, _ = build()
    with pytest.raises(AgentError, match="not an ADF document"):
        await adapter.create_issue(
            project_key="MAIN",
            summary="x",
            description="plain",  # type: ignore[arg-type]
        )


def test_mention_notifies_whereas_plain_text_does_not() -> None:
    """FR-07/EH-01 require *tagging* a human; "@Name" as text notifies nobody and strands the run."""
    node = adf.mention("acct-pm-1", "Reviewer")
    assert node["type"] == "mention"
    assert node["attrs"]["id"] == "acct-pm-1"


def test_adf_round_trips_back_to_text() -> None:
    """Jira returns comment bodies as ADF; the Feedback interpreter must read what the PM wrote."""
    document = adf.doc(
        adf.paragraph(adf.text("Section: Intro")), adf.paragraph(adf.text("Issue: unclear"))
    )
    assert "Section: Intro" in adf.extract_text(document)
    assert "Issue: unclear" in adf.extract_text(document)


# ---------------------------------------------------------------------------------------------
# AD-11: the correlation marker is in the create payload, so an orphan is always adoptable.
# ---------------------------------------------------------------------------------------------


async def test_create_issue_stamps_the_prd_correlation_label() -> None:
    adapter, transport = build(json_response(201, {"key": "MAIN-7"}))

    issue = await adapter.create_issue(
        project_key="MAIN",
        summary="UserDoc review",
        description=adf.text_to_doc("body"),
        prd_id="page-123",
        assignee_account_id="acct-pm-1",
    )

    assert issue.key == "MAIN-7"
    assert b"prd-page-123" in transport.requests[0].content, (
        "the marker must be in the create payload itself so it is atomic with the create (AD-11)"
    )


async def test_create_issue_carries_extra_labels_into_the_payload() -> None:
    """`extra_labels` (e.g. the `agent-generated` marker) rides alongside the AD-11 prd- label."""
    adapter, transport = build(json_response(201, {"key": "MAIN-8"}))

    issue = await adapter.create_issue(
        project_key="MAIN",
        summary="UserDoc publishing",
        description=adf.text_to_doc("body"),
        prd_id="page-123",
        extra_labels=("agent-generated",),
    )

    assert issue.key == "MAIN-8"
    payload = transport.requests[0].content
    assert b"agent-generated" in payload and b"prd-page-123" in payload
    assert "agent-generated" in issue.labels and "prd-page-123" in issue.labels


async def test_find_issue_by_prd_marker_adopts_an_orphan() -> None:
    """The create-succeeded-then-crashed-before-persisting window (AD-11 hardening)."""
    adapter, transport = build(
        json_response(200, {"issues": [issue_payload(key="MAIN-7", labels=["prd-page-123"])]})
    )

    found = await adapter.find_issue_by_prd_marker("MAIN", "page-123")

    assert found is not None and found.key == "MAIN-7"
    assert b"prd-page-123" in transport.requests[0].content


async def test_find_issue_by_prd_marker_returns_none_when_there_is_no_orphan() -> None:
    adapter, _ = build(json_response(200, {"issues": []}))
    assert await adapter.find_issue_by_prd_marker("MAIN", "page-123") is None


async def test_marker_search_with_a_type_skips_a_different_ticket_that_shares_the_marker() -> None:
    """After a rename detour the Review project holds a rename request AND (eventually) a Review
    ticket, both marked. Untyped search returns the older rename request; the typed search must skip
    it — this is the bug where the drafting step adopted the rename ticket as the Review ticket."""
    adapter, _ = build(
        json_response(
            200,
            {
                "issues": [
                    # oldest first — the rename request was created before drafting
                    issue_payload(
                        key="REV-1",
                        labels=["prd-page-123"],
                        summary="Please confirm & rename PRD page: X",
                    ),
                    issue_payload(
                        key="REV-2", labels=["prd-page-123"], summary="Review UserDoc: X"
                    ),
                ]
            },
        )
    )

    found = await adapter.find_issue_by_prd_marker(
        "REV", "page-123", summary_prefix="Review UserDoc:"
    )

    assert found is not None and found.key == "REV-2", "adopt the Review ticket, not the rename one"


async def test_typed_marker_search_returns_none_when_only_the_other_type_exists() -> None:
    """Only the rename request exists → no Review ticket to adopt → the caller must create one."""
    adapter, _ = build(
        json_response(
            200,
            {
                "issues": [
                    issue_payload(
                        key="REV-1",
                        labels=["prd-page-123"],
                        summary="Please confirm & rename PRD page: X",
                    )
                ]
            },
        )
    )

    found = await adapter.find_issue_by_prd_marker(
        "REV", "page-123", summary_prefix="Review UserDoc:"
    )

    assert found is None, "the rename request must not be mistaken for a Review ticket"


async def test_search_does_not_assume_a_fixed_ticket_location() -> None:
    """FR-04 — a human-created tracking ticket may live anywhere, so the search is JQL over projects."""
    adapter, transport = build(json_response(200, {"issues": []}))
    await adapter.search_issues('project = "MAIN" AND summary ~ "Widget"')
    assert transport.requests[0].url.path == "/rest/api/3/search/jql"


# ---------------------------------------------------------------------------------------------
# NFR-08: transient failures retry with backoff; everything else escalates immediately.
# ---------------------------------------------------------------------------------------------


async def test_transient_5xx_is_retried_and_then_succeeds() -> None:
    adapter, transport = build(
        json_response(503, {"message": "unavailable"}),
        json_response(200, {"accountId": "acct-agent"}),
    )
    assert await adapter.get_current_user() == "acct-agent"
    assert len(transport.requests) == 2


async def test_rate_limit_is_retried() -> None:
    adapter, transport = build(
        json_response(429, {"message": "too many"}), json_response(200, {"accountId": "a"})
    )
    await adapter.get_current_user()
    assert len(transport.requests) == 2


async def test_retries_are_capped_then_raise_a_normalized_error() -> None:
    adapter, transport = build(*[json_response(503, {"message": "down"})] * 3, max_attempts=3)

    with pytest.raises(AgentError) as caught:
        await adapter.get_current_user()

    assert len(transport.requests) == 3, "NFR-08 — ~3 attempts, then escalate"
    assert caught.value.retryable
    assert caught.value.operation == "jira.get_current_user"


async def test_permission_error_is_not_retried() -> None:
    """Retrying a 403 three times only delays the escalation and burns the admin's time."""
    adapter, transport = build(json_response(403, {"errorMessages": ["Forbidden"]}))

    with pytest.raises(AgentError) as caught:
        await adapter.get_issue("MAIN-1")

    assert len(transport.requests) == 1
    assert not caught.value.retryable
    assert "lacks permission" in caught.value.suggested_fix


async def test_connection_failure_is_retried_then_normalized() -> None:
    class DeadTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.attempts = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.attempts += 1
            raise httpx.ConnectError("no route to host")

    transport = DeadTransport()
    client = AtlassianClient(
        CREDENTIALS,
        product="jira",
        max_attempts=3,
        backoff_seconds=0,
        client=httpx.AsyncClient(transport=transport, base_url=CREDENTIALS.base_url),
        sleep=_no_sleep,
    )

    with pytest.raises(AgentError, match="Could not reach Jira"):
        await JiraAdapter(client).get_current_user()

    assert transport.attempts == 3


# ---------------------------------------------------------------------------------------------
# AD-19: errors carry what the EH-01 comment needs — plain language plus a suggested fix.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected_fix_fragment"),
    [
        (401, "Regenerate it"),
        (403, "lacks permission"),
        (404, "config registry"),
    ],
)
async def test_errors_carry_an_actionable_suggested_fix(
    status: int, expected_fix_fragment: str
) -> None:
    adapter, _ = build(json_response(status, {"errorMessages": ["nope"]}))

    with pytest.raises(AgentError) as caught:
        await adapter.get_issue("MAIN-1")

    assert expected_fix_fragment in caught.value.suggested_fix
    assert caught.value.status_code == status


async def test_error_context_names_the_affected_entity() -> None:
    adapter, _ = build(json_response(404, {"errorMessages": ["Issue does not exist"]}))

    with pytest.raises(AgentError) as caught:
        await adapter.get_issue("MAIN-404")

    assert caught.value.context["issue"] == "MAIN-404"
    assert "MAIN-404" in str(caught.value)


async def test_error_message_includes_the_upstream_detail() -> None:
    adapter, _ = build(json_response(400, {"errors": {"summary": "is required"}}))

    with pytest.raises(AgentError, match="summary: is required"):
        await adapter.get_issue("MAIN-1")


# -- get_comments: the poll-path read behind FR-09 feedback ------------------------------------


async def test_get_comments_flattens_adf_bodies_to_plain_text() -> None:
    """The Feedback interpreter must read what the PM wrote, not a JSON tree."""
    adapter, transport = build(
        json_response(
            200,
            {
                "comments": [
                    {
                        "id": "10001",
                        "author": {"accountId": "acct-pm"},
                        "created": "2026-07-24T10:00:00.000+0000",
                        "body": adf.doc(
                            adf.paragraph(adf.text("Section: Setup")),
                            adf.paragraph(adf.text("Issue: too terse")),
                        ),
                    }
                ]
            },
        )
    )

    comments = await adapter.get_comments("UDR-1")

    assert [c.id for c in comments] == ["10001"]
    assert comments[0].author_account_id == "acct-pm"
    assert "Section: Setup" in comments[0].body_text
    assert "Issue: too terse" in comments[0].body_text
    assert transport.requests[0].url.path == "/rest/api/3/issue/UDR-1/comment"


async def test_get_comments_returns_empty_for_an_issue_with_no_comments() -> None:
    adapter, _ = build(json_response(200, {"comments": []}))

    assert await adapter.get_comments("UDR-1") == []


async def test_get_comments_keeps_soft_line_breaks_that_carry_the_feedback_format() -> None:
    """A PM typing the §6.2 format with Shift+Enter produces one paragraph of `hardBreak` nodes.

    Dropping them concatenates the labels ("What Quick Notes doesIssue: ...") and hides the
    structure the Feedback interpreter keys on, so the breaks must survive the flattening.
    """
    paragraph_with_breaks = {
        "type": "paragraph",
        "content": [
            {"type": "text", "text": "Section: What Quick Notes does"},
            {"type": "hardBreak"},
            {"type": "text", "text": "Issue: too much detail"},
            {"type": "hardBreak"},
            {"type": "text", "text": "Suggested change: trim it"},
        ],
    }
    adapter, _ = build(
        json_response(
            200,
            {
                "comments": [
                    {
                        "id": "10001",
                        "author": {"accountId": "acct-pm"},
                        "body": {"version": 1, "type": "doc", "content": [paragraph_with_breaks]},
                    }
                ]
            },
        )
    )

    body_text = (await adapter.get_comments("UDR-1"))[0].body_text

    assert "Section: What Quick Notes does" in body_text
    assert "doesIssue" not in body_text
    assert body_text.splitlines()[1].startswith("Issue:")
