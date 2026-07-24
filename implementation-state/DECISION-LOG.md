# Decision Log — Implementation

Append-only. One entry per judgment call made **during the build** that is not already settled by the
PRD, the Architecture Spine, or `epics.md`. Newest at the bottom.

Record a decision here when: the source docs left a choice open, an assumption had to be made to keep
moving, or reality (an API, a library, the host) contradicted the plan.

**Entry format**

```
### D-NN · <short title>            (YYYY-MM-DD, story S<X.Y>)
**Context:**   what forced a choice
**Decision:**  what was chosen
**Rationale:** why, and which AD/FR it serves
**Alternatives rejected:** ...
**Revisit if:** the condition that would overturn this
```

---

### D-01 · Adopt an in-repo `implementation-state/` system as the resume contract  (2026-07-24, bootstrap)
**Context:** The build runs autonomously across sessions that may be interrupted or run out of context.
Nothing in the planning artifacts specifies how build progress is persisted.
**Decision:** Four files under `implementation-state/` — `STATE.md` (single resume pointer),
`EPIC-STORY-TRACKER.md` (per-story status), `DECISION-LOG.md` (this file), `SESSION-LOG.md`
(chronological journal) — plus `BLOCKERS.md` for human gates. `CLAUDE.md` names `STATE.md` as the
mandatory first read of every session.
**Rationale:** Mirrors the system's own architecture: one authoritative state record (`STATE.md`) with
an append-only event log beside it. A cold start reads two files, not five planning documents.
**Alternatives rejected:** tracking progress in git commit messages (no git repo yet, and commit
history is a poor query surface); a single monolithic notes file (mixes the "where am I" pointer with
history, so the pointer gets buried).
**Revisit if:** the repo gains git + an issue tracker that supersedes the story tracker.

### D-02 · Local Python 3.12 via pyenv, pinned with `.python-version`  (2026-07-24, story S1.1)
**Context:** The Spine Stack table specifies Python 3.12 (`python:3.12-slim` base image). The machine's
default is 3.14.0.
**Decision:** Install 3.12.12 via pyenv and pin it per-project with `.python-version`, so local dev
matches the container base.
**Rationale:** Avoids a class of "works locally, fails in the image" bugs on a 1 GB box where debugging
is expensive (AD-21). 3.12 was chosen in architecture as the conservative widely-supported base.
**Alternatives rejected:** developing on 3.14 and trusting the pins (the deps support 3.14, but the
deployed artifact is what matters and divergence buys nothing).
**Revisit if:** a required dependency turns out to have no 3.12 wheel.

### D-03 · `env:PREFIX` credential references expand to a three-variable triple  (2026-07-24, story S1.2)
**Context:** PRD §11 specifies `jira_credentials_ref: "env:ALPHA_JIRA"` but does not say what the
reference resolves *to*. Atlassian Cloud REST needs three values (base URL, account email, API token)
for both Jira v3 and Confluence v2/v1.
**Decision:** the reference names an environment *prefix*; `env:ALPHA_JIRA` resolves
`ALPHA_JIRA_BASE_URL`, `ALPHA_JIRA_EMAIL`, `ALPHA_JIRA_API_TOKEN`. Resolution is lazy and explicit —
loading the registry never requires secrets, and a missing variable raises an error naming the exact
variable to set.
**Rationale:** keeps the PRD's literal `env:NAME` syntax, stays 12-factor (NFR-07), and keeps the whole
unit suite runnable offline with an empty environment. A missing credential fails with an actionable
message rather than surfacing later as a confusing 401.
**Alternatives rejected:** packing three values into one env var (unparseable, easy to corrupt);
resolving eagerly at load (would make every test need credentials).
**Revisit if:** OAuth replaces API-token auth, which needs a different credential shape.

### D-04 · LLM model ids live in system config, not per-tenant config  (2026-07-24, story S1.2)
**Context:** AD-17 requires the classifier model id to be "pinned in config (AD-4)". AD-4 governs
*project-specific* literals.
**Decision:** model ids sit in `SystemConfig.models` (classifier / author / feedback_interpreter),
not in each `TenantConfig`.
**Rationale:** a model id is identical for every tenant, so it is not a project literal — repeating it
per tenant would invite two tenants drifting onto different models and silently invalidating the AD-17
holdout eval. Still fully "pinned in config", still a config-only change.
**Alternatives rejected:** per-tenant model ids (duplication with no demo value); hard-coded model ids
at the call site (directly violates AD-17).
**Revisit if:** a tenant needs a different cost/quality tradeoff — add an optional per-tenant override
rather than moving the default.

### D-05 · Encode a legal-transition map for the §9 stage machine  (2026-07-24, story S1.3)
**Context:** The PRD lists the §9 stages but not which transitions are legal. The solution design §4
has a state diagram; the Spine does not bind it.
**Decision:** encode that diagram as data in `app/domain/stage.py` and have `advance_stage()` reject
edges it does not contain. Self-transitions and `* → error` and `error → *` are always legal.
**Rationale:** the most dangerous possible bug in this system is a run skipping a human gate — exactly
what AD-15 exists to prevent. A rejected transition makes that failure loud instead of silent. The
tests assert specific gate-skipping edges (`awaiting_review → publishing`, `drafted → passed`) are
refused, which turns AD-15 from prose into an executable invariant.
**Two additions beyond the literal diagram:** (a) `prd_ticket_done → awaiting_clarification`, because
FR-08 applies to "drafting/redrafting" and so can fire before the first draft, which the diagram does
not show; (b) `awaiting_clarification` may return to `prd_ticket_done`, `awaiting_review`, or
`revising`, since where a clarification returns to depends on what asked for it — the orchestrator
restores the stage recorded in `last_good_checkpoint`.
**Alternatives rejected:** no validation (loses the AD-15 safety net); a strict reading of the diagram
only (would make a legitimate FR-08 clarification-before-first-draft impossible).
**Revisit if:** a story needs an edge this map lacks — add it here deliberately rather than loosening
the check.

### D-06 · `ruff format` owns code width; E501 disabled in lint  (2026-07-24, story S1.2)
**Context:** With `line-length = 100`, `ruff format` normalizes all code, leaving E501 firing only on
prose inside docstrings and comments.
**Decision:** keep `line-length = 100` for the formatter, add `ignore = ["E501"]` to the linter.
**Rationale:** the formatter is authoritative for code layout; hard-wrapping explanatory prose to fit
a column hurts readability more than it helps. This is the mainstream ruff setup.
**Revisit if:** the team wants enforced prose width — use a docs-specific tool instead.

### D-07 · Tenant resolution runs before the dedupe check  (2026-07-24, stories S1.4–1.6)
**Context:** AD-8 states the ingress order as "validate → dedupe → route". Taken literally that is
unbuildable: AD-9's dedupe key *begins with* the tenant id
(`<tenant_id>:<event_type>:<entity_id>:<version_marker>`), so the key cannot be computed before the
tenant is known. The two decisions are in tension as written.
**Decision:** the implemented order is **authenticate → parse → resolve tenant → dedupe-check →
admit**. Tenant resolution is a pure in-memory config lookup — no socket, no state write, no agent call.
**Rationale:** both decisions' actual guarantees are preserved. Nothing at all happens before the
signature is validated (AD-8's real point — an unauthenticated request must not touch anything); no
*work* begins until both a tenant is resolved and dedupe has passed; and the key is the full AD-9
composite including the tenant component. Reading "route" as "work" would make AD-9's key
unimplementable. Tests assert an invalid signature causes zero state writes and zero recorded events.
**Alternatives rejected:** a tenant-agnostic pre-check on entity+version (contrived, and it would be a
second dedupe scheme — exactly what AD-9 forbids); dropping the tenant from the key (breaks tenant
isolation, since two tenants could then collide on the same page id).
**Revisit if:** the Spine is amended — this is a documented reconciliation of two bound decisions, so
it is worth confirming with the architecture owner.

### D-08 · Webhook event *types* live in `app/domain/`, parsing stays in `app/webhooks/`  (2026-07-24, story S1.6)
**Context:** `app/router.py` needs the event types to dispatch on. Importing them from
`app/webhooks/events.py` is an upward dependency — a real AD-1 violation, caught by import-linter.
**Decision:** the event types (`ConfluencePageEvent`, `JiraCommentEvent`, `JiraIssueUpdatedEvent`,
`EventType`) moved to `app/domain/events.py`; `app/webhooks/events.py` keeps only the payload parsers.
**Rationale:** the types describe *what happened* — independent of the HTTP transport that carried the
news — so they are domain concepts. Parsing a specific vendor's JSON shape is genuinely a transport
concern. The layering violation was a symptom of the types being in the wrong place, not of the rule
being too strict.
**Revisit if:** never expected to — this is the correct decomposition.

### D-09 · Forbidden-module contracts check direct imports only  (2026-07-24, story S1.6)
**Context:** With `include_external_packages = true` (required for the contracts to run at all),
import-linter follows chains *through* third-party packages. `app.orchestrator → langgraph → httpx`
would register as a violation.
**Decision:** set `allow_indirect_imports = "true"` on the three forbidden contracts.
**Rationale:** the rule being enforced is "this module must not *itself* open a socket / run SQL /
call the LLM". A library reaching httpx internally is not a boundary violation, and flagging it would
train people to ignore the contract — which costs more than the rule is worth. The layers contract
still catches indirect app-to-app inversions, which is where the real risk lives.
**Revisit if:** a genuine indirect violation slips through — then tighten with an explicit allowlist
rather than removing the flag.

### D-10 · Adapters and agents are async; the repository stays sync  (2026-07-24, story S1.7)
**Context:** The service is a FastAPI app. A drafting run makes many sequential Atlassian calls plus
LLM calls, and the architecture does not state whether the adapter surface is sync or async.
**Decision:** `AtlassianClient`, both adapters, the agents, and the orchestrator are **async**
(`httpx.AsyncClient`). The repository stays **sync** (`sqlite3`).
**Rationale:** blocking the event loop for the length of a drafting run would stall webhook
acknowledgement, and Atlassian treats a slow response as a delivery failure — which then triggers
redelivery, which the dedupe layer has to absorb. SQLite stays sync because it is an in-process file
read measured in microseconds, guarded by a lock, with the serial queue (AD-5) guaranteeing no
contention; making it async would add a thread pool and RAM for no benefit on the 1 GB box (AD-21).
**Alternatives rejected:** sync throughout (stalls webhook ack); async SQLite via `aiosqlite` (a
dependency and a thread pool to solve a problem that does not exist at this volume).
**Revisit if:** the serial queue is lifted for true parallel multi-tenancy — re-measure then.

### D-11 · `env:PREFIX` auth headers are attached per-request  (2026-07-24, story S1.7)
**Context:** A test injecting its own `httpx.AsyncClient` silently lost the `Authorization` header,
because auth was baked into the client at construction time.
**Decision:** build the header dict once in `__init__` and pass it on every request.
**Rationale:** an injected or pooled client must not be able to silently drop authentication — that
failure mode surfaces as a confusing 401 far from its cause. Found by a test, fixed in the design
rather than worked around in the test.
