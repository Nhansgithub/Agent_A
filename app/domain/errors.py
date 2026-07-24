"""`AgentError` — the one error type adapters normalize failures into (AD-7, AD-19).

Every Atlassian failure that survives the adapters' retry-with-backoff (NFR-08) surfaces as this
single type, carrying enough context for the Error handler to write the EH-01 comment without
knowing anything about HTTP: a plain-language description, a suggested fix, and the failing operation.

Keeping this in `app/domain/` means the Error handler depends on a domain type, not on `httpx`.

**Deliberately a plain class, not a dataclass.** `@dataclass(slots=True)` replaces the class object,
which breaks `super()` inside the generated methods when an exception instance is copied or re-raised
by a framework — LangGraph does exactly that when a node fails. An exception must survive being
raised through arbitrary machinery, so the boring implementation is the correct one here.
"""

from __future__ import annotations


class AgentError(Exception):
    """A normalized, human-explainable failure."""

    __slots__ = ("context", "message", "operation", "retryable", "status_code", "suggested_fix")

    def __init__(
        self,
        message: str,
        suggested_fix: str,
        operation: str,
        retryable: bool = False,
        status_code: int | None = None,
        context: dict[str, str] | None = None,
    ) -> None:
        """
        Args:
            message: plain-language description of what went wrong. Goes straight into the EH-01
                comment, so it is written for a human reading a Jira ticket — not a stack trace.
            suggested_fix: the concrete action the admin should take. EH-01 requires this.
            operation: the domain verb that failed, e.g. ``jira.transition_issue``.
            retryable: whether a bare retry could plausibly succeed (a 5xx or timeout), as opposed
                to a failure needing human intervention first (a 403, or a workflow with no legal
                path to Done).
            status_code: the upstream HTTP status, when there was one.
            context: extra key/values worth surfacing (issue key, page id, folder id).
        """
        super().__init__(message)
        self.message = message
        self.suggested_fix = suggested_fix
        self.operation = operation
        self.retryable = retryable
        self.status_code = status_code
        self.context = dict(context or {})

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code is not None:
            parts.append(f"(HTTP {self.status_code})")
        if self.context:
            details = ", ".join(f"{k}={v}" for k, v in sorted(self.context.items()))
            parts.append(f"[{details}]")
        return " ".join(parts)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(operation={self.operation!r}, message={self.message!r}, "
            f"retryable={self.retryable}, status_code={self.status_code})"
        )

    def __reduce__(self):
        """Keep the error intact across copy/pickle — frameworks re-raise exceptions freely."""
        return (
            type(self),
            (
                self.message,
                self.suggested_fix,
                self.operation,
                self.retryable,
                self.status_code,
                self.context,
            ),
        )


class ConfigurationError(AgentError):
    """A failure caused by config, not by the upstream service.

    Distinguished because the suggested fix is always "edit the config registry", and because
    retrying is pointless — a missing folder id will still be missing next time.
    """
