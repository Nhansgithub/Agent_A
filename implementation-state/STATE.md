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

**Epic 4 — Human Review & Revision Loop.** The flow parks at `awaiting_review`. Epic 4 handles what
the Reviewer PM does next: leave feedback (→ revise loop) or move the ticket to Done (→ PASS).

Stories:
- **4.1** (critical) Ingest a PM comment; the Feedback interpreter returns a typed
  `FeedbackDecision{route, trigger, assumption}` in `app/domain/`. Orchestrator routing off it is
  deterministic + unit-tested; only the decision-producing LLM is eval-tested (AD-16).
- **4.2** (critical) Apply structured feedback → revised draft + change summary + re-request;
  `review_round++`, per-round cost in LangSmith (NFR-09). Uses `Author.revise` + `Publisher.update_draft`.
- **4.3** (critical) Detect PASS: the PM's Done transition on the Review ticket → advance to `passed`.
  The agent never transitions it (AD-15). This is an `issue_updated` webhook path, not a stage handler.
- **4.4** (hardening) Structure-confirmation sub-loop for plain-language feedback → `awaiting_
  structure_confirm`, block until the PM confirms (EH-08).
- **4.5** (hardening) Bounded clarification sub-loop — the 4 enumerated FR-08 triggers only; else
  proceed with a stated assumption. → `awaiting_clarification` (EH-08).
- **4.6** (hardening) Late-feedback-after-Done ignored (EH-06); non-Done terminal transitions park (EH-09).

Key design point: gate detection (4.3) and feedback ingest (4.1) are driven by **Jira webhooks**, not
by the graph's advancing stages. The orchestrator's advancing-stage set already stops at
`awaiting_review`; a comment or transition webhook is what re-enters the flow. This needs a small
"apply an external event to a parked run" path alongside `advance()` — design it in `app/orchestrator/`
so the webhook layer (Epic 1) feeds PM comments/transitions to it.

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
