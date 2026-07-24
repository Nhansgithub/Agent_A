"""Env-reference secret resolution (AD-4, PRD §11, §13 Q6).

Config never contains a credential. It contains a *reference* of the form ``env:PREFIX``, and the real
values are read from the process environment at call time. This keeps the registry file safe to commit
and satisfies "credentials injected via environment references, never inline in config or code."

An ``env:ALPHA_JIRA`` reference resolves the triple::

    ALPHA_JIRA_BASE_URL   https://your-site.atlassian.net
    ALPHA_JIRA_EMAIL      service-account@example.com
    ALPHA_JIRA_API_TOKEN  <Atlassian API token>

Resolution is **lazy and explicit** — loading the registry must never require secrets, so the whole
unit suite runs with no credentials. A missing variable fails loudly at the point of use, naming the
exact variable to set, rather than surfacing later as a confusing 401.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_REF_SCHEME = "env:"


class SecretResolutionError(RuntimeError):
    """A credential reference could not be resolved from the environment."""


@dataclass(frozen=True, slots=True)
class AtlassianCredentials:
    """Basic-auth credentials for one Atlassian product on one tenant.

    Atlassian Cloud REST uses HTTP Basic with the account email as the username and an API token as
    the password — for both Jira v3 and Confluence v2/v1.
    """

    base_url: str
    email: str
    api_token: str

    def __repr__(self) -> str:  # pragma: no cover - defensive, keeps tokens out of tracebacks
        return (
            f"AtlassianCredentials(base_url={self.base_url!r}, email={self.email!r}, api_token=***)"
        )


def _env_prefix(ref: str) -> str:
    if not ref.startswith(ENV_REF_SCHEME):
        raise SecretResolutionError(
            f"Credential reference {ref!r} is not an environment reference. "
            f"It must look like {ENV_REF_SCHEME}MY_PREFIX — inline secrets are forbidden (AD-4)."
        )
    prefix = ref[len(ENV_REF_SCHEME) :].strip()
    if not prefix:
        raise SecretResolutionError(
            f"Credential reference {ref!r} has an empty environment prefix."
        )
    return prefix


def _require(var: str, env: dict[str, str]) -> str:
    value = env.get(var, "").strip()
    if not value:
        raise SecretResolutionError(
            f"Environment variable {var} is not set. It is required by a credential reference in "
            f"the config registry. Set it in the service's .env (never in the registry file)."
        )
    return value


def resolve_atlassian_credentials(
    ref: str, env: dict[str, str] | None = None
) -> AtlassianCredentials:
    """Resolve an ``env:PREFIX`` reference into usable Atlassian credentials.

    Args:
        ref: the reference string from the tenant config, e.g. ``env:ALPHA_JIRA``.
        env: environment mapping to read from. Defaults to ``os.environ``; injected in tests.
    """
    environ = dict(os.environ) if env is None else env
    prefix = _env_prefix(ref)
    return AtlassianCredentials(
        base_url=_require(f"{prefix}_BASE_URL", environ).rstrip("/"),
        email=_require(f"{prefix}_EMAIL", environ),
        api_token=_require(f"{prefix}_API_TOKEN", environ),
    )


def resolve_secret(ref: str, env: dict[str, str] | None = None) -> str:
    """Resolve a single-value ``env:VAR_NAME`` reference (webhook secret, admin token, API key)."""
    environ = dict(os.environ) if env is None else env
    return _require(_env_prefix(ref), environ)
