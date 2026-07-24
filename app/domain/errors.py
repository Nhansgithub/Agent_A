"""`AgentError` — the one error type adapters normalize failures into (AD-7, AD-19).

Every Atlassian failure that survives the adapters' retry-with-backoff (NFR-08) surfaces as this
single type, carrying enough context for the Error handler to write the EH-01 comment without
knowing anything about HTTP: a plain-language description, a suggested fix, and the ticket to post on.

Keeping this in `app/domain/` means the Error handler depends on a domain type, not on `httpx`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AgentError(Exception):
    """A normalized, human-explainable failure.

    Args:
        message: plain-language description of what went wrong. Goes straight into the EH-01
            comment, so it is written for a human reading a Jira ticket — not a stack trace.
        suggested_fix: the concrete action the admin should take. EH-01 requires this.
        operation: the domain verb that failed, e.g. ``jira.transition_issue``.
        retryable: whether a bare retry could plausibly succeed (a 5xx or timeout), as opposed to a
            failure that needs human intervention first (a 403, or a workflow with no legal path).
        status_code: the upstream HTTP status, when there was one.
        context: extra key/values worth surfacing (issue key, page id, folder id).
    """

    message: str
    suggested_fix: str
    operation: str
    retryable: bool = False
    status_code: int | None = None
    context: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code is not None:
            parts.append(f"(HTTP {self.status_code})")
        if self.context:
            details = ", ".join(f"{k}={v}" for k, v in sorted(self.context.items()))
            parts.append(f"[{details}]")
        return " ".join(parts)


class ConfigurationError(AgentError):
    """A failure caused by config, not by the upstream service.

    Distinguished because the suggested fix is always "edit the config registry", and because
    retrying is pointless — a missing folder id will still be missing next time.
    """
