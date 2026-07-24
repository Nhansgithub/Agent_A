---
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epics
  - step-03-create-stories
  - step-04-final-validation
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-LeapXpert_AgentA-2026-07-23/prd.md
  - _bmad-output/planning-artifacts/architecture/architecture-LeapXpert_AgentA-2026-07-23/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/architecture/architecture-LeapXpert_AgentA-2026-07-23/solution-design.md
generation: headless (bmad-create-epics-and-stories, non-interactive)
scope: DEMO with FULL HARDENING
---

# LeapXpert_AgentA (PRD-to-UserDoc Automation Agent Flow) - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for **LeapXpert_AgentA**, the multi-tenant PRD-to-UserDoc automation agent flow, decomposing the requirements from the PRD (v0.3) and the Architecture Spine (r2) into implementable stories.

**System in one line:** a multi-tenant Python service where a Confluence webhook detects a finalized PRD, a LangGraph-orchestrated pipeline of role-agents drafts an end-user help doc, drives a human review loop in Jira, waits for two human approvals (Reviewer PM PASS, then Head of Product), then restricts + moves + exports the doc. FastAPI + LangGraph MIT core (in-invocation only) + direct Atlassian REST + SQLite via a repository. Jira/Confluence are the entire human interface (no GUI).

**Scope decision (explicit):** DEMO with FULL HARDENING. The single happy-path end-to-end run is the demo's success metric (PRD §3, §12) and defines the critical path. Full robustness/edge/ops handling (AD-9 dedupe, AD-10 self-ingestion guard, AD-12 cross-org identity fallback, AD-13 multi-hop transitions, AD-18 idempotent publish, AD-19 error+resume, AD-22 reconciler/liveness, AD-23 off-box backup, EH-01..09) is IN scope as buildable stories.

**Story tagging:** every story is tagged `critical-path` (needed for the first successful end-to-end run) or `hardening` (robustness / edge / ops) so sprint-planning can order the backlog.

**Preserved invariants (must hold across all stories):** the two human gates are never auto-transitioned by the agent (AD-15); durable state is mutated only via the repository and `stage` is advanced only by the orchestrator (AD-2); no project-specific literal appears outside the config registry (AD-4, grep-clean); the running service fits the 1 GB memory envelope (AD-21).

## Requirements Inventory

### Functional Requirements

- **FR-01** — Detect new PRD: receive Confluence `page-created` webhooks for configured source folder(s); resolve tenant via config registry (source folder → project); self-ingestion guard so the agent's own output is never re-detected.
- **FR-02** — Title gate: treat a page as a candidate PRD only if its title matches `final_PRD_<name>`.
- **FR-02a** — Title mismatch: create a separate rename-request task ticket in the Review project assigned to the Uploading PM (page-creator, resolved at runtime), and do not process further until a matching page-created/updated event arrives.
- **FR-03** — LLM PRD confirmation: the Classifier agent reads the page and confirms a genuinely finalized PRD (ACCEPT/REJECT rubric); ship a labeled fixture set and be correct on 100% (0 FP / 0 FN).
- **FR-04** — Locate or create the PRD-tracking ticket (Main project) and transition it to Done; search must not assume a fixed location; skip if already Done; resolve the legal transition path at runtime.
- **FR-05** — Generate the first UserDoc draft: the Author agent drafts a tailored end-user guide (structure via prompt + SKILL.md, no fixed template) with one lightweight self-critique pass. Content-quality acceptance is solely the human PM PASS (FR-12).
- **FR-06** — Publish draft to Confluence draft/review folder + create a Review ticket in the Review project assigned to the Reviewer PM, linking the draft.
- **FR-07** — Request review with framing: comment tags the PM, requests the exact structured feedback format (§6.2), asks the PM to adopt the users' POV, and states that transitioning the Review ticket to Done is the only pass signal.
- **FR-08** — PM clarification sub-loop: ask a clarifying question and block on the human answer ONLY when one of four enumerated triggers holds; otherwise proceed with a stated assumption.
- **FR-09** — Ingest PM feedback: on a new PM comment, parse it; structured → FR-11; plain language → FR-10.
- **FR-10** — Structure-confirmation sub-loop: convert plain-language feedback to the structured format, ask the PM to confirm, and block until they confirm before applying.
- **FR-11** — Apply feedback → new draft: revise the UserDoc, update the draft page, post a change summary, re-request review; loop uncapped (each round requires a fresh human comment).
- **FR-12** — Detect PASS: interpret the Reviewer PM transitioning the Review ticket to Done as the sole PASS/approval signal; if the PM neither comments nor transitions, park at `awaiting_review` indefinitely (no timeout).
- **FR-13** — Confirm pass + create Publishing ticket in the Main project for the Head of Product.
- **FR-14** — Head of Product publish gate: wait for the Publishing ticket to transition to Done (sole approval signal); park at `awaiting_publish_approval` indefinitely otherwise.
- **FR-15** — Publish on approval: apply Confluence edit restrictions, move the page to the Published-UserDocs folder (adjacent to source), export the doc as Markdown to server storage, mark the flow Complete.

### NonFunctional Requirements

- **NFR-01** — Observability: 100% of LLM steps traced in LangSmith (latency, speed, token cost); demo traces non-confidential test PRDs only. (Must)
- **NFR-02** — Modifiability: reviewers/assignees and all project locations are config-only changes, no code edits. (Must)
- **NFR-03** — Portability: state access via a repository layer so SQLite → Postgres is a single-module change; no raw SQL elsewhere. (Must)
- **NFR-04** — Idempotency: every webhook handler idempotent; dedupe key = event id + monotonic content/version marker, so duplicates are suppressed but a real rename/update re-enters. (Must)
- **NFR-05** — Isolation of config: no project-specific literal outside config; source tree grep-clean. (Must)
- **NFR-06** — Concurrency: demo processes one PRD at a time (serial queue); design must not preclude later parallelism. (Should)
- **NFR-07** — Portability of runtime: single Docker image, 12-factor; per-project instance = image + its `.env`; nothing project-specific baked in. (Should)
- **NFR-08** — Resilience: transient API failures retried with backoff (~3 tries) before escalating. (Should)
- **NFR-09** — Cost control: Anthropic usage visible per run via LangSmith; no loop self-spins; `review_round` + per-round cost surfaced. (Should)
- **NFR-10** — Licensing hygiene: only the MIT-licensed LangGraph core; no dependency on the licensed `langgraph-api` server product. (Should)
- **NFR-11** — Memory footprint: run stable within a 1 GB RAM host. (Must)

### Additional Requirements

Technical / architectural requirements (from ARCHITECTURE-SPINE r2 and PRD §9-§15) that shape stories:

- **Starter template:** none. Greenfield build. The architecture provides a "cold-start scaffold" source tree (`app/{webhooks,router,orchestrator,agents,adapters,repository,config,domain,admin}`, `fixtures/`, `deploy/`, `tests/`). Epic 1 Story 1 stands up this scaffold — it is NOT a third-party starter template.
- **Layered boundaries (AD-1):** inward-only dependencies; only adapters touch Atlassian, only the repository touches SQLite.
- **Single durable store (AD-2, AD-11):** one repository-owned SQLite state record is the single authoritative durable truth; `stage` is an explicit §9 enum advanced only by the orchestrator; LangGraph is in-invocation control-flow only (ephemeral `InMemorySaver`), never a durable store. Resume = idempotent-create replay of the failed §9 stage.
- **Adapters (AD-7):** `JiraAdapter` (v3, ADF bodies), `ConfluenceAdapter` (v2 default + v1 for move/restrictions), `markdown` (storage→md via markdownify); domain verbs, env-ref auth, retry-with-backoff, `AgentError` normalization.
- **Dedupe (AD-9):** composite key `<tenant>:<event_type>:<entity_id>:<version_marker>` in a repository-owned `processed_events` table (UNIQUE constraint); recorded at flow admission, not on mere receipt.
- **Config registry (AD-4, §11):** per-tenant folders/project-keys/identities/credential-refs/`md_export_dir`; the only home for project literals; credentials by env reference.
- **Orchestration/runtime (AD-6):** LangGraph MIT core behind a self-built FastAPI wrapper; six role-agents (Classifier, Ticket manager, Author, Feedback interpreter, Publisher, Error handler) as graph nodes with per-role prompt + SKILL.md over one shared runtime; all LLM via the Anthropic Python SDK.
- **Typed routing (AD-16):** Feedback interpreter returns a typed `FeedbackDecision{route, trigger, assumption}` in `app/domain/`; orchestrator stage-routing is deterministic + unit-tested; only the decision-producing LLM is eval-tested.
- **Classifier eval (AD-17):** `fixtures/classifier/{dev,holdout}/`; dev tunes the prompt; the 0-FP/0-FN bar applies to holdout only (no train-on-test); eval runs ×3 emitting a confusion matrix + flake budget; classifier model id pinned in config. Both fixture sets are build deliverables.
- **Deploy envelope (AD-21, §15):** single slim Docker image built OFF the 1 GB box and pulled; single Uvicorn worker bound to localhost behind Caddy (TLS/Let's Encrypt); firewall opens only 443 + 22; 1-2 GB swap; no co-located DB server; one PRD resident in memory. Host: DO Droplet 1 GB / 1 vCPU / 25 GB, Ubuntu LTS. 1 GB is reversible (resize up if the §12 run OOMs).
- **Reconciler/liveness (AD-22):** lightweight scheduled sweep (default: system `cron` → authenticated localhost `/admin` endpoint) that alerts on stale parked/error runs and reconcile-polls the two gate tickets, feeding a found Done as an input (never writing `stage`).
- **Backup/DR (AD-23):** the single SQLite store replicated off-box (default: `litestream` WAL → DO Spaces; alternative: hourly `sqlite3 .backup` + tar). Point-in-time restore. Ships for the demo.
- **Stack (pin at build):** Python 3.12-slim, FastAPI 0.136.3, Uvicorn 0.51.0, langgraph 1.2.9 (MIT core), langgraph-checkpoint 4.1.1 (InMemorySaver), anthropic 0.117.0, langsmith 0.10.9, markdownify 1.2.3, Caddy 2.11.4, litestream 0.5.15, stdlib sqlite3; Jira v3, Confluence v2 (+v1 for move/restrictions).

Error-handling & edge requirements (PRD §8) that must be covered by stories:

- **EH-01** error surfacing; **EH-02** admin resume (`@agent resume`/`fixed` → re-run failed step from checkpoint); **EH-03** title mismatch (→ FR-02a); **EH-04** rename re-trigger; **EH-05** concurrency/queue; **EH-06** late feedback after Done ignored; **EH-07** ambiguous/empty PRD (→ FR-02a); **EH-08** clarification/structure loops never auto-advance; **EH-09** non-Done gate transitions & indefinite stalls (park, no timeout).

### UX Design Requirements

**Not applicable.** This system has no GUI or bespoke UX surface — Jira and Confluence are the entire human interface (PRD §1, §4). All human interaction is via Jira tickets/comments/transitions and Confluence pages; the "interaction design" that exists (comment framing, structured feedback format, Done-only gate rule) is captured as functional requirements (FR-07, §6.2, FR-12/14) and their stories, not as separate UX-DRs. No UX design contract was found or is required.

### FR Coverage Map

**FR → Epic (every FR mapped):**

- FR-01: Epic 2 (Story 2.1) — detect PRD in watched folder + self-ingestion structural guard
- FR-02: Epic 2 (Story 2.2) — title gate `final_PRD_<name>`
- FR-02a: Epic 2 (Story 2.6) — rename-request task to Uploading PM
- FR-03: Epic 2 (Stories 2.3, 2.4) — Classifier confirmation + held-out eval
- FR-04: Epic 2 (Story 2.5) — PRD-tracking ticket → Done
- FR-05: Epic 3 (Stories 3.1, 3.2) — first draft + self-critique
- FR-06: Epic 3 (Stories 3.3, 3.4) — draft page + Review ticket
- FR-07: Epic 3 (Story 3.5) — framed review request
- FR-08: Epic 4 (Story 4.5) — bounded clarification sub-loop
- FR-09: Epic 4 (Story 4.1) — ingest + route feedback
- FR-10: Epic 4 (Story 4.4) — structure-confirmation sub-loop
- FR-11: Epic 4 (Story 4.2) — apply feedback → new draft
- FR-12: Epic 4 (Story 4.3) — detect PASS
- FR-13: Epic 5 (Story 5.1) — confirm pass + Publishing ticket
- FR-14: Epic 5 (Story 5.2) — Head of Product publish gate
- FR-15: Epic 5 (Story 5.3) — publish transaction (restrict/move/export/complete)

**NFR → Epic:** NFR-01 → E1(1.10)/E6(6.7); NFR-02 → E1(1.2)/E6(6.6); NFR-03 → E1(1.3); NFR-04 → E1(1.5)/E2(2.6); NFR-05 → E1(1.2)/E6(6.6); NFR-06 → E1(1.9)/E6(6.5); NFR-07 → E1(1.1)/E6(6.4); NFR-08 → E1(1.7,1.8); NFR-09 → E1(1.10)/E4(4.2); NFR-10 → E1(1.1); NFR-11 → E6(6.4,6.5).

**EH → Epic:** EH-01/02 → E6(6.1); EH-03/04/07 → E2(2.6); EH-05 → E1(1.9); EH-06 → E4(4.6); EH-08 → E4(4.4,4.5); EH-09 → E4(4.6)/E5(5.2)/E6(6.2).

**AD → Epic (governance trace):** AD-1 → E1(1.1); AD-2 → E1(1.3,1.9); AD-3 → E1(1.6); AD-4 → E1(1.2),E6(6.6); AD-5 → E1(1.9); AD-6 → E1(1.1,1.9); AD-7 → E1(1.7,1.8); AD-8 → E1(1.4,1.5),E2(2.1); AD-9 → E1(1.5),E2(2.6); AD-10 → E2(2.1,2.7),E3(3.3),E5(5.3); AD-11 → E1(1.3,1.9),E3(3.3,3.4),E5(5.1),E6(6.1); AD-12 → E2(2.6,2.8); AD-13 → E2(2.5); AD-14 → E1(1.8),E2(2.1),E3(3.3),E5(5.3); AD-15 → E3(3.4),E4(4.3),E5(5.1,5.2); AD-16 → E4(4.1,4.2,4.4,4.5); AD-17 → E2(2.3,2.4),E3(3.1,3.2); AD-18 → E5(5.3); AD-19 → E6(6.1); AD-20 → E1(1.10),E6(6.7); AD-21 → E1(1.1),E6(6.4,6.5); AD-22 → E6(6.2); AD-23 → E6(6.3).

## Epic List

### Epic 1: Multi-Tenant Foundation & Deployable Skeleton
Stand up the deployable, observable, multi-tenant substrate — a walking skeleton that validates + dedupes + routes a webhook, persists run state through the repository, round-trips Atlassian and LLM calls, and advances explicit stages — so every downstream feature builds on a correct, config-driven, invariant-preserving base.
**FRs covered:** (enabling substrate for all FRs; no user-facing FR completed alone) · **NFRs:** NFR-01,02,03,04,05,06,07,08,09,10 · **Mostly:** critical-path (10/10).

### Epic 2: PRD Detection & Confirmation
When a finalized PRD lands in a watched folder, the system detects it, gates by title, LLM-confirms it is a genuine PRD (with a measured 0-FP/0-FN bar), records/creates-and-Dones the tracking ticket, and handles mislabels via a rename request that re-enters cleanly.
**FRs covered:** FR-01, FR-02, FR-02a, FR-03, FR-04 · **Mix:** critical-path (5) + hardening (3).

### Epic 3: UserDoc Authoring & Draft Publication
From a confirmed PRD, the Author drafts a tailored end-user UserDoc with one self-critique pass, publishes it to the Confluence draft folder, creates the Review ticket assigned to the Reviewer PM, and posts the framed review request — parking the flow at `awaiting_review`.
**FRs covered:** FR-05, FR-06, FR-07 · **Mostly:** critical-path (5/5).

### Epic 4: Human Review & Revision Loop
Drive the Reviewer PM feedback loop to PASS: ingest structured or plain feedback via a typed decision, run the human-blocking structure-confirmation and bounded-clarification sub-loops, revise with a change summary and re-request, and detect the PM's Done transition as the sole PASS signal — never self-advancing.
**FRs covered:** FR-08, FR-09, FR-10, FR-11, FR-12 · **Mix:** critical-path (3) + hardening (3).

### Epic 5: Approval & Publishing
On PASS, create the Publishing ticket for the Head of Product, wait for their Done approval (never auto-transitioned), then run the ordered, per-side-effect-idempotent publish transaction — restrict edit, move to the published folder, export Markdown — and mark the flow Complete.
**FRs covered:** FR-13, FR-14, FR-15 · **Mostly:** critical-path (3/3).

### Epic 6: Resilience, Recovery & Operations
Harden the running system: structured error surfacing with admin resume, a liveness/reconcile sweep for dropped gate webhooks, off-box state backup, the 1 GB deploy envelope on the target host (the reachable endpoint the demo needs), and verification of the config-only-modifiability promise.
**FRs covered:** (cross-cutting; realizes the end-to-end demo run) · **NFRs:** NFR-01,02,05,06,07,11 · **Mostly:** hardening (6) + critical-path (1: the reachable deploy the demo run requires).

<!-- Repeat for each epic in epics_list (N = 1, 2, 3...) -->

## Epic 1: Multi-Tenant Foundation & Deployable Skeleton

Stand up the deployable, observable, multi-tenant substrate — a walking skeleton that validates + dedupes + routes a webhook, persists run state through the repository, round-trips Atlassian and LLM calls, and advances explicit stages. This epic delivers no end-user FR alone but is demonstrable: a signed webhook can be sent and observed to dedupe, resolve a tenant, persist a state row, and emit a trace; adapters round-trip a real Atlassian call. Every downstream epic depends only on this one.

### Story 1.1: Project scaffold, layered module skeleton, and pinned dependency manifest

As the operator of the multi-tenant service,
I want the layered application scaffold and pinned dependencies stood up exactly as the architecture prescribes,
So that all subsequent work has a correct, license-clean, inward-only-dependency base to build on.

**Tag:** critical-path
**Traces:** NFR-07, NFR-10 · **Governed by:** AD-1, AD-6

**Acceptance Criteria:**

**Given** a fresh clone of the repository
**When** the project is initialized
**Then** the `app/` tree exists with `webhooks/`, `router.py`, `orchestrator/`, `agents/`, `adapters/`, `repository/`, `config/`, `domain/`, `admin/`, plus `fixtures/classifier/{dev,holdout}/`, `deploy/`, and `tests/`
**And** the dependency manifest pins the MIT-licensed `langgraph` core with NO dependency on `langgraph-api` / the `langgraph dev|build` server product (NFR-10)
**And** import-linting or an equivalent check enforces inward-only dependencies: `agents/` and `orchestrator/` must not import an HTTP client or a DB driver directly (AD-1).

**Given** the pinned manifest
**When** the versions are checked against the architecture Stack table
**Then** Python 3.12-slim, FastAPI, Uvicorn, langgraph core, langgraph-checkpoint (InMemorySaver base), anthropic SDK, langsmith, markdownify, and sqlite3 (stdlib) are present at compatible pins.

### Story 1.2: Per-tenant config registry, tenant-config schema, and env-ref secrets

As the operator onboarding projects,
I want every project-specific value to live in one config registry loaded as a tenant-config object, with credentials supplied only by environment reference,
So that swapping a reviewer or adding a project is a config-only change and the source tree stays free of project literals.

**Tag:** critical-path
**Traces:** NFR-02, NFR-05, §11 · **Governed by:** AD-4

**Acceptance Criteria:**

**Given** a config registry with one tenant entry
**When** the loader parses it
**Then** it produces a validated tenant-config object exposing `confluence_source_folder_id`, `confluence_draft_folder_id`, `confluence_published_folder_id`, `jira_main_project_key`, `jira_review_project_key`, `pm_account_id`, `head_of_product_account_id`, `admin_account_id`, `md_export_dir`, and credential references (`jira_credentials_ref`, `confluence_credentials_ref`)
**And** credentials are resolved from environment references, never read inline from config or code (§11, AD-4).

**Given** the whole source tree (code, prompts, SKILL.md files)
**When** a grep for known project literals (project keys, space/folder ids, account ids) is run
**Then** no such literal appears outside the config registry (NFR-05, grep-clean) — the reserved system label `agent-generated` is the only allowed cross-tenant constant.

### Story 1.3: Repository + single SQLite store with the state record and stage enum

As a developer building the pipeline,
I want the per-PRD state record and the §9 stage enum owned exclusively by a repository over a single SQLite store,
So that there is one durable truth for run state and SQLite → Postgres is a single-module change.

**Tag:** critical-path
**Traces:** NFR-03, §9, §10 · **Governed by:** AD-2, AD-11

**Acceptance Criteria:**

**Given** the repository module
**When** a state record is created and read back
**Then** it carries `prd_id`, `project_id`, `stage`, `review_ticket_key`, `prd_tracking_ticket_key`, `publishing_ticket_key`, `userdoc_page_id`, `review_round`, `pending_gate`, `last_good_checkpoint`, `md_export_path`, and timestamps (§10)
**And** `stage` is a snake_case enum with exactly the §9 values (`detected`, `confirmed`, `prd_ticket_done`, `drafted`, `awaiting_review`, `awaiting_clarification`, `awaiting_structure_confirm`, `revising`, `passed`, `awaiting_publish_approval`, `publishing`, `complete`, `error`).

**Given** any module outside the repository
**When** the codebase is inspected
**Then** no raw SQL exists outside the repository and all state access goes through repository methods (NFR-03, AD-2)
**And** the SQLite file is the single durable store (there is no second durable store; AD-11).

### Story 1.4: Webhook ingress with shared-secret / signature validation

As the operator exposing a public endpoint,
I want every inbound Atlassian webhook validated by a shared-secret/signature check before any processing,
So that the endpoint that triggers real Jira/Confluence writes cannot be spoofed.

**Tag:** critical-path
**Traces:** FR-01, §15.4 (webhook auth) · **Governed by:** AD-8

**Acceptance Criteria:**

**Given** one public HTTPS webhook entrypoint accepting Confluence page-created/updated and Jira comment-created / issue-updated events
**When** a request arrives with a valid signature/shared secret
**Then** it passes validation and proceeds to the dedupe check (Story 1.5)
**And** a request with a missing or invalid signature is dropped with no side effects and no state write (AD-8 step 1).

**Given** the ordered ingress pipeline
**When** a valid request is processed
**Then** the order is strictly validate → dedupe → route (AD-8), with work beginning only after a tenant is resolved.

### Story 1.5: Idempotency — processed_events table and composite dedupe key recorded at admission

As a developer relying on correct-once processing,
I want a composite dedupe key stored in a repository-owned `processed_events` table with a UNIQUE constraint, recorded at flow admission,
So that the common Jira/Confluence duplicate deliveries never double-process while a genuine update can still re-enter.

**Tag:** critical-path
**Traces:** NFR-04 · **Governed by:** AD-9, AD-8

**Acceptance Criteria:**

**Given** two deliveries of the same event
**When** each is ingested
**Then** the composite key `<tenant_id>:<event_type>:<entity_id>:<version_marker>` is derived — Confluence page → (page id, `version.number`); Jira comment-created → (comment id); Jira issue-updated → (issue key, changelog history id)
**And** the second delivery loses the UNIQUE-insert race on `processed_events` and is dropped safely (NFR-04, AD-9).

**Given** a page-created event that arrives before any PRD row exists
**When** it is admitted
**Then** the key lives in the per-tenant `processed_events` table (NOT nested in a PRD row) and is recorded transactionally with the first state write that admits the PRD (the `detected` checkpoint) — so a crash before admission leaves the event safely redeliverable (AD-9)
**And** `processed_events` is the single authoritative dedupe store; any §10 `dedupe_keys` projection is read-only.

### Story 1.6: Route-before-work tenant resolution

As the operator of a multi-tenant service,
I want every inbound event mapped to exactly one tenant via the config registry before any work happens,
So that no step ever runs without a resolved tenant and one tenant's flow can never touch another's resources.

**Tag:** critical-path
**Traces:** FR-01 (tenant routing) · **Governed by:** AD-3

**Acceptance Criteria:**

**Given** a validated, deduped event
**When** the router runs
**Then** it resolves exactly one tenant from the config registry (source folder / project key → tenant) before any downstream work (AD-3)
**And** the resolved tenant-config object is threaded through the whole flow so no step accesses a resource for a tenant it was not handed.

**Given** an event that resolves to no configured tenant
**When** routing runs
**Then** the event is dropped without side effects (no work begins).

### Story 1.7: JiraAdapter with domain verbs, ADF bodies, retry, and error normalization

As a developer,
I want all Jira access behind a single `JiraAdapter` exposing domain verbs, building ADF bodies, retrying transient failures, and normalizing errors,
So that no agent writes raw HTTP and retry/auth/ADF rules are enforced in one place.

**Tag:** critical-path
**Traces:** NFR-08 · **Governed by:** AD-7

**Acceptance Criteria:**

**Given** the `JiraAdapter`
**When** its surface is inspected
**Then** it exposes domain verbs including `search_issue`, `get_transitions`, `transition_issue`, `add_comment(adf)`, `create_issue`, and `get_current_user` (`/myself`), targeting Jira REST v3, with token auth from an env reference (AD-7)
**And** every comment/description body is built as ADF (never a plain string), or Jira rejects it (AD-7, Consistency Conventions).

**Given** a transient API failure
**When** a call is made
**Then** the adapter retries with backoff (~3 tries, NFR-08) and, on final failure, raises a single normalized `AgentError` for the Error handler to consume.

### Story 1.8: ConfluenceAdapter + markdown converter (v2 default, v1 for move/restrictions)

As a developer,
I want all Confluence access behind a single `ConfluenceAdapter` with the correct API-version selection and a storage→Markdown converter,
So that folder placement, restrictions, labels, and reads are consistent and the known v2 folder-parent trap is avoided.

**Tag:** critical-path
**Traces:** NFR-08 · **Governed by:** AD-7, AD-14

**Acceptance Criteria:**

**Given** the `ConfluenceAdapter`
**When** its surface is inspected
**Then** it exposes `get_page`, `create_page`, `move_page`, `set_edit_restriction`, `get_label`/`add_label`, folder reads, and `storage_to_markdown`, defaulting to Confluence v2 but using v1 specifically for the move (`PUT /wiki/rest/api/content/{id}/move/append/{folderId}`) and content-restriction endpoints (AD-7, AD-14)
**And** placing a page into a folder never uses the v2 `parentId` path (which 500s for folder parents) (AD-14).

**Given** Confluence storage-format content
**When** `storage_to_markdown` runs
**Then** it converts via markdownify (subclassed for Atlassian `ac:`/`ri:` tags), with minor formatting loss acceptable (PRD §13 Q5)
**And** transient failures retry with backoff and normalize to `AgentError` (NFR-08, AD-7).

### Story 1.9: In-invocation LangGraph orchestrator, stage machine, and serial queue

As a developer,
I want an orchestrator that loads the state record, re-enters the LangGraph graph at the recorded stage, advances only stages that can progress without a new external event, persists the new stage, and stops — processing one PRD at a time,
So that the pipeline advances correctly and resumably with the repository as the sole durable truth.

**Tag:** critical-path
**Traces:** NFR-06, EH-05 · **Governed by:** AD-6, AD-11, AD-2, AD-5

**Acceptance Criteria:**

**Given** an admitted event for a PRD
**When** the orchestrator runs
**Then** it (1) loads the state record, (2) re-enters the graph at `stage`/`last_good_checkpoint` keyed `thread_id = prd_id`, (3) runs the stages that can advance without a new external event, (4) persists the new `stage` + any recorded ids through the repository in one transaction, and (5) stops (AD-11)
**And** `stage` is written ONLY by the orchestrator, never by a role-agent and never inferred from an Atlassian field (AD-2).

**Given** LangGraph is used
**When** the graph runs
**Then** its checkpointer is an in-memory `InMemorySaver` scoped to that one invocation and is discarded when the invocation ends — it is not a durable/cross-webhook store (AD-6, AD-11).

**Given** multiple valid PRDs arriving close together
**When** they are processed
**Then** they are queued and processed one at a time; the state store tracks queued vs in-progress; the per-PRD row (keyed by `prd_id`) is the isolation unit and no cross-PRD mutable singleton is held (NFR-06, EH-05, AD-5).

### Story 1.10: LangSmith tracing harness for all LLM calls

As the product owner watching cost and latency,
I want every LLM call traced in LangSmith with latency, tokens, cost, the run correlation id, and `review_round`,
So that per-step cost/latency/speed is observable for the run and no runaway loop is invisible.

**Tag:** critical-path
**Traces:** NFR-01, NFR-09 · **Governed by:** AD-20

**Acceptance Criteria:**

**Given** any LLM call anywhere in the pipeline
**When** it executes
**Then** it is traced in LangSmith with latency, tokens, and cost, carrying the run's correlation id and current `review_round` (NFR-01, NFR-09, AD-20) — 100% of LLM calls.

**Given** a completed run
**When** LangSmith is inspected
**Then** it shows per-step latency, speed, and cost for that run (PRD §12 DoD line).

## Epic 2: PRD Detection & Confirmation

When a finalized PRD lands in a watched folder, the system detects it, gates by title, LLM-confirms it is a genuine PRD (with a measured 0-FP/0-FN bar), records/creates-and-Dones the tracking ticket, and handles mislabels via a rename request that re-enters cleanly. Depends only on Epic 1.

### Story 2.1: Detect a new PRD page in the watched source folder

As the product team uploading a finalized PRD,
I want a page created in my project's watched source folder to be detected and admitted to the flow,
So that documentation work starts automatically without anyone kicking it off.

**Tag:** critical-path
**Traces:** FR-01 · **Governed by:** AD-8, AD-9, AD-14, AD-10 (structural guard)

**Acceptance Criteria:**

**Given** a Confluence `page-created` event that has passed validate → dedupe → route (Epic 1)
**When** detection runs
**Then** the page's folder/ancestor id is compared to the tenant `confluence_source_folder_id` and only a page in that folder is admitted (FR-01, AD-14)
**And** an admitted page creates the PRD state row at `stage = detected` in the same transaction that records the dedupe key (AD-9).

**Given** the published/draft folders are different, adjacent folder ids
**When** an agent-published page appears
**Then** it is never in the watched set and is not detected (primary structural self-ingestion guard, AD-10 (a)).

### Story 2.2: Title-gate candidate PRDs on the `final_PRD_<name>` pattern

As the product team,
I want only pages whose title matches `final_PRD_<name>` treated as candidate PRDs,
So that arbitrary pages in the folder do not spuriously start the flow.

**Tag:** critical-path
**Traces:** FR-02 · **Governed by:** AD-8

**Acceptance Criteria:**

**Given** a detected page
**When** the title gate runs
**Then** a title matching `final_PRD_<name>` advances toward confirmation (FR-02)
**And** a non-matching title does NOT advance to `confirmed` and is routed to the title-mismatch handling (Story 2.6) (FR-02a).

### Story 2.3: Classifier agent confirms a genuine finalized PRD

As the Reviewer PM,
I want the Classifier to read a title-matching page and confirm it is genuinely a finalized PRD before any drafting,
So that empty pages, bare templates, and mislabeled docs never produce a UserDoc.

**Tag:** critical-path
**Traces:** FR-03, EH-07 · **Governed by:** AD-17

**Acceptance Criteria:**

**Given** a title-matching page
**When** the Classifier agent evaluates it against the FR-03 rubric
**Then** it ACCEPTs only if the page has substantive prose, describes a product/feature (problem, solution/requirements, and/or scope), and reads as completed; and REJECTs empty/near-empty pages, unfilled templates (TODO/Lorem/placeholder), and non-PRD docs (FR-03)
**And** on ACCEPT the orchestrator advances `stage` to `confirmed`; on REJECT the page is routed to the title-mismatch/confirm-correct handling (Story 2.6), not guessed (EH-07).

**Given** the Classifier runs as a graph node
**When** it makes its LLM call
**Then** the call is traced in LangSmith (AD-20) and uses the classifier model id pinned in config (AD-17, AD-4).

### Story 2.4: Classifier held-out fixture set and ×3 evaluation harness (0-FP / 0-FN bar)

As the product owner,
I want a labeled fixture set split into dev and holdout, evaluated three times with a confusion matrix and flake budget,
So that the Classifier's accuracy is provably at the 0-FP/0-FN demo counter-metric without train-on-test.

**Tag:** critical-path (PRD §3 demo counter-metric + explicit build deliverable)
**Traces:** FR-03, PRD §3 counter-metric · **Governed by:** AD-17

**Acceptance Criteria:**

**Given** the build deliverables
**When** `fixtures/classifier/` is inspected
**Then** it contains a `dev/` and a `holdout/` set of labeled ACCEPT/REJECT example pages (a real finalized PRD, an empty page, a bare template, a mislabeled non-PRD; ~3-5 each), and the dev set is the only set used to tune the prompt (AD-17).

**Given** the evaluation harness
**When** it runs the Classifier against the holdout set three times
**Then** it emits a confusion matrix and a flake budget, and the acceptance bar is 0 false-positives and 0 false-negatives on the holdout set only (AD-17, PRD §3)
**And** the classifier model id used is the one pinned in config (AD-4).

### Story 2.5: Locate-or-create the PRD-tracking ticket and drive it to Done

As the product team,
I want the Main-project PRD-tracking ticket found (anywhere) or created at the top of the hierarchy and transitioned to Done via a legal path,
So that the PRD's tracking status reflects that documentation is underway, without assuming a fixed ticket location or a direct-to-Done transition.

**Tag:** critical-path
**Traces:** FR-04, PRD §13 Q1 · **Governed by:** AD-13

**Acceptance Criteria:**

**Given** a confirmed PRD
**When** the Ticket manager searches Jira across the configured project(s) by PRD name/link
**Then** an existing tracking ticket is reused (search assumes no fixed location); if none exists, a new ticket is created at the top of the Main project hierarchy (not a subtask) (FR-04)
**And** the created ticket carries the run correlation marker (`prd_id`) so a resume adopts the orphan rather than double-creating (AD-11).

**Given** a tracking ticket to drive to Done
**When** the transition runs
**Then** if `statusCategory.key == "done"` it is skipped (idempotent); else the legal transition set is read from the current status and a transition to a `done`-category status is taken; if none is directly available, the config-declared preferred path is traversed hop-by-hop, escalating to the admin only if no path is configured or a hop is illegal (FR-04, AD-13)
**And** "done-ness" is judged by `statusCategory == done`, not a literal status name (AD-13).

### Story 2.6: Title-mismatch / REJECT rename-request task and clean re-entry

As the Uploading PM (the page creator),
I want a separate rename-request task in the Review project when my page is mislabeled or not a real PRD, and I want my corrected re-upload to re-enter the flow,
So that mislabeled pages are corrected by a human without the agent guessing, and a rename is not lost or double-processed.

**Tag:** hardening
**Traces:** FR-02a, EH-03, EH-04, EH-07, NFR-04 · **Governed by:** AD-12, AD-9

**Acceptance Criteria:**

**Given** a page whose title does not match (FR-02) or that the Classifier REJECTs (FR-03/EH-07)
**When** the mismatch handler runs
**Then** it creates a small rename-request task ticket in the Review project (entirely separate from any draft-review ticket), assigned to the Uploading PM resolved from the Confluence page-creator `accountId` on the event (NOT the config Reviewer PM), asking them to confirm/rename to `final_PRD_...` (FR-02a, AD-12)
**And** the flow does not process the page further until a matching page-created/updated event arrives (FR-02a).

**Given** the page-creator `accountId` is used directly as the same-org Jira assignee
**When** assignment runs
**Then** no mapping table is used for the same-org case (AD-12).

**Given** the Uploading PM renames the page to `final_PRD_...`
**When** the resulting `page-updated`/`page-created` event arrives
**Then** it re-enters the flow at FR-02 as a new page `version.number`, so it is NOT suppressed as a duplicate while genuine duplicate deliveries of the same version still are (EH-04, AD-9)
**And** the earlier rename-request task does not cause duplicate processing.

### Story 2.7: Self-ingestion defense-in-depth (label + agent-account exclusion)

As the operator,
I want detection to additionally exclude pages carrying the reserved `agent-generated` label or created by the agent's own account, with the agent account resolved once per tenant and cached,
So that the agent's own output can never re-enter an infinite draft loop even if the structural guard is bypassed.

**Tag:** hardening
**Traces:** FR-01 · **Governed by:** AD-10

**Acceptance Criteria:**

**Given** a page in the watched source folder
**When** the detection guard runs
**Then** the page is admitted only if it also (b) lacks the reserved system label `agent-generated` and (c) was not created by the agent's own account id (AD-10 (b),(c))
**And** the reserved label is a fixed cross-tenant system constant (does not violate AD-4).

**Given** the agent's own account id is needed for check (c)
**When** it is required
**Then** it is resolved once per tenant via the adapter (`get_current_user` / `/myself`) and cached — never guessed independently by two units (AD-10) — and this same cached id is reused by the publish restriction (Story 5.3, AD-18).

### Story 2.8: Cross-org identity fallback for rename-task assignment

As the operator running across Atlassian organizations,
I want a config `identity_overrides` map consulted first and an email-match fallback when Jira and Confluence do not share an `accountId`,
So that the rename-request task is assigned to the right human even in the cross-org edge, per full hardening.

**Tag:** hardening
**Traces:** FR-02a · **Governed by:** AD-12

**Acceptance Criteria:**

**Given** a page-creator whose Confluence `accountId` is not valid as a Jira assignee (cross-org)
**When** the Ticket manager resolves the assignee
**Then** it first consults the config `identity_overrides` map (Confluence → Jira accountId); if absent, it resolves via email-match fallback (AD-12)
**And** only fully-automatic zero-configuration cross-org auto-resolution (neither override nor email match) remains deferred (AD-12, Deferred).

## Epic 3: UserDoc Authoring & Draft Publication

From a confirmed PRD, the Author drafts a tailored end-user UserDoc with one self-critique pass, publishes it to the Confluence draft folder, creates the Review ticket assigned to the Reviewer PM, and posts the framed review request — parking the flow at `awaiting_review`. Depends on Epics 1-2.

### Story 3.1: Author agent drafts the first UserDoc from the PRD

As the Reviewer PM,
I want the Author agent to read the PRD and produce a first end-user UserDoc draft whose structure it tailors per PRD,
So that I get a usable starting draft with no manual drafting.

**Tag:** critical-path
**Traces:** FR-05 · **Governed by:** AD-17

**Acceptance Criteria:**

**Given** a confirmed PRD (`stage = prd_ticket_done`)
**When** the Author agent runs
**Then** it produces a first UserDoc draft (an end-user onboarding/help guide) whose structure is decided by the agent's system prompt + context + SKILL.md, not a hardcoded template (FR-05)
**And** the Author's LLM calls are traced in LangSmith (AD-20).

### Story 3.2: Author self-critique pass (draft → critique → one revision)

As the Reviewer PM,
I want the Author to run exactly one self-critique-and-revise pass before I see the draft,
So that obvious weaknesses are caught early — while my PASS remains the only true quality gate.

**Tag:** critical-path
**Traces:** FR-05 · **Governed by:** AD-17

**Acceptance Criteria:**

**Given** a first draft
**When** the authoring step completes
**Then** exactly one lightweight self-critique pass has run (draft → critique against the skill file → single revision) before the draft is posted (FR-05)
**And** the self-critique is a drafting aid only: it never signals done-ness and is not an acceptance gate — content-quality acceptance is solely the human PM PASS at FR-12 (AD-17, FR-05 acceptance oracle).

### Story 3.3: Publish the draft to the Confluence draft/review folder (idempotent, self-stamped)

As the Reviewer PM,
I want the self-critiqued draft published as a Confluence page in my project's draft/review folder, stamped so the agent never re-ingests it,
So that I can read and review the draft in Confluence.

**Tag:** critical-path
**Traces:** FR-06 · **Governed by:** AD-14, AD-10, AD-11

**Acceptance Criteria:**

**Given** a self-critiqued draft and a tenant `confluence_draft_folder_id`
**When** the Publisher/Author creates the page
**Then** the page is created and placed into the draft folder via the v1 move/append endpoint (AD-14), stamped with the reserved `agent-generated` label and the `prd_id` content property (AD-10, AD-11)
**And** `userdoc_page_id` is recorded in the state record; a resume reuses that id and never creates a second page (find-or-create keyed on `prd_id`; AD-11).

### Story 3.4: Create the Review ticket assigned to the Reviewer PM

As the Reviewer PM,
I want a Review ticket created in the Review project, assigned to me and linked to the draft page,
So that I have a single place to review, give feedback, and (later) signal PASS.

**Tag:** critical-path
**Traces:** FR-06 · **Governed by:** AD-11, AD-15

**Acceptance Criteria:**

**Given** a published draft page
**When** the Ticket manager creates the Review ticket
**Then** a ticket is created in the tenant `jira_review_project_key`, assigned to the config `pm_account_id`, linking to the draft page (FR-06)
**And** the ticket carries the `prd_id` correlation marker and `review_ticket_key` is recorded; a resume adopts the existing ticket rather than double-creating (AD-11)
**And** the orchestrator advances `stage` to `awaiting_review` (the run now parks pending a human; AD-15).

### Story 3.5: Post the framed review-request comment

As the Reviewer PM,
I want the review request to tell me exactly how to give feedback and how to pass the draft,
So that my review is well-framed and the Done-only pass rule is unambiguous.

**Tag:** critical-path
**Traces:** FR-07, §6.2 · **Governed by:** AD-7 (ADF), AD-15

**Acceptance Criteria:**

**Given** a created Review ticket
**When** the request-review comment is posted
**Then** the comment (built as ADF) tags the PM (`@<PM>`), requests the exact structured feedback format (`Section:` / `Issue:` / `Suggested change:`, §6.2), explicitly asks the PM to "please put yourself in the users' shoes," and states that the only way to pass is for the PM to transition the Review ticket to Done themselves — that feedback after Done is not processed and the agent will not change status on their behalf (FR-07)
**And** the comment content satisfies the PRD §12 DoD review-request checklist item.

## Epic 4: Human Review & Revision Loop

Drive the Reviewer PM feedback loop to PASS: ingest structured or plain feedback via a typed decision, run the human-blocking structure-confirmation and bounded-clarification sub-loops, revise with a change summary and re-request, and detect the PM's Done transition as the sole PASS signal. Depends on Epics 1-3.

### Story 4.1: Ingest PM feedback and route via a typed FeedbackDecision

As a developer,
I want PM feedback parsed into a typed `FeedbackDecision{route, trigger, assumption}` on which the orchestrator routes deterministically,
So that which stage feedback goes to is unit-testable and never lives in untestable prose.

**Tag:** critical-path
**Traces:** FR-09 · **Governed by:** AD-16

**Acceptance Criteria:**

**Given** a new PM comment on the Review ticket (via webhook)
**When** the Feedback interpreter runs
**Then** it returns a typed `FeedbackDecision{route, trigger, assumption}` (defined in `app/domain/`): structured feedback (§6.2) routes toward apply (FR-11 / Story 4.2); plain-language routes toward structure-confirmation (FR-10 / Story 4.4) (FR-09, AD-16).

**Given** hand-built `FeedbackDecision` objects
**When** the orchestrator's stage routing is tested
**Then** the routing is deterministic and unit-tested on those objects, while only the LLM that produces the decision is eval-tested (held-out set; AD-16).

### Story 4.2: Apply structured feedback to produce a revised draft

As the Reviewer PM,
I want my structured feedback applied to a revised draft with a summary of what changed and a fresh review request,
So that the draft converges toward something I can pass, one human-driven round at a time.

**Tag:** critical-path
**Traces:** FR-11, NFR-09 · **Governed by:** AD-16, AD-20

**Acceptance Criteria:**

**Given** confirmed structured feedback
**When** the Author revises
**Then** the UserDoc is revised per the feedback, the Confluence draft page is updated, a comment tagging the PM summarizes what changed, and review is re-requested per FR-07 framing (FR-11); `stage` returns to `awaiting_review`.

**Given** the redraft loop (FR-07 → FR-11)
**When** it repeats
**Then** it is uncapped but cannot self-spin — each round requires a fresh human PM feedback comment; `review_round` increments per applied round and is surfaced with per-round token cost in LangSmith as the guardrail (no hard cap) (FR-11, NFR-09, AD-16).

### Story 4.3: Detect PASS on the Reviewer PM's Done transition

As the Reviewer PM,
I want my transition of the Review ticket to Done to be the single, unambiguous PASS signal,
So that I stay in control of approval and nothing advances without my explicit action.

**Tag:** critical-path
**Traces:** FR-12, EH-09 · **Governed by:** AD-15

**Acceptance Criteria:**

**Given** a Review ticket in `awaiting_review`
**When** an issue-updated webhook reports a human transition into a `done`-category status
**Then** the agent interprets it as the sole PASS/approval signal and advances `stage` to `passed` (FR-12)
**And** the agent never transitions the Review ticket itself (AD-15).

**Given** the PM takes no action
**When** time passes
**Then** the run parks at `awaiting_review` indefinitely — no timeout, no auto-escalation (FR-12, EH-09, AD-15).

### Story 4.4: Structure-confirmation sub-loop for plain-language feedback

As the Reviewer PM,
I want the agent to restate my plain-language feedback in the structured format and wait for me to confirm before changing anything,
So that the agent never acts on a misread of what I meant.

**Tag:** hardening
**Traces:** FR-10, EH-08 · **Governed by:** AD-16

**Acceptance Criteria:**

**Given** unstructured (plain-language) PM feedback
**When** the Feedback interpreter runs
**Then** it converts the feedback into the structured format, comments back tagging the PM ("You didn't feedback following the format so I curated it like this — is this what you mean?"), and moves `stage` to `awaiting_structure_confirm` (FR-10)
**And** it blocks on the PM's confirming reply and applies no changes until confirmation — never fabricating the answer or auto-advancing (FR-10, EH-08, AD-16). Only on confirmation does it proceed to Story 4.2 (FR-11).

### Story 4.5: Bounded clarification sub-loop (four enumerated triggers only)

As the Reviewer PM,
I want the agent to ask me a clarifying question and wait ONLY in the four enumerated blocking situations, and otherwise proceed with a stated assumption,
So that I am interrupted only when genuinely necessary and the doc is never silently wrong on something material.

**Tag:** hardening
**Traces:** FR-08, EH-08 · **Governed by:** AD-16

**Acceptance Criteria:**

**Given** drafting/redrafting
**When** one of the four enumerated triggers holds — (1) an undefined feature name/term/acronym that materially changes the doc, (2) two parts of the PRD directly contradict on a user-facing behavior, (3) a user-facing flow the doc must describe is left incomplete, (4) the PM's own feedback is internally contradictory or points to a non-existent section
**Then** the agent posts a clarifying question tagging the PM, moves to `awaiting_clarification`, and blocks on the answer (FR-08)
**And** outside these four cases the agent proceeds without asking, filling trivial gaps with a reasonable, stated assumption (FR-08); it never fabricates the PM's answer or auto-advances in this gate (EH-08, AD-16).

### Story 4.6: Late-feedback-after-Done ignored; non-Done transitions & reassignment park

As the operator,
I want feedback added after the Review ticket is Done to be ignored, and non-Done terminal transitions / reassignment to simply park the run,
So that the pass is final at the Done transition and unhandled gate states never mis-advance the flow.

**Tag:** hardening
**Traces:** EH-06, EH-09 · **Governed by:** AD-15

**Acceptance Criteria:**

**Given** a Review ticket already transitioned to Done
**When** a later PM comment arrives
**Then** it is not processed — the pass is final at the Done transition (EH-06).

**Given** a non-Done terminal transition (Rejected / Won't Do / Duplicate) or a reassignment on a gate ticket
**When** it is observed
**Then** the run parks at its current gate stage with no timeout and no auto-escalation (out of demo scope by explicit decision) (EH-09, AD-15).

## Epic 5: Approval & Publishing

On PASS, create the Publishing ticket for the Head of Product, wait for their Done approval (never auto-transitioned), then run the ordered, per-side-effect-idempotent publish transaction — restrict edit, move to the published folder, export Markdown — and mark the flow Complete. Depends on Epics 1-4.

### Story 5.1: Confirm PASS and create the Publishing ticket for the Head of Product

As the Head of Product,
I want a Publishing ticket created in the Main project requesting my approval to publish, once the PM has passed the draft,
So that I have a single explicit gate to approve production publishing.

**Tag:** critical-path
**Traces:** FR-13 · **Governed by:** AD-11, AD-15

**Acceptance Criteria:**

**Given** `stage = passed`
**When** the Ticket manager runs
**Then** it posts a confirmation comment on the Review ticket (tagging the PM) and creates a UserDoc Publishing ticket in the tenant `jira_main_project_key`, reported to / assigned for approval by `head_of_product_account_id`, linking the passed UserDoc and requesting approval to publish (FR-13)
**And** the ticket carries the `prd_id` marker and `publishing_ticket_key` is recorded; a resume adopts the existing ticket (AD-11); `stage` advances to `awaiting_publish_approval`.

### Story 5.2: Head of Product publish gate

As the Head of Product,
I want my transition of the Publishing ticket to Done to be the single approval-to-publish signal,
So that nothing is ever published without my explicit action.

**Tag:** critical-path
**Traces:** FR-14, EH-09 · **Governed by:** AD-15

**Acceptance Criteria:**

**Given** a Publishing ticket in `awaiting_publish_approval`
**When** an issue-updated webhook reports a human transition into a `done`-category status
**Then** the agent interprets it as the sole approve-to-publish signal and advances `stage` to `publishing` (FR-14); the agent never transitions the Publishing ticket itself (AD-15).

**Given** the Head of Product takes no action
**When** time passes
**Then** the run parks at `awaiting_publish_approval` indefinitely — no timeout, no auto-escalation (FR-14, EH-09).

### Story 5.3: Ordered, idempotent publish transaction (restrict / move / export / complete)

As the Head of Product,
I want approval to atomically-enough restrict the page, move it to the published folder, export the Markdown, and complete the flow — each step safe to re-run,
So that the approved doc is locked from casual edits, out of the detection path, and exported for the SSG, with no partial-publish or double-publish on resume.

**Tag:** critical-path
**Traces:** FR-15 · **Governed by:** AD-18, AD-14, AD-10

**Acceptance Criteria:**

**Given** `stage = publishing`
**When** the Publisher runs
**Then** it performs, in order, four side-effects each guarded by its own idempotency marker/sub-checkpoint in the state record: (1) apply a Confluence edit restriction that MUST include the agent account (the cached AD-10 account) and space admins — this restricts who may edit, it is not a content freeze/version pin; (2) move the page via v1 move/append into `confluence_published_folder_id` (a no-op if already placed); (3) export storage → Markdown (markdownify) to the tenant `md_export_dir` on server disk, recording `md_export_path`; (4) mark state `complete` (FR-15, AD-18, AD-14).

**Given** a resume of the `publishing` stage
**When** it re-runs
**Then** it skips any completed side-effect and never re-applies one (per-side-effect idempotency is a tested deliverable) (AD-18)
**And** the published folder is adjacent to (not inside) the source folder, so the published page is never re-ingested by detection (FR-15, AD-10).

## Epic 6: Resilience, Recovery & Operations

Harden the running system: structured error surfacing with admin resume, a liveness/reconcile sweep for dropped gate webhooks, off-box state backup, the 1 GB deploy envelope on the target host, and verification of the config-only-modifiability promise. Mostly hardening; contains the one critical-path deploy story the end-to-end demo run requires. Depends on Epics 1-5.

### Story 6.1: Error surfacing and admin resume from checkpoint

As the Admin,
I want any unrecovered error to post one clear ticket comment tagging me with a fix suggestion and exact resume instructions, and my `@agent resume` reply to re-run only the failed stage,
So that I can fix root causes and resume without restarting the whole flow.

**Tag:** hardening
**Traces:** EH-01, EH-02 · **Governed by:** AD-19, AD-11

**Acceptance Criteria:**

**Given** any error after NFR-08 retries
**When** the orchestrator handles it
**Then** it sets `stage = error` preserving `last_good_checkpoint` + `pending_gate`, and the Error handler posts exactly one structured comment (ADF) on the relevant ticket: plain-language error + suggested fix + `@admin` (config) + the literal instruction ("Reply `@agent resume` or `fixed` on this comment and I'll retry from where I stopped") + a correlation id logged to LangSmith (EH-01, AD-19).

**Given** an admin comment webhook on that ticket containing `@agent resume` / `fixed`
**When** it is processed
**Then** the flow re-runs from `last_good_checkpoint` (the failed stage only), never the whole flow (EH-02, AD-11)
**And** that resume comment is dedupe-guarded (AD-9) so a duplicate delivery cannot double-resume.

### Story 6.2: Reconciliation & liveness sweep (dropped-gate-webhook recovery)

As the Admin,
I want a lightweight scheduled sweep that alerts on stale parked/error runs and re-polls the two gate tickets, feeding a found Done as an input,
So that a silently-dropped gate webhook never strands a run that a human already approved — without introducing a timeout.

**Tag:** hardening
**Traces:** EH-09, PRD §13 Q3 · **Governed by:** AD-22, AD-2, AD-15

**Acceptance Criteria:**

**Given** the reconciler (default: system `cron` → authenticated localhost `/admin` endpoint, within the AD-21 envelope)
**When** a sweep runs
**Then** (a) it finds runs in `awaiting_review` / `awaiting_publish_approval` / `error` whose `updated_at` is older than a threshold and alerts through the EH-01 admin surface plus a LangSmith/log signal, recording a `liveness_alerted_at` marker so a stuck run is alerted once per threshold crossing (AD-22)
**And** (b) it re-polls the two gate tickets via the Jira adapter; a gate now `statusCategory == done` is fed to the orchestrator as an input identical to the missed issue-updated webhook.

**Given** a reconcile finding and a webhook for the same gate transition
**When** both occur
**Then** the reconciler writes only non-`stage` markers (never advances `stage`; AD-2), the agent still never transitions a gate ticket (AD-15), and a gate-Done cannot double-advance a stage — collisions resolve via the same serial queue (AD-5), the same AD-9 dedupe key, and idempotent stage advance (AD-11, AD-22)
**And** indefinite-park semantics are unchanged — this adds recoverability, not a timeout.

### Story 6.3: Off-box state backup / disaster recovery

As the Admin,
I want the single SQLite store continuously replicated off the Droplet with point-in-time restore,
So that losing the box mid-run does not cause unrecoverable state loss or a post-crash double-create/re-publish.

**Tag:** hardening
**Traces:** PRD §15.5 · **Governed by:** AD-23

**Acceptance Criteria:**

**Given** the run state (`last_good_checkpoint`, `processed_events`, recorded external ids) is authoritative only on the Droplet disk
**When** the backup is configured
**Then** the single SQLite store is replicated off-box — default `litestream` (pin `>= 0.5.4`) streaming the WAL to DigitalOcean Spaces as a small-RAM sidecar within the AD-21 envelope; alternative hourly `sqlite3 .backup` + `/data` tar to Spaces (AD-23).

**Given** a simulated disk loss
**When** a restore is performed
**Then** the store is restored point-in-time from the replica and a redelivered webhook does not double-create or re-publish (AD-23, AD-11). This ships for the demo (full hardening), not as a seam.

### Story 6.4: Deploy to the reachable 1 GB host and run the end-to-end demo

As the product owner,
I want the service deployed on the target 1 GB Droplet behind Caddy with a public HTTPS endpoint Atlassian can reach, built off-box and pulled,
So that a real `final_PRD_<name>` page flows end-to-end to a published `.md` — the demo's single success metric.

**Tag:** critical-path
**Traces:** NFR-07, NFR-11, PRD §3, §12, §15 · **Governed by:** AD-21

**Acceptance Criteria:**

**Given** the target host (DO Droplet 1 GB / 1 vCPU / 25 GB, Ubuntu LTS)
**When** the service is deployed
**Then** the single slim Docker image is built OFF the box (CI/registry) and pulled — never built on the box; a single Uvicorn worker runs FastAPI bound to localhost behind Caddy (TLS via Let's Encrypt); the firewall opens only 443 + 22; the webhook endpoint is reachable by Jira/Confluence over public HTTPS (AD-21, NFR-07, §15).

**Given** the deployed, reachable service and a configured tenant
**When** a genuine `final_PRD_<name>` page is created in the source folder and the two human gates are passed (PM PASS, then Head of Product Done)
**Then** the full happy path runs end-to-end to a published, edit-restricted, moved UserDoc and an exported `.md`, with LangSmith showing per-step latency/speed/cost — one successful full run (PRD §3, §12 DoD).

### Story 6.5: 1 GB memory-envelope hardening

As the operator,
I want the runtime tuned and verified to stay stable within 1 GB under one-PRD load,
So that the demo does not OOM on the deliberately small box (and resizing up remains the only lever if it does).

**Tag:** hardening
**Traces:** NFR-06, NFR-11, PRD §15.2 · **Governed by:** AD-21

**Acceptance Criteria:**

**Given** the Droplet
**When** it is provisioned
**Then** a 1-2 GB swap file is added; the container uses a slim Python base with lean dependencies only; a single worker process is used; no database server is co-located (SQLite is in-process); at most one PRD is resident in memory (the serial queue is a memory-safety measure) (AD-21, NFR-06, NFR-11).

**Given** the §12 end-to-end run under a representative PRD payload
**When** memory is observed
**Then** the service runs stable within the envelope; if it runs tight/OOMs, resizing the Droplet up is the documented reversible remedy — no 1-GB-only assumption is hard-coded (AD-21, §15.3).

### Story 6.6: Config-only modifiability verification (add a 2nd project; swap identities)

As the product owner,
I want to prove that adding a second project and swapping the PM/Head of Product/admin require only config edits,
So that the "easy to mod" promise (NFR-02/05) is demonstrably true and no project literal leaked into code.

**Tag:** hardening
**Traces:** NFR-02, NFR-05, PRD §12 DoD · **Governed by:** AD-4

**Acceptance Criteria:**

**Given** a running, configured tenant
**When** a second project is added purely by adding a config registry entry (folders, project keys, identities, credential refs, `md_export_dir`) with no code change
**Then** events for the second project route and process correctly (NFR-02, §12 DoD).

**Given** a request to change the Reviewer PM / Head of Product / admin
**When** only the corresponding config account ids are edited
**Then** the new assignees take effect with no code change (NFR-02)
**And** a grep of the source tree (code, prompts, SKILL.md) finds no project-specific literal outside config — only the `agent-generated` system constant (NFR-05, AD-4).

### Story 6.7: Content-gating observability flag and data-governance seam

As the operator,
I want a config flag governing what content is attached to a LangSmith trace, with the demo tracing non-confidential test PRDs only,
So that the seam toward metadata-only tracing exists before any confidential content is ever processed.

**Tag:** hardening
**Traces:** NFR-01 · **Governed by:** AD-20

**Acceptance Criteria:**

**Given** the observability configuration
**When** tracing runs
**Then** a content-gating config flag governs what content rides along on a trace (the seam that later distinguishes metadata-only from full-content tracing); the demo traces non-confidential test PRDs only (AD-20, NFR-01)
**And** full redaction/retention for confidential content remains a documented post-demo item (not built now).
