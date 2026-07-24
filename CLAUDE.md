# CLAUDE.md — LeapXpert_AgentA

> **PRD-to-UserDoc Automation Agent Flow.** A multi-tenant Python service: a Confluence webhook
> detects a finalized PRD → a LangGraph-orchestrated pipeline of role-agents drafts an end-user
> help doc → drives a human review loop in Jira → waits for two human approvals (Reviewer PM PASS,
> then Head of Product) → restricts + moves + exports the doc as Markdown.
> **Jira and Confluence are the entire human interface. There is no GUI.**

---

## 🔴 FIRST ACTION IN EVERY SESSION

Read these two files **before doing anything else**:

1. [implementation-state/STATE.md](implementation-state/STATE.md) — where the build is *right now*, and the exact next action.
2. [implementation-state/BLOCKERS.md](implementation-state/BLOCKERS.md) — what is waiting on a human. Never work around a blocker; ask.

Then continue from `STATE.md → Next Action`. Do **not** re-plan, re-derive, or re-read all planning
artifacts unless `STATE.md` tells you to. Update `STATE.md` **before** the context runs out, not after.

---

## Source of Truth (read-only — never edit these)

These five documents are the **contract**. Code serves them; when code and these disagree, these win.

| Doc | Path | Role |
|---|---|---|
| PRD v0.3 | [planning-artifacts/prds/prd-LeapXpert_AgentA-2026-07-23/prd.md](planning-artifacts/prds/prd-LeapXpert_AgentA-2026-07-23/prd.md) | FR-01…FR-15, NFR-01…NFR-11, EH-01…EH-09, §9 stages, §10 state, §11 config, §15 deploy |
| Architecture Spine r2 | [planning-artifacts/architecture/architecture-LeapXpert_AgentA-2026-07-23/ARCHITECTURE-SPINE.md](planning-artifacts/architecture/architecture-LeapXpert_AgentA-2026-07-23/ARCHITECTURE-SPINE.md) | **Binding.** AD-1…AD-23, Stack table, layer map |
| Solution Design r2 | [planning-artifacts/architecture/architecture-LeapXpert_AgentA-2026-07-23/solution-design.md](planning-artifacts/architecture/architecture-LeapXpert_AgentA-2026-07-23/solution-design.md) | Readable companion. **Spine wins on conflict.** |
| Epics & Stories | [planning-artifacts/epics.md](planning-artifacts/epics.md) | 6 epics / 40 stories with Given-When-Then ACs. The build backlog. |
| Readiness Report | [planning-artifacts/implementation-readiness-report-2026-07-24.md](planning-artifacts/implementation-readiness-report-2026-07-24.md) | Traceability proof + 4 carry-forward items |

**Scope:** DEMO with FULL HARDENING. The demo-trim of AD-9 / AD-12 / AD-13 was explicitly *rejected* —
all three ship fully specced. Only genuinely post-demo items are deferred (see Spine → Deferred).

---

## Non-Negotiable Invariants

Every change must preserve all of these. If a task seems to require breaking one, stop and ask.

1. **AD-1 — Inward-only dependencies.** `webhooks → router → orchestrator → agents → {adapters, repository} → {Atlassian, SQLite}`.
   **Only adapters open an HTTP socket to Atlassian. Only the repository runs SQL.** Agents and the
   orchestrator receive adapters/repository by injection — they never construct a transport or DB connection.
2. **AD-2 / AD-11 — One durable store.** The repository-owned SQLite state record is the *single*
   authoritative durable truth. `stage` is an explicit §9 enum written **only by the orchestrator**,
   never by a role-agent, never inferred from an Atlassian field. LangGraph is **in-invocation control
   flow only** — its checkpointer is an ephemeral `InMemorySaver`, never a cross-webhook store.
3. **AD-4 / NFR-05 — Config isolation.** No project-specific literal (Jira project key, Confluence
   space/folder id, account id, `md_export_dir`) appears in code, prompts, or `SKILL.md`. The tree stays
   grep-clean. The reserved label `agent-generated` is the **only** allowed cross-tenant constant.
   Secrets come from env references only — never inline.
4. **AD-15 — The agent never transitions a human-gate ticket.** It *detects* a human moving the Review
   or Publishing ticket into a `done`-category status. It auto-transitions **only** the PRD-tracking
   ticket (AD-13). No timeouts, no auto-escalation — parked runs park indefinitely.
5. **AD-16 — No loop self-spins.** The clarification (FR-08, 4 enumerated triggers only) and
   structure-confirmation (FR-10) loops **block on a human reply** and must never fabricate the answer.
   The redraft loop is uncapped but requires a fresh human comment each round.
6. **AD-9 — Idempotency.** Dedupe key = `<tenant_id>:<event_type>:<entity_id>:<version_marker>`, stored
   in `processed_events` (UNIQUE constraint), **recorded at flow admission, not on receipt**.
7. **AD-21 / NFR-11 — The 1 GB box is a design input.** Lean deps, single Uvicorn worker, one PRD
   resident in memory, no co-located DB server. Image built **off** the box and pulled.
8. **AD-20 / NFR-01 — 100% of LLM calls traced** in LangSmith with correlation id + `review_round`.

---

## Tech Stack (pinned — Spine → Stack table)

Python **3.12** (`python:3.12-slim`) · FastAPI 0.136.3 · Uvicorn 0.51.0 · **langgraph 1.2.9 (MIT core only —
never `langgraph-api`, NFR-10)** · langgraph-checkpoint 4.1.1 (`InMemorySaver`) · anthropic 0.117.0 ·
langsmith 0.10.9 · markdownify 1.2.3 · httpx · stdlib `sqlite3` · Caddy 2.11.4 · litestream ≥0.5.4.
**Jira Cloud REST v3** (ADF bodies mandatory) · **Confluence Cloud REST v2** (+ **v1** for move & content-restriction).

---

## Layout

```
app/
  main.py         FastAPI app + single webhook entrypoint
  webhooks/       signature validation, event parsing, dedupe check   (AD-8, AD-9)
  router.py       tenant resolution via config registry               (AD-3)
  orchestrator/   in-invocation LangGraph graph + stage nodes         (AD-6, AD-11)
  agents/         classifier, ticket_manager, author, feedback_interpreter,
                  publisher, error_handler — each: node + prompt + SKILL.md
  adapters/       jira.py, confluence.py, markdown.py                 (AD-7, AD-14)
  repository/     state record (single durable truth) + processed_events (AD-2, AD-9)
  config/         registry loader + tenant-config schema              (AD-4)
  domain/         state model, Stage enum, FeedbackDecision, AgentError, ADF helpers
  admin/          authenticated localhost reconcile/liveness endpoint (AD-22)
fixtures/classifier/{dev,holdout}/   labeled ACCEPT/REJECT pages; holdout = the 0-FP/0-FN bar (AD-17)
deploy/           Dockerfile, Caddyfile, swap+firewall, litestream, cron reconcile
tests/
implementation-state/   ← build state, tracker, decisions, blockers, session log
```

---

## Conventions

- **Stages** = snake_case §9 enum: `detected, confirmed, prd_ticket_done, drafted, awaiting_review,
  awaiting_clarification, awaiting_structure_confirm, revising, passed, awaiting_publish_approval,
  publishing, complete, error`.
- **Ids:** `prd_id` = Confluence page id (the stable key + the correlation marker stamped on every
  created artifact). Jira referenced by issue **key**. Tenant by `project_id`.
- **Timestamps** ISO-8601 UTC. **Jira bodies** always ADF, never a plain string. **Errors** normalize to
  one `AgentError` raised by adapters.
- **Agents** named `<role>_agent`. **Adapters** expose domain verbs (`transition_issue`), not HTTP.
- **Tests:** every story lands with tests. External services are faked at the adapter boundary — no
  network in the unit suite.

---

## Human-Block Protocol

This build is autonomous **until it needs something only a human can supply** — a 3rd-party credential,
an account, a paid resource, or a live tenant. When that happens:

1. **Stop that thread of work.** Do not fake, stub-around, or skip the requirement silently.
2. Record it in [implementation-state/BLOCKERS.md](implementation-state/BLOCKERS.md) with: what is needed,
   which story it blocks, why it cannot be self-served, and exact steps for the human.
3. **Ask the user**, then continue with everything *not* blocked by it.

**Setup instructions for every known gate live in [SETUP-GUIDE.md](SETUP-GUIDE.md)** — written for
someone who has not connected a third-party API before. Two scripts back it up:
`scripts/discover_ids.py` (prints the Atlassian IDs for `config/registry.yaml`) and
`scripts/verify_setup.py` (read-only check of the whole configuration). Keep both current when
config gains a field.

Known gates: Anthropic API key · LangSmith account+key · Atlassian Cloud tenant (Jira + Confluence)
with API tokens and test projects/spaces/folders · DigitalOcean Droplet + Spaces · container registry / CI
· Docker (not installed locally).

---

## Working Rhythm

1. Pick the next story from [implementation-state/EPIC-STORY-TRACKER.md](implementation-state/EPIC-STORY-TRACKER.md)
   (critical-path before hardening, epic order 1→6).
2. Re-read that story's ACs in `epics.md` — they are Given/When/Then and are the definition of done.
3. Implement + test. Run the suite.
4. Update the tracker (status + evidence), append to `SESSION-LOG.md`, record any judgment call in
   `DECISION-LOG.md`, and refresh `STATE.md → Next Action`.

**Never mark a story Done without its tests passing.** Mark it `Blocked` or `Partial` and say why.
