# Epic & Story Tracker — LeapXpert_AgentA

**Source of truth for scope:** [../planning-artifacts/epics.md](../planning-artifacts/epics.md).
This file tracks *status only* — it never redefines a story. Re-read the story's Given/When/Then
acceptance criteria in `epics.md` before implementing it.

**Status legend:** `TODO` · `WIP` · `DONE` (code + tests pass) · `PARTIAL` (code done, verification
blocked) · `BLOCKED` (cannot start — see BLOCKERS.md)

**Order of work:** epic 1 → 6; within an epic, `critical-path` before `hardening`.

**Progress: 39 / 39 DONE in code; live-verified against the real tenant.** 469 offline tests pass, ruff clean, 5/5 import-linter contracts kept. **S2.4 classifier eval PASSED live (0 FP / 0 FN ×3).** **S6.4 ran the FULL flow live to `complete`** — detect → classify → AMS-11 → draft → UDR-1 → 2 PM feedback rounds → PASS → AMS-12 → Head of Product approval → move + export. Two caveats: FR-15 step 1 (edit restriction) is skipped by decision D-21 (Confluence Free has no restrictions, B-7), and the **webhook ingress layer has only ever run offline** — the live demo was driven by `scripts/run_local_demo.py`. Closing S6.4 fully needs the Droplet deploy (B-4/B-5).

| | Epic | Stories | Done |
|---|---|---|---|
| 1 | Multi-Tenant Foundation & Deployable Skeleton | 10 | **10 ✅** |
| 2 | PRD Detection & Confirmation | 8 | **8 ✅** |
| 3 | UserDoc Authoring & Draft Publication | 5 | **5 ✅** |
| 4 | Human Review & Revision Loop | 6 | **6 ✅** |
| 5 | Approval & Publishing | 3 | **3 ✅** |
| 6 | Resilience, Recovery & Operations | 7 | **6 ✅ + 1 partial** |

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
| 1.8 | `ConfluenceAdapter` + markdown converter (v2 default, v1 move/restrictions) | critical-path | AD-7, AD-14 | **DONE** | `app/adapters/{confluence,markdown}.py`; `tests/test_confluence_adapter.py` — 35 tests. v1 move/append + v1 restrictions; markdownify with an Atlassian `ac:`/`ri:` normalization pass. |
| 1.9 | In-invocation LangGraph orchestrator, stage machine, serial queue | critical-path | AD-6, AD-11, AD-2, AD-5 | **DONE** | `app/orchestrator/{stages,graph,runner}.py`; `tests/test_orchestrator.py` — 25 tests incl. gate-stop, per-stage persistence, error checkpointing, and a serial-queue concurrency probe. |
| 1.10 | LangSmith tracing harness for all LLM calls | critical-path | AD-20 | **DONE** | `app/agents/{llm,tracing}.py`; `tests/test_llm_client.py` — 26 tests. Tracing is structural: the span wraps the request inside the only module allowed to import the Anthropic SDK (asserted by test). |

## Epic 2 — PRD Detection & Confirmation

| ID | Story | Tag | Governed by | Status | Evidence |
|---|---|---|---|---|---|
| 2.1 | Detect a new PRD page in the watched source folder | critical-path | AD-8, AD-9, AD-14, AD-10 | **DONE** | `app/agents/detection.py`; folder check + ancestors fallback. |
| 2.2 | Title-gate on the `final_PRD_<name>` pattern | critical-path | AD-8 | **DONE** | `matches_prd_title`; mismatch routes to rename, not dropped. |
| 2.3 | Classifier agent confirms a genuine finalized PRD | critical-path | AD-17 | **DONE** | `app/agents/classifier/{agent,SKILL.md}.py`; model from config, temp 0, JSON parse. |
| 2.4 | Classifier held-out fixtures + ×3 eval harness (0-FP / 0-FN bar) | critical-path | AD-17 | **DONE ✅ (live PASS)** | `fixtures/classifier/{dev,holdout}`, `evaluation.py`, `scripts/run_classifier_eval.py`. **Live: 0 FP / 0 FN, stable ×3 on the holdout set (15 classifications, claude-sonnet-5).** |
| 2.5 | Locate-or-create the PRD-tracking ticket and drive it to Done | critical-path | AD-13 | **DONE** | `app/agents/ticket_manager.py`; adopt-orphan → search → create; AD-13 skip/direct/multi-hop/escalate. |
| 2.6 | Title-mismatch / REJECT rename-request task and clean re-entry | hardening | AD-12, AD-9 | **DONE** | `create_rename_request` in Review project; self-park + re-upload re-entry (EH-04). |
| 2.7 | Self-ingestion defense-in-depth (label + agent-account exclusion) | hardening | AD-10 | **DONE** | label + agent-account checks; account resolved once per tenant and cached. |
| 2.8 | Cross-org identity fallback for rename-task assignment | hardening | AD-12 | **DONE** | `app/agents/identity.py`; override → same-org → email-match → unresolved. |

## Epic 3 — UserDoc Authoring & Draft Publication

| ID | Story | Tag | Governed by | Status | Evidence |
|---|---|---|---|---|---|
| 3.1 | Author agent drafts the first UserDoc from the PRD | critical-path | AD-17 | **DONE** | `app/agents/author/{agent,SKILL.md}`; structure via prompt, model from config. |
| 3.2 | Author self-critique pass (draft → critique → one revision) | critical-path | AD-17 | **DONE** | one draft+critique call each; drafting aid only, not a gate (test asserts exactly 2 LLM calls). |
| 3.3 | Publish the draft to the Confluence draft folder (idempotent, self-stamped) | critical-path | AD-14, AD-10, AD-11 | **DONE** | `app/agents/publisher.py` + `markdown_to_storage`; create/adopt/reuse; v1 move; label + prd_id stamp. |
| 3.4 | Create the Review ticket assigned to the Reviewer PM | critical-path | AD-11, AD-15 | **DONE** | `TicketManager.create_review_ticket` + find-or-create by marker; parks at awaiting_review. |
| 3.5 | Post the framed review-request comment | critical-path | AD-7, AD-15 | **DONE** | `app/agents/review_request.py`; real @mention, §6.2 format, users'-shoes, Done-only rule (all asserted). |

## Epic 4 — Human Review & Revision Loop

| ID | Story | Tag | Governed by | Status | Evidence |
|---|---|---|---|---|---|
| 4.1 | Ingest PM feedback and route via a typed `FeedbackDecision` | critical-path | AD-16 | **DONE** | `app/domain/feedback.py`, `app/agents/feedback_interpreter/`, `app/orchestrator/feedback_routing.py` (pure, unit-tested on hand-built decisions). |
| 4.2 | Apply structured feedback to produce a revised draft | critical-path | AD-16, AD-20 | **DONE** | `handlers_review.on_revising`; revise→update→summary→re-request; `review_round++`; uncapped, needs fresh comment each round (tested). |
| 4.3 | Detect PASS on the Reviewer PM's Done transition | critical-path | AD-15 | **DONE** | `Orchestrator.apply_gate_done`; matches the Review ticket key; agent never transitions it. |
| 4.4 | Structure-confirmation sub-loop for plain-language feedback | hardening | AD-16 | **DONE** | restate + park `awaiting_structure_confirm`; blocks until confirm (EH-08). |
| 4.5 | Bounded clarification sub-loop (four enumerated triggers only) | hardening | AD-16 | **DONE** | `ClarificationTrigger` enum closes the list; CLARIFY without a trigger is rejected at construction (EH-08). |
| 4.6 | Late-feedback-after-Done ignored; non-Done transitions park | hardening | AD-15 | **DONE** | comment outside review stages is a no-op (EH-06); gate-Done on the wrong ticket ignored (EH-09). |

## Epic 5 — Approval & Publishing

| ID | Story | Tag | Governed by | Status | Evidence |
|---|---|---|---|---|---|
| 5.1 | Confirm PASS and create the Publishing ticket for the Head of Product | critical-path | AD-11, AD-15 | **DONE** | `handlers_publishing.on_passed`; confirm comment + find-or-create Publishing ticket; parks at awaiting_publish_approval. |
| 5.2 | Head of Product publish gate | critical-path | AD-15 | **DONE** | `Orchestrator.apply_gate_done` matches `publishing_ticket_key`; wrong ticket / no action → park (no timeout). |
| 5.3 | Ordered, idempotent publish transaction (restrict / move / export / complete) | critical-path | AD-18, AD-14, AD-10 | **DONE** | `Publisher.publish` — 4 ordered side-effects, each `*_done`-guarded; restriction always includes the agent account; overwrite-safe export. |

## Epic 6 — Resilience, Recovery & Operations

| ID | Story | Tag | Governed by | Status | Evidence |
|---|---|---|---|---|---|
| 6.1 | Error surfacing and admin resume from checkpoint | hardening | AD-19, AD-11 | **DONE** | `app/agents/error_handler.py`; one EH-01 comment on the relevant ticket; `apply_admin_resume` re-runs `last_good_checkpoint` only. |
| 6.2 | Reconciliation & liveness sweep (dropped-gate-webhook recovery) | hardening | AD-22, AD-2, AD-15 | **DONE** | `app/admin/{reconciler,endpoint,wiring}.py`; alert-once + gate reconcile-poll fed as input (never a stage write); cron → localhost. |
| 6.3 | Off-box state backup / disaster recovery | hardening | AD-23 | **DONE (artifact)** | `deploy/litestream.yml` + restore procedure in `deploy/README.md`. Live replication to DO Spaces needs B-4. |
| 6.4 | Deploy to the reachable 1 GB host and run the end-to-end demo | critical-path | AD-21 | **PARTIAL (full flow live ✅ via driver; webhook path not deployed)** | The complete flow ran live to `complete` against the real tenant, incl. both human gates and 2 feedback rounds — but driven by `scripts/run_local_demo.py` standing in for webhooks. `deploy/` + CI are built and unused. Remaining: Droplet deploy + webhook registration so a real Atlassian delivery starts a run, then one clean run (B-4/B-5). FR-15 step 1 skipped per D-21/B-7. |
| 6.5 | 1 GB memory-envelope hardening | hardening | AD-21 | **DONE (artifact)** | slim base, single worker, non-root, swap, no co-located DB, one PRD resident (AD-5). Live measurement is part of the S6.4 run. |
| 6.6 | Config-only modifiability verification (2nd project; swap identities) | hardening | AD-4 | **DONE** | `test_operations.py` proves a 2nd tenant routes + a PM swap is one field; NFR-05 grep test guards literal isolation. |
| 6.7 | Content-gating observability flag and data-governance seam | hardening | AD-20 | **DONE** | `trace_content` flag; metadata-only by default, content never egressed unless opted in (tested). |

---

## Post-readiness hardening additions (Nhan requests, 2026-07-25/26)

Features added after the readiness report, each spec'd into the PRD + Spine and fully tested offline.

| ID | Feature | Governed by | Status | Evidence |
|---|---|---|---|---|
| H-1 | Conversational review loop (interpreter gets transcript + memory) | FR-10a, D-30 | **DONE** | `interpret_comment` + `_review_conversation`; conversation-aware routing tests in `test_review_loop.py`. |
| H-2 | Rename-churn guard + source-folder admission gate | FR-01a, AD-24, D-35 | **DONE** | `_dispatch_page` churn guard; `test_webhook_dispatch.py` rename-after-drafting tests. |
| H-3 | Draft-deletion detection + human-gated recovery | FR-16, AD-25, D-36/D-38 | **DONE** | `apply_draft_deleted`/`apply_deletion_decision`; `test_draft_recovery.py`. **Needs the *Page trashed* Automation rule live.** |
| H-4 | Tracking-ticket search: don't adopt another run's same-named ticket | FR-04, D-39 | **DONE** | `_search_by_name` excludes `agent-generated`; typed marker search; `test_ticket_manager.py`. |
| H-5 | Inline-comment feedback channel | FR-17, AD-26, D-40 | **DONE (code+tests); live-activation pending** | `ConfluenceCommentEvent` + `get_inline_comment` + `apply_inline_comment`; `test_inline_comment.py` (20 tests). **Needs the *Page commented* Automation rule (SETUP-GUIDE 7c) + redeploy.** |

---

## Carry-forward watch items (from the readiness report §7)

1. **Classifier accuracy is the one hard, measurable gate** (0 FP / 0 FN on *holdout*, S2.4). Riskiest
   single deliverable — front-load the fixture sets.
2. **EH-09 is intentionally out of scope** — non-Done gate transitions and stalls simply park, no timeout.
3. **LangSmith tracing egresses content** — demo traces non-confidential test PRDs only.
4. **1 GB box is deliberately tight** — build the image off-box; resize up is the documented remedy.
