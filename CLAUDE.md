# CLAUDE.md — LeapXpert_AgentA · the agent's brain & router

> **PRD-to-UserDoc Automation Agent Flow.** A multi-tenant Python service: a Confluence webhook
> detects a finalized PRD → a LangGraph-orchestrated pipeline of role-agents drafts an end-user help
> doc → drives a human review loop in Jira → waits for two human approvals (Reviewer PM PASS, then
> Head of Product) → restricts + moves + exports the doc as Markdown.
> **Jira and Confluence are the entire human interface. There is no GUI.**

This file is the **map, not the territory**. It exists so you can find the right file, understand the
shape of the system, and know what to do next **without re-reading the codebase or the planning docs**.
Read it fully once; after that, trust its routing.

---

## 🚦 FIRST ACTION EVERY SESSION

**Read [implementation-state/NOW.md](implementation-state/NOW.md).** It is short by design and answers one
question: *what is the state of play, and what is the next action?* Do not scan the whole codebase or the
planning docs to orient — `NOW.md` plus this file's [Codebase Map](#-codebase-map) is enough.

Then classify the request in front of you with the decision tree below.

### Is this an *incomplete task* or a *new requirement*?

```
   User request ──▶ Read implementation-state/NOW.md
                              │
        ┌─────────────────────┴─────────────────────┐
        ▼                                            ▼
 NOW.md has an Active Story (WIP/BLOCKED)     Request ≠ the Active Story
 AND the request continues it                        │
        │                              ┌─────────────┴─────────────┐
        ▼                              ▼                           ▼
  ▶ INCOMPLETE TASK          Matches a BACKLOG.md item        Nothing matches
  Resume from NOW.md →                │                           │
  "Next action". Re-read              ▼                           ▼
  the story's acceptance     ▶ INCOMPLETE TASK             ▶ NEW REQUIREMENT
  criteria in BACKLOG.md     Promote it to Active in        Write a story in BACKLOG.md
  before coding.             NOW.md, then implement.        (id, intent, acceptance
                                                            criteria), make it Active,
                                                            then implement.
```

- **When in doubt, it's cheap to check:** scan `BACKLOG.md` headings (one screen) before assuming a
  request is brand-new. A duplicate story is worse than a 10-second scan.
- **A new requirement is not started until it's a story.** Capture intent + acceptance criteria in
  `BACKLOG.md` first — that is the definition of done you'll be held to, and the next agent's context.

---

## 🗺️ Codebase Map

The route to *where the work is*. Directories are inward-dependency ordered (AD-1:
`webhooks → router → orchestrator → agents → {adapters, repository} → {Atlassian, SQLite}`).
**Only adapters open an HTTP socket to Atlassian; only the repository runs SQL.** Everything else
receives its collaborators by injection.

| Area | Path | What lives here / when to open it |
|---|---|---|
| **Entry point** | [app/main.py](app/main.py) | FastAPI app; the one public webhook route is mounted here. |
| **Webhook ingress** | [app/webhooks/](app/webhooks/) | `signature.py` (HMAC), `events.py` (parse), `ingress.py` (validate→dedupe→route pipeline), `router.py` (**`_dispatch_*`: maps an event to an orchestrator call** — the busiest edge-case file: self-ingestion, rename-churn, deletion detection). |
| **Tenant routing** | [app/router.py](app/router.py) | Resolve one tenant from the event via the config registry (AD-3). |
| **Orchestrator** | [app/orchestrator/](app/orchestrator/) | `runner.py` (the only writer of `stage`; the 5-step invocation + all webhook re-entry methods), `graph.py` (in-invocation LangGraph router), `stages.py` (`Advance`/`Park`/`Stay` outcomes + `HandlerRegistry`), `feedback_routing.py` (pure, LLM-free routing of a `FeedbackDecision`), `context.py` (the per-run `RunContext` — what feeds the agents), `handlers_{detection,authoring,review,publishing}.py` (one stage → one unit of work). |
| **Agents (LLM)** | [app/agents/classifier/](app/agents/classifier/), [author/](app/agents/author/), [feedback_interpreter/](app/agents/feedback_interpreter/) | Each = `agent.py` + **`SKILL.md`** (the persona/rubric — the primary tuning surface; edit this to change behavior). Classifier also has `evaluation.py` (the 0-FP/0-FN harness). |
| **Agents (mechanical, no LLM)** | [app/agents/](app/agents/) | `detection.py` (admission rule), `ticket_manager.py` (all Jira create/find/transition), `publisher.py` (create/restrict/move/export), `error_handler.py`, `identity.py` (Confluence→Jira assignee), `review_request.py` (comment builders). |
| **Shared LLM runtime** | [app/agents/llm.py](app/agents/llm.py), [tracing.py](app/agents/tracing.py), [skills.py](app/agents/skills.py) | The **only** module that imports the Anthropic SDK; every call is traced (AD-20). `load_skill(role)` reads a `SKILL.md` at call time. |
| **Adapters** | [app/adapters/](app/adapters/) | `jira.py`, `confluence.py`, `markdown.py`, `http.py`. Domain verbs (`transition_issue`), not HTTP. ADF bodies + retry + `AgentError` normalization live here. |
| **Repository** | [app/repository/](app/repository/) | `state_repository.py` (the single durable truth + the only `stage`-mutator API), `event_repository.py` (`processed_events` dedupe), `database.py`. |
| **Config** | [app/config/](app/config/) | `schema.py` (`TenantConfig`), `registry.py`, `secrets.py`, `constants.py` (the one allowed cross-tenant literal: the `agent-generated` label). Live values in `config/registry.yaml` (**gitignored**). |
| **Domain** | [app/domain/](app/domain/) | `stage.py` (the §9 `Stage` enum + legal-transition map), `state.py` (`PrdState`), `feedback.py` (`FeedbackDecision`), `events.py`, `dedupe.py`, `errors.py` (`AgentError`), `adf.py`. Pure types, no I/O. |
| **Composition root** | [app/composition.py](app/composition.py) | The one place that *constructs* things: reads config, builds adapters + the LLM client, wires the 6 agents, registers handlers, produces the `Orchestrator`. Start here to trace how anything is assembled. |
| **Admin / ops** | [app/admin/](app/admin/) | Authenticated localhost reconcile + liveness endpoint (AD-22). |
| **Fixtures** | [fixtures/classifier/{dev,holdout}/](fixtures/classifier/) | Labeled ACCEPT/REJECT pages; `holdout` is the 0-FP/0-FN acceptance bar (AD-17). |
| **Scripts** | [scripts/](scripts/) | `discover_ids.py`, `verify_setup.py` (read-only config check), `run_classifier_eval.py`, `run_local_demo.py`. |
| **Deploy** | [deploy/](deploy/) | Dockerfile, Caddyfile, swap/firewall, litestream, cron reconcile. |
| **Tests** | [tests/](tests/) | Every story has tests; external services faked at the adapter boundary — **no network in the unit suite**. |

### The request lifecycle in six lines (so you know which handler owns a behavior)

1. **Webhook in** → `webhooks/ingress.py` validates + dedupes + routes; `webhooks/router.py` maps it to an orchestrator method.
2. **`advance()`** ([orchestrator/runner.py](app/orchestrator/runner.py)) loads state, re-enters the graph at the recorded `stage`, runs advancing stages, persists, stops. Serial (one PRD at a time).
3. **Detect → confirm** (`handlers_detection.py`): Detection rule admits; **Classifier** (LLM) confirms it's a real PRD; Ticket manager drives the tracking ticket to Done.
4. **Draft** (`handlers_authoring.py`): **Author** (LLM) drafts + self-critiques → Publisher creates the draft page → Review ticket → **park at `awaiting_review`**.
5. **Review loop** (`runner.apply_pm_comment` + `handlers_review.py`): **Feedback interpreter** (LLM) classifies the PM comment → `feedback_routing.py` acts → Author revises. Uncapped; each round needs a fresh human comment.
6. **Publish** (`handlers_publishing.py`): two human "Done" gates (PM PASS, then Head of Product) → Publisher restricts + moves + exports → `complete`.

> Agents never call each other. Every hand-off goes through the orchestrator and the state record.
> The `RunContext` ([orchestrator/context.py](app/orchestrator/context.py)) gathers live Confluence/Jira
> data and feeds it into each agent's prompt.

---

## 🔒 Non-Negotiable Invariants

Every change preserves all of these. If a task seems to require breaking one, **stop and ask**.

1. **AD-1 — Inward-only dependencies.** Only adapters touch Atlassian; only the repository runs SQL. Agents/orchestrator receive transports by injection.
2. **AD-2 / AD-11 — One durable store.** The repository-owned SQLite state record is the single authoritative truth. `stage` is written **only by the orchestrator**. LangGraph is in-invocation control flow only (`InMemorySaver`, discarded per webhook).
3. **AD-4 — Config isolation.** No project literal (Jira key, folder id, account id, `md_export_dir`) in code, prompts, or `SKILL.md`. Tree stays grep-clean. Only `agent-generated` is an allowed constant. Secrets via env refs only.
4. **AD-15 — The agent never transitions a human-gate ticket.** It *detects* a human moving the Review/Publishing ticket to Done. It auto-transitions only the PRD-tracking ticket. No timeouts; parked runs park indefinitely.
5. **AD-16 — No loop self-spins.** The clarification (4 enumerated triggers only) and structure-confirmation loops block on a human reply. The redraft loop is uncapped but needs a fresh human comment each round.
6. **AD-9 — Idempotency.** Dedupe key `<tenant>:<event_type>:<entity>:<version>` in `processed_events` (UNIQUE), recorded at flow admission. Every external create is find-or-create by the `prd_id` marker.
7. **AD-21 — The 1 GB box is a design input.** Lean deps, single Uvicorn worker, one PRD resident, image built off-box.
8. **AD-20 — 100% of LLM calls traced** in LangSmith with correlation id + `review_round`.

The full rationale for any `AD-xx` / `FR-xx` / `D-xx` is in the 3 critical docs and the decision log —
consult on demand, don't reload by default.

---

## 📚 The 3 Critical Documents (consult on demand — not every session)

These describe **what was designed and why**. Read them **only when you need the product's history or a
rule's rationale** — e.g. before changing agent behavior, the state machine, or a webhook guard. They are
large; don't load them to answer a routing question this file already answers.

| Doc | Path | Read it when… |
|---|---|---|
| **PRD v0.3** | [planning-artifacts/prds/prd-LeapXpert_AgentA-2026-07-23/prd.md](planning-artifacts/prds/prd-LeapXpert_AgentA-2026-07-23/prd.md) | you need the *product* requirement (FR/NFR/EH), the stage list, or a config field's meaning. |
| **Architecture Spine** | [planning-artifacts/architecture/architecture-LeapXpert_AgentA-2026-07-23/ARCHITECTURE-SPINE.md](planning-artifacts/architecture/architecture-LeapXpert_AgentA-2026-07-23/ARCHITECTURE-SPINE.md) | you need the *binding* architectural decision (AD-xx) behind a rule. **Spine wins on any conflict.** |
| **Solution Design** | [planning-artifacts/architecture/architecture-LeapXpert_AgentA-2026-07-23/solution-design.md](planning-artifacts/architecture/architecture-LeapXpert_AgentA-2026-07-23/solution-design.md) | you want the readable walkthrough of the same decisions. |

> ⚠️ **These may be stale.** They haven't been reconciled against the code in a while. If code and a doc
> disagree, the **code is what runs** — trust it, and treat the mismatch as a doc bug to fix (see the DoD
> rule below). The Spine wins only where it and the Solution Design disagree with *each other*.

### 🔧 RULE: keep the 3 docs in sync after you change behavior

The critical docs are only trustworthy if we keep them current. **When your change alters something they
describe — a functional behavior (FR/EH), an architectural rule (AD), the stage machine, a config field,
or the tech stack — updating the relevant doc is part of the task, not optional cleanup:**

- New/changed **product behavior** → update the PRD (add/amend the `FR`/`EH`, dated like the existing amendments).
- New/changed **architectural rule or boundary** → update the Spine (add/amend the `AD`), and mirror the readable version into the Solution Design.
- Record the *why* as a new decision in [implementation-state/DECISION-LOG.md](implementation-state/DECISION-LOG.md) (next `D-` number).
- A pure refactor that changes **no** externally-described behavior needs **no** doc change — say so instead of touching them.

---

## 🔁 Agile Working Rhythm

One story at a time. The state files are the shared memory between sessions — keep them honest.

1. **Orient** — `NOW.md` → identify the Active Story (or create one in `BACKLOG.md` for a new requirement).
2. **Understand the story** — re-read its acceptance criteria in `BACKLOG.md`. If you need *why*, follow the pointers into the 3 docs / decision log. Don't re-derive.
3. **Implement + test** — write code that reads like its neighbors. Every story lands with tests; fakes at the adapter boundary (no network).
4. **Verify** — `make check` (lint + format + import-linter contracts + the offline suite) must be green. Never mark a story Done with a red build; mark it `BLOCKED`/`PARTIAL` and say why.
5. **Close the loop (Definition of Done):**
   - [ ] `make check` green.
   - [ ] Move the story to Done in `BACKLOG.md`; append a one-line entry to `CHANGELOG.md`.
   - [ ] Update `NOW.md` → new state + next action.
   - [ ] **Sync the 3 critical docs** if behavior/architecture/stack changed (rule above); log the decision in `DECISION-LOG.md`.
   - [ ] **Update this file's [Codebase Map](#-codebase-map)** if you added/moved/renamed a module or changed the layering (procedure below).
   - [ ] If you hit a human/3rd-party gate, record it in `BLOCKERS.md` and ask the user.

---

## 🧠 Procedure: updating the codebase knowledge (this file)

CLAUDE.md is only a good router if it stays true. **Update the [Codebase Map](#-codebase-map) (and, if
relevant, the invariants or stack) in the same change that makes it stale.** Trigger it when you:

- add, remove, move, or rename a module/directory → fix its row in the map;
- add a new agent, adapter, handler, or stage → add it where a future agent will look for it;
- change the dependency direction or a boundary → update AD-1's line and the invariants;
- change a pinned dependency → update the Tech Stack table.

Keep the map at **routing altitude**: say *where a thing is and when to open it*, never reproduce the
code. If a row would need a paragraph, the file's own module docstring should carry that detail instead.
A stale map costs every future session tokens and trust — treat fixing it as part of the diff.

---

## 🧱 Tech Stack (pinned — Spine → Stack table)

Python **3.12** (`python:3.12-slim`) · FastAPI 0.136.3 · Uvicorn 0.51.0 · **langgraph 1.2.9 (MIT core
only — never `langgraph-api`, NFR-10)** · langgraph-checkpoint 4.1.1 (`InMemorySaver`) · anthropic
0.117.0 · langsmith 0.10.9 · markdownify 1.2.3 · httpx · stdlib `sqlite3` · Caddy 2.11.4 · litestream
≥0.5.4. **Jira Cloud REST v3** (ADF bodies mandatory) · **Confluence Cloud REST v2** (+ **v1** for move
& content-restriction). Default to the latest, most capable Claude models for LLM work; model ids come
from `config/registry.yaml` (`system.models.*`), never a literal at a call site (AD-17).

## 🧭 Conventions

- **Stages** = snake_case §9 enum ([app/domain/stage.py](app/domain/stage.py)). `stage` is authoritative over any Atlassian status.
- **Ids:** `prd_id` = Confluence page id (the stable key + correlation marker). Jira by issue **key**. Tenant by `project_id`.
- **Timestamps** ISO-8601 UTC. **Jira bodies** always ADF, never a plain string. **Errors** normalize to one `AgentError` raised by adapters.
- **Agents** named `<role>_agent`; **adapters** expose domain verbs, not HTTP.
- **Behavior tuning is a prompt edit**, not a code edit: change a `SKILL.md` before changing agent code.

## ⌨️ Commands

```bash
make check     # everything CI runs: ruff lint + format check + import-linter + offline pytest
make test      # just the offline suite
make format    # auto-fix formatting + safe lint fixes
.venv/bin/python -m pytest -q tests/test_<x>.py    # one test file
.venv/bin/python scripts/run_classifier_eval.py    # the 0-FP/0-FN classifier gate (needs an API key)
.venv/bin/python scripts/verify_setup.py           # read-only check of the live Atlassian config
```

Python is pinned via `.python-version` (pyenv); venv at `.venv/`. Docker isn't installed locally and
isn't needed — CI builds the image off-box (AD-21).

---

## 🛑 Human-Block Protocol

This build is autonomous **until it needs something only a human can supply** — a 3rd-party credential,
an account, a paid resource, or a live tenant. When that happens: **stop that thread** (don't fake or
stub around it), record it in [implementation-state/BLOCKERS.md](implementation-state/BLOCKERS.md) (what's
needed, which story it blocks, why it can't be self-served, exact steps for the human), **ask the user**,
and continue with everything not blocked. Setup steps live in [SETUP-GUIDE.md](SETUP-GUIDE.md), backed by
`scripts/verify_setup.py` and `scripts/discover_ids.py` — keep both current when config gains a field.
