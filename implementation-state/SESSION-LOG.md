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
