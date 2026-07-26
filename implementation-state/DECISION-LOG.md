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

### D-12 · `AgentError` is a plain Exception class, not a dataclass  (2026-07-24, story S1.9)
**Context:** `AgentError` was `@dataclass(frozen=True, slots=True)`. When a stage handler raised one,
LangGraph's node machinery produced `TypeError: super(type, obj): obj must be an instance or subtype
of type` instead of the error — so every error path was broken, not just cosmetically wrong.
**Decision:** rewrite as a plain `Exception` subclass with an explicit `__init__`, `__slots__`,
`__str__`, and `__reduce__`.
**Rationale:** `slots=True` creates a *new* class object, and the dataclass-generated methods close
over the original — so copying or re-raising the instance fails. Frameworks re-raise exceptions
freely, so an exception type must survive that. The boring implementation is the correct one here.
`__reduce__` keeps every field intact across copy/pickle.
**Found by:** the Story 1.9 orchestrator tests, which were the first code to raise an `AgentError`
through a framework rather than catching it locally.
**Revisit if:** never — this is a language-level constraint, not a preference.

### D-13 · Tracing is structural, so it cannot be configured off  (2026-07-24, story S1.10)
**Context:** NFR-01 requires 100% of LLM calls traced. The obvious implementation — branch on
`langsmith_enabled` and skip tracing when off — makes that guarantee depend on configuration.
**Decision:** every call goes through a tracer unconditionally. Disabling LangSmith swaps in a
`NullTracer` that still opens a span and logs latency/tokens; it does not remove the span. Tracing
lives *inside* `LlmClient`, which a test enforces is the only module importing the Anthropic SDK.
**Rationale:** "100% of calls are traced" should be a property of the code shape, not of a config
value someone can flip. With one door to Claude and a span inside it, there is no path that skips
tracing — the same reasoning as AD-1 putting all Atlassian I/O behind two adapters.
**Alternatives rejected:** decorating each agent's call site (any new agent could forget);
conditionally skipping the span (makes the NFR configuration-dependent).
**Revisit if:** span overhead ever shows up in the 1 GB memory envelope — measure before changing.

### D-14 · The rename-request wait self-parks at the current stage  (2026-07-24, stories S2.6/2.1/2.3)
**Context:** A title mismatch (FR-02a) or a Classifier REJECT (EH-07) files a rename-request task and
must then wait for a corrected re-upload. The §9 stage set has no dedicated "awaiting_rename" stage,
and `awaiting_clarification` is semantically the FR-08 PM clarification loop.
**Decision:** the handler files the task and returns `Park(to_stage=<current stage>)` — a legal
self-transition — with `pending_gate = UPLOADING_PM_RENAME`. Title-mismatch self-parks at `detected`;
REJECT self-parks at `confirmed`. The corrected page re-uploads as a new version (EH-04), the
orchestrator re-enters at that stage, and the stage handler re-runs — now passing.
**Rationale:** it keeps the §9 stage set intact (no invented stage, no migration), does not conflate
the rename wait with the PM clarification loop, and models the truth: the run has not advanced past
detection/confirmation, it is waiting for the input to be fixed. The distinct `pending_gate` value
keeps it observable and lets the re-file idempotency guard (`rename_request_ticket_key`) work.
**Alternatives rejected:** a new `awaiting_rename` stage (a schema/state-machine change for a branch
that the §9 list deliberately omits); reusing `awaiting_clarification` (overloads the FR-08 gate and
would confuse the Feedback interpreter's routing later).
**Revisit if:** the rename wait ever needs the AD-22 liveness sweep to watch it — then a dedicated
stage in `LIVENESS_WATCHED_STAGES` would be worth the change.

### D-15 · No `temperature`/`top_p`/`top_k` — the pinned models reject them  (2026-07-24, live)
**Context:** The first live classifier call returned `400: temperature is deprecated for this model`.
The claude-api skill confirms sampling parameters are **removed** on the Claude 5 family (Sonnet 5)
and Opus 4.7+ — every model this project pins rejects them.
**Decision:** removed the `temperature` parameter from `LlmClient.complete` and the classifier /
feedback-interpreter call sites (they had passed `temperature=0`).
**Rationale:** determinism where it matters (the classification bar, feedback routing) is steered by
the SKILL.md rubric and the typed-decision contract, not a sampling knob — which is the guidance for
these models. Sending the parameter is a hard 400, so this is required, not optional.
**Found by:** the live classifier eval — offline fakes accepted `temperature`, so only the real API
surfaced it. Reinforces running the live smoke early.

### D-16 · The agent's own Jira comments are suppressed by an AD-9 claim, not an author check  (2026-07-24, live)
**Context:** Jira echoes every comment back as a `comment-created` webhook — including the agent's
own. `_dispatch_comment` had no guard, so the clarification question the agent posts at
`awaiting_clarification` would return as an event and be interpreted as the PM's reply: the agent
answering its own question, which AD-16 explicitly forbids ("must never fabricate the answer"). The
FR-07 review-request comment had the same problem at `awaiting_review`.
**Decision:** `RunContext.post_comment` records the new comment's id in `processed_events` at post
time (`<tenant>:jira.comment_created:<comment_id>`). The echo then collides on the AD-9 UNIQUE
constraint and the ingress drops it as a duplicate. `handlers_authoring` was switched from
`ticket_manager.comment` to `context.post_comment`; `ErrorHandler` takes an `on_comment` hook for the
same claim (its escalation comment quotes the literal `@agent resume`, which `is_resume_request`
would otherwise match).
**Rationale:** reuses the existing idempotency mechanism rather than inventing a second guard, and it
is *exact* — it identifies the specific comment the agent wrote.
**Alternatives rejected:** comparing `author_account_id` to the agent account (AD-10's shape for
Confluence). It fails whenever the agent's token and a human reviewer share one Atlassian account —
it would make the agent deaf to that human's real feedback, a worse failure than the one it fixes.
Kept as the router's existing admin-account check only, where a mismatch is harmless.
**Found by:** the live review-loop run; verified in the tenant (the change-summary comment came back
already marked seen).

### D-17 · The Author emits a fixed Markdown subset; stray raw HTML is dropped, not escaped  (2026-07-24, live)
**Context:** Asked for "2 column format", the Author emitted raw `<table>/<td width="50%">` HTML.
`markdown_to_storage` escapes anything outside its subset (an injection guard, deliberately), so the
page rendered the literal `&lt;table&gt;` markup as visible text. The SKILL.md said "Write in
Markdown" but never enumerated the supported subset or forbade raw HTML.
**Decision:** two layers. (1) The Author's SKILL.md now lists the exact round-trip-safe subset,
forbids raw HTML outright, and requires that feedback it cannot express in Markdown be **applied as
far as possible and the shortfall stated in the change summary** — never faked, never silently
skipped. (2) `markdown_to_storage` drops a line consisting *solely* of HTML tags, degrading to clean
single-column Markdown; lines mixing tags with prose are still escaped so the words survive and the
slip stays visible.
**Rationale:** Markdown is the deliverable (FR-15 exports `.md`), so the subset is a real contract,
not a style preference. A tag-only line carries no prose — dropping it is lossless — while a
tag-stripper over all content risked mangling legitimate text.
**Alternatives rejected:** teaching the converter real HTML tables (Markdown still cannot express a
multi-column *layout*, and it would weaken the escaping guard); silently ignoring un-expressible
feedback (the PM must learn from the summary that it was not applied).
**Found by:** the live FR-09 feedback round on UDR-1.

### D-18 · The local driver polls comments; newest unseen wins  (2026-07-24, live)
**Context:** With no deployed webhook endpoint there is no listener, so a PM's Jira feedback sat
unread and no Claude call ever fired. `--resume` only polled the gate ticket for a Done transition.
**Decision:** `--resume` now reads the ticket's comments back and feeds the **newest unseen** one to
`apply_pm_comment`, marking older unseen ones as history; a `--baseline` flag claims everything
currently present. Feedback is processed before the gate check, the order a webhook would have
delivered them in.
**Rationale:** polling is the AD-22 reconciler's own mechanism, so this is not a shortcut around a
gate — a human still acts, and AD-15 still holds (the driver never transitions a ticket). Newest-wins
because a poll sees a whole backlog at once and the latest word supersedes; it also stops comments
written before D-16's claim existed from replaying as fresh feedback.

### D-19 · A 403 on `set_edit_restriction` names the plan tier, not the permissions  (2026-07-24, live)
**Context:** The publish transaction failed with `PermissionException: Not enough permissions to
alter ContentRestrictions`. The generic 403 fix text says "grant the account access to the space" —
but the account already held `restrict_content:space` and `administer:space`, and the content
permission check returned `hasPermission: true`. The real cause was `"edition": "free"`: Confluence
Cloud Free does not include page restrictions and reports the gap as a permission error.
**Decision:** added a narrow `(status, operation) → fix` override table in `app/adapters/http.py`;
the `(403, "set_edit_restriction")` entry tells the admin to check the site's **plan** before its
permissions and names the `systemInfo` probe that settles it. Generic 403s are unchanged.
**Rationale:** NFR-08 requires an error an admin can act on from a Jira comment. The generic advice
was worse than none here — it sends them hunting for an already-granted permission. Kept the table
narrow so it does not become a dumping ground for per-call messages.
**Alternatives rejected:** rewriting the generic 403 text (would lose the common, correct advice);
detecting the edition at startup (an extra call per boot to pre-empt one rare failure, and the
edition can change under a running deployment).

### D-20 · The local driver exposes `apply_admin_resume`  (2026-07-24, live)
**Context:** Once a run enters `error` it is deliberately inert — it must never restart on the next
unrelated event (EH-02). The only way out is `@agent resume`, which is routed by the webhook layer.
With no endpoint deployed, the B-7 failure left the demo with no path forward at all.
**Decision:** added `--admin-resume` to `scripts/run_local_demo.py`, calling
`orchestrator.apply_admin_resume` directly.
**Rationale:** it is the same call the webhook makes, not a bypass — it re-enters only at
`last_good_checkpoint`, and AD-18's ordered idempotent publish adopts completed steps rather than
repeating them. Without it the driver could drive every path *except* recovery, which is the one the
live run actually needed.

### D-21 · `require_edit_restriction` — a per-tenant opt-out from FR-15 step 1  (2026-07-24, Nhan's call)
**Context:** B-7 — the live Atlassian site is Confluence Cloud **Free**, which has no page
restrictions and rejects the call with a 403 regardless of permissions. FR-15 step 1 / AD-18 mandate
the restriction, so the publish transaction could never complete on that site. Options put to Nhan:
upgrade to Standard, add an opt-out flag, or leave the run parked.
**Decision (Nhan, explicitly):** add `require_edit_restriction: bool = True` to `TenantConfig`. Set
to false, the publisher skips step 1 and still moves + exports. Set false for `project_alpha`.
**This knowingly relaxes a binding requirement** — recorded here because it is a spec deviation, not
an implementation detail, and it is reversible by flipping the flag after a plan upgrade.
**Guardrails, so the relaxation cannot become a silent lie:**
- The default is `True`. Only an explicit per-tenant opt-out changes behaviour; every other tenant
  and every test keeps the spec'd path.
- A skip **never** records `restriction_applied_at`. A checkpoint claiming a restriction that was not
  applied would make a later resume — and any admin reading the state — believe the page is
  protected when it is editable.
- The agent posts a comment on the Publishing ticket @-mentioning the Head of Product saying the page
  is **not** edit-restricted. They approved expecting publishing to lock the page; a silent skip
  would leave them trusting protection that does not exist. The `--admin-resume` banner is likewise
  conditional.
**Alternatives rejected:** auto-detecting the Free edition and skipping without being told (turns a
spec deviation into invisible behaviour, and the edition can change under a running deployment);
treating the 403 as success (loses the distinction between protected and unprotected entirely).

### D-22 · `md_export_dir` points into the repo for the local demo  (2026-07-24, live)
**Context:** the registry carried `/data/userdocs/alpha`, the *container* path from `deploy/`. On
macOS `/data` is a read-only filesystem, so FR-15 step 3 would have failed immediately after the
restriction — the next domino.
**Decision:** the live registry now uses the relative `data/userdocs/alpha` for local runs, with a
comment to restore the absolute container path when deploying to the Droplet.
**Rationale:** config, not code — exactly what AD-4's registry is for. Caught by reading the path
before running rather than by a second failed publish.

### D-23 · Deploy-asset fixes found by reading them before running them  (2026-07-24)
**Context:** `deploy/` and `.github/workflows/build-image.yml` were written in Epic 6 and had **never
been executed** — no Droplet, no Docker locally, no git remote. Reviewing them before the first
deploy surfaced four defects, each of which would have failed *after* the box was live.
**Decisions:**
1. **Added `.dockerignore`.** There was none, so the build context carried `.env` (Atlassian + Claude
   credentials) and the live `data/state.db`. Also excludes `config/registry.yaml`, keeping the image
   **tenant-generic** — the real config is mounted read-only at run time, so one artifact serves every
   tenant and a pushed image publishes nobody's ids.
2. **`provision.sh` now installs Docker and Caddy.** It configured swap, firewall and `/data` but
   installed neither runtime; a stock Ubuntu Droplet would have failed at `docker pull`. Caddy in
   particular is **not** in the Ubuntu repos — the README's `apt-get install -y caddy` could never
   have worked, so provisioning adds the official Cloudsmith repo first.
3. **Fixed the reconcile cron's token.** It used `${ADMIN_API_TOKEN}`, but cron runs with a near-empty
   environment: the variable expanded to nothing and **every AD-22 sweep would have 401'd silently**.
   Now read from `/opt/agent/.env` at run time with `grep|cut` (not `source`, so a value containing
   shell metacharacters cannot execute), keeping the secret out of the world-readable crontab.
   Verified against a stub `curl` with a token containing quotes, `r`s and a CRLF ending.
4. **`/health` now reports config state** (`{"status","config","webhooks"}`). A container started
   without its config volume is *alive but deaf* — it answered a bare `{"status":"ok"}`, would have
   passed the deploy smoke test, and then dropped every Atlassian delivery with one log line as the
   only evidence.
**Rationale:** these are exactly the failures that are cheap on a laptop and expensive at 2am on a
box you just paid for. Reading an unexercised runbook is part of deploying it.

### D-24 · `config/registry.yaml` is gitignored  (2026-07-24, Nhan's call)
**Context:** the off-box build (AD-21) requires the repo on GitHub, and the file holds one tenant's
Jira project keys, Confluence folder ids and account ids. No secrets — credentials are env refs —
but it identifies a specific Atlassian site.
**Decision (Nhan):** gitignore it. The repo ships `registry.example.yaml`; the real file is scp'd to
`/opt/agent/config` on the Droplet and mounted read-only.
**Rationale:** the tree stays tenant-free (the spirit of AD-4/NFR-05), the repo can be made public
later without rewriting history, and it mirrors how `.env` is already handled. Verified after the
change: a scan of every committable file found no tenant literal and no secret-shaped string.

### D-25 · Deploy fixes found only on the live Droplet  (2026-07-24)
**Context:** the `deploy/` assets had never been executed. Three defects appeared only against a real
box, all after the runbook said "done".
**Decisions:** (1) `provision.sh` chowns `/data` to uid 10001 — the container runs as the non-root
`agent` user, but a bind mount keeps the HOST's ownership and shadows what the image set, so a
root-owned `/data` crash-looped on `sqlite3.OperationalError: unable to open database file`. Matching
the uid beats loosening the mode: the agent holds broad Atlassian rights and must not also be root.
(2) Caddy logs to stderr/journald, not a file — the packaged unit runs under `ProtectSystem=full` and
EACCES'd on `/var/log/caddy` even chowned, and an unrotated access log on a 25 GB disk is its own
hazard. (3) The Droplet's registry uses **absolute** `/data/...` paths; the relative ones resolve
under `/app` inside the container, so state and exports would have been silently discarded on every
`docker run`.
**Note:** the Droplet's `registry.yaml` therefore differs from the local copy on purpose. Re-copying
the local file over it breaks persistence with no error.

### D-26 · Webhook drops must be visible  (2026-07-24, live)
**Context:** the first real Confluence Automation delivery returned 200 and started nothing, with no
explanation anywhere. Uvicorn configures only `uvicorn.*` loggers; `app.*` propagates to a root
logger with no handler, so Python's `lastResort` emitted WARNING+ and **discarded every INFO** — and
all four AD-8 drop reasons are logged at INFO.
**Decision:** `configure_logging()` in `app/main.py`, called by `create_app`, honouring `LOG_LEVEL`
and no-oping if a host already configured logging.
**Rationale:** a webhook endpoint that answers 200, does nothing, and cannot say why is unoperable.
It paid for itself immediately: the very next delivery printed the real cause (D-27).

### D-27 · The page version is resolved app-side; Automation cannot supply it  (2026-07-24, live)
**Context:** every real page delivery was dropped with `missing required field
'page.version.number'`. **Confluence Cloud Automation exposes no page-version smart value at all**
(confirmed against Atlassian docs), and an Automation rule is the only way to trigger on a page event
without a Connect app — so the parser's requirement made the product untriggerable.
**Decision:** `version_number` is optional. An unversioned event gets **no dedupe key** from the
ingress, and the router resolves the authoritative version with one `GET page` (after the signature
check) before keying and admitting.
**Rationale:** keying an empty marker would have been far worse than dropping — one key per page
forever, so the first edit recorded and every later one dropped as a duplicate, silently disabling
EH-04 rename re-entry. Fetching is also *more* correct than trusting a payload field: a redelivery
and the AD-22 reconciler now derive the same key from the same source of truth.
**Alternatives rejected:** a timestamp marker (every delivery unique → redeliveries reprocessed);
guessing further smart-value names (already cost one deploy cycle).

### D-28 · AD-10 is enforced at admission, not only in detection  (2026-07-24, live)
**Context:** a Confluence Automation trigger is space-wide, so it fires for the agent's **own** draft
and published pages. Detection declined them correctly — but only *after* `admit` had written a state
row, so the first webhook-driven run left a permanent `detected` row for its own draft page.
**Decision:** `_dispatch_page` refuses before admission on two **certain** signals: the reserved
`agent-generated` label, or the page sitting directly in this tenant's draft/published folder. Both
come from the page fetch already made for D-27, so there is no extra call.
**Rationale:** keeps the single durable store (AD-2) free of runs that can never advance. The guard
is deliberately conservative — a page with an unknown or nested container is still admitted and left
to detection's ancestors lookup, so it can never refuse a genuine PRD.

### D-30 · Conversational review loop — the interpreter gets transcript + memory  (2026-07-25, Nhan's request)
**Context:** the structure-confirmation loop (FR-10) interpreted each PM comment in isolation. A bare
"yes" happened to work (falls back to the stored restatement), but the interpreter was judging the
reply **blind** — its prompt never included the question it asked or the restatement it proposed. So
"yes, but drop the last point" or "no, I meant the intro" could not be handled: the correction had
no anchor. And a plain "no" was worse than useless (see D-31).
**Decision (Nhan asked for context + memory + a real brainstorm):** feed the Feedback interpreter the
**review-ticket transcript** (PM/agent-labelled by AD-10 account) and the **pending restatement** as
input on every interpretation. New domain type `ReviewTurn`; `TicketManager.discussion()` +
`JiraAdapter.get_comments` supply the thread; `RunContext.interpret_comment` assembles it. The
transcript read is best-effort — a Jira failure degrades to no transcript, never a failed round.
**Why this respects AD-16:** it enriches the interpreter's *input* only. The output is still a typed
`FeedbackDecision` and the routing is still deterministic + unit-tested. Verified live: "yes"→apply
as-is; "yes but…"→APPLY with the adjustment folded in; "no, I meant…"→CONFIRM_STRUCTURE with a new
restatement; bare "no"→CONFIRMATION/confirmed=false. Recorded in the planning docs as amendment
FR-10a (PRD, Spine AD-16, solution-design), per Nhan's instruction to keep the change written.
**Alternatives rejected:** storing a `pending_question` state column (a schema migration for something
the transcript already contains); re-interpreting from scratch each round without memory (the status quo, which is exactly what failed).

### D-31 · Fixed: a rejected restatement 500'd the webhook  (2026-07-25, found while adding D-30 tests)
**Context:** adding the first end-to-end test of the "no" path surfaced that
`awaiting_structure_confirm → awaiting_review` was **not a legal edge** in the §9 state machine
(`AWAITING_STRUCTURE_CONFIRM` allowed only `→ REVISING`). So a PM "no" raised `IllegalStageTransition`
— a `ValueError`, not an `AgentError`, so it propagated **uncaught** out of `apply_pm_comment` and
would have returned HTTP 500 to Atlassian, which then retries into the same crash. The clarification
loop already had the `→ awaiting_review` fallback edge; structure-confirm was missing it.
**Decision:** add `AWAITING_REVIEW` to `AWAITING_STRUCTURE_CONFIRM`'s legal transitions, and make the
not-confirmed branch **post an acknowledgment** ("what would you like changed instead?", @-mentioning
the PM) and clear the stale restatement before returning to review — no silent dead-end, no crash.
**Found by:** writing the test that should always have existed. The "no" path had **zero** end-to-end
coverage, which is exactly why a broken state edge shipped. Now covered.

### D-32 · Marker search is type-aware — a rename detour no longer eats the Review ticket  (2026-07-25, Nhan's bug report)
**Context:** a PRD uploaded with the wrong name files an FR-02a **rename request** in the Review
project (marker `prd-<id>`). After the PM renames the page, drafting runs and calls
`find_ticket_by_marker(review_project, prd_id)` to adopt-or-create the Review ticket (AD-11). But that
search was `ORDER BY created ASC LIMIT 1` — it returned the **oldest** marked ticket, which is the
rename request. So the run adopted the rename ticket as its "Review ticket" and **never created a real
one**: the draft existed, but there was no draft-review ticket (exactly the reported symptom). Two
ticket types share the marker in one project (rename + Review in Review; tracking + Publishing in
Main); only the publishing handler had a guard (an inline `startswith("approve & publish")` on
limit=1, which had the same latent duplicate risk).
**Decision:** `find_issue_by_prd_marker` gains an optional `summary_prefix`. When given, it scans a
bounded oldest-first set and returns the first ticket of that **type**. Summary prefixes are module
constants in `ticket_manager` (`REVIEW_TICKET_SUMMARY_PREFIX`, `PUBLISHING_TICKET_SUMMARY_PREFIX`),
used by both the `create_*` summary and the finder so they cannot drift. `on_drafted` searches for the
Review type; `on_passed` for the Publishing type (replacing the fragile inline check). The tracking
search stays untyped — it runs before any sibling type exists, so "oldest" is correct.
**Why not a per-type label:** AD-4 forbids new cross-tenant label constants (`agent-generated` is the
only allowed one). The summary prefix is agent-authored English, not a config literal, so it is
AD-4-clean — and it is the pattern already in the codebase.
**Robustness:** filtering within a bounded set (not limit=1) means it adopts a genuine Review orphan
if one exists **and** skips the rename ticket — so no duplicate and no mis-adoption. Covered by
adapter tests (typed skip + none-when-only-other-type) and an end-to-end handler test
(after-rename → Review ticket created).

### D-33 · Tickets are labelled `agent-generated`; the Publishing ticket no longer spoofs the Reporter  (2026-07-25, Nhan's request)
**Context:** Nhan asked why agent-created tickets appear to be authored by a human account, and in
particular why the Publishing ticket shows the **Head of Product as both Reporter and Assignee**. Audit
(5-agent verification) found two causes. (1) The system authenticates as a single account — whatever
login owns the `.env` token — which is the immutable Jira **Creator** of every ticket. (2)
`create_publishing_ticket` alone passed `reporter_account_id=head_of_product_account_id`
(ticket_manager.py:300) — the only Reporter override in the codebase. It came from a literal reading of
FR-13 "reported to / assigned for approval by the Head of Product" (PRD §12 / epics.md echo it), but
the Architecture Spine and solution-design render FR-13 as "@Head of Product" (assignee/mention
semantics), no AD mandates the Reporter field, and no test pinned it. The three other ticket types
never set a reporter, so the Publishing ticket was inconsistent and misstated who filed it.
**Decision:** Two code changes plus one setup gate, all requested by Nhan (all three "routes"):
- **Route 2 —** delete the `reporter_account_id=head_of_product_account_id` override. FR-13's "reported
  to / assigned for approval by" is satisfied by the **assignee** (unchanged) and the publisher's
  @-mention; the Reporter now defaults to the agent account like every other ticket.
- **Route 3 —** stamp the reserved `AGENT_GENERATED_LABEL` (`agent-generated`) on all four ticket
  creates via `create_issue(extra_labels=…)`, giving an in-Jira, filterable "from the agent flow"
  marker that is independent of which account holds the token.
- **Route 1 (human gate, [BLOCKERS.md](BLOCKERS.md) B-8) —** provision a dedicated "UserDoc Agent"
  Atlassian account to own the token, so the immutable **Creator** field is the agent. Only this moves
  Creator off a human; it needs an org admin + a licensed seat, so it is a human-block gate, not code.
**Rationale:** serves NFR-01/traceability and plain honesty of provenance — the agent files these
tickets, so it should read as the reporter/creator, and the team should be able to see and filter
"made by the agent". Reusing the single AD-4-sanctioned cross-tenant label keeps the tree grep-clean;
the label is functionally inert on Jira (detection only inspects Confluence page labels), so it is a
pure visibility marker there.
**Alternatives rejected:** a new Jira-only label constant (a second cross-tenant constant AD-4
discourages, and `agent-generated` reads correctly enough); keeping reporter=HoP (misleading and
undocumented); a code-only fix without Route 1 (cannot change the immutable Creator field).
**Tests:** `test_ticket_manager.py` — the fake `create_issue` now captures assignee/reporter/labels;
new tests assert all four creates carry `agent-generated`, the Publishing ticket assigns the HoP with
**no** reporter, and the Review ticket assigns the PM with no reporter. `test_jira_adapter.py` — a new
test asserts `extra_labels` reach the create payload. Suite 496 passing, ruff clean, 5/5 contracts.
**Revisit if:** a tenant's Jira workflow makes the `agent-generated` label meaningful (e.g. an
automation rule keys on it) and it needs to be Jira-specific, or FR-13 is amended to genuinely require
a specific Reporter.

### D-34 · CI gates the image build; a pre-push hook mirrors CI locally  (2026-07-25, Nhan's request)
**Context:** A master push (9e483a3) deployed successfully **while CI was red**. Two gaps caused it:
(1) the image build lived in a *separate* workflow (`build-image.yml`) that ran on every master push
with **no dependency** on the `test` workflow — so a red `ci.yml` never blocked a deployable image;
(2) locally only `ruff check` had been run, not `ruff format --check`, and CI runs both — so the
format break wasn't caught before push. The deployed code was functionally correct (the failure was a
one-line test-name wrap), but "green before shipping" was a habit, not a guarantee.
**Decision:** make it structural, two parts.
- **Gate the build on the tests.** Merged the image build into `ci.yml` as a `build` job with
  `needs: test` and an `if` ref guard (master or `v*` tag, plus manual dispatch); deleted
  `build-image.yml`. A red `test` job now leaves `build` skipped — no image ships. Still built on the
  GitHub runner, never the 1 GB box (AD-21). Encoded as a test: `test_operations.py` asserts
  `needs: test` is present and that the build steps live in `ci.yml`.
- **Mirror CI locally.** Committed `.githooks/pre-push` (enabled with `git config core.hooksPath
  .githooks`, or `make hooks`) and a `Makefile` with `check` / `lint` / `test` / `format`, both
  running the exact four CI gates — `ruff check`, `ruff format --check`, `lint-imports`, `pytest`.
  `git push --no-verify` is the documented emergency bypass.
**Rationale:** one source of truth for the build steps (no duplication to drift), and the gate is now
a property of the pipeline rather than of whoever pushed. The hook stops the specific failure that
occurred (a format-only miss) plus any contract/test break, before it leaves the machine.
**Alternatives rejected:** a `workflow_run` trigger keeping the two files separate — it re-checks out
the *default branch* head, not the commit under test, so the build/version-tag SHA can silently drift,
and it needs an explicit `conclusion == success` guard; a single workflow with `needs` uses the
correct commit SHA and is simpler. Also rejected: copying the ruff/pytest steps into `build-image.yml`
(two sources of truth). **Note:** the hook proved itself while being introduced — `make check` caught
a Make built-in `LINT` variable collision *and* the `test_operations.py` assertions on the removed
`build-image.yml`, before either reached CI.
**Revisit if:** the image must build on non-master branches, or the offline suite grows slow enough
that a full pre-push run is painful (then scope the hook to a fast subset and keep the full run in CI).

### D-35 · Rename-churn guard + source-folder admission gate (FR-01a / AD-24)  (2026-07-25, Nhan's request + audit)
**Context:** Nhan worried the agent would re-catch an already-drafted PRD and spawn duplicate tickets/
drafts as its source page name was toggled back and forth. A page id is stable across renames, and
each rename bumps the Confluence version → a new AD-9 key, so version-dedup alone doesn't stop it.
The deep audit also found (a) a page created anywhere in the space was admitted then left a dead
`detected` row, and (b) the EH-04 re-entry recorded its dedupe key BEFORE the advance, so a crash
mid-advance stranded the run behind a committed-but-unworked key (`detected` isn't liveness-watched).
**Decision:** in `_dispatch_page`: an existing run's source-page event is actionable **only** while
`pending_gate == UPLOADING_PM_RENAME` (the FR-02a/EH-07 waits), dropped otherwise **before** the
version GET; a new page is admitted only if `_in_source_folder` (container or ancestors); and the
re-entry dedupe key is recorded **after** `advance`, not before, so a crash lets the redelivery
re-advance (idempotent) instead of stranding the run.
**Rationale:** the only time re-processing a source page matters is the rename/reject correction,
which is exactly the `UPLOADING_PM_RENAME` wait. Everything past detection took the PRD from its
finalized version; re-drafting on a source edit is out of scope. Cheap (no GET for the churn case).
**Alternatives rejected:** dedup by content hash (heavier, and the version already changes); pruning
dead rows after detection (a delete path the store doesn't have — refusing at the door is simpler).

### D-36 · Draft-deletion detection & recovery (FR-16 / AD-25)  (2026-07-25, Nhan's request + audit finding #3)
**Context:** deleting the draft UserDoc mid-flow stranded the run — it stood still, then the publish
404'd on the missing page and `@agent resume` replayed the same 404 forever (2/2-confirmed audit
finding). Nhan asked the agent to catch the deletion, alert + @mention the PM, and recover (restore
from trash or recreate with the exact latest content).
**Decision:** a third Confluence Automation rule (`page_trashed`) → `EventType.CONFLUENCE_PAGE_TRASHED`
→ `_dispatch_page_trashed` → `Orchestrator.apply_draft_deleted`, delegating to
`Publisher.recover_draft`: read the page; if `current` → healthy no-op; if `trashed` → restore in
place (v1 PUT `status: current`, then re-move to the draft folder) or, on failure, recreate a new
page with the trashed page's still-readable content (stamped, marker, moved) and repoint
`userdoc_page_id`; if unreadable → unrecoverable. Always @-mention the PM on the Review ticket with
the outcome. If the run had errored on the missing page, re-enter at `last_good_checkpoint`. The same
`recover_draft` runs at the top of `on_publishing`, so a missed deletion event self-heals at publish.
**Rationale:** restore keeps the page id (the review-ticket link survives); recreate guarantees "exact
same content as the latest version" even when restore is blocked; idempotency (a redelivery finds the
page healthy) makes it safe to fire on every event and at publish. No dedicated untrash endpoint
exists — the v1 status PUT is the supported workaround; a trashed page is still readable, which is
what makes both paths possible.
**Alternatives rejected:** re-drafting from the source PRD on deletion (loses the PM's review edits);
relying only on the deletion webhook (best-effort delivery — hence the publish-time defensive
recovery too).

### D-37 · Missing review-loop state edges added (structure-confirm ↔ clarification)  (2026-07-25, audit)
**Context:** the audit (state-machine dimension, rated high) found `awaiting_structure_confirm →
awaiting_clarification` and `awaiting_clarification → awaiting_structure_confirm` were not legal
edges. With the conversation-aware interpreter (D-30), a reply at one wait can legitimately route to
the other (a clarification answer that is really plain feedback; a confirmation reply that surfaces an
FR-08 trigger) → `IllegalStageTransition` → HTTP 500 (the D-31 class of bug). The verifiers couldn't
vote (org spend limit), so this was confirmed by reading the code directly.
**Decision:** added both edges; the two review-loop waits are now fully interconnected. Self-
transitions were already legal, so re-restate/re-clarify needed nothing.

### D-38 · Draft-deletion recovery is human-gated (ask first), not automatic (FR-16 revised)  (2026-07-26, Nhan's change)
**Context:** Nhan reported the deleted draft wasn't recovered despite the Automation firing, and — more
importantly — changed the requirement: **do not auto-recover.** Instead, ask the reviewing PM whether
the deletion was intentional; restore only if they confirm it was a mistake.
**Bug found:** the deleted page (2064481, run 2195475's draft) arrived as a **page-updated** event, not
page-trashed (the Automation body wasn't labelled), so it hit `_is_agent_output` (the draft carries the
`agent-generated` label) and was dropped before the trash logic ran. Root cause: detection trusted the
event *label* instead of the page's real *status*.
**Decisions:**
1. **Robust detection:** in `_dispatch_page`, a page event whose id is a run's `userdoc_page_id` is
   checked by live *status*; if trashed/missing → the deletion flow, regardless of the event label.
   (A healthy draft edit is ignored; a non-draft trash is ignored.)
2. **Ask-first (AD-16/EH-08 spirit):** `apply_draft_deleted` posts a question on the Review ticket
   (@mention PM), sets `pending_deletion_page_id` + `pending_gate = PM_DELETION_DECISION`, and does
   **nothing else**. A PM comment while that marker is set routes to `apply_deletion_decision`, which
   classifies the reply into a typed **RESTORE / LEAVE / UNCLEAR** (Feedback interpreter; deterministic
   routing) — restore only on RESTORE, re-ask on UNCLEAR (never guess).
3. **Publish stops auto-recovering:** `on_publishing` now *refuses* a missing/trashed draft with an
   actionable error instead of silently restoring it — consistent with "never auto-recover."
4. **Schema:** `pending_deletion_page_id` column + an idempotent additive migration (`ALTER TABLE …
   ADD COLUMN` for a store that predates it — the live Droplet DB), since `CREATE TABLE IF NOT EXISTS`
   never alters an existing table.
**Rationale:** a deletion can be deliberate; auto-restoring would fight the human. Gating on the PM's
confirmation matches how the clarification/structure loops already work. Keying on status (not the
webhook label) makes it work with the user's existing, imperfectly-configured Automation rule.
**Supersedes D-36's auto-recovery** (which was the prior FR-16 behavior).
