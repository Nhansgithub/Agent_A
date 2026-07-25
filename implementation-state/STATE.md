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

**DEPLOYED AND LIVE (2026-07-24) — `https://poetroastery.com`.** Droplet 143.198.218.143
(Ubuntu 24.04, 1 GB). Verified externally: `/health` → `{"status":"ok","config":"loaded",
"webhooks":"mounted"}`, valid Let's Encrypt cert (TLS-ALPN-01; port 80 stays closed), unsigned
webhooks 401, `/admin` + `/docs` not proxied, port 8000 unreachable, reconcile cron 200, and **a real
Jira `comment_created` webhook delivered and accepted (200)** — the webhook ingress layer's first
live run. Container: 59 MiB of a 768 MiB cap (AD-21). Image `ghcr.io/nhansgithub/agent_a`, built
off-box by GitHub Actions; CI green (471 tests).

Six defects were found and fixed during deploy — three of them only a real box could surface
(D-23/D-25): lowercase GHCR tag, pytest `pythonpath` (CI collected nothing), unreadable CI logs,
`/data` ownership vs the non-root container uid, Caddy file logging under `ProtectSystem=full`, and
relative `database_path`/`md_export_dir` that would have silently discarded state on every container
recreate. Nhan's Jira webhook also pointed at the raw IP (TLS could never validate) and filtered on
`project in (MAIN, REV)` instead of `AMS, UDR`; both fixed.

### ✅ S6.4 IS CLOSED — the first fully webhook-driven run happened

Both Confluence Automation rules are live. A `final_PRD_Cold Brew Scheduler` page created in the
watched folder started a run **with no local driver and no human standing in for the webhook layer**:

page created → Automation → HTTPS → Caddy → container → detect → classify → tracking ticket
**AMS-13** (auto-transitioned to Done, AD-13) → Claude drafted → draft page **1474798** (labelled
`agent-generated`, moved to the draft folder) → Review ticket **UDR-2** → review requested → parked
at `awaiting_review`.

The D-16 self-ingestion guard was observed working live in the same run:
`webhook dropped: dropped_duplicate (project_alpha:jira.comment_created:10014 was already admitted)`
— the agent's own review-request comment echoing back and being refused.

### ▶ Current finding (2026-07-25 readiness check)

The webhook-driven run (`final_PRD_Cold Brew Scheduler`, page 1474778) went all the way to the publish
transaction and then **errored** — `last_good_checkpoint = publishing`, ticket AMS-14. Both gates were
walked (UDR-2 and AMS-14 are Done), the edit restriction was correctly **skipped** (Free-tier, D-21),
but `move_page` returned 404: **the draft page 1474798 was trashed by a human between the two gates**
(`status: trashed`; all three folders are healthy). The system behaved correctly — errored, preserved
the checkpoint, parked for admin resume (EH-01/AD-19). It cannot self-heal: the page it must publish
is in the trash.

**Consequence for readiness:** the publish transaction has completed to `complete` **locally** (the
Quick Notes run) but has **never reached `complete` on the Droplet**. The production last mile
(publishing → move → export → complete over the webhook path) is still unproven. Recommended close:
clear this dead run, create one fresh `final_PRD_*` page, and walk both gates **without touching the
draft**.

### Known gaps, deliberate

1. **AD-23 off-box backup is NOT running** (litestream skipped by Nhan's call). The Droplet's SQLite
   is single-copy: losing the disk loses in-flight runs. `deploy/litestream.yml` is ready if wanted.
2. **FR-15 step 1 has never executed anywhere** — Confluence Free has no page restrictions (B-7,
   D-21). The published page will be editable by anyone with space access, and the agent says so on
   the Publishing ticket.
3. **The Droplet and the Mac have separate state.** The Mac's `data/state.db` holds the older
   `final_PRD_Quick Notes` run (complete); the Droplet holds the Cold Brew run. They never sync.
4. **Droplet config diverges from the repo copy on purpose** — `/opt/agent/config/registry.yaml`
   uses absolute `/data/...` paths (D-25). Re-copying the local file breaks persistence with no error.

## Environment notes

- **Python 3.12.12** installed via pyenv and pinned in `.python-version`; venv at `.venv/`.
  Run tests with `.venv/bin/python -m pytest`.
- **Docker:** still not installed on this Mac, and not needed — GitHub Actions builds the image
  off-box (AD-21) and the Droplet only pulls.
- **Git:** remote is `github.com/Nhansgithub/Agent_A` (public), branch `master`, CI green. Tags
  `v*` trigger the image build.
- **Droplet:** `root@143.198.218.143`, key-only SSH. The Mac's key is passphrase-protected — run
  `ssh-add --apple-use-keychain ~/.ssh/id_ed25519` once per boot if SSH starts refusing.
- **Secrets:** `.env` and `config/registry.yaml` are both **gitignored** (D-24) and also live on the
  Droplet at `/opt/agent/.env` (mode 600) and `/opt/agent/config/registry.yaml`. Never commit them
  (AD-4).

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
