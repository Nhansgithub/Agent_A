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
