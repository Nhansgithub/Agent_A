"""S-B1 — ConfluenceAdapter.list_descendant_pages: folder-tree crawl, pagination, exclude (AD-14)."""

from __future__ import annotations

from tests.test_confluence_adapter import build
from tests.test_jira_adapter import json_response


async def test_lists_pages_in_a_folder() -> None:
    adapter, transport = build(
        json_response(
            200,
            {
                "results": [
                    {"id": "P1", "type": "page", "title": "A"},
                    {"id": "P2", "type": "page", "title": "B"},
                ]
            },
        ),
        json_response(200, {"results": []}),  # P2 children
        json_response(200, {"results": []}),  # P1 children
    )

    refs = await adapter.list_descendant_pages("F1")

    assert [r.id for r in refs] == ["P1", "P2"]
    assert all(r.parent_id == "F1" and r.parent_type == "folder" for r in refs)
    assert len(transport.requests) == 3


async def test_paginates_and_skips_excluded_subfolders() -> None:
    adapter, transport = build(
        json_response(
            200,
            {
                "results": [{"id": "P1", "type": "page", "title": "A"}],
                "_links": {"next": "/wiki/api/v2/folders/F1/direct-children?cursor=x"},
            },
        ),
        json_response(
            200,
            {
                "results": [
                    {"id": "F2", "type": "folder", "title": "sub"},
                    {"id": "P2", "type": "page", "title": "B"},
                ]
            },
        ),
        json_response(200, {"results": []}),  # P2 children
        json_response(200, {"results": []}),  # P1 children
    )

    refs = await adapter.list_descendant_pages("F1", exclude_folder_ids={"F2"})

    assert [r.id for r in refs] == ["P1", "P2"]
    # F2 excluded → never fetched: 2 folder pages + 2 page-children = 4 requests, none to F2.
    assert len(transport.requests) == 4
    assert not any("/folders/F2/" in str(req.url) for req in transport.requests)
