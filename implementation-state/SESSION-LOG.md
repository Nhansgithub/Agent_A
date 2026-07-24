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
