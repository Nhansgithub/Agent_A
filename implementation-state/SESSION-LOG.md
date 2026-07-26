# Session Log

Append-only chronological journal of build work. Newest at the bottom. One block per working session.
Keep entries factual and short — what was built, what passed, what broke, what is next.

---

## 2026-07-24 · Session 1 — Bootstrap

**Started from:** greenfield. Only `planning-artifacts/` existed (PRD v0.3, Architecture Spine r2,
solution design r2, epics.md, readiness report). No code, no git repo.

**Read:** all five source-of-truth documents in full, plus the architecture `.memlog.md` (which carries
the version-verification rationale and the r2 roundtable decisions behind AD-11 / AD-22 / AD-23).

**Built:**
- `CLAUDE.md` — project contract: source-of-truth index, the 8 non-negotiable invariants
  (AD-1, AD-2/11, AD-4, AD-15, AD-16, AD-9, AD-21, AD-20), pinned stack, layout, conventions,
  human-block protocol, working rhythm.
- `implementation-state/STATE.md` — resume pointer, named in CLAUDE.md as the mandatory first read.
- `implementation-state/EPIC-STORY-TRACKER.md` — all 39 stories across 6 epics with tag, governing ADs,
  status, evidence column.
- `implementation-state/BLOCKERS.md` — 6 anticipated human/3rd-party gates (Anthropic key, LangSmith,
  Atlassian tenant, DigitalOcean, registry/CI, local Docker), each with what is needed and the
  workaround that keeps the build moving while it is open.
- `implementation-state/DECISION-LOG.md` — D-01 (state system), D-02 (Python 3.12 pin).
- `implementation-state/SESSION-LOG.md` — this file.

**Environment:** started `pyenv install 3.12.12` (system default is 3.14.0). Docker not installed;
not needed until Epic 6.

**Next:** Story 1.1 — project scaffold, layered module skeleton, pinned dependency manifest.

---

## 2026-07-24 · Session 1 (cont.) — Epic 1 foundation, stories 1.1 → 1.3

### S1.1 — Project scaffold, layered modules, pinned dependency manifest · **DONE**
- `pyproject.toml`: runtime deps pinned to the Spine Stack table; `[tool.importlinter]` carries four
  contracts encoding AD-1/AD-2/AD-4/AD-6 (layer order; no HTTP client outside adapters; no `sqlite3`
  outside the repository; no `anthropic` outside agents; config is a leaf).
- `app/` tree per the Spine's Structural Seed, plus `fixtures/classifier/{dev,holdout}/`, `deploy/`,
  `tests/`. Each package `__init__.py` names the story that fills it.
- **Verification:** every Stack-table pin resolved *exactly* as the architecture web-verified
  (fastapi 0.136.3, uvicorn 0.51.0, langgraph 1.2.9, langgraph-checkpoint 4.1.1, anthropic 0.117.0,
  langsmith 0.10.9, markdownify 1.2.3). `langgraph-api` is **absent** from the resolved tree — NFR-10
  holds and is now an executable test, not a promise.
- Tests: `test_stack_and_licensing.py`, `test_architecture_boundaries.py` (17).

### S1.2 — Config registry, tenant-config schema, env-ref secrets · **DONE**
- `app/config/schema.py`: frozen `TenantConfig` covering every PRD §11 field plus the full-hardening
  fields (`identity_overrides` AD-12, `preferred_transition_path` AD-13, `trace_content` AD-20), and
  `SystemConfig` (models pinned per AD-17, retry budget, reconciler threshold).
- `app/config/registry.py`: loader + the two AD-3 lookup indexes (Confluence folder → tenant, Jira
  project key → tenant), with `watches_source_folder()` distinguishing the *watched* folder from
  merely-owned draft/published folders.
- `app/config/secrets.py`: `env:PREFIX` resolution to a `(base_url, email, api_token)` triple. Lazy,
  so config loads with no credentials; `__repr__` redacts the token.
- `app/config/constants.py`: the cross-tenant system constants — `agent-generated` label (AD-10),
  `prd_id` correlation property (AD-11), the FR-02 title-gate pattern, EH-02 resume keywords.
- **Two validations worth noting**, both catching real incidents at load time rather than in prod:
  published folder == source folder is rejected (it would defeat the *primary* AD-10 self-ingestion
  guard and loop the agent on its own output), and duplicate folder ids / project keys across tenants
  are rejected (ambiguous AD-3 routing lets one tenant touch another's resources).
- Tests: `test_config_registry.py`, `test_config_isolation.py` (45) — including an **automated NFR-05
  grep-clean check** that extracts every literal from the configured registry and greps `app/`,
  `fixtures/`, `deploy/` for it. It keeps protecting the invariant as the tree grows.

### S1.3 — Repository + single SQLite store, state record, stage enum · **DONE**
- `app/domain/stage.py`: `Stage` (exactly the §9 values, order-asserted by test), `PendingGate`,
  `QueueStatus`, and the legal-transition map from the solution-design §4 diagram.
- `app/domain/state.py`: the §10 `PrdState` record + AD-18 per-side-effect publish sub-checkpoints
  and the AD-22 `liveness_alerted_at` marker. `dedupe_keys` is modelled as a *read-only projection*,
  never a second write target (AD-9).
- `app/domain/errors.py`: the single `AgentError` adapters normalize into, carrying the plain-language
  message and suggested fix EH-01 requires.
- `app/repository/database.py`: WAL journal mode (required by litestream, AD-23); one lock-guarded
  connection, which the serial queue makes contention-free and keeps lean on the 1 GB box.
- `app/repository/state_repository.py`: `advance_stage()` writes the stage **and** the ids that stage
  recorded in one transaction (AD-11 — a crash between them is precisely what causes a double-create
  on replay); `update_fields()` **refuses** to write `stage`, so the AD-22 reconciler and role-agents
  structurally cannot advance one (AD-2); `mark_error()` preserves `last_good_checkpoint` as the
  resume point (EH-02).
- Tests: `test_state_repository.py` (55) — including a table of edges that *would* skip a human gate
  (`awaiting_review → publishing`, `drafted → passed`, …) asserted illegal, which is the AD-15
  invariant made executable.

**Suite: 117 passed. `ruff check` clean. All 4 import-linter contracts hold.**

**Decisions recorded:** D-03 (env-ref triple), D-04 (model ids in system not tenant config),
D-05 (legal-transition map beyond the literal spec), D-06 (ruff owns code width).

**Correction to process:** the tracker and logs were not being updated per story — Nhan flagged it
mid-run. Fixed here, and `STATE.md` standing rule 5 now makes log-flush part of a story's
definition of done.

**Next:** Story 1.4 — webhook ingress with shared-secret / signature validation (AD-8).

---

## 2026-07-24 · Session 1 (cont.) — Epic 1 ingress pipeline, stories 1.4 → 1.6

Built together, because signature validation, dedupe, and routing are one pipeline and only make
sense tested end to end.

### S1.4 — Webhook ingress with signature validation · **DONE**
- `app/webhooks/signature.py`: HMAC-SHA256 over the **raw** body against `X-Hub-Signature`, plus an
  `X-Webhook-Secret` shared-secret fallback (Automation rules cannot compute an HMAC). Both use
  `hmac.compare_digest`, so a wrong secret cannot be recovered by timing the endpoint. An empty
  configured secret refuses to serve at all.
- `app/webhooks/events.py` + `app/domain/events.py`: the four subscribed event shapes. ADF comment
  bodies are flattened to text — a naive `str(body)` would hand the Feedback interpreter a Python
  dict repr instead of what the PM wrote.
- `app/webhooks/ingress.py`: the ordered pipeline with a typed `IngressOutcome` for every drop reason.

### S1.5 — Idempotency: `processed_events` + composite dedupe key · **DONE**
- `app/domain/dedupe.py`: the AD-9 composite key. Placed in `domain/` because the webhook layer
  derives it and the repository stores it — a disagreement between those two would either loop on
  duplicates or drop genuine renames.
- `app/repository/event_repository.py` + `Repository.admit()`: the insert **is** the check (UNIQUE
  constraint), not check-then-write, so two concurrent deliveries cannot both see "not processed".
  The key and the PRD row commit in one transaction — a crash before that leaves the event safely
  redeliverable rather than silently consumed.
- Tested: a rename produces a new `version.number` and re-enters (EH-04) while a same-version
  redelivery is dropped; a gate webhook and an AD-22 reconcile-poll observing the same transition
  derive the same key and collide, so a gate-Done cannot double-advance.

### S1.6 — Route-before-work tenant resolution · **DONE**
- `app/router.py`: `TenantRouter` resolving Confluence events by container folder and Jira events by
  project key, returning a `RoutingDecision` that carries *why* — so an unrouted event in production
  is diagnosable without reproducing it.
- Added optional `confluence_space_key` to tenant config as a routing fallback, because the
  `page-created` payload is not guaranteed to carry the container (PRD §13 Q3/Q4). Fallbacks are
  routing conveniences only — FR-01 detection still independently applies the watched-folder check,
  so a fallback can never smuggle a page into the flow.

### Two problems found and fixed
1. **The architecture test was vacuously passing.** `python -m importlinter.cli lint` exits 0 without
   running anything. The test now invokes the `lint-imports` console script and asserts the contract
   summary parses with `kept >= 5, broken == 0` — the worst failure mode for an architecture guard is
   reporting green while enforcing nothing.
2. **A real AD-1 violation, caught by the fixed guard.** `app/router.py` imported event types from
   `app/webhooks/`, an upward dependency. Fixed by moving the event *types* to `app/domain/events.py`
   (they describe what happened, independent of transport) and leaving *parsing* in the webhook layer.
   Also added `include_external_packages` and `allow_indirect_imports` so the forbidden-module
   contracts actually execute rather than erroring out.

**Suite: 160 passed. `ruff check` clean. 5/5 import-linter contracts kept.**

**Decisions recorded:** D-07 (AD-8 vs AD-9 ordering), D-08 (event types belong to domain),
D-09 (direct-import-only forbidden contracts).

**Next:** Story 1.7 — `JiraAdapter` with domain verbs, ADF bodies, retry, and error normalization.

---

## 2026-07-24 · Session 1 (cont.) — Story 1.7, JiraAdapter

### S1.7 — `JiraAdapter`: domain verbs, ADF, retry, error normalization · **DONE**
- `app/domain/adf.py`: ADF builders. The load-bearing one is `mention()` — FR-07, FR-08, FR-10,
  FR-11 and EH-01 all require *tagging* a human, and a literal `"@Name"` text node renders fine but
  notifies nobody, which would strand a run at a gate waiting on someone never told. Also
  `extract_text()` for reading PM comments back out of ADF.
- `app/domain/atlassian.py`: `JiraIssue`, `JiraTransition`, `ConfluencePage` value objects, so agents
  read `issue.is_done` rather than digging through `fields.status.statusCategory.key`.
- `app/adapters/http.py`: the shared `AtlassianClient` — Basic auth, retry-with-backoff, and
  `AgentError` normalization. Retries cover **transient conditions only** (timeouts, connection
  errors, 429, 5xx); a 403 escalates immediately, because retrying it three times only delays the
  escalation. Each status maps to an actionable `suggested_fix` that reads well in a Jira comment
  (EH-01) — a 401 says "regenerate the API token", a 404 says "check the ids in the config registry".
- `app/adapters/jira.py`: the domain verbs. `create_issue` puts the AD-11 correlation label **in the
  create payload**, so the marker is atomic with the create and an orphan is always findable by
  `find_issue_by_prd_marker` — this closes the create-succeeded-then-crashed-before-persisting window
  the r2 roundtable flagged. `_require_adf` rejects a plain-string body up front instead of letting
  Jira v3 return an opaque 400 mid-run.
- Async throughout (D-10): a drafting run makes many sequential Atlassian calls, and blocking the
  event loop would stall webhook acknowledgement, which Atlassian treats as delivery failure.

**One design gap found by a test:** injecting a test HTTP client bypassed the auth headers, because
they were baked into the client at construction. Fixed properly — headers are attached per-request,
so an injected or pooled client cannot silently lose authentication.

**Suite: 192 passed.** 32 new adapter tests, all against a fake transport — no network, no credentials.

**Next:** Story 1.8 — `ConfluenceAdapter` + markdown converter (v2 default, v1 for move/restrictions).

---

## 2026-07-24 · Session 1 (cont.) — Setup guide + Story 1.8

### Setup guide (requested by Nhan, mid-run)
Nhan has the Atlassian account, both Jira projects and all three Confluence folders, plus an
Anthropic key — but no API token, no webhook secret, and no LangSmith account, and asked for
step-by-step instructions written for someone who has not connected a third-party API before.

- **`SETUP-GUIDE.md`** (root): 8 parts, checklist-driven. Covers creating the Atlassian API token,
  collecting every ID, placing the Anthropic key, *generating* the webhook secret (Nhan did not know
  this is self-invented rather than fetched), LangSmith signup, filling `.env` + `registry.yaml`,
  registering webhooks, and the DigitalOcean steps. Plus a troubleshooting table keyed by symptom and
  a per-secret blast-radius table.
- **`scripts/discover_ids.py`**: prints Jira project keys, Confluence space/folder ids, and account
  ids in one command. Finding folder ids by hand is the fiddliest part of setup and a wrong one
  surfaces much later as a confusing 404.
- **`scripts/verify_setup.py`**: read-only end-to-end config check. Notably catches the
  **published-folder-nested-inside-source** case — the config loader can only see the exact-match
  case, not the folder *tree*, and nesting would make the agent re-ingest its own output forever.

Both scripts smoke-tested for clean, actionable failure output with no credentials present.

`git init` + two commits (Nhan approved). Nothing pushed to a remote.

### S1.8 — `ConfluenceAdapter` + markdown converter · **DONE**
- `app/adapters/confluence.py`: v2 by default, with the two v1 exceptions the architecture already
  researched — folder placement via `PUT /wiki/rest/api/content/{id}/move/append/{folderId}` (the v2
  `parentId` path 500s for folder parents, AD-14) and content restrictions (v1-only Cloud endpoints).
  `set_edit_restriction` **refuses an empty allow-list before making any HTTP call**, because omitting
  the agent's own account locks it out of the page it just published (AD-18).
- `app/adapters/markdown.py`: storage→Markdown. Rather than subclassing markdownify's converter and
  overriding `convert_ac:structured-macro`, a BeautifulSoup pass normalizes Atlassian `ac:`/`ri:` tags
  into plain HTML first — that dispatch naming has changed between markdownify releases, and a
  normalization pass is stable across upgrades and independently testable. Unknown macros keep their
  prose: losing a macro's *rendering* is acceptable (§13 Q5), losing the words inside it is not.
- Tests assert **no `ac:` or `ri:` tag survives conversion** — a leaked namespace tag in the exported
  `.md` would be visible to end users on the published help site.

**Suite: 227 passed. ruff clean. 5/5 import-linter contracts kept.**

**Next:** Story 1.9 — in-invocation LangGraph orchestrator, stage machine, and serial queue.

---

## 2026-07-24 · Session 1 (cont.) — Stories 1.9 + 1.10 · **EPIC 1 COMPLETE**

### S1.9 — In-invocation orchestrator, stage machine, serial queue · **DONE**
- `app/orchestrator/stages.py`: three outcomes a handler can return — `Advance`, `Park`, `Stay`.
  Handlers decide *what happened*; the orchestrator decides *what is written* (AD-2's split). A stage
  with no handler **stops** rather than skipping: silently advancing would push the run past a human
  gate, the exact failure AD-15 prevents.
- `app/orchestrator/graph.py`: LangGraph as in-invocation control flow only. A plain `StateGraph`
  always starts at START, so START dispatches *conditionally* to the node named by the recorded
  stage — that conditional entry router is what makes "re-enter at `stage`" work. Checkpointer is
  `InMemorySaver`; the graph is rebuilt per invocation so no cross-PRD singleton survives (AD-5).
- `app/orchestrator/runner.py`: the five AD-11 steps. Persistence happens **per stage boundary**, not
  once at the end — that is what makes resume cheap, and a test asserts a stage failing after two
  successful ones keeps their recorded ticket ids.
- Serial queue via a single `asyncio.Lock` — deliberately the only cross-PRD object, so lifting it
  later yields parallelism rather than a redesign. A concurrency probe asserts peak overlap is 1.

### S1.10 — LangSmith tracing harness · **DONE**
- `app/agents/tracing.py`: `Tracer` protocol with `NullTracer` (log-only) and `LangSmithTracer`
  (`RunTree`). LangSmith failures are caught and logged, never raised — losing observability is bad,
  dropping a PRD because a metrics backend is down is worse.
- `app/agents/llm.py`: the one shared Anthropic client all six roles use. Tracing is **structural**,
  not conventional: the span wraps the request inside the only module permitted to import the SDK,
  and a test greps `app/` for `import anthropic` statements to keep it that way. Cost is derived per
  call from pinned per-model rates, so NFR-01's cost figure is always present.
- Content gating (AD-20) defaults to off: timing/tokens/cost always recorded, prompt and completion
  text not egressed. Tests assert a confidential prompt does not appear anywhere in the span.

### A real bug the orchestrator tests caught
`AgentError` was `@dataclass(frozen=True, slots=True)` on an `Exception` subclass. `slots=True`
replaces the class object, which breaks `super()` inside the generated methods when the exception is
copied or re-raised — and LangGraph does exactly that when a node fails, producing
`TypeError: super(type, obj): obj must be an instance or subtype of type`. Rewritten as a plain
class with an explicit `__init__` and `__reduce__`. An exception has to survive arbitrary machinery,
so the boring implementation is the correct one.

Three test-fixture bugs were also genuine: fake handlers attempted transitions the §9 machine
rejects (`detected → awaiting_review`). The guard working on my own test code is a good sign.

**Suite: 278 passed. ruff clean. 5/5 import-linter contracts kept.**

**Decisions recorded:** D-12 (AgentError is a plain class), D-13 (tracing is structural, not
configuration-dependent).

**Next:** Epic 2 — PRD Detection & Confirmation, starting at Story 2.1.

---

## 2026-07-24 · Session 1 (cont.) — Epic 2: PRD Detection & Confirmation · **COMPLETE (8/8)**

Built the five agents Epic 2 needs, then wired them into the orchestrator's stage machine.

### Agents
- **Detection** (`app/agents/detection.py`, S2.1/2.2/2.7): the AD-10 admission guard in order —
  watched-folder (primary), then label + agent-account (defense-in-depth), then title gate. A title
  mismatch routes to rename rather than being dropped. The agent account is resolved once per tenant
  and cached (AD-10's "one source" rule), verified by a test asserting a single `get_current_user`.
- **Classifier** (`app/agents/classifier/`, S2.3): thin agent over a precise `SKILL.md` rubric; model
  from config, temperature 0, tolerant JSON parse. A parse failure raises rather than silently
  becoming a REJECT — a misbehaving classifier must not quietly pass the 0-FN bar.
- **Eval harness** (`app/agents/classifier/evaluation.py`, S2.4): dev + holdout fixtures (5+5,
  disjoint, both labels each), ×3 runs, confusion matrix, flake budget. The bar is 0-FP/0-FN **and**
  no flakes — an unstable pass is not a pass (AD-17 "distribution, not a boolean"). `scripts/run_
  classifier_eval.py` runs it live. **Harness fully unit-tested; the live accuracy run is PARTIAL
  pending the Anthropic key.**
- **Ticket manager** (`app/agents/ticket_manager.py`, S2.5/2.6): FR-04 adopt-orphan → search → create,
  then AD-13 drive-to-done (skip-if-done / direct / config multi-hop / escalate). A method-level
  interlock **refuses to transition a Review or Publishing ticket** — AD-15 as code, not convention.
  Plus the FR-02a rename-request task in the Review project.
- **Identity** (`app/agents/identity.py`, S2.8): AD-12 resolution — config override → same-org
  accountId → email match → unresolved (create the task unassigned for the admin rather than
  mis-assign).

### Orchestration
- `app/orchestrator/handlers_detection.py`: the `detected → confirmed → prd_ticket_done` handlers.
  Title-mismatch (FR-02a) and Classifier-REJECT (EH-07) both **self-park at the current stage** with
  `pending_gate = UPLOADING_PM_RENAME`, rather than inventing a stage or overloading
  `awaiting_clarification` (the FR-08 PM loop). A corrected re-upload arrives as a new page version
  (EH-04), re-enters at that stage, and re-runs — now passing. `test_handlers_detection.py` proves
  the full walk to `drafted` and both rename branches, including that the rename task is not filed
  twice on a re-entry (AD-11).

**Suite: 341 passed. ruff clean. 5/5 import-linter contracts kept.**

**Decisions recorded:** D-14 (rename-wait self-parks at the current stage).

**One PARTIAL:** S2.4's live 0-FP/0-FN measurement needs the Anthropic key. Everything else in Epic 2
is DONE and offline-tested.

**Next:** Epic 3 — UserDoc Authoring & Draft Publication (the Author agent, self-critique, draft
publication, Review ticket, framed review request).

---

## 2026-07-24 · Session 1 (cont.) — Epic 3: UserDoc Authoring & Draft Publication · **COMPLETE (5/5)**

- **Author** (`app/agents/author/`, S3.1/3.2): drafts from the PRD with structure decided by its
  `SKILL.md` (no fixed template), then runs **exactly one** self-critique pass — draft call + critique
  call, asserted at 2 LLM calls. The self-critique is a drafting aid, never a gate (AD-17). `revise()`
  applies PM feedback with no self-critique (the human is already the reviewer). Model from config.
- **markdown_to_storage** (`app/adapters/markdown.py`): the reverse of the FR-15 export converter —
  Confluence Cloud has no markdown body representation, so a Markdown draft must become storage-format
  XHTML to be a page. A small line-based converter over the Author's constrained subset rather than a
  CommonMark dependency (AD-21). Escapes user text first, so a PRD with angle brackets can't inject
  markup (tested).
- **Publisher** (`app/agents/publisher.py`, S3.3): create / adopt-orphan / reuse-known-id for the
  draft page (AD-11), placed in the draft folder via the v1 move (AD-14), stamped with the
  `agent-generated` label + `prd_id` content property (AD-10/AD-11).
- **Review ticket + framed request** (S3.4/3.5): `TicketManager.create_review_ticket` (find-or-create
  by marker), and `app/agents/review_request.py` building the FR-07 comment — a **real @mention**
  (not plain text that notifies nobody), the §6.2 structured format, the users'-shoes framing, and the
  Done-only pass rule. All four requirements are asserted by test, because the wording is a product
  decision that an LLM could quietly drop.
- **Orchestration** (`handlers_authoring.py`): fills `Stage.DRAFTED` — draft → publish → Review ticket
  → framed comment → **park at `awaiting_review`** (AD-15). Re-run adopts the recorded page/ticket ids
  rather than duplicating (AD-11), proven by test.

The flow now walks `detected → confirmed → prd_ticket_done → drafted → awaiting_review` and parks on
the PM. **Suite: 363 passed. ruff clean. 5/5 contracts.**

**Next:** Epic 4 — Human Review & Revision Loop (feedback ingest + typed routing, apply feedback,
detect PASS, structure-confirmation and clarification sub-loops, late-feedback handling).

---

## 2026-07-24 · Session 1 (cont.) — Epic 4: Human Review & Revision Loop · **COMPLETE (6/6)**

The first **webhook-driven** epic: a parked run reacts to a PM comment or a gate transition, rather
than the graph driving it. Added two event-application methods to the orchestrator, both under the
serial lock (AD-5): `apply_pm_comment` and `apply_gate_done`.

- **`FeedbackDecision`** (`app/domain/feedback.py`, S4.1): the typed decision (AD-16) with four routes
  and the four FR-08 clarification triggers as a closed enum. `__post_init__` rejects a CLARIFY with
  no trigger and an APPLY with no feedback — EH-08's "may not block outside the enumerated cases" as a
  construction-time invariant.
- **Feedback interpreter** (`app/agents/feedback_interpreter/`, S4.1): the LLM half — reads a comment,
  returns a `FeedbackDecision`. Parse failure raises rather than defaulting to a route.
- **`route_feedback`** (`app/orchestrator/feedback_routing.py`, S4.1): the deterministic half — a pure
  total function (decision × stage → action), unit-tested on hand-built decisions with no LLM. This is
  the exact AD-16 split: fake the LLM, test the routing.
- **`on_revising`** (S4.2): apply the confirmed feedback → update the draft in place → summarize →
  re-request → park at `awaiting_review`. `review_round++` per applied round; `pending_feedback`
  cleared so a later resume can't re-apply it. Tested that the loop runs 3 rounds but does **not**
  self-spin — a fourth `advance()` with no fresh comment is a no-op (NFR-09).
- **Structure-confirmation** (S4.4): plain feedback → restate in §6.2 → park
  `awaiting_structure_confirm` → block until the PM confirms (EH-08).
- **Clarification** (S4.5): a trigger → post the question → park `awaiting_clarification` → block.
- **PASS detection** (S4.3): `apply_gate_done` matches the Review ticket key and advances to `passed`;
  a Done on any other ticket is ignored. The agent only *detects* — never transitions a gate (AD-15).
- **Late feedback** (S4.6): a comment outside the review stages is a no-op (EH-06).

`pending_feedback` was added to the state record + schema so confirmed feedback survives to the
revising stage across a crash (AD-11).

**Suite: 390 passed. ruff clean. 5/5 contracts.** The flow now runs the full human loop:
`awaiting_review ⇄ revising` and `→ passed` on PM Done.

**Next:** Epic 5 — Approval & Publishing (Publishing ticket for the Head of Product, the publish
gate, and the ordered idempotent publish transaction: restrict → move → export → complete).

---

## 2026-07-24 · Session 1 (cont.) — Epic 5: Approval & Publishing · **COMPLETE (3/3)**

- **`on_passed`** (S5.1, FR-13): posts a PASS confirmation to the Review ticket, creates (or adopts by
  marker) the Publishing ticket for the Head of Product in the Main project, parks at
  `awaiting_publish_approval`. Tested that it does **not** publish on its own — creating the ticket is
  the second human gate (AD-15).
- **Publish gate** (S5.2, FR-14): reuses `apply_gate_done`, matched to `publishing_ticket_key`. A Done
  on any other ticket, or no action at all, leaves the run parked (no timeout).
- **`Publisher.publish`** (S5.3, FR-15, AD-18): the four ordered side-effects — restrict → move →
  export → (orchestrator marks complete) — each guarded by a `*_done` flag from the state record, so a
  resume skips what already succeeded. The edit restriction **always includes the agent account**
  (AD-18 — an empty/agent-less allow-list would lock the agent out of its own page; the adapter also
  refuses an empty list). The `.md` export is overwrite-safe (a resume re-export produces one file,
  tested). Sub-checkpoints are persisted via the context's progress callback → `update_fields` (a
  non-`stage` write; the orchestrator still owns `stage = complete`, AD-2).

`on_publishing` records each sub-checkpoint then advances to `complete`, stamping `md_export_path` and
`completed_at`. A test drives the whole tail: `passed → awaiting_publish_approval → (HoP Done) →
publishing → complete`.

**Suite: 402 passed. ruff clean. 5/5 contracts.** The entire happy path — detect → classify → draft →
review loop → PASS → publish gate → publish → complete — now runs end to end against fakes.

**Next:** Epic 6 — Resilience, Recovery & Operations (error surfacing + admin resume, the AD-22
liveness/reconcile sweep, AD-23 off-box backup, the 1 GB deploy + end-to-end run, config-only
modifiability check, content-gating flag). This is the epic with the most human/3rd-party gates
(BLOCKERS B-3/B-4/B-5), so several stories will land PARTIAL pending the live tenant and Droplet.

---

## 2026-07-24 · Session 1 (cont.) — Epic 6 + composition root · **ALL CODE COMPLETE**

Hardening, operations, and the production wiring that turns the tested pieces into a running service.

### Epic 6
- **6.1 Error + resume** (`app/agents/error_handler.py`): posts exactly one EH-01 comment on the
  ticket closest to the failure (review/publishing/tracking), with plain error + fix + `@admin`
  mention + literal `@agent resume` instruction + correlation id. `Orchestrator.apply_admin_resume`
  re-runs `last_good_checkpoint` only; a resume on a healthy run is a no-op.
- **6.2 Reconciler/liveness** (`app/admin/`): `Reconciler.sweep()` alerts stale parked/error runs
  once per threshold (`liveness_alerted_at`) and reconcile-polls the two gate tickets, feeding a found
  Done to `apply_gate_done` as an **input** — never a stage write (AD-2), agent never transitions a
  gate (AD-15). Exposed via an authenticated localhost `/admin/reconcile` for the cron sweep.
- **6.3 Backup** (`deploy/litestream.yml` + restore doc): built as an artifact; live Spaces
  replication needs B-4.
- **6.4 Deploy** (`deploy/` + CI): Dockerfile (slim, non-root, single worker), provision.sh (swap +
  443/22 firewall), Caddyfile (proxies only `/webhooks` + `/health`, never `/admin`), litestream,
  cron, and two GitHub workflows (CI + build-image-off-box). **The live deploy + end-to-end run is
  PARTIAL — needs the Droplet + tenant (B-3/B-4/B-5).**
- **6.5 1 GB envelope**: encoded in the Dockerfile/provision artifacts + asserted by tests.
- **6.6 Config-only modifiability**: tests prove a 2nd tenant routes and a PM swap is one field.
- **6.7 Content-gating**: `trace_content` — metadata-only by default; content never egressed unless
  a tenant opts in (tested).

### Composition root (the production wiring)
- `app/orchestrator/context.py` — the real per-run `RunContext` satisfying every handler protocol.
- `app/composition.py` — reads config, resolves credentials, builds per-tenant adapters (cached), the
  shared LLM client + tracer, the six agents, and registers **every** advancing-stage handler
  (a test asserts none is missing). Lazy construction, so building the wiring needs no credentials.
- `app/webhooks/router.py` — maps each authenticated+deduped event to the right orchestrator call
  (page → advance, PM comment → apply_pm_comment / admin resume, gate Done → apply_gate_done), then
  surfaces any resulting error (EH-01). Returns 2xx for handled drops so Atlassian doesn't retry.
- `app/main.py` — `create_app()` factory with a lifespan handler; serves `/health` even without
  config, mounts webhook + admin routes when config is present.

**Smoke-tested the running app:** `/health` → 200; unauthenticated webhook → 401; admin without
token → 401; all routes mounted. The walking skeleton is fully assembled.

**All 5 architecture contracts still hold** with the composition root, webhook router, admin endpoint,
and wiring added — 95 files, 354 dependencies analyzed, 0 broken.

**Suite: 451 passed. ruff clean. 5/5 contracts.**

### Status
**38 / 39 stories DONE in code** (S6.4 is PARTIAL — the live deploy). Two live-only verifications wait
on credentials/infra: the S2.4 classifier 0-FP/0-FN eval (Anthropic key, B-1) and the S6.4 end-to-end
demo run (Droplet + tenant, B-3/B-4/B-5). Everything is built and unit-tested offline; when the
credentials arrive, the remaining work is running two commands and walking the two gates.

---

## 2026-07-24 · Session 1 (cont.) — LIVE integration against the real tenant

Nhan completed third-party setup (`verify_setup.py` green). Ran the live work.

### Security remediation (first)
Real secrets (Atlassian token, Anthropic key, webhook/admin secrets, LangSmith key) had been pasted
into the **git-tracked** `.env.example` working copy. Verified they were **never committed** and `.env`
is correctly gitignored; restored `.env.example` to placeholders. Also restored
`config/registry.example.yaml` (deleted from the working tree during setup). `config/registry.yaml`
(the real one) is left untracked — decision pending (see below).

### Live findings & fixes (things offline fakes could not catch)
1. **`temperature` → 400.** Claude Sonnet 5 / Opus 4.8 reject sampling params (removed on the Claude
   5 family / Opus 4.7+). Removed `temperature` from `LlmClient.complete` and the classifier/feedback
   call sites; determinism now comes from the SKILL.md rubric. (D-15)
2. **Confluence v2 folder children are at `/direct-children`, not `/children`** (the latter 404s to the
   web UI). Fixed `find_page_by_prd_marker` (AD-11 orphan adoption).
3. **Composition didn't thread the triggering page event** → detection crashed on `None`. Reworked
   `RunContext` to fetch the page live (`get_page_event` / `page_markdown` / `confluence_space_id`) and
   added `composition.stash_event` so the webhook layer hands the parsed event in; added `page_url`.
4. **Single-account demo:** the demo PRD is created with the agent's own token, which AD-10's
   self-author guard (correctly) declines. Faithful in production (human PM ≠ agent service account,
   per SETUP-GUIDE Part 1). The demo driver presents the event as authored by the configured PM — the
   real "a human uploaded this" input a webhook carries.

### Live verification — PASSED
- **S2.4 classifier eval:** ran `scripts/run_classifier_eval.py` against Claude — **0 FP / 0 FN,
  stable across 3 runs** on the holdout set (15 classifications). The demo's one objective quality
  gate, now genuinely met.
- **Adapters:** live read-only smoke — Jira + Confluence auth, folder read, JQL search all work.
- **S6.4 happy path (phase 1):** `scripts/run_local_demo.py` created a real PRD page, and the flow ran
  live: detect → classify (Claude ACCEPT) → tracking ticket **AMS-11** created & driven to Done →
  **Opus 4.8 drafted + self-critiqued** the UserDoc → published to the draft folder (**page 1540119**,
  stamped `agent-generated`) → Review ticket **UDR-1** created, assigned to the PM, framed comment
  posted → parked at `awaiting_review`. The drafted guide is genuinely good (task-based, user's
  language, a stated assumption about the shortcut key).

### Where it stopped — a human gate (by design)
The run is parked at `awaiting_review`. AD-15 forbids the agent moving a gate ticket, so this is the
correct hand-off point. **Remaining is human action:** the PM moves UDR-1 to Done (→ PASS → Publishing
ticket), then the Head of Product moves the Publishing ticket to Done (→ restrict/move/export →
complete). Drive with `scripts/run_local_demo.py --resume` after each. The webhook-driven form of all
this needs the Droplet deploy (B-4/B-5).

**Decisions recorded:** D-15 (no temperature — models reject it).

---

## Session 2 — 2026-07-24 · Live review loop (FR-09) + three defects fixed

**Trigger:** Nhan reported "I tried to reply with a feedback in Jira ticket but nothing runs, in the
Anthropic Console log also doesn't appear any Claude API calls."

**Diagnosis — not a bug, two gaps.** (a) No webhook endpoint is deployed, so a Jira comment reaches
nothing; the flow only runs when the local driver invokes it. (b) `run_local_demo.py --resume` only
polled the gate ticket for a Done transition — it never read comments, so even re-running did not
reach `apply_pm_comment`. The FR-09 loop was unreachable locally.

**Fixed (D-18):** `--resume` now reads the Review ticket's comments back and feeds the newest unseen
one to `apply_pm_comment`, before the gate check. Added `JiraAdapter.get_comments` (+ `JiraComment`)
and a `--baseline` escape hatch.

**Found while fixing — two real defects beyond the driver:**

1. **Comment self-ingestion (D-16).** Jira echoes the agent's own comments back as webhooks, and
   `_dispatch_comment` had no guard: the clarification question the agent posts would have returned
   as "the PM's reply" and been answered by the agent itself — an AD-16 violation. Fixed by claiming
   each posted comment's id in `processed_events` (AD-9) at post time, so the echo dedupes. Verified
   live: the round-1 change summary came back already marked seen.
2. **Author emitted raw HTML (D-17).** Asked for a two-column layout, it produced `<table>/<td>`,
   which the converter escaped into visible `&lt;table&gt;` text on the page. The SKILL.md never
   named the supported Markdown subset. Fixed in the prompt (subset + "state what you couldn't do"
   rule) and defensively in `markdown_to_storage` (drop tag-only lines).

Also fixed `adf.extract_text` dropping `hardBreak` nodes, which ran the PM's `Section:` / `Issue:` /
`Suggested change:` lines together into one string.

**Live result:** review round 1 completed end-to-end — feedback interpreted → APPLY_FEEDBACK →
draft revised (both points applied) → change summary posted → review re-requested. Run is parked at
`awaiting_review`, `review_round` = 1.

**Tests:** 451 → 462 passing. New: `tests/test_comment_self_ingestion.py` (4), `get_comments` +
hardBreak in `test_jira_adapter.py` (3), stray-HTML handling in `test_author_and_publish.py` (4).

**Not done / handed back:** the two human gates (AD-15 — only Nhan can move UDR-1 and the Publishing
ticket), and the round-1 formatting blemish on page 1540119, which needs a fresh human comment to
trigger a redraft (AD-16 forbids the agent self-spinning the loop).

---

## Session 2 (cont.) — 2026-07-24 · Publish gate failure → end-to-end COMPLETE

**Trigger:** Nhan reported the publish transaction failing when the Head of Product moved AMS-12 to
Done: `stage failed: stage=publishing operation=confluence.set_edit_restriction`.

**Diagnosis.** `403 PermissionException: Not enough permissions to alter ContentRestrictions`. Probed
the live tenant read-only and ruled out the obvious cause: the agent account **already** held
`restrict_content:space` and `administer:space`, and the content permission check returned
`hasPermission: true`. The real cause was the plan tier — `settings/systemInfo` returns
**`"edition": "free"`**, and page restrictions are not part of Confluence Cloud Free, which reports
the gap as a permission error. Recorded as B-7.

**Put to Nhan as a decision** (paid upgrade vs. relaxing a spec'd requirement vs. stopping) rather
than resolved unilaterally. **Nhan chose the config flag.**

**Implemented (D-21):** `TenantConfig.require_edit_restriction`, defaulting to **True** so the spec'd
path stays the default. Guardrails so the relaxation can't become a silent lie: a skip never records
`restriction_applied_at`; the agent posts an @-mention notice on the Publishing ticket telling the
Head of Product the page is *not* edit-restricted; the CLI banner is conditional.

**Two more defects fixed on the way:**
- **D-19** — the generic 403 advice ("grant the account access") was actively misleading here. Added a
  narrow `(status, operation)` override so a restriction 403 names the plan tier and the `systemInfo`
  probe first.
- **D-20** — an errored run was unrecoverable locally: `@agent resume` only existed on the webhook
  path, so B-7 left the demo with no way forward. Added `--admin-resume`.
- **D-22** — `md_export_dir` was the container path `/data/userdocs/alpha`; `/data` is a read-only
  filesystem on macOS, so FR-15 step 3 was the next domino. Caught by reading the path before
  re-running, not by a second failure.

**Live result — the demo is COMPLETE.** `--admin-resume` re-entered at `publishing` and finished.
Verified independently: page 1540119 now has `parent = 1441796` (published folder); the export exists
at `data/userdocs/alpha/1441969-final-prd-quick-notes.md` (2038 bytes, and grep-clean of the earlier
stray `<table>` markup); the "not edit-restricted" notice is on AMS-12 tagging the Head of Product.
Full path: detect → classify → AMS-11 → draft → UDR-1 → 2 feedback rounds → PASS → AMS-12 →
approval → move + export → `complete`.

**Tests:** 462 → 469. New: publisher opt-out + "never checkpoint a skipped restriction" + the
Head-of-Product notice (`test_publishing.py`), and the 403 message overrides (`test_operations.py`).

**Honest status:** every FR has now run live **except** FR-15 step 1 (never executed — no plan
supports it) and the **webhook ingress layer**, which has only ever run offline. The entire live demo
was driven by `scripts/run_local_demo.py` standing in for webhooks. S6.4 is not fully closed until
the service is deployed and a real Atlassian delivery starts a run (B-4/B-5).

---

## Session 3 — 2026-07-25 · Conversational review loop (FR-10a)

**Trigger:** Nhan wanted the review loop to be a real brainstorm — the agent should have context and
memory of the discussion and handle nuanced replies, not just a bare yes/no to its restatement.

**Diagnosis.** Verified the interpreter classified bare yes/no correctly (live) and the "yes" path
was wired + tested. But the interpreter judged confirmations **blind** — the prompt omitted the
question and the restatement — so "yes but…" / "no, I meant…" couldn't work. Writing the first
end-to-end "no" test then exposed a latent crash: `awaiting_structure_confirm → awaiting_review` was
an illegal state edge, so a plain "no" raised `IllegalStageTransition` (uncaught → HTTP 500 → Atlassian
retry loop). The "no" path had never had end-to-end coverage.

**Built (D-30 / D-31):**
- The Feedback interpreter now receives the review-ticket **transcript** (PM/agent-labelled) + the
  **pending restatement** on every interpretation (new `ReviewTurn`; `TicketManager.discussion()` +
  `JiraAdapter.get_comments`; assembled in `RunContext.interpret_comment`, best-effort).
- SKILL.md rewritten for conversational behavior (confirm / confirm-with-adjustment / redirect /
  bare-reject).
- Added the missing `awaiting_structure_confirm → awaiting_review` state edge; the not-confirmed
  branch now posts an acknowledgment (@-mention) asking what to change, and clears the stale
  restatement — no silent dead-end, no crash.
- AD-16 preserved: input enrichment only; typed decision + deterministic routing unchanged.

**Live verification** (real model): "yes"→apply as-is · "yes, but keep sharing as 'coming soon'"→
APPLY with the tweak folded in · "no, I meant Search is too thin"→CONFIRM_STRUCTURE with a fresh
Search restatement · bare "no"→confirmed=false → orchestrator asks what to change.

**Planning docs amended** (per Nhan's instruction to keep it written): PRD FR-10a, Spine AD-16 note,
solution-design review-loop + state diagram — all marked "amendment 2026-07-25".

**Tests:** 484 → 489. New: interpreter-carries-transcript, RunContext PM/agent labelling, transcript
degrades on failure, empty-`structured_feedback` fallback, bare-"no" acknowledgment + legal edge.
Suite green, ruff clean, 5/5 contracts.

---

## Session 3 (cont.) — 2026-07-25 · Rename detour ate the Review ticket (D-32)

**Reported:** wrong-name PRD → rename request filed → PM renames → draft created but **no
draft-review ticket**, unlike a correct first upload.

**Root cause:** the rename request and the Review ticket both live in the Review project with the same
`prd-<id>` marker. `find_issue_by_prd_marker` returned the oldest (`ORDER BY created ASC LIMIT 1`) =
the rename ticket, so drafting adopted it as the Review ticket and never created a real one. The
publishing handler had a guard against the analogous tracking/publishing collision; drafting did not.

**Fix:** type-aware marker search (`summary_prefix`), filtering within a bounded set so it adopts a
real orphan of the right type but never mistakes another type for it. Both handlers routed through it;
the fragile inline `startswith` check in publishing removed. AD-4-clean (summary text, not a label).

**Tests:** 489 → 492. New: adapter typed-skip + none-when-only-other-type; handler
after-rename-creates-Review. Suite green, ruff clean, 5/5 contracts.

---

## Session 3 (cont.) — 2026-07-25 · Ticket authorship / attribution (D-33)

**Asked:** why do agent tickets look authored by a human account, and why does the Publishing ticket
show the **Head of Product as both Reporter and Assignee**? Plus: can tickets be made visibly
agent-authored so the team knows the flow created them?

**Audit (5-agent adversarial verification).** Two independent causes:
1. **Single-account auth.** One Basic-auth identity per product per tenant (the `.env` token); it is
   the immutable Jira **Creator** of every ticket/comment/page. No per-role token switching anywhere.
   (Nuance: Jira and Confluence resolve *separate* refs `ALPHA_JIRA`/`ALPHA_CONF`; AD-10 *assumes*
   they are the same account but nothing enforces it.)
2. **A lone Reporter override.** `create_publishing_ticket` was the only create passing
   `reporter_account_id` (= HoP), from a literal reading of FR-13 "reported to … the Head of Product".
   Undocumented, untested, inconsistent with the other three tickets, and misleading (the agent files
   it). The binding Spine renders FR-13 as "@Head of Product" — assignee/mention, not a Reporter field.

**Shipped (all three routes Nhan chose):**
- **Route 2** — removed the reporter override; the Publishing ticket keeps `assignee = HoP`, Reporter
  now defaults to the agent account like every other ticket.
- **Route 3** — all four ticket creates now stamp the reserved `agent-generated` label (via
  `create_issue(extra_labels=…)`): an in-Jira, filterable "from the agent flow" marker, account-agnostic.
- **Route 1** — documented as a human gate: [BLOCKERS.md](BLOCKERS.md) B-8 + SETUP-GUIDE Part 1
  ("Recommended: a dedicated UserDoc Agent account"). Only this changes the immutable Creator field;
  it needs an org admin + a licensed seat, so it is provisioning, not code.

**Tests:** 492 → 496. New: all-four-tickets-labelled; Publishing assigns HoP with no reporter; Review
assigns PM with no reporter; adapter carries `extra_labels` into the payload. The fake `create_issue`
now captures assignee/reporter/labels. Suite green, ruff clean, 5/5 contracts. Recorded as **D-33**.

---

## Session 3 (cont.) — 2026-07-25 · CI red but deployed anyway → pipeline hardening (D-34)

**Reported:** Nhan saw the `test` workflow red on GitHub while the change had already deployed, and
asked whether a workaround had shipped and whether things were clean.

**What happened.** The D-33 push (9e483a3) failed CI on **`ruff format --check`** — an over-long test
function name. Locally only `ruff check` had been run, not `ruff format --check` (CI runs both). It
was **not** a functional failure (496 passed locally) and **not** a workaround — but the deploy
succeeded despite red CI because `build-image.yml` built the image **independently** of `ci.yml`, with
no dependency. Two gaps: a red gate could still ship, and the format check wasn't in the local loop.

**Fixed the immediate red (b62288e):** shortened the test name; verified all four CI steps locally;
redeployed the Droplet to `sha-b62288e` so deployed == green HEAD (app code was byte-identical).

**Then hardened the pipeline (D-34), per Nhan's request:**
1. **Build gated on tests.** Merged the image build into `ci.yml` as a `build` job with `needs: test`
   + a ref guard (master / `v*` tag / manual dispatch); deleted `build-image.yml`. A red `test` now
   leaves `build` skipped — no image ships. Still off-box (AD-21). Asserted in `test_operations.py`
   (`needs: test` present; build steps in `ci.yml`).
2. **Local mirror of CI.** Committed `.githooks/pre-push` (enable: `git config core.hooksPath
   .githooks` or `make hooks`) + a `Makefile` (`check`/`lint`/`test`/`format`) running the exact four
   gates. `--no-verify` is the documented bypass. `core.hooksPath` set locally this session.

**The net proved itself while being built:** `make check` caught (a) a Make built-in `LINT` variable
collision and (b) `test_operations.py` still asserting the removed `build-image.yml` — both before CI.
Also updated `deploy/README.md` to the new build path.

**Tests:** 496 → 496 (net: −`build-image.yml` artifact test, +`needs: test` gate test). Suite green,
ruff clean (incl. `ruff format --check`), 5/5 contracts. Recorded as **D-34**.

---

## Session 4 — 2026-07-25 · Two resilience features + a deep adversarial audit

**Trigger:** Nhan asked for (1) draft-deletion detection + recovery, (2) rename-churn protection, and
a deep audit for any remaining mismatch/dedup/illogic/inefficiency.

**Audit.** Ran a 7-dimension adversarial audit workflow (opus finders + skeptic verifiers). It hit the
org monthly spend limit partway (16 of 39 agents errored), but returned 4 verified findings plus a
high-rated state-machine finding the verifiers couldn't reach. All fixed:
- **record-after-advance** (rename re-entry recorded its dedupe key before the work → a crash
  stranded the run behind a committed key). Now recorded after `advance`.
- **source-folder admission gate** — a page created anywhere in the space was admitted then left a
  dead `detected` row; now refused at the door unless in the source folder (container/ancestors).
- **`_first` list-index traversal** — the `ancestors.0.id` container fallback was dead code.
- **draft-deletion dead-end** (= Feature A below).
- **state-machine cross-edges** structure-confirm ↔ clarification (D-37) — the D-31 class of 500,
  now reachable because of the conversational interpreter (D-30).

**Feature B — rename-churn guard (FR-01a / AD-24, D-35).** An existing run re-processes a source-page
event only while parked awaiting a rename correction (`UPLOADING_PM_RENAME`); past detection it's
dropped before the version GET. Toggling a name after drafting is a no-op — no duplicate tickets/
drafts.

**Feature A — draft-deletion recovery (FR-16 / AD-25, D-36).** New `page_trashed` event → recover the
draft: restore from trash in place (same id → ticket link survives), else recreate with the exact
latest content; @-mention the PM on the Review ticket; self-recover an errored run. The same recovery
runs defensively before publish, so a missed deletion no longer dead-ends `@agent resume`. Needs a
third Confluence Automation rule (SETUP-GUIDE Part 7b).

**Tests:** 492 → 515. New: `tests/test_draft_recovery.py` (9) + trash routing/parsing + rename-churn
guard + source-folder gate + cross-edge + publish self-heal. Suite green, ruff clean, 5/5 contracts.

**Planning docs amended** (dated 2026-07-25): PRD FR-01a + FR-16; Spine AD-24 + AD-25; solution-design
note; SETUP-GUIDE Part 7b (three Automation rules, Custom-data body).

**Blocker noted:** the audit workflow was cut short by the org **monthly spend limit** — some verify
agents didn't run. The confirmed findings are solid; a re-run for full verification is possible once
the limit resets (`/usage-credits`).

---

## Session 5 — 2026-07-26 · FR-16 becomes human-gated (ask before recover) + the real bug

**Reported:** deleted draft not recovered despite the Automation firing; and a requirement change —
**never auto-recover**; ask the reviewing PM first, restore only on their confirmation of a mistake.

**Bug (from the live logs):** the deleted draft (page 2064481, run 2195475) arrived as a *page-updated*
event, not *page-trashed*, so `_is_agent_output` dropped it (the draft has the `agent-generated` label)
before any trash logic. Detection trusted the event label, not the page's real status.

**Built (D-38):**
- Robust detection — a page event for a run's own draft is judged by live *status*; trashed/missing →
  deletion flow, whatever the event label. Healthy draft edits and non-draft trashes are ignored.
- Ask-first — `apply_draft_deleted` posts a question (@mention PM), parks with a
  `pending_deletion_page_id` marker + `PM_DELETION_DECISION` gate; recovers nothing.
- `apply_deletion_decision` — classifies the PM reply (RESTORE/LEAVE/UNCLEAR via the Feedback
  interpreter), restores only on RESTORE, re-asks on UNCLEAR, self-heals an errored run on restore.
- `on_publishing` no longer auto-recovers — it refuses a missing draft with an actionable error.
- New `pending_deletion_page_id` column + idempotent additive DB migration (the live store predates it).

**Tests:** 515 → 524. Rewrote the recovery tests for ask-first; added the robust-detection,
comment-routing, migration, and classifier tests. Suite green, ruff clean, 5/5 contracts.

**Docs:** PRD FR-16 and Spine AD-25 rewritten (human-gated); SETUP-GUIDE note updated (status-based
detection; ask-first). Supersedes D-36's auto-recovery.

---

## Session 6 — 2026-07-26 · Tracking ticket skipped for same-named PRDs (D-39)

**Reported:** after a wrong-named PRD is renamed correctly, the PRD tracking ticket is no longer sent.

**Diagnosis:** the detection→tracking flow is correct in code — two new re-entry tests (handler-level
and dispatch-level) both pass. Querying the live tenant directly (Jira, no SSH) showed the truth:
page 2129949 ("final_PRD_booth_app", a renamed *copy*) had rename/Review/Publishing tickets but no
tracking ticket. Root cause: `_search_by_name` (the FR-04 name fallback) matched other booth_app runs'
tickets — Nhan tests with copies that share the title — and adopted one instead of creating a new
tracking ticket.

**Fix (D-39):** `_search_by_name` excludes `agent-generated` tickets (a same-named match from another
run is the agent's, never adopt it — this run's own is found by the marker); and the tracking marker
search is typed to "PRD tracking:" so it can't adopt the Publishing ticket sharing the marker.

**Tests:** 524 → 528. New: two rename→tracking re-entry tests, and two ticket-manager regression tests
(don't adopt another run's same-named ticket; still adopt a human's). Suite green, ruff clean, 5/5.

---

## Session 7 — 2026-07-26 · Inline-comment feedback channel (FR-17 / AD-26, D-40)

**Requested:** let a reviewer give feedback by leaving a **Confluence inline comment** on the draft;
the agent should post it on the Jira Review ticket **@-mentioning the exact commenter** (not the config
PM), anchor it to the highlighted "section", **propose a solution if none was given**, and then let the
conversation-aware Feedback interpreter drive the back-and-forth.

**Research (offline):** confirmed via Atlassian docs that the *Page commented* Automation trigger fires
for inline **and** footer comments (no per-type trigger), that comment smart values (`{{comment.id}}`,
`{{comment.author.accountId}}`) populate only on that trigger, and that the highlighted passage is
**not** a smart value — so the agent must re-read the comment. The tenant currently has no live comments
to sample, so the adapter parses defensively (v1 primary — where the other Confluence exceptions live
and which reports inline-vs-footer via `extensions.location`; v2 fallback for the documented v2 404).

**Built:**
- `ConfluenceCommentEvent` + `EventType.CONFLUENCE_INLINE_COMMENT_CREATED`; parser (`page_commented`
  marker + structural `comment`+`page` fallback); tenant routing (space key / single-tenant); dispatch
  `_dispatch_confluence_comment` (acts only if the comment's page is a run's `userdoc_page_id`).
- `ConfluenceAdapter.get_inline_comment` (v1-primary, v2 fallback, tolerant parse) → `InlineComment`.
- `FeedbackInterpreter.restate_inline_comment` → `InlineRestatement` (proposes a fix, flags it);
  SKILL.md gained the propose-a-solution skill for the ordinary `CONFIRM_STRUCTURE` route too.
- `Orchestrator.apply_inline_comment` — reads, restates, posts @-mentioning the exact commenter, parks
  at `AWAITING_STRUCTURE_CONFIRM` (reusing the existing conversation loop for the reply). New
  `active_reviewer_account_id` column (additive migration) so the whole sub-conversation addresses the
  commenter; review-loop @-mention helpers take a `mention_id`, cleared when the feedback resolves.

**Tests:** 528 → 548 (+20). Adapter v1/v2/footer, parse + structural fallback, routing, interpreter
restatement (incl. propose-solution + parse-failure), orchestrator pickup + exact-commenter mention +
hand-off + state round-trip. Suite green, ruff clean, 5/5 contracts.

**Docs:** PRD FR-17, Spine AD-26, DECISION-LOG D-40, SETUP-GUIDE Part 7c (the 4th Automation rule).
**Live-activation pending:** the *Page commented* Automation rule + a Droplet redeploy.
