"""The shared Atlassian HTTP transport (AD-7, NFR-08).

Both adapters sit on this. It owns the details that would otherwise diverge across ~20 call sites:
API-token auth, retry-with-backoff, and normalizing every failure into the single `AgentError` the
Error handler consumes (AD-19).

**What is retried, and what is not.** Retrying a 403 three times just delays the escalation and burns
the admin's time; retrying a 429 or a 503 usually succeeds. So retries cover transient conditions only
— timeouts, connection errors, 429, and 5xx — and every other failure escalates immediately with a
suggested fix pointing at the actual cause.

Async throughout: the service is a FastAPI app, and a drafting run makes many sequential Atlassian
calls. Blocking the event loop for the duration would stall webhook acknowledgement, which Atlassian
treats as a delivery failure.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx

from app.config.secrets import AtlassianCredentials
from app.domain.errors import AgentError

#: Transient upstream conditions worth retrying (NFR-08).
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

#: `(status, operation)` → a fix that beats the generic per-status advice. Kept narrow: an entry
#: belongs here only when the generic message would actively mislead an admin.
_OPERATION_FIXES: dict[tuple[int, str], str] = {
    # Confluence Cloud **Free** does not include page restrictions. The API answers a permission
    # error, so the generic "grant the account access" advice sends an admin hunting for a permission
    # that is already granted — the account can hold `restrict_content` and `administer` on the space
    # and still get this 403. Name the real cause first.
    (403, "set_edit_restriction"): (
        "Confluence rejected the restriction. Check the site's plan **before** its permissions: page "
        "restrictions are not part of the Confluence Cloud Free edition, and on Free this fails with "
        "a permission error even for a space admin. Confirm with GET /wiki/rest/api/settings/"
        "systemInfo → `edition`. If it reads `free`, upgrade the site to Standard or above (FR-15 "
        "step 1 / AD-18 require the restriction). If it does not, grant the agent account "
        "'Add and delete restrictions' on the space, then reply to resume."
    ),
}


class AtlassianClient:
    """An authenticated, retrying HTTP client for one Atlassian product on one tenant."""

    __slots__ = (
        "_backoff",
        "_client",
        "_credentials",
        "_headers",
        "_max_attempts",
        "_product",
        "_sleep",
    )

    def __init__(
        self,
        credentials: AtlassianCredentials,
        *,
        product: str,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        client: httpx.AsyncClient | None = None,
        sleep: Any = None,
    ) -> None:
        """
        Args:
            credentials: resolved from an env reference (AD-4).
            product: "jira" or "confluence" — used in `AgentError.operation` so an escalation says
                which system failed.
            max_attempts: total attempts including the first (NFR-08, ~3).
            client: injected in tests so the whole suite runs with no network.
            sleep: injected in tests so retry backoff does not actually wait.
        """
        self._credentials = credentials
        self._product = product
        self._max_attempts = max(1, max_attempts)
        self._backoff = backoff_seconds
        self._sleep = sleep or asyncio.sleep
        token = base64.b64encode(f"{credentials.email}:{credentials.api_token}".encode()).decode()
        # Attached per-request rather than baked into the client, so an injected client (tests, or a
        # future shared connection pool) cannot silently lose authentication.
        self._headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._client = client or httpx.AsyncClient(
            base_url=credentials.base_url,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        json: Any = None,
        params: dict[str, Any] | None = None,
        context: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200, 201, 204),
    ) -> Any:
        """Perform one API call, retrying transient failures.

        Args:
            operation: the domain verb for error messages, e.g. ``transition_issue``.
            expected: status codes treated as success. Anything else raises `AgentError`.

        Returns:
            The decoded JSON body, or ``None`` for an empty (204) response.
        """
        last_error: AgentError | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.request(
                    method, path, json=json, params=params, headers=self._headers
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = AgentError(
                    message=f"Could not reach {self._product.title()} ({type(exc).__name__}).",
                    suggested_fix=(
                        f"Check the Droplet's outbound network access and that "
                        f"{self._credentials.base_url} is correct in the tenant's credentials."
                    ),
                    operation=f"{self._product}.{operation}",
                    retryable=True,
                    context=dict(context or {}),
                )
            else:
                if response.status_code in expected:
                    return self._decode(response)
                last_error = self._to_agent_error(response, operation, context)
                if response.status_code not in RETRYABLE_STATUS:
                    raise last_error

            if attempt < self._max_attempts:
                await self._sleep(self._backoff * (2 ** (attempt - 1)))

        assert last_error is not None
        raise last_error

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def _to_agent_error(
        self, response: httpx.Response, operation: str, context: dict[str, str] | None
    ) -> AgentError:
        """Turn an HTTP failure into something an admin can act on from a Jira comment (EH-01)."""
        status = response.status_code
        detail = self._detail(response)
        fixes = {
            401: (
                f"The {self._product} API token was rejected. Regenerate it at "
                "id.atlassian.com → Security → API tokens and update the tenant's env variables."
            ),
            403: (
                f"The agent account lacks permission for this {self._product} operation. Grant it "
                "access to the project/space in question, then reply to resume."
            ),
            404: (
                "The target does not exist. Check the ids in the tenant's config registry entry "
                "(project keys, folder ids) still match the real Atlassian instance."
            ),
            429: (
                f"{self._product.title()} is rate-limiting the agent. It will retry; if this "
                "persists, reduce concurrent activity on the site."
            ),
        }
        default_fix = (
            f"{self._product.title()} returned an unexpected {status}. Check the Atlassian status "
            "page and the service logs for the correlation id, then reply to resume."
        )
        return AgentError(
            message=f"{self._product.title()} rejected {operation}: {detail}",
            suggested_fix=_OPERATION_FIXES.get((status, operation))
            or fixes.get(status, default_fix),
            operation=f"{self._product}.{operation}",
            retryable=status in RETRYABLE_STATUS,
            status_code=status,
            context=dict(context or {}),
        )

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        """Pull the human-readable part out of an Atlassian error body."""
        try:
            body = response.json()
        except ValueError:
            return (response.text or "no response body")[:300]
        if isinstance(body, dict):
            for key in ("errorMessages", "errors", "message", "title"):
                value = body.get(key)
                if isinstance(value, list) and value:
                    return "; ".join(str(v) for v in value)[:300]
                if isinstance(value, dict) and value:
                    return "; ".join(f"{k}: {v}" for k, v in value.items())[:300]
                if isinstance(value, str) and value:
                    return value[:300]
        return str(body)[:300]

    async def aclose(self) -> None:
        await self._client.aclose()
