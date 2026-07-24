# Reviewer lens: Adversarial "two-units-diverge"

**Method:** construct two units one level down (epics/stories) that each obey every AD to the letter yet still build incompatibly. Each pair is a hole to close with a new or tightened AD.

**Verdict:** PLAUSIBLE-to-CONFIRMED holes found — 3 that warrant a fix, 1 minor. The spine's boundaries are strong; the gaps are all around *state ownership on the write/resume paths*.

## Finding A (CONFIRMED, high) — Two owners of the dedupe record
AD-9 puts dedupe keys in a repository-owned `processed_events` table and says they are "NOT nested in a PRD row." But the driving PRD's §10 state record still lists a `dedupe_keys` field. Two epics comply and diverge: the "webhook/idempotency" epic writes to `processed_events`; a "state record" epic writes processed ids into the row's `dedupe_keys`. Now there are two dedupe stores and a duplicate can slip through the one a given handler doesn't consult.
**Close:** AD-9 must state that `processed_events` is the single authoritative dedupe store and that §10 `dedupe_keys` is at most a read-only per-PRD projection of it (or dropped) — never a second write target.

## Finding B (CONFIRMED, high) — Non-idempotent creates on stage resume
AD-11 makes the §9 *stage* the resumable unit and AD-19 re-runs the failed stage on `@agent resume`. But nothing requires the externally-visible **creates** inside a stage to be idempotent. Two units diverge: the Author node re-runs on resume and creates a *second* draft page; the Ticket manager re-runs and opens a *second* Review ticket (or a second tracking/publishing ticket). AD-18 demands idempotency for the *publish* transaction only — the draft/ticket creates are unguarded.
**Close:** add a rule (fold into AD-11) that every externally-visible create is idempotent on resume — guarded by the id already recorded in the state record (`userdoc_page_id`, `review_ticket_key`, `prd_tracking_ticket_key`, `publishing_ticket_key`): if present, reuse; only create when absent, and record the id in the same transaction that advances the stage.

## Finding C (CONFIRMED, high) — Dedupe "record before work" can silently drop an event
AD-8/AD-9 record the dedupe key "at ingestion, before routing/work." If the process crashes (or the stage fails) *after* recording but *before* admitting the PRD to the flow, Atlassian's redelivery is suppressed as a duplicate and the PRD is lost — with no state row to resume from. Two units diverge on what "recorded" guarantees: one treats it as "seen," another as "safely admitted."
**Close:** AD-9 should record the key **transactionally with the first state write (flow admission / `detected` checkpoint)** via a UNIQUE constraint on the composite key — so "processed" means "admitted," a concurrent duplicate loses the insert race (and is dropped safely), and a crash before admission leaves the key unrecorded and the event safely redeliverable.

## Finding D (PLAUSIBLE, medium) — "The agent's own account" has no single source
AD-10(c) excludes pages created by "the agent's own account"; AD-18(1) requires including "the agent account" in edit restrictions. Two units resolve it differently — one hard-reads a config value, another calls `/myself` — and if they disagree, detection mis-fires or the restriction locks the agent out. Also note each tenant's token is a different account, so it is per-tenant.
**Close:** name one source — the agent account id is resolved once per tenant via the adapter (`get_current_user` / `/myself`) and cached; detection and Publisher both read that.

## Passed attacks (no hole)
- `prd_id` = page id is stable across rename/move (only `version.number` changes), so AD-9's rename re-entry and AD-2's stable key do not conflict.
- Two Main-project tickets (tracking vs publishing) are distinguished by distinct §10 key fields, so AD-13 (auto-Done tracking only) and AD-15 (never transition gate tickets) cannot be conflated given the fields.
- Stage vocabulary is enumerated in Consistency Conventions, so two units cannot invent divergent stage strings.
