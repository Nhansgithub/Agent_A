"""Atlassian Document Format builders (AD-7, Spine → Consistency Conventions).

Jira REST **v3 rejects a plain string** as a comment or description body — it requires ADF, a nested
JSON document. Every comment this system posts (review requests, change summaries, clarifying
questions, error escalations) is built here, so no call site can accidentally send a string and
discover it as a 400 in production.

The one piece that genuinely matters for behaviour is :func:`mention`. FR-07, FR-08, FR-10, FR-11 and
EH-01 all require the agent to **tag** a specific human so they are notified. A literal ``"@Name"``
text node renders as plain text and notifies nobody — the run would then park forever waiting on a
person who was never told. Only a ``mention`` node with an ``accountId`` actually notifies.
"""

from __future__ import annotations

from typing import Any

ADFNode = dict[str, Any]


def text(value: str) -> ADFNode:
    """An inline text node."""
    return {"type": "text", "text": value}


def strong(value: str) -> ADFNode:
    """Bold inline text."""
    return {"type": "text", "text": value, "marks": [{"type": "strong"}]}


def code(value: str) -> ADFNode:
    """Inline monospace — used for the literal `@agent resume` instruction in EH-01."""
    return {"type": "text", "text": value, "marks": [{"type": "code"}]}


def link(value: str, url: str) -> ADFNode:
    return {"type": "text", "text": value, "marks": [{"type": "link", "attrs": {"href": url}}]}


def mention(account_id: str, display_name: str | None = None) -> ADFNode:
    """A real @mention that notifies the account.

    A plain-text "@Name" looks right and notifies nobody, which would silently strand a run at a
    human gate. Always use this for the PM, Head of Product, admin, and Uploading PM.
    """
    attrs: dict[str, Any] = {"id": account_id}
    if display_name:
        attrs["text"] = f"@{display_name}"
    return {"type": "mention", "attrs": attrs}


def paragraph(*content: ADFNode) -> ADFNode:
    return {"type": "paragraph", "content": list(content)}


def heading(value: str, level: int = 3) -> ADFNode:
    return {
        "type": "heading",
        "attrs": {"level": max(1, min(6, level))},
        "content": [text(value)],
    }


def code_block(value: str, language: str | None = None) -> ADFNode:
    """A fenced block — used to show the PM the exact §6.2 feedback format."""
    node: ADFNode = {"type": "codeBlock", "content": [text(value)]}
    if language:
        node["attrs"] = {"language": language}
    return node


def bullet_list(*items: str | ADFNode) -> ADFNode:
    return {
        "type": "bulletList",
        "content": [
            {
                "type": "listItem",
                "content": [paragraph(text(item)) if isinstance(item, str) else item],
            }
            for item in items
        ],
    }


def rule() -> ADFNode:
    return {"type": "rule"}


def doc(*blocks: ADFNode) -> ADFNode:
    """Wrap block nodes into a complete ADF document — what the Jira API expects as `body`."""
    return {"version": 1, "type": "doc", "content": list(blocks)}


def text_to_doc(value: str) -> ADFNode:
    """Convert plain text into an ADF document, one paragraph per non-empty line block.

    A convenience for simple bodies. Anything that needs to tag a human must be composed explicitly
    with :func:`mention` instead — see this module's docstring for why.
    """
    blocks = [
        paragraph(text(block.strip()))
        for block in value.replace("\r\n", "\n").split("\n\n")
        if block.strip()
    ]
    return doc(*(blocks or [paragraph(text(""))]))


def extract_text(node: Any) -> str:
    """Flatten an ADF document back to plain text.

    Needed because Jira returns comment bodies as ADF: the Feedback interpreter must read what the PM
    actually wrote, not a JSON structure.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(extract_text(child) for child in node)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return str(node.get("text", ""))
        if node.get("type") == "mention":
            return str(node.get("attrs", {}).get("text", ""))
        children = node.get("content")
        if isinstance(children, list):
            parts = [extract_text(child) for child in children]
            block_types = {"doc", "bulletList", "orderedList", "blockquote"}
            separator = "\n" if node.get("type") in block_types else ""
            joined = separator.join(p for p in parts if p)
            # Paragraphs and headings are block-level: keep them on their own lines.
            return joined + "\n" if node.get("type") in {"paragraph", "heading"} else joined
    return ""
