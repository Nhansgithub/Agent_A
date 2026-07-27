"""S-B3 — LLM curation: tags, quarantined suggested links, MOC notes, cached (AD-20/AD-30), offline."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent_b.agents.librarian import LibrarianAgent, parse_curation
from agent_b.config import AgentBConfig, load_agent_b_config
from agent_b.pipeline import curate_vault, link_vault, render_note
from agent_b.repository import AgentBRepository
from app.agents.llm import CallMetadata

CANNED = json.dumps(
    {
        "tags": {"P1": ["onboarding"], "P2": ["billing"]},
        "mocs": [{"title": "Onboarding", "page_ids": ["P1", "P3"]}],
        "suggested_links": [{"from": "P1", "to": "P3", "reason": "both cover onboarding"}],
    }
)


class FakeLlm:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    async def complete(self, *, model, system, prompt, metadata):  # noqa: ANN001, ARG002
        self.calls += 1
        return SimpleNamespace(text=self._text, model=model, input_tokens=1, output_tokens=1)


def _meta() -> CallMetadata:
    return CallMetadata(correlation_id="c1", prd_id="kb", agent_role="librarian")


def _config(vault_dir: Path) -> AgentBConfig:
    cfg = load_agent_b_config(
        {
            "agent_b": {
                "space_key": "PM",
                "confluence_credentials_ref": "env:ALPHA_CONF",
                "include_folder_ids": ["F"],
                "vault_dir": str(vault_dir),
            }
        }
    )
    assert cfg is not None
    return cfg


def _seed(repo: AgentBRepository, *, page_id: str, title: str, body: str) -> None:
    note = render_note(
        page_id=page_id,
        title=title,
        doc_type="prd",
        parent_id="F",
        space_key="PM",
        source_url=f"https://x/wiki/pages/{page_id}",
        markdown=body,
    )
    repo.upsert_document(
        page_id=page_id,
        space_key="PM",
        title=title,
        doc_type="prd",
        vault_path=note.vault_path,
        content_hash=note.content_hash,
        base_content=note.content,
        parent_id="F",
    )


def _corpus(repo: AgentBRepository) -> None:
    _seed(repo, page_id="P1", title="Onboarding PRD", body="Welcome aboard.")
    _seed(repo, page_id="P2", title="Billing PRD", body="How billing works.")
    _seed(repo, page_id="P3", title="How to onboard", body="Step one.")


def test_parse_curation_drops_unknown_and_self_ids() -> None:
    text = json.dumps(
        {
            "tags": {"P1": ["a"], "GHOST": ["x"]},
            "suggested_links": [
                {"from": "P1", "to": "P2"},
                {"from": "P1", "to": "P1"},  # self → dropped
                {"from": "P1", "to": "GHOST"},  # unknown → dropped
            ],
        }
    )
    curation = parse_curation(text, valid_ids={"P1", "P2"})
    assert curation.tags == {"P1": ("a",)}
    assert curation.suggested == (("P1", "P2"),)


async def test_curation_applies_tags_moc_and_quarantines_suggestions(tmp_path: Path) -> None:
    repo = AgentBRepository.open(":memory:")
    _corpus(repo)
    llm = FakeLlm(CANNED)
    librarian = LibrarianAgent(llm, model="test-model")

    stats = await curate_vault(librarian, repo, _config(tmp_path), metadata=_meta())
    link_vault(repo, str(tmp_path))  # materialize the overlay into the notes

    assert stats.tagged == 2 and stats.suggested == 1 and stats.mocs == 1
    assert stats.from_cache is False and llm.calls == 1

    # Suggested edge is recorded as an LLM edge, not a deterministic one.
    assert ("P1", "P3", "suggested") in {
        (str(x["from_page_id"]), str(x["to_page_id"]), str(x["kind"])) for x in repo.all_links()
    }

    note = (tmp_path / "notes" / "P1-onboarding-prd.md").read_text(encoding="utf-8")
    body, _, related = note.partition("<!-- agent-b:related:start -->")
    assert 'tags: ["onboarding"]' in body  # tag went to frontmatter
    assert "[[P3-how-to-onboard|How to onboard]]" in related  # suggestion quarantined in Related
    assert "[[P3-how-to-onboard" not in body  # ...and NEVER inlined into the prose (AD-30)

    moc = (tmp_path / "notes" / "moc-onboarding.md").read_text(encoding="utf-8")
    assert "[[P1-onboarding-prd|Onboarding PRD]]" in moc
    assert "[[P3-how-to-onboard|How to onboard]]" in moc
    repo.close()


async def test_curation_is_cached_and_not_re_billed(tmp_path: Path) -> None:
    repo = AgentBRepository.open(":memory:")
    _corpus(repo)
    llm = FakeLlm(CANNED)
    librarian = LibrarianAgent(llm, model="test-model")
    config = _config(tmp_path)

    first = await curate_vault(librarian, repo, config, metadata=_meta())
    second = await curate_vault(librarian, repo, config, metadata=_meta())

    assert first.from_cache is False
    assert second.from_cache is True
    assert llm.calls == 1  # the unchanged corpus was not re-sent to the model
    repo.close()
