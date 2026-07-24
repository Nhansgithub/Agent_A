"""Confluence storage format → Markdown (AD-7, FR-15 step 3, PRD §13 Q5).

Confluence "storage format" is XHTML carrying Atlassian-specific namespaced tags — `ac:` for macros
and `ri:` for resource identifiers. Handing that straight to markdownify produces output littered
with macro noise, because markdownify has never heard of `<ac:structured-macro>`.

**Approach: normalize first, then convert.** A BeautifulSoup pass rewrites the Atlassian tags into
plain HTML equivalents (`<ac:structured-macro ac:name="code">` → `<pre><code>`), and markdownify then
handles ordinary HTML. Doing it this way rather than subclassing `MarkdownConverter` and overriding
`convert_ac:structured-macro` keeps us off markdownify's internal method-dispatch naming, which has
changed shape between releases — a normalization pass is stable across upgrades.

Minor formatting loss is explicitly acceptable for the demo (PRD §13 Q5). What must survive is the
*content*: headings, prose, lists, tables, code, and links.
"""

from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup, NavigableString
from markdownify import MarkdownConverter

#: Macros that carry an admonition. Rendered as blockquotes, which survive Markdown round-tripping.
_PANEL_MACROS = {"info", "note", "warning", "tip", "panel", "success", "error"}

#: Macros whose body is decorative or navigational — drop them rather than emit noise.
_DROPPED_MACROS = {"toc", "children", "pagetree", "anchor", "excerpt-include", "recently-updated"}

_AC_NAME = "ac:name"


class _ConfluenceConverter(MarkdownConverter):
    """markdownify with settings suited to a user-facing help document."""

    class Options(MarkdownConverter.DefaultOptions):
        heading_style = "ATX"  # `## Heading`, not underlines — friendlier for an SSG
        bullets = "-"
        code_language = ""
        escape_underscores = False
        escape_asterisks = False


def _macro_name(tag) -> str:
    return (tag.get(_AC_NAME) or tag.get("name") or "").strip().lower()


def _text_of(tag) -> str:
    return tag.get_text() if tag is not None else ""


def normalize_storage(storage_html: str) -> str:
    """Rewrite Atlassian `ac:` / `ri:` tags into plain HTML.

    Exposed separately from :func:`storage_to_markdown` so the transformation is testable on its own
    — the failure mode here is silent content loss, which is much easier to catch at this seam.
    """
    soup = BeautifulSoup(storage_html or "", "html.parser")

    for macro in soup.find_all(["ac:structured-macro", "ac:macro"]):
        name = _macro_name(macro)

        if name in _DROPPED_MACROS:
            macro.decompose()
            continue

        if name == "code":
            body = macro.find(["ac:plain-text-body", "ac:plaintextbody"])
            language = ""
            for parameter in macro.find_all("ac:parameter"):
                if (parameter.get(_AC_NAME) or "").strip().lower() == "language":
                    language = parameter.get_text().strip()
            pre = soup.new_tag("pre")
            code = soup.new_tag("code")
            if language:
                code["class"] = [f"language-{language}"]
            code.string = _text_of(body)
            pre.append(code)
            macro.replace_with(pre)
            continue

        rich_body = macro.find(["ac:rich-text-body", "ac:richtextbody"])
        if name in _PANEL_MACROS:
            quote = soup.new_tag("blockquote")
            for child in list(rich_body.children) if rich_body else []:
                quote.append(child.extract())
            macro.replace_with(quote)
            continue

        # Unknown macro: keep whatever prose it wrapped, discard the wrapper. Losing a macro's
        # rendering is acceptable (§13 Q5); losing the words inside it is not.
        if rich_body is not None:
            macro.replace_with(rich_body)
            rich_body.unwrap()
        else:
            macro.decompose()

    # <ac:link><ri:page ri:content-title="Target"/><ac:plain-text-link-body>Text</...></ac:link>
    for link in soup.find_all("ac:link"):
        target = link.find(["ri:page", "ri:attachment", "ri:user"])
        label_tag = link.find(["ac:link-body", "ac:plain-text-link-body"])
        title = ""
        if target is not None:
            title = (
                target.get("ri:content-title")
                or target.get("ri:filename")
                or target.get("ri:account-id")
                or ""
            )
        label = _text_of(label_tag) or title
        if not label:
            link.decompose()
            continue
        anchor = soup.new_tag("a", href=title or "#")
        anchor.string = label
        link.replace_with(anchor)

    # <ac:image><ri:attachment ri:filename="diagram.png"/></ac:image>
    for image in soup.find_all("ac:image"):
        resource = image.find(["ri:attachment", "ri:url"])
        source = ""
        if resource is not None:
            source = resource.get("ri:filename") or resource.get("ri:value") or ""
        replacement = soup.new_tag("img", src=source)
        replacement["alt"] = image.get("ac:alt") or source
        image.replace_with(replacement)

    # Confluence task lists → GitHub-style checkboxes.
    for task_list in soup.find_all("ac:task-list"):
        unordered = soup.new_tag("ul")
        for task in task_list.find_all("ac:task"):
            status = _text_of(task.find("ac:task-status")).strip().lower()
            body = task.find("ac:task-body")
            item = soup.new_tag("li")
            item.append(NavigableString(f"[{'x' if status == 'complete' else ' '}] "))
            for child in list(body.children) if body else []:
                item.append(child.extract())
            unordered.append(item)
        task_list.replace_with(unordered)

    # Layout containers carry no meaning in Markdown — unwrap, never discard.
    for container in soup.find_all(
        ["ac:layout", "ac:layout-section", "ac:layout-cell", "ac:rich-text-body"]
    ):
        container.unwrap()

    # Anything `ac:`/`ri:` still standing is unrecognised; unwrap so its text survives.
    for leftover in soup.find_all(lambda tag: tag.name.startswith(("ac:", "ri:"))):
        leftover.unwrap()

    return str(soup)


def storage_to_markdown(storage_html: str) -> str:
    """Convert a Confluence page body to Markdown for the FR-15 `.md` export."""
    normalized = normalize_storage(storage_html)
    markdown = _ConfluenceConverter().convert(normalized)
    return _tidy(markdown)


def _tidy(markdown: str) -> str:
    """Collapse the runs of blank lines the conversion leaves behind."""
    lines = [line.rstrip() for line in markdown.replace("\r\n", "\n").split("\n")]
    tidied: list[str] = []
    for line in lines:
        if not line and tidied and not tidied[-1]:
            continue
        tidied.append(line)
    return "\n".join(tidied).strip() + "\n"


# ---------------------------------------------------------------------------------------------
# Markdown -> Confluence storage format (FR-06 — publishing the Author's draft).
#
# Confluence Cloud has no "markdown" body representation, so a draft written in Markdown must be
# converted to storage-format XHTML to become a page. This handles exactly the subset the Author's
# SKILL.md constrains its output to (headings, paragraphs, bullet/ordered lists, bold/italic/inline
# code, fenced code blocks, links). Minor formatting loss on anything outside that subset is
# acceptable for the demo (PRD §13 Q5).
# ---------------------------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_ITEM = re.compile(r"^[-*]\s+(.*)$")
_OL_ITEM = re.compile(r"^\d+[.)]\s+(.*)$")
_FENCE = re.compile(r"^```(\w*)\s*$")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

#: A line consisting solely of HTML tags and whitespace — e.g. `<table> <tr> <td width="50%">`.
#: The Author's SKILL.md forbids raw HTML, but if one slips through, escaping it would render the
#: literal markup as visible page text. Such a line carries no prose, so dropping it is lossless and
#: degrades to plain single-column Markdown instead of a page full of `&lt;td&gt;`. Lines that mix
#: tags with real words are still escaped, so the words survive and the slip stays visible.
_TAG_ONLY_LINE = re.compile(r"^(?:\s*</?[a-zA-Z][^>]*/?>\s*)+$")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*]+)\*(?![*\w])")
_CODE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """Convert inline spans, escaping first so user text can never inject storage markup."""
    escaped = html.escape(text, quote=False)
    # Protect inline code from bold/italic processing by handling it first with a placeholder.
    codes: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        codes.append(f"<code>{match.group(1)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    escaped = _CODE.sub(_stash_code, escaped)
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITALIC.sub(r"<em>\1</em>", escaped)
    escaped = _LINK.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', escaped
    )
    for index, code_html in enumerate(codes):
        escaped = escaped.replace(f"\x00{index}\x00", code_html)
    return escaped


def markdown_to_storage(markdown: str) -> str:
    """Convert a Markdown draft to Confluence storage format (XHTML).

    Deliberately line-based and small: the Author produces a constrained Markdown subset, so a full
    CommonMark parser would be a dependency and a memory cost (AD-21) buying fidelity the demo does
    not need. Unrecognised lines degrade to paragraphs rather than being dropped.
    """
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    paragraph: list[str] = []
    list_stack: list[str] = []  # "ul" / "ol", innermost last

    def flush_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def close_lists() -> None:
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        fence = _FENCE.match(stripped)
        if fence:
            flush_paragraph()
            close_lists()
            language = fence.group(1)
            body: list[str] = []
            i += 1
            while i < len(lines) and not _FENCE.match(lines[i].strip()):
                body.append(lines[i])
                i += 1
            macro = '<ac:structured-macro ac:name="code">'
            if language:
                macro += f'<ac:parameter ac:name="language">{html.escape(language)}</ac:parameter>'
            macro += f"<ac:plain-text-body><![CDATA[{chr(10).join(body)}]]></ac:plain-text-body>"
            macro += "</ac:structured-macro>"
            out.append(macro)
            i += 1
            continue

        if not stripped:
            flush_paragraph()
            close_lists()
            i += 1
            continue

        if _TAG_ONLY_LINE.match(stripped):
            # Stray layout markup (see _TAG_ONLY_LINE). Treated as a block break so the prose either
            # side is not glued into one paragraph. Reached only after the fenced-code branch above,
            # so an HTML example inside ``` is never touched.
            flush_paragraph()
            i += 1
            continue

        heading = _HEADING.match(stripped)
        if heading:
            flush_paragraph()
            close_lists()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2).strip())}</h{level}>")
            i += 1
            continue

        ul = _UL_ITEM.match(stripped)
        ol = _OL_ITEM.match(stripped)
        if ul or ol:
            flush_paragraph()
            wanted = "ul" if ul else "ol"
            if not list_stack or list_stack[-1] != wanted:
                close_lists()
                out.append(f"<{wanted}>")
                list_stack.append(wanted)
            item = (ul or ol).group(1)
            out.append(f"<li>{_inline(item)}</li>")
            i += 1
            continue

        close_lists()
        paragraph.append(stripped)
        i += 1

    flush_paragraph()
    close_lists()
    return "".join(out)
