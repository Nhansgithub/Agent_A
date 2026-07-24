# Epic & Story Tracker — LeapXpert_AgentA

**Source of truth for scope:** [../planning-artifacts/epics.md](../planning-artifacts/epics.md).
This file tracks *status only* — it never redefines a story. Re-read the story's Given/When/Then
acceptance criteria in `epics.md` before implementing it.

**Status legend:** `TODO` · `WIP` · `DONE` (code + tests pass) · `PARTIAL` (code done, verification
blocked) · `BLOCKED` (cannot start — see BLOCKERS.md)

**Order of work:** epic 1 → 6; within an epic, `critical-path` before `hardening`.

**Progress: 7 / 39 DONE** · test suite: **192 passed**, ruff clean, **5/5 import-linter contracts kept**.

| | Epic | Stories | Done |
|---|---|---|---|
| 1 | Multi-Tenant Foundation & Deployable Skeleton | 10 | 7 |
| 2 | PRD Detection & Confirmation | 8 | 0 |
| 3 | UserDoc Authoring & Draft Publication | 5 | 0 |
| 4 | Human Review & Revision Loop | 6 | 0 |
| 5 | Approval & Publishing | 3 | 0 |
| 6 | Resilience, Recovery & Operations | 7 | 0 |

---

## Epic 1 — Multi-Tenant Foundation & Deployable Skeleton

Walking skeleton: validate + dedupe + route a webhook, persist run state via the repository,
round-trip Atlassian and LLM calls, advance explicit stages. Everything downstream depends on this.

| ID | Story | Tag | Governed by | Status | Evidence |
|---|---|---|---|---|---|
| 1.1 | Project scaffold, layered module skeleton, pinned dependency manifest | critical-path | AD-1, AD-6 | **DONE** | `pyproject.toml` (4 import-linter contracts), `app/` tree, `tests/test_architecture_boundaries.py`, `tests/test_stack_and_licensing.py` — 17 tests. Every Stack-table pin resolved exactly; `langgraph-api` absent from the tree (NFR-10). |
| 1.2 | Per-tenant config registry, tenant-config schema, env-ref secrets | critical-path | AD-4 | **DONE** | `app/config/{schema,registry,secrets,constants}.py`, `config/registry.example.yaml`, `.env.example`; `tests/test_config_registry.py` + `tests/test_config_isolation.py` — 45 tests incl. the automated NFR-05 grep-clean check. |
| 1.3 | Repository + single SQLite store: state record + stage enum | critical-path | AD-2, AD-11 | **DONE** | `app/domain/{stage,state,errors}.py`, `app/repository/{database,state_repository}.py`; `tests/test_state_repository.py` — 55 tests incl. gate-skip rejection and atomic stage+id writes. |
| 1.4 | Webhook ingress with shared-secret / signature validation | critical-path | AD-8 | **DONE** | `app/webhooks/{signature,events,ingress}.py`, `app/domain/events.py`. HMAC-SHA256 over the raw body + shared-secret header fallback, both constant-time. |
| 1.5 | Idempotency: `processed_events` + composite key recorded at admission | critical-path | AD-9, AD-8 | **DONE** | `app/domain/dedupe.py`, `app/repository/event_repository.py`, `Repository.admit()` — UNIQUE-constraint race, not check-then-write; key + state row in one transaction. |
| 1.6 | Route-before-work tenant resolution | critical-path | AD-3 | **DONE** | `app/router.py` — `TenantRouter` with folder / project-key / space-key resolution and a documented single-tenant fallback. |
| 1.7 | `JiraAdapter` — domain verbs, ADF bodies, retry, error normalization | critical-path | AD-7 | **DONE** | `app/adapters/{http,jira}.py`, `app/domain/{adf,atlassian}.py`; `tests/test_jira_adapter.py` — 32 tests against a fake transport (no network). |
| 1.8 | `ConfluenceAdapter` + markdown converter (v2 default, v1 move/restrictions) | critical-path | AD-7, AD-14 | TODO | |
| 1.9 | In-invocation LangGraph orchestrator, stage machine, serial queue | critical-path | AD-6, AD-11, AD-2, AD-5 | TODO | |
| 1.10 | LangSmith tracing harness for all LLM calls | critical-path | AD-20 | TODO | |

## Epic 2 — PRD Detection & Confirmation

| ID | Story | Tag | Governed by | Status | Evidence |
|---|---|---|---|---|---|
| 2.1 | Detect a new PRD page in the watched source folder | critical-path | AD-8, AD-9, AD-14, AD-10 | TODO | |
| 2.2 | Title-gate on the `final_PRD_<name>` pattern | critical-path | AD-8 | TODO | |
| 2.3 | Classifier agent confirms a genuine finalized PRD | critical-path | AD-17 | TODO | |
| 2.4 | Classifier held-out fixtures + ×3 eval harness (0-FP / 0-FN bar) | critical-path | AD-17 | TODO | |
| 2.5 | Locate-or-create the PRD-tracking ticket and drive it to Done | critical-path | AD-13 | TODO | |
| 2.6 | Title-mismatch / REJECT rename-request task and clean re-entry | hardening | AD-12, AD-9 | TODO | |
| 2.7 | Self-ingestion defense-in-depth (label + agent-account exclusion) | hardening | AD-10 | TODO | |
| 2.8 | Cross-org identity fallback for rename-task assignment | hardening | AD-12 | TODO | |

## Epic 3 — UserDoc Authoring & Draft Publication

| ID | Story | Tag | Governed by | Status | Evidence |
|---|---|---|---|---|---|
| 3.1 | Author agent drafts the first UserDoc from the PRD | critical-path | AD-17 | TODO | |
| 3.2 | Author self-critique pass (draft → critique → one revision) | critical-path | AD-17 | TODO | |
| 3.3 | Publish the draft to the Confluence draft folder (idempotent, self-stamped) | critical-path | AD-14, AD-10, AD-11 | TODO | |
| 3.4 | Create the Review ticket assigned to the Reviewer PM | critical-path | AD-11, AD-15 | TODO | |
| 3.5 | Post the framed review-request comment | critical-path | AD-7, AD-15 | TODO | |

## Epic 4 — Human Review & Revision Loop

| ID | Story | Tag | Governed by | Status | Evidence |
|---|---|---|---|---|---|
| 4.1 | Ingest PM feedback and route via a typed `FeedbackDecision` | critical-path | AD-16 | TODO | |
| 4.2 | Apply structured feedback to produce a revised draft | critical-path | AD-16, AD-20 | TODO | |
| 4.3 | Detect PASS on the Reviewer PM's Done transition | critical-path | AD-15 | TODO | |
| 4.4 | Structure-confirmation sub-loop for plain-language feedback | hardening | AD-16 | TODO | |
| 4.5 | Bounded clarification sub-loop (four enumerated triggers only) | hardening | AD-16 | TODO | |
| 4.6 | Late-feedback-after-Done ignored; non-Done transitions park | hardening | AD-15 | TODO | |

## Epic 5 — Approval & Publishing

| ID | Story | Tag | Governed by | Status | Evidence |
|---|---|---|---|---|---|
| 5.1 | Confirm PASS and create the Publishing ticket for the Head of Product | critical-path | AD-11, AD-15 | TODO | |
| 5.2 | Head of Product publish gate | critical-path | AD-15 | TODO | |
| 5.3 | Ordered, idempotent publish transaction (restrict / move / export / complete) | critical-path | AD-18, AD-14, AD-10 | TODO | |

## Epic 6 — Resilience, Recovery & Operations

| ID | Story | Tag | Governed by | Status | Evidence |
|---|---|---|---|---|---|
| 6.1 | Error surfacing and admin resume from checkpoint | hardening | AD-19, AD-11 | TODO | |
| 6.2 | Reconciliation & liveness sweep (dropped-gate-webhook recovery) | hardening | AD-22, AD-2, AD-15 | TODO | |
| 6.3 | Off-box state backup / disaster recovery | hardening | AD-23 | TODO | |
| 6.4 | Deploy to the reachable 1 GB host and run the end-to-end demo | critical-path | AD-21 | TODO | |
| 6.5 | 1 GB memory-envelope hardening | hardening | AD-21 | TODO | |
| 6.6 | Config-only modifiability verification (2nd project; swap identities) | hardening | AD-4 | TODO | |
| 6.7 | Content-gating observability flag and data-governance seam | hardening | AD-20 | TODO | |

---

## Carry-forward watch items (from the readiness report §7)

1. **Classifier accuracy is the one hard, measurable gate** (0 FP / 0 FN on *holdout*, S2.4). Riskiest
   single deliverable — front-load the fixture sets.
2. **EH-09 is intentionally out of scope** — non-Done gate transitions and stalls simply park, no timeout.
3. **LangSmith tracing egresses content** — demo traces non-confidential test PRDs only.
4. **1 GB box is deliberately tight** — build the image off-box; resize up is the documented remedy.
