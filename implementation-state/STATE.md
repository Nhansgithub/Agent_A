# STATE — Resume Pointer

> **This is the file to read first in every session.** It answers exactly one question:
> *what do I do next?* Keep it short and current. Update it at every story boundary.

**Last updated:** 2026-07-24 (session 1)
**Phase:** Phase 4 — Implementation
**Scope:** DEMO with FULL HARDENING

---

## Where the build is

**All six epics are code-complete. 38 / 39 stories DONE; S6.4 (live deploy) is PARTIAL.**
Test suite: **451 passed**, `ruff check` clean, **5/5 import-linter contracts kept**. The whole
service composes and runs offline (`/health` 200; unauthenticated webhook/admin → 401).

The full happy path runs end to end against fakes:
`detect → title-gate → classify → tracking ticket → draft + self-critique → publish draft → Review
ticket + framed request → [PM feedback ⇄ revise loop] → PASS → Publishing ticket → [Head of Product
Done] → restrict + move + export .md → complete`, plus every hardening path (rename request, cross-org
identity, structure-confirm, clarification, error+resume, reconcile/liveness, idempotent publish).

**Two things remain, both gated on credentials/infra (not code):**
1. **S2.4 — live classifier eval** (Anthropic key, BLOCKERS B-1): `.venv/bin/python
   scripts/run_classifier_eval.py` must show 0 FP / 0 FN on the holdout set ×3. The harness + fixtures
   are done and unit-tested; only the real Claude run is pending.
2. **S6.4 — deploy + end-to-end demo run** (Droplet + Atlassian tenant + Spaces, B-3/B-4/B-5):
   follow `deploy/README.md`. Everything it needs is built.

## ▶ Next Action

**Waiting on the user.** Nhan is completing third-party setup via `SETUP-GUIDE.md` and will send a
message when done. When credentials land:

1. Create `.env` + `config/registry.yaml` (SETUP-GUIDE Parts 1-6); run
   `scripts/verify_setup.py` until green.
2. Run `scripts/run_classifier_eval.py` — confirm the 0-FP/0-FN holdout bar (S2.4). If a fixture
   fails, tune the classifier `SKILL.md` against the **dev** set only (never the holdout).
3. Build the image in CI (`.github/workflows/build-image.yml`), provision the Droplet
   (`deploy/provision.sh`), deploy (`deploy/README.md`), register the webhooks (SETUP-GUIDE Part 7).
4. Create a `final_PRD_<name>` page and walk the two gates — the §12 Definition of Done.

Until then there is no code work that is not blocked. If asked to keep improving offline, candidates:
broaden the classifier fixture set, add more markdown-conversion edge cases, or an end-to-end
integration test that drives the whole flow through the composition with fakes injected.

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
