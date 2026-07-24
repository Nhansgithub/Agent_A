"""Stories 2.1 / 2.2 / 2.7 — PRD detection, title gate, self-ingestion guard (FR-01, FR-02, AD-10)."""

from __future__ import annotations

import pytest

from app.agents.detection import DetectionAgent, DetectionVerdict
from app.config.constants import AGENT_GENERATED_LABEL
from app.domain.events import ConfluencePageEvent, EventType
from tests.conftest import tenant_entry

from app.config.schema import TenantConfig  # isort: skip

TENANT = TenantConfig.model_validate({**tenant_entry(), "project_id": "tenant_one"})
AGENT_ACCOUNT = "acct-agent-self"


class FakeConfluence:
    """Only the reads detection performs. No network."""

    def __init__(
        self,
        *,
        ancestors: tuple[str, ...] = (),
        labels: tuple[str, ...] = (),
        agent_account: str = AGENT_ACCOUNT,
    ) -> None:
        self._ancestors = ancestors
        self._labels = labels
        self._agent_account = agent_account
        self.current_user_calls = 0

    async def get_page_ancestors(self, _page_id: str) -> tuple[str, ...]:
        return self._ancestors

    async def get_labels(self, _page_id: str) -> tuple[str, ...]:
        return self._labels

    async def get_current_user(self) -> str:
        self.current_user_calls += 1
        return self._agent_account


def page_event(
    *,
    title: str = "final_PRD_Widget",
    container: str | None = "folder-source-1",
    creator: str | None = "acct-uploader-1",
    labels: tuple[str, ...] = (),
    version: int = 1,
) -> ConfluencePageEvent:
    return ConfluencePageEvent(
        event_type=EventType.CONFLUENCE_PAGE_CREATED,
        page_id="page-1",
        version_number=version,
        title=title,
        creator_account_id=creator,
        container_id=container,
        labels=labels,
    )


# ---------------------------------------------------------------------------------------------
# Story 2.1 — admit a candidate PRD in the watched source folder.
# ---------------------------------------------------------------------------------------------


async def test_a_titled_page_in_the_source_folder_is_admitted() -> None:
    agent = DetectionAgent(FakeConfluence())
    result = await agent.evaluate(page_event(), TENANT)
    assert result.verdict is DetectionVerdict.ADMIT
    assert result.admitted


async def test_a_page_in_another_folder_is_dropped() -> None:
    """Draft and published pages route to the tenant but are not in the watched set (FR-01, AD-10 a)."""
    agent = DetectionAgent(FakeConfluence())
    result = await agent.evaluate(page_event(container="folder-published-1"), TENANT)
    assert result.verdict is DetectionVerdict.NOT_IN_SOURCE_FOLDER
    assert not result.admitted


async def test_container_absent_falls_back_to_ancestors_lookup() -> None:
    """The page-created payload does not reliably carry the container (PRD §13 Q3/Q4, AD-14)."""
    agent = DetectionAgent(FakeConfluence(ancestors=("folder-source-1", "space-root")))
    result = await agent.evaluate(page_event(container=None), TENANT)
    assert result.verdict is DetectionVerdict.ADMIT


async def test_ancestors_lookup_that_excludes_source_is_dropped() -> None:
    agent = DetectionAgent(FakeConfluence(ancestors=("some-other-folder",)))
    result = await agent.evaluate(page_event(container=None), TENANT)
    assert result.verdict is DetectionVerdict.NOT_IN_SOURCE_FOLDER


# ---------------------------------------------------------------------------------------------
# Story 2.2 — title gate. A mismatch routes to rename, it is not dropped.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title", ["Widget Guide", "PRD_Widget", "draft_PRD_Widget", "final PRD Widget", "prd"]
)
async def test_a_mismatched_title_routes_to_rename_not_dropped(title: str) -> None:
    agent = DetectionAgent(FakeConfluence())
    result = await agent.evaluate(page_event(title=title), TENANT)
    assert result.verdict is DetectionVerdict.TITLE_MISMATCH
    assert result.needs_rename_request


async def test_title_gate_only_applies_inside_the_source_folder() -> None:
    """A mislabeled page *elsewhere* is just noise — it must not generate a rename request."""
    agent = DetectionAgent(FakeConfluence())
    result = await agent.evaluate(
        page_event(title="Some Notes", container="folder-published-1"), TENANT
    )
    assert result.verdict is DetectionVerdict.NOT_IN_SOURCE_FOLDER
    assert not result.needs_rename_request


# ---------------------------------------------------------------------------------------------
# Story 2.7 — self-ingestion defense-in-depth (AD-10 b, c).
# ---------------------------------------------------------------------------------------------


async def test_a_page_with_the_reserved_label_is_never_ingested() -> None:
    """Even if it somehow lands in the source folder with a matching title (AD-10 b)."""
    agent = DetectionAgent(FakeConfluence())
    result = await agent.evaluate(page_event(labels=(AGENT_GENERATED_LABEL,)), TENANT)
    assert result.verdict is DetectionVerdict.AGENT_GENERATED


async def test_the_label_is_read_when_absent_from_the_event() -> None:
    agent = DetectionAgent(FakeConfluence(labels=(AGENT_GENERATED_LABEL,)))
    result = await agent.evaluate(page_event(labels=()), TENANT)
    assert result.verdict is DetectionVerdict.AGENT_GENERATED


async def test_a_page_authored_by_the_agent_is_never_ingested() -> None:
    """AD-10 c — the agent's own account, resolved via the adapter."""
    agent = DetectionAgent(FakeConfluence(agent_account=AGENT_ACCOUNT))
    result = await agent.evaluate(page_event(creator=AGENT_ACCOUNT), TENANT)
    assert result.verdict is DetectionVerdict.AGENT_GENERATED


async def test_a_page_by_a_human_is_admitted() -> None:
    agent = DetectionAgent(FakeConfluence())
    result = await agent.evaluate(page_event(creator="acct-real-pm"), TENANT)
    assert result.verdict is DetectionVerdict.ADMIT


async def test_the_agent_account_is_resolved_once_and_cached() -> None:
    """AD-10 — this id has ONE source; two units must not resolve it independently."""
    confluence = FakeConfluence()
    agent = DetectionAgent(confluence)

    await agent.evaluate(page_event(creator="acct-a"), TENANT)
    await agent.evaluate(page_event(creator="acct-b"), TENANT)

    assert confluence.current_user_calls == 1, "the agent account must be resolved once per tenant"


async def test_the_guard_order_is_folder_then_self_then_title() -> None:
    """Folder is the primary guard; a page outside it should not even trigger a label read."""
    confluence = FakeConfluence()
    agent = DetectionAgent(confluence)

    result = await agent.evaluate(
        page_event(container="folder-published-1", labels=(AGENT_GENERATED_LABEL,)), TENANT
    )

    assert result.verdict is DetectionVerdict.NOT_IN_SOURCE_FOLDER
    assert confluence.current_user_calls == 0, "no self-check needed once the folder check fails"
