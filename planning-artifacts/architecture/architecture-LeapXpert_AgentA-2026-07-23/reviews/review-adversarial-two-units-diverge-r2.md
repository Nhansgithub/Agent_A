# Reviewer Gate — Adversarial "two units diverge" (r2, 2026-07-24)

**Lens:** finalize_reviewer #2 — construct two units one level down that each obey every AD yet still build incompatibly or let state diverge. **Special mandate:** confirm the new AD-11 single-store + idempotent-replay resume model cannot let two units diverge, and that AD-22's reconcile-poll + webhook path cannot double-advance a stage.

**Verdict: BOTH MANDATED PROPERTIES CONFIRMED.** Two holes were found *during* this pass and fixed before finalize; one low residual is recorded as an open question.

## One-line verdict
With the r2 collapse to a single durable store, the classic two-store divergence is eliminated by construction; the create-then-crash double-create window (the one real hole) is closed by find-or-create-by-marker; and AD-22 cannot double-advance a stage through any of three independent guards.

## Attacks

### A. AD-11 — two durable stores drift — ELIMINATED BY CONSTRUCTION
Old model kept a LangGraph checkpoint DB *and* the state record and claimed a cross-store transaction. r2 removes the second store: the checkpointer is an ephemeral `InMemorySaver` rebuilt from the state record on every invocation and discarded at stop. There is exactly one durable cursor (`stage`/`last_good_checkpoint` in the state record). Two units cannot disagree about a cursor that exists in only one place. CONFIRMED.

### B. AD-11 — create-succeeded-then-crash-before-commit → double-create — FOUND, FIXED
A stage that creates a Confluence draft or a Jira ticket cannot enclose the remote create inside the SQLite transaction. If the create returns but the process dies before the id is persisted, a recorded-id-only guard would re-create on replay → orphan + duplicate. **Fix applied:** AD-11 now specifies **find-or-create keyed on a deterministic marker** — "absent" first searches for the artifact carrying the run correlation marker (`prd_id` as a Jira label/entity-property and a Confluence content-property) and adopts an orphan if found. Replay now converges. CONFIRMED closed (subject to residual F).

### C. AD-22 — reconcile-poll + webhook double-advance a stage — CONFIRMED SAFE (defence in depth)
Three independent guarantees, any one sufficient:
1. **Serialization** — reconcile findings enter through the same admission + serial queue (AD-5); never a concurrent graph entry. The second observer sees the already-advanced stage.
2. **Dedupe collision** — the reconcile-poll derives the *same* AD-9 key (issue key + changelog history id of the to-`done` transition) as the webhook, so the two collide on the `processed_events` UNIQUE constraint and one is dropped.
3. **Idempotent advance** — a gate-Done input arriving when the run has already left that gate is a no-op (same guard as AD-11).
Even if (2) were imperfect (key derivation drift), (1)+(3) hold. CONFIRMED no double-advance.

### D. AD-22 — reconciler mutates stage / bypasses the orchestrator — FOUND, FIXED
A reconciler that wrote `stage` would create a second stage-writer, violating AD-2. **Fix applied:** AD-22 now states the reconciler writes **only non-`stage` markers through the repository and never advances `stage`**; a found gate-Done is fed as an *input*, and the orchestrator remains the sole stage-writer. CONFIRMED AD-2 preserved.

### E. AD-22 — liveness alert spam / EH-01 "exactly one comment" violation — FOUND, FIXED
Re-alerting every sweep would spam and could collide with AD-19's "exactly one error comment." **Fix applied:** the liveness alert is recorded once per threshold crossing (`liveness_alerted_at`) and is a separate admin/log/LangSmith signal, distinct from the EH-01 ticket comment. CONFIRMED.

### F. AD-11 — Confluence marker atomicity — LOW RESIDUAL (open question)
Jira `createIssue` sets `labels` in the create payload, so the adoption marker is atomic with the Jira create — clean. Confluence page-create + set-content-property is two calls, so a crash *between* them yields a page with no property marker. Mitigation already implied by the design: the adoption search can fall back to a **structural key** (author = agent account + draft-folder id + PRD linkage, per AD-10/AD-14) to find the orphan even without the property. Recommend the build confirm the structural-search fallback or set the marker via the create body where the API allows. Not a blocker; recorded in open_questions.

## Other pairs tried, no divergence
- Two agents resolving the agent account id → AD-10 single source (`get_current_user` per tenant). OK.
- Two writers of the dedupe key → AD-9 `processed_events` single store + UNIQUE. OK.
- Publish side-effects re-applied on resume → AD-18 per-side-effect markers + inherently idempotent ops (move no-op, restriction PUT, export overwrite). OK.
- Feedback routing prose drift → AD-16 typed `FeedbackDecision` + deterministic unit-tested routing. OK.
