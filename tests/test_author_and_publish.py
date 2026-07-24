"""Epic 3 — Author, self-critique, draft publication, Review ticket, framed request (FR-05…FR-07)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.adapters.markdown import markdown_to_storage
from app.agents.author.agent import AuthorAgent
from app.agents.llm import CallMetadata, LlmClient
from app.agents.publisher import Publisher
from app.agents.review_request import build_change_summary, build_review_request
from app.domain import adf
from app.domain.atlassian import ConfluencePage
from tests.test_llm_client import FakeAnthropic


def metadata() -> CallMetadata:
    return CallMetadata(correlation_id="c", prd_id="page-1", agent_role="author", review_round=0)


# ---------------------------------------------------------------------------------------------
# Story 3.1 / 3.2 — the Author drafts and runs exactly one self-critique pass.
# ---------------------------------------------------------------------------------------------


class ScriptedAnthropic(FakeAnthropic):
    """Returns queued replies in order, so draft-then-critique can differ."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__()
        self._replies = list(replies)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        from types import SimpleNamespace

        text = self._replies.pop(0) if self._replies else "# Fallback\n\nBody."
        return SimpleNamespace(
            content=[SimpleNamespace(text=text, type="text")],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
            stop_reason="end_turn",
        )


async def test_draft_runs_a_first_pass_then_one_self_critique() -> None:
    fake = ScriptedAnthropic(["# Draft v1\n\nRough.", "# Widget Guide\n\nPolished."])
    author = AuthorAgent(LlmClient("k", client=fake), model="claude-opus-4-8")

    draft = await author.draft(
        prd_title="final_PRD_Widget", prd_markdown="Build a widget manager.", metadata=metadata()
    )

    assert len(fake.calls) == 2, "FR-05 — exactly one draft + one self-critique pass"
    assert draft.self_critique_applied
    assert draft.markdown == "# Widget Guide\n\nPolished."
    assert draft.title == "Widget Guide"


async def test_draft_title_falls_back_to_the_prd_title_when_absent() -> None:
    fake = ScriptedAnthropic(["No heading here.", "Still no heading, just prose."])
    author = AuthorAgent(LlmClient("k", client=fake), model="claude-opus-4-8")
    draft = await author.draft(prd_title="final_PRD_X", prd_markdown="body", metadata=metadata())
    assert draft.title == "final_PRD_X"


async def test_draft_tokens_are_summed_across_both_passes() -> None:
    """NFR-09 — per-round cost visibility needs the totals from both calls."""
    fake = ScriptedAnthropic(["# A\n\nx", "# A\n\ny"])
    author = AuthorAgent(LlmClient("k", client=fake), model="claude-opus-4-8")
    draft = await author.draft(prd_title="t", prd_markdown="body", metadata=metadata())
    assert draft.total_tokens == 2 * (100 + 50)


async def test_empty_prd_raises() -> None:
    import pytest

    from app.domain.errors import AgentError

    author = AuthorAgent(LlmClient("k", client=ScriptedAnthropic([])), model="claude-opus-4-8")
    with pytest.raises(AgentError, match="no content"):
        await author.draft(prd_title="t", prd_markdown="   ", metadata=metadata())


async def test_revise_applies_feedback_without_a_self_critique_pass() -> None:
    """FR-11 — the human is already in the loop; a second machine opinion is wasted cost (NFR-09)."""
    fake = ScriptedAnthropic(["# Widget Guide\n\nRevised per feedback."])
    author = AuthorAgent(LlmClient("k", client=fake), model="claude-opus-4-8")

    draft = await author.revise(
        current_markdown="# Widget Guide\n\nOld.",
        structured_feedback="Section: Intro\nIssue: unclear\nSuggested change: add an example",
        metadata=metadata(),
    )

    assert len(fake.calls) == 1, "no self-critique on revision"
    assert not draft.self_critique_applied
    assert "Revised per feedback" in draft.markdown


# ---------------------------------------------------------------------------------------------
# Markdown -> Confluence storage (FR-06 publishing needs the reverse of the export converter).
# ---------------------------------------------------------------------------------------------


def test_headings_lists_and_emphasis_convert_to_storage() -> None:
    storage = markdown_to_storage(
        "# Getting started\n\n"
        "Welcome to **Widget**.\n\n"
        "## Steps\n\n"
        "- First step\n"
        "- Second step\n\n"
        "1. Do this\n"
        "2. Then that\n"
    )
    assert "<h1>Getting started</h1>" in storage
    assert "<strong>Widget</strong>" in storage
    assert "<h2>Steps</h2>" in storage
    assert "<ul><li>First step</li><li>Second step</li></ul>" in storage
    assert "<ol><li>Do this</li><li>Then that</li></ol>" in storage


def test_links_and_inline_code_convert() -> None:
    storage = markdown_to_storage("See [the docs](https://x/docs) and run `widget init`.")
    assert '<a href="https://x/docs">the docs</a>' in storage
    assert "<code>widget init</code>" in storage


def test_fenced_code_becomes_a_code_macro() -> None:
    storage = markdown_to_storage("```python\nprint(1)\n```")
    assert 'ac:name="code"' in storage
    assert "print(1)" in storage


def test_user_text_cannot_inject_storage_markup() -> None:
    """A PRD containing angle brackets must not smuggle XHTML into the page body."""
    storage = markdown_to_storage("The tag <script>alert(1)</script> is just text.")
    assert "<script>" not in storage
    assert "&lt;script&gt;" in storage


def test_markdown_storage_round_trips_the_essentials() -> None:
    """A draft published then exported should keep its headings, prose, and lists (§13 Q5)."""
    from app.adapters.markdown import storage_to_markdown

    original = "# Guide\n\nDo the thing.\n\n## Steps\n\n- One\n- Two\n"
    round_tripped = storage_to_markdown(markdown_to_storage(original))
    assert "# Guide" in round_tripped
    assert "Do the thing." in round_tripped
    assert "- One" in round_tripped and "- Two" in round_tripped


# ---------------------------------------------------------------------------------------------
# Story 3.3 — the Publisher creates/adopts the draft page, stamped as agent output.
# ---------------------------------------------------------------------------------------------


@dataclass
class FakeConfluence:
    pages: dict[str, ConfluencePage] = field(default_factory=dict)
    created: list[dict] = field(default_factory=list)
    stamped_labels: list[str] = field(default_factory=list)
    stamped_props: list[tuple[str, str]] = field(default_factory=list)
    moved: list[tuple[str, str]] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    marker_hit: ConfluencePage | None = None
    _next: int = 1

    def markdown_to_storage(self, markdown: str) -> str:
        return markdown_to_storage(markdown)

    async def create_page(self, *, space_id, title, body_storage, parent_id=None):
        page = ConfluencePage(id=f"draft-{self._next}", title=title, version=1, space_id=space_id)
        self._next += 1
        self.created.append({"title": title, "space_id": space_id})
        self.pages[page.id] = page
        return page

    async def get_page(self, page_id, *, with_body=True):
        return self.pages[page_id]

    async def update_page(self, *, page_id, title, body_storage, version):
        self.updated.append(page_id)
        page = ConfluencePage(id=page_id, title=title, version=version + 1)
        self.pages[page_id] = page
        return page

    async def move_page(self, page_id, folder_id):
        self.moved.append((page_id, folder_id))

    async def stamp_agent_generated(self, page_id):
        self.stamped_labels.append(page_id)

    async def set_content_property(self, page_id, key, value):
        self.stamped_props.append((key, value))

    async def find_page_by_prd_marker(self, folder_id, prd_id):
        return self.marker_hit


TENANT = None  # set in fixture below


def tenant():
    from app.config.schema import TenantConfig
    from tests.conftest import tenant_entry

    return TenantConfig.model_validate({**tenant_entry(), "project_id": "tenant_one"})


async def test_publish_draft_creates_moves_and_stamps() -> None:
    confluence = FakeConfluence()
    published = await Publisher(confluence).publish_draft(
        tenant=tenant(),
        prd_id="page-1",
        title="Widget Guide",
        markdown="# Widget Guide\n\nBody.",
        space_id="space-1",
    )

    assert published.created
    assert confluence.created[0]["title"] == "Widget Guide"
    # AD-10 / AD-11 — stamped as agent output with the correlation marker.
    assert published.page.id in confluence.stamped_labels
    assert ("leapxpert-prd-id", "page-1") in confluence.stamped_props
    # AD-14 — placed into the draft folder via move, not a v2 parentId.
    assert confluence.moved == [(published.page.id, "folder-draft-1")]


async def test_publish_adopts_an_orphan_draft_rather_than_creating_a_second() -> None:
    """AD-11 — a draft created in a crash window is adopted, not duplicated."""
    confluence = FakeConfluence()
    confluence.marker_hit = ConfluencePage(id="draft-orphan", title="Old", version=2)
    confluence.pages["draft-orphan"] = confluence.marker_hit

    published = await Publisher(confluence).publish_draft(
        tenant=tenant(), prd_id="page-1", title="Widget Guide", markdown="# x", space_id="space-1"
    )

    assert not published.created
    assert published.page.id == "draft-orphan"
    assert confluence.created == [], "no second page created"
    assert "draft-orphan" in confluence.updated


async def test_publish_reuses_a_known_page_id() -> None:
    confluence = FakeConfluence()
    confluence.pages["draft-7"] = ConfluencePage(id="draft-7", title="Old", version=3)

    published = await Publisher(confluence).publish_draft(
        tenant=tenant(),
        prd_id="page-1",
        title="Widget Guide",
        markdown="# x",
        space_id="space-1",
        existing_page_id="draft-7",
    )

    assert not published.created
    assert published.page.id == "draft-7"
    assert confluence.created == []


# ---------------------------------------------------------------------------------------------
# Story 3.5 — the framed review-request comment (FR-07, §6.2, AD-15).
# ---------------------------------------------------------------------------------------------


def test_review_request_tags_the_pm_with_a_real_mention() -> None:
    body = build_review_request(pm_account_id="acct-pm-1", draft_page_url="https://x/draft")
    assert "acct-pm-1" in _mentions(body), (
        "a real @mention, not plain '@name' which notifies nobody"
    )


def test_review_request_asks_for_the_structured_format() -> None:
    body = build_review_request(pm_account_id="acct-pm-1", draft_page_url="url")
    text = adf.extract_text(body)
    assert "Section" in text and "Issue" in text and "Suggested change" in text


def test_review_request_includes_the_users_shoes_framing() -> None:
    text = adf.extract_text(build_review_request(pm_account_id="acct-pm-1", draft_page_url="url"))
    assert "users' shoes" in text


def test_review_request_states_the_done_only_pass_rule() -> None:
    """FR-07 / AD-15 — the comment must make the Done-only rule unambiguous."""
    text = adf.extract_text(build_review_request(pm_account_id="acct-pm-1", draft_page_url="url"))
    assert "Done" in text
    assert "only way to pass" in text
    assert "not processed" in text  # feedback after Done is ignored
    assert "status" in text  # don't ask the agent to change status


def test_change_summary_lists_changes_and_repeats_the_done_rule() -> None:
    body = build_change_summary(
        pm_account_id="acct-pm-1",
        summary="- Rewrote the intro\n- Added a setup example",
        draft_page_url="url",
    )
    text = adf.extract_text(body)
    assert "Rewrote the intro" in text
    assert "Added a setup example" in text
    assert "Done" in text
    assert "acct-pm-1" in _mentions(body)


def _mentions(node) -> list[str]:
    found: list[str] = []

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "mention":
                found.append(n.get("attrs", {}).get("id", ""))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for item in n:
                walk(item)

    walk(node)
    return found
