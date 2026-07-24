# STATE — Resume Pointer

> **This is the file to read first in every session.** It answers exactly one question:
> *what do I do next?* Keep it short and current. Update it at every story boundary.

**Last updated:** 2026-07-24 (session 2)
**Phase:** Phase 4 — Implementation
**Scope:** DEMO with FULL HARDENING

---

## Where the build is

**All 39 stories built; the system is LIVE-VERIFIED against the real Atlassian tenant + Claude.**
462 offline tests pass, ruff clean, 5/5 import-linter contracts kept.

- **S2.4 (the one measurable gate) PASSED live:** classifier eval is 0 FP / 0 FN, stable ×3 on the
  holdout set. Re-run any time: `.venv/bin/python scripts/run_classifier_eval.py`.
- **S6.4 happy path ran live through the review gate:** `scripts/run_local_demo.py` created a real
  PRD, and the flow did detect → classify → tracking ticket (AMS-11, Done) → Opus 4.8 draft →
  published draft (page 1540119) → Review ticket (UDR-1) → parked at `awaiting_review`.
- **The FR-09 review loop ran live (round 1):** Nhan's feedback on UDR-1 was interpreted, routed to
  APPLY_FEEDBACK, the draft was revised (page v2), a change summary posted, and review re-requested.
  `review_round` = 1, parked again at `awaiting_review`.

Seven live-only fixes landed (things offline fakes couldn't catch): `temperature` 400 (D-15),
Confluence `/direct-children` path, live page-event threading, the single-account self-author nuance,
comment self-ingestion (D-16), the Author's Markdown subset (D-17), and comment polling in the driver
(D-18). Suite green.

**The run is parked at a human gate (`awaiting_review`) — correct by AD-15.** The agent must never
move a gate ticket; a human does.

⚠️ **Known blemish on the live draft (page 1540119 v2).** Round 1 ran under the *old* Author prompt
and answered "make it 2-column" with raw HTML, which the converter escaped — the page currently shows
literal `<table>/<td>` text. D-17 fixes this for every future round. It does **not** self-heal: AD-16
requires a fresh human comment per redraft round, so the agent must not re-revise on its own. The
next feedback round will clean it up.

---

## ✅ The end-to-end demo is COMPLETE (live)

PRD 1441969 ran the whole flow and reached `stage = complete`:

detect → classify → tracking ticket AMS-11 (Done) → draft → Review ticket UDR-1 → **2 human feedback
rounds** (FR-09 revise loop) → PM PASS → Publishing ticket AMS-12 → Head of Product approval →
move + export → complete.

Live artifacts: [page 1540119](https://hoangnhan0402.atlassian.net/wiki/pages/viewpage.action?pageId=1540119)
now in the published folder (1441796), exported to
`data/userdocs/alpha/1441969-final-prd-quick-notes.md`.

**One deviation, by Nhan's explicit decision (D-21):** the site is Confluence Cloud **Free**, which
has no page restrictions (B-7). `require_edit_restriction: false` is set for `project_alpha`, so
FR-15 step 1 is skipped — the published page is **not** write-protected, and the agent said so on
AMS-12. Flip the flag back to `true` after any upgrade to Standard.

---

## ▶ Next Action

**Deploy prep is done (2026-07-24).** The `deploy/` assets had never been executed; reviewing them
first surfaced four defects, now fixed (D-23): no `.dockerignore` (the build context carried `.env`
and the live DB), `provision.sh` installed neither Docker nor Caddy, the AD-22 reconcile cron would
have 401'd on every sweep, and `/health` could not distinguish "alive" from "alive but no config
mounted". `config/registry.yaml` is now gitignored (D-24). 471 tests pass.

**Blocked on Nhan for three things:** the Droplet IP, the domain hostname (A record pointed at it),
and a GitHub repo + remote so Actions can build the image off-box. See below.

Every functional requirement has now run live end-to-end. What remains is deployment, not features.

1. **Webhook-driven form (S6.4 full) — the one substantive gap.** The whole flow has only ever been
   driven by `scripts/run_local_demo.py`, which stands in for the webhook layer. The real trigger
   path (`app/webhooks/`) is covered by the offline suite but has **never run against live Atlassian
   deliveries**. Deploy to the Droplet (`deploy/README.md`) and register the webhooks (SETUP-GUIDE
   Part 7) so a real page-create starts a run. Needs BLOCKERS B-4 (Droplet + Spaces) and B-5
   (registry/CI to build the image off-box, AD-21).
2. **A second clean run** once webhooks are live — the current run's artifacts carry demo history
   (2 feedback rounds, one errored publish). A fresh PRD through the deployed path is the honest
   S6.4 evidence. `--cleanup` removes the demo page.
3. **Optional:** upgrade Confluence to Standard and set `require_edit_restriction: true` to exercise
   FR-15 step 1 for real (D-21 / B-7). It is the only requirement never executed live.

> **Reminder for local runs:** nothing happens the moment you comment or transition — no endpoint is
> deployed, so nothing is listening. `--resume` polls Jira for both signals; `--admin-resume`
> recovers an errored run.

Open decision for the user: `config/registry.yaml` holds their real account/folder IDs and is
currently **untracked**. SETUP-GUIDE says it is committable (no secrets), but for a shared/template
repo it is cleaner to gitignore it. Decide before pushing anywhere.

## Environment notes

- **Python 3.12.12** installed via pyenv and pinned in `.python-version`; venv at `.venv/`.
  Run tests with `.venv/bin/python -m pytest`.
- **Docker:** not installed on this machine (BLOCKERS B-6). Not needed until Epic 6.
- **Git:** initialised, work committed locally. Nothing has been pushed to a remote — S6.4 will need
  a remote + CI to build the image off-box.
- **Secrets:** setup is **done** — `.env` and `config/registry.yaml` exist and `verify_setup.py` is
  fully green. Both are gitignored/untracked; never commit them (AD-4). Remaining gates (Droplet,
  registry/CI, Docker) are tracked in [BLOCKERS.md](BLOCKERS.md) → OPEN.

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
