# STATE — Resume Pointer

> **This is the file to read first in every session.** It answers exactly one question:
> *what do I do next?* Keep it short and current. Update it at every story boundary.

**Last updated:** 2026-07-24 (session 1)
**Phase:** Phase 4 — Implementation
**Scope:** DEMO with FULL HARDENING

---

## Where the build is

Epic 1 (foundation) in progress. **8 / 39 stories DONE** — 1.1 … 1.8.
Test suite: **227 passed**, `ruff check` clean, **5/5 import-linter contracts kept**.

What exists and works:

- **Scaffold + pinned deps** (S1.1). Every Architecture Spine Stack-table version resolved exactly as
  specified; `langgraph-api` is absent from the resolved tree (NFR-10 holds and is now a test).
- **Config registry** (S1.2). `TenantConfig` / `SystemConfig` with env-ref-only credentials, folder
  and project-key indexes for AD-3 routing, and load-time rejection of a published folder equal to the
  watched source folder (the primary AD-10 self-ingestion guard). NFR-05 grep-clean is an automated test.
- **Single durable store** (S1.3). `Stage` / `PendingGate` / `QueueStatus` enums, the §10 `PrdState`
  record, `Database` (WAL for litestream, AD-23) and `StateRepository` — with stage+id written in one
  transaction (AD-11) and gate-skipping transitions rejected (AD-15).

- **Ingress pipeline** (S1.4–1.6). `authenticate → parse → resolve tenant → dedupe-check → admit`,
  with a typed outcome for every drop reason. Admission writes the dedupe key and the PRD row in one
  transaction, and the UNIQUE constraint *is* the duplicate check (not check-then-write).

- **Both Atlassian adapters** (S1.7–1.8). Jira v3 with mandatory ADF bodies; Confluence v2 with the
  two v1 exceptions (folder move, content restrictions) and a storage→Markdown converter.

Nothing is blocked yet — the whole suite runs offline with no credentials. Live verification of the
adapters waits on the Atlassian API token (BLOCKERS → OPEN).

---

## ▶ Next Action

**Story 1.9 — In-invocation LangGraph orchestrator, stage machine, and serial queue**
(`app/orchestrator/`, AD-6 / AD-11 / AD-2 / AD-5).

Read its ACs in [`planning-artifacts/epics.md`](../planning-artifacts/epics.md) (Epic 1, Story 1.9).
The shape AD-11 mandates, per webhook invocation:

1. load the state record, 2. re-enter the graph at `stage` / `last_good_checkpoint`
(`thread_id = prd_id`), 3. run the stages that can advance **without a new external event**,
4. persist the new stage + recorded ids through the repository in one transaction, 5. **stop**.

Non-negotiables: LangGraph's checkpointer is an ephemeral `InMemorySaver` scoped to that one
invocation — never a cross-webhook durable store. `stage` is written only here. One PRD at a time
(AD-5), which is also a memory-safety measure on the 1 GB box.

Then: 1.10 (LangSmith tracing) → Epic 2.

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
