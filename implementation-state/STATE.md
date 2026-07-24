# STATE — Resume Pointer

> **This is the file to read first in every session.** It answers exactly one question:
> *what do I do next?* Keep it short and current. Update it at every story boundary.

**Last updated:** 2026-07-24 (session 1)
**Phase:** Phase 4 — Implementation
**Scope:** DEMO with FULL HARDENING

---

## Where the build is

**All 39 stories built; the system is LIVE-VERIFIED against the real Atlassian tenant + Claude.**
451 offline tests pass, ruff clean, 5/5 import-linter contracts kept.

- **S2.4 (the one measurable gate) PASSED live:** classifier eval is 0 FP / 0 FN, stable ×3 on the
  holdout set. Re-run any time: `.venv/bin/python scripts/run_classifier_eval.py`.
- **S6.4 happy path ran live through the review gate:** `scripts/run_local_demo.py` created a real
  PRD, and the flow did detect → classify → tracking ticket (AMS-11, Done) → Opus 4.8 draft →
  published draft (page 1540119) → Review ticket (UDR-1) → parked at `awaiting_review`.

Four live-only fixes landed (things offline fakes couldn't catch): `temperature` 400 (D-15),
Confluence `/direct-children` path, live page-event threading, and the single-account self-author
nuance. All committed; suite green.

**The run is parked at a human gate (`awaiting_review`) — correct by AD-15.** The agent must never
move a gate ticket; a human does.

---

## ▶ Next Action

**Hand-off to Nhan for the two human gates**, then optionally deploy for the webhook-driven form.

1. **PM PASS:** open Review ticket **UDR-1**, optionally leave feedback in the `Section: / Issue: /
   Suggested change:` format (the agent will revise), then move it to **Done**. Run
   `.venv/bin/python scripts/run_local_demo.py --resume` → creates the Publishing ticket, parks at
   `awaiting_publish_approval`.
2. **Head of Product approve:** move the Publishing ticket to **Done**, then `--resume` again →
   restrict + move to the published folder + export the `.md` + mark complete.
3. **Webhook-driven form (S6.4 full):** deploy to the Droplet (`deploy/README.md`) and register the
   webhooks (SETUP-GUIDE Part 7) so the flow triggers on a real page-create instead of the driver.

Open decision for the user: `config/registry.yaml` holds their real account/folder IDs and is
currently **untracked**. SETUP-GUIDE says it is committable (no secrets), but for a shared/template
repo it is cleaner to gitignore it. Decide before pushing anywhere.

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
