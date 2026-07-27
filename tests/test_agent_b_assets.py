"""S-B10 — image assets: adapter verbs, binary fetch (idempotent, image-only), ref rewrite, sync wiring."""

from __future__ import annotations

from pathlib import Path

from agent_b.config import load_agent_b_config
from agent_b.pipeline import fetch_page_assets, remove_page_assets, render_note, sync_vault
from agent_b.repository import AgentBRepository
from app.adapters.confluence import ConfluenceAdapter
from app.domain.atlassian import ConfluencePage, ConfluencePageRef

# --- the shared-transport + adapter verbs (over a fake client) --------------------------------


class FakeClient:
    """A stand-in AtlassianClient: JSON `request` for the list, `download` for the binary."""

    def __init__(self, listing: dict, binary: bytes = b"PNGDATA") -> None:
        self._listing = listing
        self._binary = binary
        self.downloaded: list[str] = []

    async def request(self, method, path, *, operation, params=None, context=None, **kw):  # noqa: ANN001
        return self._listing

    async def download(self, path, *, operation, params=None, context=None):  # noqa: ANN001
        self.downloaded.append(path)
        return self._binary


def _listing() -> dict:
    return {
        "results": [
            {
                "id": "att1",
                "title": "diagram.png",
                "mediaType": "image/png",
                "_links": {"download": "/download/attachments/1/diagram.png"},
            },
            {
                "id": "att2",
                "title": "spec.pdf",
                "mediaType": "application/pdf",
                "_links": {"download": "/download/attachments/1/spec.pdf"},
            },
        ],
        "_links": {},
    }


async def test_list_attachments_parses_and_flags_images() -> None:
    adapter = ConfluenceAdapter(FakeClient(_listing()))
    attachments = await adapter.list_attachments("P1")
    assert [a.filename for a in attachments] == ["diagram.png", "spec.pdf"]
    assert [a.is_image for a in attachments] == [True, False]


async def test_download_attachment_prefixes_wiki() -> None:
    client = FakeClient(_listing())
    adapter = ConfluenceAdapter(client)
    data = await adapter.download_attachment("/download/attachments/1/diagram.png")
    assert data == b"PNGDATA"
    assert client.downloaded == ["/wiki/download/attachments/1/diagram.png"]  # /wiki prepended once
    await adapter.download_attachment("/wiki/already/rooted.png")
    assert client.downloaded[-1] == "/wiki/already/rooted.png"  # not double-prefixed


# --- fetch_page_assets: writes images only, idempotent -----------------------------------------


class FakeConfluenceAssets:
    def __init__(self, binary: bytes = b"PNGDATA") -> None:
        self._adapter = ConfluenceAdapter(FakeClient(_listing(), binary))
        self.list_calls = 0

    async def list_attachments(self, page_id):  # noqa: ANN001
        self.list_calls += 1
        return await self._adapter.list_attachments(page_id)

    async def download_attachment(self, download_path):  # noqa: ANN001
        return await self._adapter.download_attachment(download_path)


async def test_fetch_writes_only_images_and_is_idempotent(tmp_path: Path) -> None:
    confluence = FakeConfluenceAssets()

    written = await fetch_page_assets(confluence, str(tmp_path), "P1")

    asset = tmp_path / "assets" / "P1" / "diagram.png"
    assert written == 1  # only the image, not the PDF
    assert asset.read_bytes() == b"PNGDATA"
    assert not (tmp_path / "assets" / "P1" / "spec.pdf").exists()

    # A second pull with unchanged bytes writes nothing (idempotent, no churn).
    assert await fetch_page_assets(confluence, str(tmp_path), "P1") == 0


async def test_fetch_rewrites_on_changed_bytes(tmp_path: Path) -> None:
    await fetch_page_assets(FakeConfluenceAssets(b"v1"), str(tmp_path), "P1")
    written = await fetch_page_assets(FakeConfluenceAssets(b"v2"), str(tmp_path), "P1")
    assert written == 1  # the binary changed → rewritten
    assert (tmp_path / "assets" / "P1" / "diagram.png").read_bytes() == b"v2"


def test_remove_page_assets(tmp_path: Path) -> None:
    d = tmp_path / "assets" / "P1"
    d.mkdir(parents=True)
    (d / "x.png").write_bytes(b"x")
    remove_page_assets(str(tmp_path), "P1")
    assert not d.exists()
    remove_page_assets(str(tmp_path), "P1")  # a second call is a no-op, not an error


# --- the deterministic ref rewrite in render_note ---------------------------------------------


def test_render_localizes_attachment_image_refs_but_not_urls() -> None:
    note = render_note(
        page_id="P1",
        title="Guide",
        doc_type="userdoc",
        parent_id="F",
        space_key="PM",
        source_url="https://x/wiki/pages/P1",
        markdown="![arch](diagram.png)\n\n![logo](https://cdn/logo.svg)",
    )
    assert "![arch](../assets/P1/diagram.png)" in note.content  # bare filename → local asset path
    assert "![logo](https://cdn/logo.svg)" in note.content  # external URL untouched


# --- sync wiring: fetch for changed pages only; tombstone removes assets ------------------------


class FakeConfluenceTree:
    def __init__(self) -> None:
        self.pages: dict[str, ConfluencePage] = {}

    def set(self, page_id: str, title: str, body: str) -> None:
        self.pages[page_id] = ConfluencePage(id=page_id, title=title, body_storage=body)

    async def list_descendant_pages(self, folder_id, *, exclude_folder_ids=None):  # noqa: ANN001
        return tuple(
            ConfluencePageRef(id=p.id, title=p.title, parent_id="F", parent_type="folder")
            for p in self.pages.values()
        )

    async def get_page(self, page_id, *, with_body=True):  # noqa: ANN001
        return self.pages[page_id]

    @staticmethod
    def storage_to_markdown(storage):  # noqa: ANN001
        from app.adapters.markdown import storage_to_markdown

        return storage_to_markdown(storage)


def _config(vault_dir: Path):
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


async def test_sync_fetches_assets_for_changed_pages_and_removes_on_delete(tmp_path: Path) -> None:
    fake = FakeConfluenceTree()
    fake.set("P1", "One", "<p>a</p>")
    fake.set("P2", "Two", "<p>b</p>")
    repo = AgentBRepository.open(":memory:")
    config = _config(tmp_path)
    fetched: list[str] = []

    async def fetch_assets(page_id: str) -> int:
        fetched.append(page_id)
        return 0

    # Run 1: both pages new → assets fetched for both.
    await sync_vault(fake, repo, config, base_url="https://x", fetch_assets=fetch_assets)
    assert sorted(fetched) == ["P1", "P2"]

    # Run 2: change P1 only; P2 unchanged; assets fetched for P1 alone.
    fetched.clear()
    fake.set("P1", "One", "<p>a changed</p>")
    await sync_vault(fake, repo, config, base_url="https://x", fetch_assets=fetch_assets)
    assert fetched == ["P1"]

    # Run 3: delete P2 → its asset dir (pre-created) is removed on tombstone.
    (tmp_path / "assets" / "P2").mkdir(parents=True)
    (tmp_path / "assets" / "P2" / "img.png").write_bytes(b"x")
    fake.pages.pop("P2")
    await sync_vault(fake, repo, config, base_url="https://x", fetch_assets=fetch_assets)
    assert not (tmp_path / "assets" / "P2").exists()
    repo.close()
