"""Agent B config schema (AD-4 mirror, AD-27, Epic 7).

Agent B keeps Agent A's rule that no project literal lives in code: the space key, the folder ids to
crawl, the vault location, the model ids, and every credential *reference* live in the `agent_b:` block
of `config/registry.yaml`, never here and never at a call site. Secrets are `env:` references only,
resolved lazily by `app.config.secrets` at the point of use — loading config needs no credentials and
no network, which is what keeps the unit suite offline.

This module is a **leaf** (AD-27): it imports nothing from `app.*` or the rest of `agent_b` — enforced
by an import-linter contract.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_NonEmptyStr = Field(min_length=1)


def _must_be_env_ref(value: str) -> str:
    if not value.startswith("env:"):
        raise ValueError(
            f"credential reference {value!r} must be an environment reference (env:PREFIX). "
            "Inline secrets are forbidden (AD-4)."
        )
    return value


class EmbeddingsConfig(BaseModel):
    """Local, no-egress embeddings for the RAG index (AD-31, D-43, D-49)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime: str = "fastembed"
    """ONNX runtime — chosen over sentence-transformers/torch to stay inside the 1 GB box (AD-21)."""
    model: str = "BAAI/bge-small-en-v1.5"
    chunk_chars: int = Field(default=1200, gt=0)
    """Target characters per embedded chunk — small enough to retrieve a precise passage, big enough
    to keep a paragraph's context. The vectors live in Agent B's own SQLite store (D-49), not a
    separate sqlite-vec DB: this Python build has no loadable-extension support, and a brute-force
    numpy cosine over a small internal corpus is more than fast enough (AD-32 one-store spirit)."""
    chunk_overlap: int = Field(default=150, ge=0)
    """Characters of overlap between adjacent chunks, so a passage split across a boundary is still
    retrievable from at least one chunk."""


class RagConfig(BaseModel):
    """Retrieval + grounding knobs (AD-30: always cite, and refuse below the floor)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    top_k: int = Field(default=8, gt=0)
    min_score: float = Field(default=0.35, ge=0.0, le=1.0)
    """A top hit below this similarity → refuse ("I don't have a doc on that") rather than guess."""
    graph_expansion_hops: int = Field(default=1, ge=0)
    """After the vector hit, also pull in notes this many `[[link]]` hops away."""


class SlackConfig(BaseModel):
    """Slack bot-user surface (Socket Mode). Every secret is an env ref (AD-4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bot_token_ref: str = "env:AGENTB_SLACK_BOT_TOKEN"
    app_token_ref: str = "env:AGENTB_SLACK_APP_TOKEN"
    signing_secret_ref: str = "env:AGENTB_SLACK_SIGNING_SECRET"
    allowed_channel_ids: tuple[str, ...] = ()

    @field_validator("bot_token_ref", "app_token_ref", "signing_secret_ref")
    @classmethod
    def _env_ref(cls, value: str) -> str:
        return _must_be_env_ref(value)


class PublishConfig(BaseModel):
    """Quartz static-site publish target (AD-28: read-only projection). Public, no auth (D-45)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = "https://agent.poetroastery.com"
    output_dir: str = "data/site"


class AgentBConfig(BaseModel):
    """One knowledge base built from one curated Confluence space (Epic 7).

    Single-tenant for the demo (D-41); a per-tenant mapping is a later seam, mirroring Agent A.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    space_key: str = _NonEmptyStr
    confluence_credentials_ref: str = _NonEmptyStr
    """Reused from the Agent A tenant (e.g. `env:ALPHA_CONF`) — same site, read-only pull."""

    include_folder_ids: tuple[str, ...] = Field(min_length=1)
    """PRD source + published UserDocs + designs. At least one is required."""
    exclude_folder_ids: tuple[str, ...] = ()
    """Never ingested — the draft/review folder, so in-flight drafts stay out of the KB (D-41)."""

    folder_types: dict[str, str] = Field(default_factory=dict)
    """Map an include-folder id → the `doc_type` its pages get (`prd` | `userdoc` | `design`). An
    included folder not listed here defaults to `other`. Drives the note's `doc_type` frontmatter."""

    vault_dir: str = "data/vault"
    """Git-backed Obsidian vault on the Droplet; gitignored from source (a generated projection)."""
    database_path: str = "data/agent_b.db"
    """Agent B's OWN SQLite store, separate from Agent A's state.db (AD-32)."""
    schedule_cron: str = "0 3 * * *"
    """Nightly pull by default (D-41). The pull is a short-lived job, not resident (AD-21)."""

    curator_model: str = "claude-sonnet-5"
    """Librarian agent model (MOC/tags/suggested links). Pinned in config, never a literal (AD-17)."""
    answerer_model: str = "claude-opus-4-8"
    """RAG answerer model (AD-17)."""

    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    rag: RagConfig = Field(default_factory=RagConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    publish: PublishConfig = Field(default_factory=PublishConfig)

    @field_validator("confluence_credentials_ref")
    @classmethod
    def _env_ref(cls, value: str) -> str:
        return _must_be_env_ref(value)

    @model_validator(mode="after")
    def _folders_disjoint(self) -> AgentBConfig:
        """An id in both lists is a config bug — a folder can't be both crawled and skipped."""
        overlap = set(self.include_folder_ids) & set(self.exclude_folder_ids)
        if overlap:
            raise ValueError(
                f"folder id(s) {sorted(overlap)!r} appear in both include_folder_ids and "
                "exclude_folder_ids — a folder cannot be both crawled and skipped."
            )
        return self
