"""Agent B — internal Knowledge Base + Slack Q&A (Epic 7).

A monorepo sibling to `app` (Agent A). It projects a *curated* Confluence space into a git-backed,
Obsidian-compatible Markdown vault (linked + graph-navigable), publishes that vault read-only at a URL,
and answers questions over it in Slack. It reuses Agent A's adapters, LLM runtime, config, and tracing
by injection — never by reaching into Agent A's internals.

Boundaries mirror Agent A's (AD-27): only adapters open an HTTP socket, only `agent_b.repository` runs
SQL, only `agent_b.agents` import the Anthropic SDK, and `agent_b.config` is a leaf — all enforced by
import-linter contracts over `root_packages = ["app", "agent_b"]`.

See `implementation-state/BACKLOG.md` → Epic 7 for the story breakdown, and the Agent B PRD under
`planning-artifacts/prds/` for the product spec.
"""
