"""System constants — identical across every tenant, so they do not violate AD-4.

AD-4 forbids *project-specific* literals outside the config registry. A value that is the same for
every tenant is not a project literal; hard-coding it here is correct and keeps it from being
accidentally made configurable (which would let two tenants disagree about it).

Everything in this module must satisfy: "if a second project were onboarded tomorrow, this value
would be identical for it."
"""

from __future__ import annotations

import re
from typing import Final

# AD-10 — the reserved detection-exclusion label. The Publisher stamps it on every page it creates;
# detection refuses any page carrying it. Explicitly called out in the Spine as a fixed system
# constant that does NOT violate AD-4.
AGENT_GENERATED_LABEL: Final[str] = "agent-generated"

# AD-11 — the correlation marker. Every externally-visible artifact the flow creates (draft page,
# tracking / Review / Publishing ticket) is stamped with the run's `prd_id` under this key, so a
# resume can *adopt* an orphan created in a crash window instead of double-creating it.
PRD_CORRELATION_PROPERTY: Final[str] = "leapxpert-prd-id"

#: Prefix for the Jira label carrying the same marker. Jira labels cannot contain spaces, so the
#: `prd_id` (a Confluence page id — always numeric) is appended directly. Labels go in the
#: `createIssue` payload, which makes the Jira side of AD-11's marker atomic with the create.
PRD_LABEL_PREFIX: Final[str] = "prd-"


def prd_marker_label(prd_id: str) -> str:
    """The AD-11 correlation label stamped on every ticket a run creates.

    This is what lets a resume *adopt* a ticket that was created remotely a beat before its id was
    persisted — the crash window that would otherwise cause a double-create.
    """
    return f"{PRD_LABEL_PREFIX}{prd_id}"

# FR-02 — the title gate. The demo-agreed convention is the same for all tenants.
PRD_TITLE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^final_PRD_(?P<name>.+)$")

# EH-02 — the admin resume keywords. An admin comment on the error ticket containing either of these
# re-runs the failed stage from `last_good_checkpoint`.
RESUME_KEYWORDS: Final[tuple[str, ...]] = ("@agent resume", "fixed")

# §6.2 — the structured PM feedback format the Author requests and the Feedback interpreter parses.
FEEDBACK_BLOCK_FIELDS: Final[tuple[str, ...]] = ("Section", "Issue", "Suggested change")


def matches_prd_title(title: str) -> bool:
    """FR-02 title gate: is this page a candidate PRD?"""
    return PRD_TITLE_PATTERN.match(title.strip()) is not None


def prd_name_from_title(title: str) -> str | None:
    """Extract `<name>` from a `final_PRD_<name>` title, or None if it does not match."""
    match = PRD_TITLE_PATTERN.match(title.strip())
    return match.group("name").strip() if match else None
