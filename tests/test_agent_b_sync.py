"""S-B4 — incremental sync + deletion reconcile + pull ledger + git commit (offline, fakes only)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_b.config import AgentBConfig, load_agent_b_config
from agent_b.pipeline import GitVault, run_pull, sync_vault
from agent_b.repository import AgentBRepository
from app.adapters.markdown import storage_to_markdown
from app.domain.atlassian import ConfluencePage, ConfluencePageRef


class FakeConfluence:
    """A mutable stand-in for the injected adapter — the test edits the tree between pulls."""

    def __init__(self) -> None:
        self.pages: dict[str, ConfluencePage] = {}
        self.fail = False

    def set(self, page_id: str, title: str, body: str) -> None:
        self.pages[page_id] = ConfluencePage(id=page_id, title=title, body_storage=body)

    def remove(self, page_id: str) -> None:
        self.pages.pop(page_id, None)

    async def list_descendant_pages(
        self, folder_id: str, *, exclude_folder_ids: set[str] | None = None
    ) -> tuple[ConfluencePageRef, ...]:
        if self.fail:
            raise RuntimeError("confluence unreachable")
        return tuple(
            ConfluencePageRef(id=p.id, title=p.title, parent_id="F", parent_type="folder")
            for p in self.pages.values()
        )

    async def get_page(self, page_id: str, *, with_body: bool = True) -> ConfluencePage:
        return self.pages[page_id]

    @staticmethod
    def storage_to_markdown(storage_html: str) -> str:
        return storage_to_markdown(storage_html)


class FakeVcs:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def commit(self, message: str) -> str | None:
        self.messages.append(message)
        return f"sha{len(self.messages)}"


def _config(vault_dir: Path) -> AgentBConfig:
    cfg = load_agent_b_config(
        {
            "agent_b": {
                "space_key": "PM",
                "confluence_credentials_ref": "env:ALPHA_CONF",
                "include_folder_ids": ["F"],
                "folder_types": {"F": "prd"},
                "vault_dir": str(vault_dir),
            }
        }
    )
    assert cfg is not None
    return cfg


def _notes(vault_dir: Path) -> set[str]:
    return {p.name for p in (vault_dir / "notes").glob("*.md")}


async def _sync(fake: FakeConfluence, repo: AgentBRepository, config: AgentBConfig):
    return await sync_vault(fake, repo, config, base_url="https://x.atlassian.net")


async def test_first_pull_counts_everything_added(tmp_path: Path) -> None:
    fake = FakeConfluence()
    fake.set("P1", "Onboarding PRD", "<p>Welcome.</p>")
    fake.set("P2", "Billing PRD", "<p>Billing.</p>")
    repo = AgentBRepository.open(":memory:")
    config = _config(tmp_path)

    stats = await _sync(fake, repo, config)

    assert (stats.added, stats.changed, stats.unchanged, stats.deleted) == (2, 0, 0, 0)
    assert _notes(tmp_path) == {"P1-onboarding-prd.md", "P2-billing-prd.md"}
    repo.close()


async def test_second_pull_unchanged_is_byte_identical(tmp_path: Path) -> None:
    fake = FakeConfluence()
    fake.set("P1", "Onboarding PRD", "<p>Welcome.</p>")
    repo = AgentBRepository.open(":memory:")
    config = _config(tmp_path)

    await _sync(fake, repo, config)
    before = {p.name: p.read_bytes() for p in (tmp_path / "notes").glob("*.md")}
    stats = await _sync(fake, repo, config)
    after = {p.name: p.read_bytes() for p in (tmp_path / "notes").glob("*.md")}

    assert (stats.added, stats.changed, stats.unchanged, stats.deleted) == (0, 0, 1, 0)
    assert before == after
    repo.close()


async def test_second_pull_reports_add_change_delete_rename(tmp_path: Path) -> None:
    fake = FakeConfluence()
    fake.set("P1", "Onboarding PRD", "<p>Welcome.</p>")  # will change body
    fake.set("P2", "Billing PRD", "<p>Billing.</p>")  # will be retitled (rename)
    fake.set("P3", "Legacy PRD", "<p>Old.</p>")  # will be deleted
    repo = AgentBRepository.open(":memory:")
    config = _config(tmp_path)
    await _sync(fake, repo, config)

    fake.set("P1", "Onboarding PRD", "<p>Welcome aboard, everyone.</p>")  # change
    fake.set("P2", "Payments PRD", "<p>Billing.</p>")  # rename (new slug/path)
    fake.remove("P3")  # delete
    fake.set("P4", "Search PRD", "<p>Find things.</p>")  # add

    stats = await _sync(fake, repo, config)

    # P4 added; P1 (body) and P2 (title → its frontmatter/hash) both changed; P3 deleted.
    assert (stats.added, stats.changed, stats.unchanged, stats.deleted) == (1, 2, 0, 1)
    # rename dropped the stale slug and wrote the new one; delete removed the note; add wrote a note.
    assert _notes(tmp_path) == {
        "P1-onboarding-prd.md",
        "P2-payments-prd.md",
        "P4-search-prd.md",
    }
    assert "P2-billing-prd.md" not in _notes(tmp_path)
    assert "P3-legacy-prd.md" not in _notes(tmp_path)
    repo.close()


async def test_deletion_tombstones_row_and_cleans_links(tmp_path: Path) -> None:
    fake = FakeConfluence()
    # P1 links to P2 by title, so a restored (deterministic) edge exists P1 -> P2.
    fake.set("P1", "Onboarding PRD", "<p>See [Billing PRD](Billing PRD).</p>")
    fake.set("P2", "Billing PRD", "<p>Billing.</p>")
    repo = AgentBRepository.open(":memory:")
    config = _config(tmp_path)

    from agent_b.pipeline import link_vault

    await _sync(fake, repo, config)
    link_vault(repo, str(tmp_path))
    assert any(e["to_page_id"] == "P2" for e in repo.all_links())

    fake.remove("P2")
    await _sync(fake, repo, config)

    doc = repo.get_document("P2")
    assert doc is not None and doc["deleted_at"] is not None  # tombstoned, not purged
    assert repo.document_ids() == ["P1"]  # dropped from the live index
    assert "P2" not in repo.document_ids(include_deleted=False)
    assert all(e["to_page_id"] != "P2" and e["from_page_id"] != "P2" for e in repo.all_links())
    repo.close()


async def test_readd_untombstones(tmp_path: Path) -> None:
    fake = FakeConfluence()
    fake.set("P1", "Onboarding PRD", "<p>Welcome.</p>")
    repo = AgentBRepository.open(":memory:")
    config = _config(tmp_path)

    await _sync(fake, repo, config)
    fake.remove("P1")
    await _sync(fake, repo, config)
    fake.set("P1", "Onboarding PRD", "<p>Welcome.</p>")
    stats = await _sync(fake, repo, config)

    assert stats.added == 1  # a resurrected tombstone counts as an add
    doc = repo.get_document("P1")
    assert doc is not None and doc["deleted_at"] is None
    assert (tmp_path / "notes" / "P1-onboarding-prd.md").exists()
    repo.close()


async def test_run_pull_ledgers_the_run_and_commits(tmp_path: Path) -> None:
    fake = FakeConfluence()
    fake.set("P1", "Onboarding PRD", "<p>Welcome.</p>")
    repo = AgentBRepository.open(":memory:")
    config = _config(tmp_path)
    vcs = FakeVcs()

    result = await run_pull(fake, repo, config, base_url="https://x.atlassian.net", vcs=vcs)

    row = repo.get_pull_run(result.run_id)
    assert row is not None
    assert row["status"] == "ok" and row["finished_at"] is not None
    assert (row["added"], row["changed"], row["deleted"]) == (1, 0, 0)
    assert result.committed == "sha1" and len(vcs.messages) == 1

    # An unchanged re-pull writes a ledger row but makes no empty commit.
    result2 = await run_pull(fake, repo, config, base_url="https://x.atlassian.net", vcs=vcs)
    assert result2.committed is None and len(vcs.messages) == 1
    assert len(repo.all_pull_runs()) == 2
    repo.close()


async def test_run_pull_marks_error_and_reraises(tmp_path: Path) -> None:
    fake = FakeConfluence()
    fake.fail = True
    repo = AgentBRepository.open(":memory:")
    config = _config(tmp_path)

    with pytest.raises(RuntimeError):
        await run_pull(fake, repo, config, base_url="https://x.atlassian.net")

    runs = repo.all_pull_runs()
    assert len(runs) == 1 and runs[0]["status"] == "error"
    repo.close()


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


@pytest.mark.skipif(not _git_available(), reason="git not installed")
def test_git_vault_commits_only_on_change(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("hello", encoding="utf-8")
    git = GitVault(str(vault))

    first = git.commit("pull 1")
    assert first  # a real sha
    assert git.commit("pull 2 (no change)") is None  # nothing staged → no empty commit

    (vault / "note.md").write_text("hello again", encoding="utf-8")
    second = git.commit("pull 3")
    assert second and second != first
