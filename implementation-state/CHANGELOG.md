# CHANGELOG — what shipped, newest first

> **Purpose:** a compact, append-only record of completed work, so history is available without git
> archaeology and without bloating [NOW.md](NOW.md). One entry per story (or per milestone). Keep each
> line to *what changed + the evidence* — the *why* lives in [DECISION-LOG.md](DECISION-LOG.md), the
> *what/how* in the code and the 3 critical docs.
>
> Format: `YYYY-MM-DD · S-xx <title> — <result / evidence>`. Add new entries at the **top**.

---

## Agile iteration (post-build)

_No stories closed yet under the agile cycle. New entries land here._

---

## Milestone: initial build (Epics 1–6) — completed & live-verified · through 2026-07-26

The full product was built as 39 stories across 6 epics and verified live against a real Atlassian
tenant + the Claude API. Highlights (detail in git history and the code):

- **Epic 1 — Foundation & deployable skeleton (10 stories):** layered module skeleton with import-linter
  contracts; per-tenant config registry + env-ref secrets (grep-clean); repository + single SQLite store
  with the `stage` machine; webhook ingress (HMAC validate → dedupe → route); `processed_events`
  idempotency; Jira + Confluence adapters (ADF, v2/v1, retry, `AgentError`); in-invocation LangGraph
  orchestrator + serial queue; LangSmith tracing harness.
- **Epic 2 — Detection & confirmation (8):** source-folder detection + title gate; **Classifier** with a
  held-out fixture eval that **passed 0-FP / 0-FN ×3 live**; tracking-ticket find-or-create → Done;
  rename-request path + clean re-entry; self-ingestion defense-in-depth; cross-org identity fallback.
- **Epic 3 — Authoring & draft publication (5):** **Author** first draft + one self-critique pass;
  idempotent self-stamped draft publish to the draft folder; Review ticket + framed review-request comment.
- **Epic 4 — Review & revision loop (6):** typed `FeedbackDecision` + deterministic routing; structured-
  feedback revise loop (`review_round++`, uncapped, fresh-comment-gated); PASS detection on the PM's Done;
  structure-confirmation + bounded 4-trigger clarification sub-loops; late-feedback / non-Done handling.
- **Epic 5 — Approval & publishing (3):** confirm PASS + Publishing ticket for the Head of Product; the
  publish gate; the ordered idempotent publish transaction (restrict / move / export / complete).
- **Epic 6 — Resilience & ops (7):** error surfacing + admin resume from checkpoint; reconciliation &
  liveness sweep; off-box backup artifact; deploy to the 1 GB Droplet + memory-envelope hardening;
  config-only modifiability; content-gating observability flag.

**Live milestones:** the end-to-end flow reached `stage = complete` against the real tenant (detect →
classify → tracking ticket → draft → 2 human feedback rounds → PM PASS → Head-of-Product approval → move
+ export). Deployed at `https://poetroastery.com`; image built off-box by CI and pulled (AD-21).

### Post-readiness hardening (owner requests, 2026-07-25 → 07-26)

- **Conversational review loop** (FR-10a) — the Feedback interpreter reads the review-ticket transcript + pending restatement; replies handled by intent (confirm / adjust / redirect / bare-reject). (D-30)
- **Rename-churn guard + source-folder admission gate** (FR-01a / AD-24) — an already-admitted PRD isn't re-caught on later renames; stray-space pages refused at the door. (D-35)
- **Draft-deletion detection + human-gated recovery** (FR-16 / AD-25) — a deleted draft is detected on real status, the PM is asked before any recovery, and an errored run self-heals on restore. (D-36, D-38) *Live activation pending — see BACKLOG S-02.*
- **Tracking-ticket search fix** (FR-04 / D-39) — don't adopt another run's same-named ticket; typed marker search excludes `agent-generated`.
- **Inline-comment feedback channel** (FR-17 / AD-26) — a Confluence inline comment on a draft is read via the adapter, restated on the Review ticket @-mentioning the exact commenter, and handed to the existing conversation loop. (D-40) *Live activation pending — see BACKLOG S-01.*

> The workflow reset to this agile system (new `CLAUDE.md` brain + `NOW`/`BACKLOG`/`CHANGELOG`/`BLOCKERS`
> + retained `DECISION-LOG`) happened on 2026-07-26, replacing the build-phase `STATE`/`EPIC-STORY-TRACKER`
> /`SESSION-LOG` docs.
