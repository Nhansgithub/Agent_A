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

**Epic 6 — Resilience, Recovery & Operations** (mostly hardening + the one deploy story), **plus the
production composition root** that wires the real context object the handlers expect.

Build order (offline-buildable first, gated last):
- **6.1** Error surfacing + admin resume (`app/agents/error_handler.py`): on any `AgentError` after
  retries, the orchestrator already sets `stage=error` + preserves `last_good_checkpoint`; the Error
  handler posts the EH-01 comment (plain error + fix + `@admin` + the literal `@agent resume`
  instruction + correlation id). Resume = an admin comment containing `@agent resume`/`fixed` re-runs
  `last_good_checkpoint` (dedupe-guarded so a duplicate can't double-resume).
- **6.2** Reconciler/liveness sweep (`app/admin/`, AD-22): authenticated localhost endpoint the cron
  hits; alerts stale parked/error runs once per threshold (`liveness_alerted_at`) and re-polls the two
  gate tickets, feeding a found Done as an *input* (never a stage write; the collision defenses are
  the serial queue + the AD-9 dedupe key + idempotent advance).
- **6.7** Content-gating flag — already threaded (`trace_content`); add the verification test + wire it.
- **Composition root** (`app/main.py` + a context module): build the real per-run context (tenant
  config + adapters + agents + repository) that satisfies the Detection/Authoring/Review/Publish
  context protocols, and register all stage handlers. Wire the FastAPI webhook endpoint (Epic 1
  ingress) → orchestrator `advance` / `apply_pm_comment` / `apply_gate_done`.
- **6.3** Off-box backup (`deploy/`, AD-23): litestream config + restore doc. **PARTIAL** — needs DO
  Spaces (BLOCKERS B-4).
- **6.5** 1 GB envelope hardening: Dockerfile (slim), Caddyfile, swap+firewall scripts, single worker.
- **6.4** Deploy + end-to-end run: **PARTIAL/BLOCKED** — needs the Droplet + live tenant (B-3/B-4/B-5).
- **6.6** Config-only modifiability: add a second tenant to a test registry, prove routing; the
  NFR-05 grep test already guards literal isolation.

Reminder: Story 2.4's *live* classifier eval and the whole live end-to-end run wait on credentials.
Build and unit-test everything offline; mark the live-only pieces PARTIAL with a clear note.

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
