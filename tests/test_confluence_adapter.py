"""Story 1.8 — `ConfluenceAdapter` + markdown converter (AD-7, AD-14, AD-18)."""

from __future__ import annotations

import httpx
import pytest

from app.adapters.confluence import ConfluenceAdapter
from app.adapters.http import AtlassianClient
from app.adapters.markdown import normalize_storage, storage_to_markdown
from app.config.constants import AGENT_GENERATED_LABEL
from app.config.secrets import AtlassianCredentials
from app.domain.errors import AgentError
from tests.test_jira_adapter import FakeTransport, _no_sleep, json_response

CREDENTIALS = AtlassianCredentials(
    base_url="https://example.atlassian.net", email="svc@example.com", api_token="token"
)


def build(*responses: httpx.Response) -> tuple[ConfluenceAdapter, FakeTransport]:
    transport = FakeTransport(*responses)
    client = AtlassianClient(
        CREDENTIALS,
        product="confluence",
        max_attempts=3,
        backoff_seconds=0,
        client=httpx.AsyncClient(transport=transport, base_url=CREDENTIALS.base_url),
        sleep=_no_sleep,
    )
    return ConfluenceAdapter(client), transport


def page_body(page_id: str = "p1", *, storage: str = "<p>Hello</p>", version: int = 1) -> dict:
    return {
        "id": page_id,
        "title": "final_PRD_Widget",
        "spaceId": "space-1",
        "parentId": "folder-source-1",
        "version": {"number": version},
        "body": {"storage": {"value": storage}},
        "labels": {"results": [{"name": "prd"}]},
    }


# ---------------------------------------------------------------------------------------------
# AC 1: the domain-verb surface.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb",
    [
        "get_page",
        "create_page",
        "move_page",
        "set_edit_restriction",
        "add_label",
        "get_labels",
        "get_folder",
        "storage_to_markdown",
    ],
)
def test_adapter_exposes_the_required_domain_verbs(verb: str) -> None:
    assert hasattr(ConfluenceAdapter, verb), f"AD-7 requires a `{verb}` domain verb"


async def test_get_page_defaults_to_v2() -> None:
    adapter, transport = build(json_response(200, page_body()))
    page = await adapter.get_page("p1")
    assert transport.requests[0].url.path == "/wiki/api/v2/pages/p1"
    assert page.title == "final_PRD_Widget"
    assert page.body_storage == "<p>Hello</p>"


async def test_create_page_uses_v2() -> None:
    adapter, transport = build(json_response(201, page_body("p9")))
    await adapter.create_page(space_id="space-1", title="Guide", body_storage="<p>x</p>")
    assert transport.requests[0].url.path == "/wiki/api/v2/pages"


async def test_update_page_increments_the_version_for_optimistic_locking() -> None:
    adapter, transport = build(json_response(200, page_body(version=4)))
    await adapter.update_page(page_id="p1", title="Guide", body_storage="<p>x</p>", version=3)
    assert b'"number":4' in transport.requests[0].content.replace(b" ", b"")


# ---------------------------------------------------------------------------------------------
# AD-14: folder placement goes through the v1 move endpoint. The v2 parentId path 500s.
# ---------------------------------------------------------------------------------------------


async def test_move_page_uses_the_v1_move_append_endpoint() -> None:
    adapter, transport = build(httpx.Response(200))
    await adapter.move_page("p1", "folder-draft-1")
    assert transport.requests[0].url.path == "/wiki/rest/api/content/p1/move/append/folder-draft-1"
    assert transport.requests[0].method == "PUT"


async def test_create_page_never_sends_a_folder_as_parent_id() -> None:
    """AD-14 — the whole reason create and move are two steps."""
    adapter, transport = build(json_response(201, page_body("p9")))
    await adapter.create_page(space_id="space-1", title="Guide", body_storage="<p>x</p>")
    assert b"parentId" not in transport.requests[0].content


async def test_get_folder_uses_the_v2_folders_api() -> None:
    adapter, transport = build(json_response(200, {"id": "folder-1", "title": "final_PRD"}))
    assert (await adapter.get_folder("folder-1"))["title"] == "final_PRD"
    assert transport.requests[0].url.path == "/wiki/api/v2/folders/folder-1"


async def test_page_ancestors_support_the_watched_folder_check() -> None:
    """FR-01 — the page-created payload does not reliably carry the container (AD-14)."""
    adapter, _ = build(json_response(200, {"results": [{"id": "folder-source-1"}, {"id": "root"}]}))
    assert await adapter.get_page_ancestors("p1") == ("folder-source-1", "root")


def test_page_knows_whether_it_is_in_a_folder() -> None:
    from app.domain.atlassian import ConfluencePage

    page = ConfluencePage(id="p1", title="t", parent_id="x", ancestor_ids=("folder-source-1",))
    assert page.in_folder("folder-source-1")
    assert not page.in_folder("folder-published-1")


# ---------------------------------------------------------------------------------------------
# AD-18: the edit restriction must include the agent, or it locks itself out.
# ---------------------------------------------------------------------------------------------


async def test_edit_restriction_is_applied_via_the_v1_restriction_endpoint() -> None:
    adapter, transport = build(json_response(200, {}))
    await adapter.set_edit_restriction("p1", allowed_account_ids=["acct-agent", "acct-admin"])
    request = transport.requests[0]
    assert request.url.path == "/wiki/rest/api/content/p1/restriction"
    assert b'"operation":"update"' in request.content.replace(b" ", b"")
    assert b"acct-agent" in request.content


async def test_empty_allow_list_is_refused_before_the_call() -> None:
    """An empty list would lock the agent out of the page it just published (AD-18)."""
    adapter, transport = build()

    with pytest.raises(AgentError, match="empty allow-list") as caught:
        await adapter.set_edit_restriction("p1", allowed_account_ids=[])

    assert "lock the agent out" in caught.value.suggested_fix
    assert transport.requests == [], "the refusal must happen before any HTTP call"


# ---------------------------------------------------------------------------------------------
# AD-10 / AD-11: self-ingestion label and the correlation marker.
# ---------------------------------------------------------------------------------------------


async def test_agent_generated_label_is_stamped_on_published_pages() -> None:
    adapter, transport = build(json_response(200, {}))
    await adapter.stamp_agent_generated("p1")
    assert AGENT_GENERATED_LABEL.encode() in transport.requests[0].content


async def test_labels_are_readable_for_the_detection_guard() -> None:
    adapter, _ = build(json_response(200, {"results": [{"name": AGENT_GENERATED_LABEL}]}))
    assert AGENT_GENERATED_LABEL in await adapter.get_labels("p1")


async def test_content_property_stamps_the_correlation_marker() -> None:
    adapter, transport = build(json_response(200, {}))
    await adapter.set_content_property("p1", "leapxpert-prd-id", "page-123")
    assert b"page-123" in transport.requests[0].content


async def test_re_stamping_an_existing_marker_is_not_an_error() -> None:
    """A resume re-running the stage must not fail because the marker is already set (AD-11)."""
    adapter, _ = build(json_response(409, {"message": "property already exists"}))
    await adapter.set_content_property("p1", "leapxpert-prd-id", "page-123")


# ---------------------------------------------------------------------------------------------
# Storage format → Markdown (FR-15 step 3, PRD §13 Q5).
# ---------------------------------------------------------------------------------------------


def test_headings_and_prose_convert() -> None:
    markdown = storage_to_markdown("<h1>Getting started</h1><p>Welcome to the app.</p>")
    assert "# Getting started" in markdown
    assert "Welcome to the app." in markdown


def test_lists_convert() -> None:
    markdown = storage_to_markdown("<ul><li>First</li><li>Second</li></ul>")
    assert "- First" in markdown and "- Second" in markdown


def test_code_macro_becomes_a_fenced_block() -> None:
    storage = (
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">python</ac:parameter>'
        "<ac:plain-text-body>print(1)</ac:plain-text-body>"
        "</ac:structured-macro>"
    )
    assert "print(1)" in storage_to_markdown(storage)


def test_info_panel_becomes_a_blockquote() -> None:
    storage = (
        '<ac:structured-macro ac:name="info">'
        "<ac:rich-text-body><p>Remember to save.</p></ac:rich-text-body>"
        "</ac:structured-macro>"
    )
    markdown = storage_to_markdown(storage)
    assert "Remember to save." in markdown
    assert ">" in markdown


def test_unknown_macro_keeps_its_prose() -> None:
    """Losing a macro's *rendering* is acceptable (§13 Q5); losing the words inside it is not."""
    storage = (
        '<ac:structured-macro ac:name="some-future-macro">'
        "<ac:rich-text-body><p>Important content.</p></ac:rich-text-body>"
        "</ac:structured-macro>"
    )
    assert "Important content." in storage_to_markdown(storage)


def test_navigational_macros_are_dropped() -> None:
    storage = '<ac:structured-macro ac:name="toc"/><p>Real content.</p>'
    markdown = storage_to_markdown(storage)
    assert "Real content." in markdown
    assert "toc" not in markdown


def test_confluence_page_link_becomes_a_markdown_link() -> None:
    storage = (
        '<ac:link><ri:page ri:content-title="Billing"/>'
        "<ac:plain-text-link-body>See billing</ac:plain-text-link-body></ac:link>"
    )
    assert "See billing" in storage_to_markdown(storage)


def test_image_attachment_is_preserved() -> None:
    storage = '<ac:image><ri:attachment ri:filename="diagram.png"/></ac:image>'
    assert "diagram.png" in storage_to_markdown(storage)


def test_task_list_becomes_checkboxes() -> None:
    storage = (
        "<ac:task-list><ac:task><ac:task-status>complete</ac:task-status>"
        "<ac:task-body>Sign in</ac:task-body></ac:task>"
        "<ac:task><ac:task-status>incomplete</ac:task-status>"
        "<ac:task-body>Verify email</ac:task-body></ac:task></ac:task-list>"
    )
    markdown = storage_to_markdown(storage)
    assert "[x] Sign in" in markdown
    assert "[ ] Verify email" in markdown


def test_no_atlassian_namespaced_tags_survive_conversion() -> None:
    """A leaked `ac:` tag in the exported .md would be visible to end users on the help site."""
    storage = (
        "<ac:layout><ac:layout-section><ac:layout-cell>"
        "<p>Body text.</p>"
        '<ac:structured-macro ac:name="expand"><ac:rich-text-body><p>Details.</p>'
        "</ac:rich-text-body></ac:structured-macro>"
        "</ac:layout-cell></ac:layout-section></ac:layout>"
    )
    markdown = storage_to_markdown(storage)
    assert "ac:" not in markdown and "ri:" not in markdown
    assert "Body text." in markdown and "Details." in markdown


def test_normalization_is_testable_on_its_own() -> None:
    """Exposed separately because silent content loss is easiest to catch at this seam."""
    assert "<pre>" in normalize_storage(
        '<ac:structured-macro ac:name="code">'
        "<ac:plain-text-body>x = 1</ac:plain-text-body></ac:structured-macro>"
    )


def test_empty_body_does_not_crash() -> None:
    assert storage_to_markdown("") == "\n"


def test_output_has_no_runs_of_blank_lines() -> None:
    markdown = storage_to_markdown("<p>One</p><p></p><p></p><p>Two</p>")
    assert "\n\n\n" not in markdown
