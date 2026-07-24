# STATE — Resume Pointer

> **This is the file to read first in every session.** It answers exactly one question:
> *what do I do next?* Keep it short and current. Update it at every story boundary.

**Last updated:** 2026-07-24 (session 1)
**Phase:** Phase 4 — Implementation
**Scope:** DEMO with FULL HARDENING

---

## Where the build is

**Epics 1 (foundation) & 2 (detection/confirmation) are COMPLETE. 18 / 39 stories DONE.**
Test suite: **341 passed**, `ruff check` clean, **5/5 import-linter contracts kept**.

- **Epic 1** — scaffold + pinned deps, config registry, single SQLite store + §9 stage machine,
  webhook ingress (validate→parse→route→dedupe→admit), both Atlassian adapters, the in-invocation
  LangGraph orchestrator + serial queue, and the LangSmith tracing harness.
- **Epic 2** — detection guard (folder + label + agent-account, AD-10), title gate, the Classifier
  agent + its `SKILL.md`, the held-out eval harness (dev+holdout fixtures, ×3, confusion matrix,
  flake budget), the ticket manager (FR-04 adopt/search/create + AD-13 drive-to-done + the AD-15
  never-transition-a-gate interlock), the FR-02a rename-request path, and AD-12 identity resolution.
  All wired into the orchestrator as the `detected → confirmed → prd_ticket_done → drafted` handlers.

**One PARTIAL:** S2.4's *live* 0-FP/0-FN classifier measurement needs the Anthropic key (BLOCKERS
B-1). The harness and fixtures are done and offline-tested; only the real Claude accuracy run waits.
Run it later with `.venv/bin/python scripts/run_classifier_eval.py`.

## ▶ Next Action

**Epic 5 — Approval & Publishing.** The flow reaches `passed` (via the PM Done transition) and stops
there (no handler). Epic 5 fills `passed` and `publishing`.

- **5.1** (critical) `passed` handler: post a confirmation comment on the Review ticket, create the
  Publishing ticket in the Main project for the Head of Product (find-or-create by marker, AD-11),
  advance to `awaiting_publish_approval` (park). Uses `TicketManager.create_publishing_ticket`
  (already built in Epic 3).
- **5.2** (critical) Head of Product publish gate: already handled by `Orchestrator.apply_gate_done`
  (matches `publishing_ticket_key` → advances to `publishing`). Story 5.2 is mostly *tests* proving
  the park + the ticket-match + no-self-transition (AD-15). Add the `passed`→`awaiting_publish_approval`
  wiring in 5.1 so 5.2's gate detection has somewhere to land.
- **5.3** (critical) `publishing` handler — the ordered, per-side-effect-idempotent transaction
  (AD-18): (1) apply the Confluence edit restriction **including the agent account** (AD-10 cached id),
  (2) v1-move the page into `confluence_published_folder_id`, (3) export storage→Markdown to
  `md_export_dir`, (4) mark `complete`. Each step guarded by its state-record sub-checkpoint
  (`restriction_applied_at` / `moved_to_published_at` / `md_exported_at`) so a resume skips what's done.

Seams ready: `ConfluenceAdapter.set_edit_restriction` (refuses empty allow-list), `move_page`,
`storage_to_markdown`; the AD-10 agent-account cache pattern from `DetectionAgent`. After Epic 5 the
whole happy path runs end to end (against fakes); Epic 6 is deploy + resilience.

## Environment notes

- **Python 3.12.12** installed via pyenv and pinned in `.python-version`; venv at `.venv/`.
  Run tests with `.venv/bin/python -m pytest`.
- **Docker:** not installed on this machine (BLOCKERS B-6). Not needed until Epic 6.
- **Git:** initialised, work committed locally. Nothing has been pushed to a remote — S6.4 will need
  a remote + CI to build the image off-box.
- **Secrets:** `.env` not yet created. Nhan is working through [../SETUP-GUIDE.md](../SETUP-GUIDE.md)
  in parallel and **will send a message when setup is done**. Current provisioning status is tracked
  in [BLOCKERS.md](BLOCKERS.md) → OPEN.

---

## Standing rules for whoever picks this up

1. `CLAUDE.md` → Non-Negotiable Invariants is not optional. AD-1, AD-2/11, AD-4, AD-15, AD-16, AD-9,
   AD-21, AD-20 must hold in every commit.
2. A story is `DONE` only when its Given/When/Then ACs are met **and** its tests pass. Otherwise it is
   `PARTIAL` (with the reason) or `BLOCKED`.
3. External services are faked at the injection boundary. The unit suite must run with **no network
   and no credentials**.
4. On hitting a human/3rd-party gate: record in `BLOCKERS.md`, ask the user, move to unblocked work.
   Never stub around a gate silently.
5. **Update `EPIC-STORY-TRACKER.md`, `SESSION-LOG.md`, and this file at every story boundary** — in
   the same step as the passing test run, not as a cleanup pass later. A stale tracker is worthless
   at exactly the moment it is needed.
