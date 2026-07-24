"""Tenant-config and system-config schemas (AD-4, PRD §11).

The config registry is the **only** home for project-specific literals — Jira project keys, Confluence
folder ids, PM / Head-of-Product / admin account ids, credential refs, `md_export_dir`. Onboarding a
project or swapping a reviewer is an edit here and nowhere else (NFR-02, NFR-05).

Validation here is not ceremony. Two of the rules below prevent genuine production incidents:

* ``published_folder != source_folder`` — the published folder sitting *inside* the watched source
  folder would make the agent re-ingest its own output in an infinite draft loop. The adjacency of
  those two folders is the *primary* self-ingestion guard (AD-10); the label and author checks are
  only defense-in-depth. A typo here defeats the primary guard, so it is caught at load time.
* folder ids and project keys unique across tenants — a duplicate makes AD-3 tenant resolution
  ambiguous, which would let one tenant's flow touch another's Jira and Confluence.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmptyStr = Field(min_length=1)


class TenantConfig(BaseModel):
    """One project/tenant entry. Mirrors PRD §11."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str = NonEmptyStr
    """Stable tenant key. Used as the `<tenant_id>` component of the AD-9 dedupe key."""

    # --- routing: Confluence folders (AD-14 first-class folder ids) ---------------------------
    confluence_source_folder_id: str = NonEmptyStr
    """WATCHED by FR-01 detection. A page-created here starts the flow."""

    confluence_draft_folder_id: str = NonEmptyStr
    """Where UserDoc drafts are posted for the FR-06/FR-07 review loop."""

    confluence_published_folder_id: str = NonEmptyStr
    """Approved UserDocs (FR-15). MUST be adjacent to — not inside — the source folder."""

    confluence_space_key: str | None = None
    """Optional AD-3 routing fallback. The `page-created` payload is not guaranteed to carry the
    page's container folder (PRD §13 Q3/Q4); when it does not, the space key disambiguates which
    tenant an event belongs to. Purely a routing hint — FR-01 detection still independently checks
    the page is in the watched source folder, so this can never admit a page on its own."""

    # --- routing: Jira projects ----------------------------------------------------------------
    jira_main_project_key: str = NonEmptyStr
    """PRD-tracking ticket (FR-04) and Publishing ticket (FR-13) live here."""

    jira_review_project_key: str = NonEmptyStr
    """Review ticket (FR-06) and rename-request tasks (FR-02a) live here."""

    # --- identities (fixed for the demo) -------------------------------------------------------
    pm_account_id: str = NonEmptyStr
    """Reviewer PM: owns the draft-review/feedback loop and the PASS gate (FR-12)."""

    head_of_product_account_id: str = NonEmptyStr
    """Final publish gate (FR-14)."""

    admin_account_id: str = NonEmptyStr
    """Receives EH-01 error escalations and drives EH-02 resume."""

    identity_overrides: dict[str, str] = Field(default_factory=dict)
    """AD-12 cross-org fallback: Confluence accountId -> Jira accountId. Consulted before the
    email-match fallback when assigning the FR-02a rename task. Empty for the same-org common case,
    where the accountId is shared across Jira and Confluence."""

    # --- Jira workflow (AD-13) ------------------------------------------------------------------
    preferred_transition_path: list[str] = Field(default_factory=list)
    """AD-13 config-declared multi-hop path to a `done`-category status, as ordered transition
    *names*, for workflows with mandatory intermediate states. Consulted only when no direct
    transition to a done-category status is legal from the current status. Empty means "direct only" —
    the agent escalates to the admin rather than guessing a path."""

    # --- infra ----------------------------------------------------------------------------------
    md_export_dir: str = NonEmptyStr
    """FR-15 step 3: where the final UserDoc `.md` is written on server disk."""

    require_edit_restriction: bool = True
    """FR-15 step 1 / AD-18: apply the Confluence edit restriction when publishing.

    **Defaults to True — the spec'd behaviour.** Setting it False is an explicit, per-tenant
    relaxation of a requirement, and exists for one reason: page restrictions are not part of the
    **Confluence Cloud Free** edition, where the API rejects them with a 403 no matter what
    permissions the agent account holds (see BLOCKERS.md → B-7). On such a site the publish
    transaction could never complete, so the choice is between an unusable flow and a published doc
    that is not write-protected.

    When False the publisher **skips** step 1 and says so in the publish confirmation, so the humans
    learn from the ticket that the page stayed editable. It never claims the restriction was applied:
    `restriction_applied_at` stays unset and `PublishResult.restriction_applied` is False. Move and
    export are unaffected. Set it back to True after upgrading the site."""

    # --- observability (AD-20) ------------------------------------------------------------------
    trace_content: bool = False
    """AD-20 content-gating flag. False = metadata-only traces (latency/tokens/cost, no document
    body). The demo traces non-confidential test PRDs only; this is the seam toward the production
    redaction/retention policy, which stays deferred."""

    # --- credentials by reference, never inline (AD-4, §13 Q6) ---------------------------------
    jira_credentials_ref: str = NonEmptyStr
    confluence_credentials_ref: str = NonEmptyStr

    @field_validator("jira_credentials_ref", "confluence_credentials_ref")
    @classmethod
    def _must_be_env_reference(cls, value: str) -> str:
        if not value.startswith("env:"):
            raise ValueError(
                f"credential reference {value!r} must be an environment reference (env:PREFIX). "
                "Inline secrets are forbidden (AD-4, PRD §13 Q6)."
            )
        return value

    @model_validator(mode="after")
    def _folders_must_be_distinct(self) -> TenantConfig:
        """AD-10 primary self-ingestion guard: published output must never sit in the watched set."""
        if self.confluence_published_folder_id == self.confluence_source_folder_id:
            raise ValueError(
                f"tenant {self.project_id!r}: confluence_published_folder_id must be ADJACENT to "
                "(not the same as) confluence_source_folder_id. Publishing into the watched folder "
                "would make the agent re-ingest its own output in an infinite draft loop (AD-10)."
            )
        if self.confluence_draft_folder_id == self.confluence_source_folder_id:
            raise ValueError(
                f"tenant {self.project_id!r}: confluence_draft_folder_id must differ from "
                "confluence_source_folder_id, or every draft would re-trigger detection (FR-01)."
            )
        return self

    @model_validator(mode="after")
    def _jira_projects_must_be_distinct(self) -> TenantConfig:
        """PRD §6.0: the Review project is a *secondary* project, separate from Main."""
        if self.jira_main_project_key == self.jira_review_project_key:
            raise ValueError(
                f"tenant {self.project_id!r}: jira_main_project_key and jira_review_project_key "
                "must differ — the Review project exists to keep the PM review loop out of the "
                "main project (PRD §6.0)."
            )
        return self


class ModelConfig(BaseModel):
    """AD-17 — LLM model ids are pinned in config, never hard-coded at a call site.

    These are not project-specific literals (every tenant uses the same models), so they live in
    system config rather than being repeated in each tenant entry. Per-tenant override is a seam,
    not a demo requirement.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    classifier: str = "claude-sonnet-5"
    author: str = "claude-opus-4-8"
    feedback_interpreter: str = "claude-sonnet-5"


class ObservabilityConfig(BaseModel):
    """AD-20 / NFR-01 — 100% of LLM calls traced with latency, tokens, cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    langsmith_enabled: bool = False
    langsmith_project: str = "leapxpert-agent-a"
    langsmith_api_key_ref: str = "env:LANGSMITH_API_KEY"


class ReconcilerConfig(BaseModel):
    """AD-22 — liveness + gate reconcile sweep. Recoverability, explicitly NOT a timeout."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    liveness_threshold_minutes: int = Field(default=1440, gt=0)
    """How stale a parked/error run must be before the admin is alerted. Default 24h. Crossing this
    threshold alerts *once* (tracked by `liveness_alerted_at`) — it never advances or fails a run."""


class SystemConfig(BaseModel):
    """Cross-tenant runtime settings. Contains no project-specific literal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database_path: str = "data/state.db"
    """The single authoritative durable store (AD-2, AD-11). Replicated off-box by AD-23."""

    webhook_secret_ref: str = "env:WEBHOOK_SHARED_SECRET"
    """AD-8 — validated before dedupe and before tenant resolution, so it is necessarily
    system-wide rather than per-tenant. Per-tenant webhook secrets are a production item, alongside
    the AD-20 note that the single `.env` gives every tenant token the same blast radius."""

    admin_token_ref: str = "env:ADMIN_API_TOKEN"
    """AD-22 — authenticates the localhost reconcile/liveness endpoint the cron sweep calls."""

    anthropic_api_key_ref: str = "env:ANTHROPIC_API_KEY"

    retry_max_attempts: int = Field(default=3, ge=1)
    """NFR-08 — transient Atlassian failures retried with backoff inside the adapters (~3 tries)."""

    retry_backoff_seconds: float = Field(default=1.0, gt=0)

    models: ModelConfig = Field(default_factory=ModelConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    reconciler: ReconcilerConfig = Field(default_factory=ReconcilerConfig)
