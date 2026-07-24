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

**Epic 3 — UserDoc Authoring & Draft Publication.** The flow currently walks to `drafted` and stops
there (no handler yet). Epic 3 fills `Stage.DRAFTED`'s handler and the review-request framing.

Stories in order (all critical-path):
- **3.1** Author agent drafts the first UserDoc (structure via prompt + `SKILL.md`, no fixed template).
- **3.2** One self-critique pass (draft → critique → single revision). A *drafting aid only* — never
  an acceptance gate; the human PM PASS is the sole quality gate (AD-17).
- **3.3** Publish the draft to the Confluence draft folder: create page, v1 move into the draft
  folder, stamp `agent-generated` label + `prd_id` content property, record `userdoc_page_id`
  (find-or-create by marker for AD-11 idempotency).
- **3.4** Create the Review ticket in the Review project assigned to `pm_account_id`; advance to
  `awaiting_review` (the run now parks on the PM — AD-15).
- **3.5** Post the framed review-request comment (ADF): tag the PM, request the §6.2 structured
  format, "put yourself in the users' shoes", and the Done-only pass rule.

Seams ready: `Author` uses `LlmClient.complete(model=system.models.author, ...)`; publication uses
`ConfluenceAdapter.create_page`/`move_page`/`stamp_agent_generated`/`set_content_property` and
`find_page_by_prd_marker`; the Review ticket uses `TicketManager` + `adf.mention`.

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
