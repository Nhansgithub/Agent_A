# NOW — the one file to read first

> **Purpose:** in ~30 seconds, tell the next agent *what is the state of play* and *what to do next*.
> Keep it short. Update it at every story boundary (in the same step as a green `make check`), never as
> a later cleanup pass. If this file and reality disagree, this file is the bug.

**Last updated:** 2026-07-26
**Phase:** Post-build — the product is complete and live-deployed. We are now in **agile iteration**:
one story at a time from [BACKLOG.md](BACKLOG.md).

---

## Active Story

**None.** No work is mid-flight. The build backlog (Epics 1–6, 39 stories) is done and live-verified;
see [CHANGELOG.md](CHANGELOG.md) for the milestone summary.

> When you start work: pick or create a story in [BACKLOG.md](BACKLOG.md), record it here as the Active
> Story (id + one-line intent + status `WIP`), and note the immediate next action.

## ▶ Next Action

**Await the next request.** Then run the CLAUDE.md decision tree (incomplete task vs new requirement):

- **New requirement** → write it as a story in [BACKLOG.md](BACKLOG.md) (id, intent, acceptance
  criteria), set it Active here, implement.
- **Continue known work** → the highest-value open items already captured in [BACKLOG.md](BACKLOG.md)
  are the **live-activation / ops** items (a redeploy + a 4th Confluence Automation rule to make the
  FR-17 inline-comment channel fire live, and proving the webhook-driven publish last-mile to
  `complete` on the Droplet). These are gated on deployment access — see [BLOCKERS.md](BLOCKERS.md).

## Snapshot of where things stand

- **Code:** all planned features built; full offline suite green, ruff clean, import-linter contracts kept.
  Run the gate locally with `make check`.
- **Live:** deployed at `https://poetroastery.com` (DigitalOcean Droplet, Ubuntu, 1 GB). The full flow
  has run end-to-end to `complete` against the real Atlassian tenant + Claude.
- **Two features await a live-activation step** (code + tests are in): FR-16 draft-deletion recovery and
  FR-17 inline-comment feedback each need a Confluence Automation rule + a Droplet redeploy. Details in
  [BACKLOG.md](BACKLOG.md).
- **Deliberate, documented gaps** (not bugs): the publish edit-restriction is skipped on this
  Confluence **Free** tenant (D-21), and off-box SQLite backup (litestream) is not running by the
  owner's call. See [BLOCKERS.md](BLOCKERS.md).

## Standing rules for whoever picks this up

1. `CLAUDE.md` → Non-Negotiable Invariants hold in every commit (AD-1, AD-2/11, AD-4, AD-9, AD-15, AD-16, AD-20, AD-21).
2. A story is Done only when its acceptance criteria are met **and** `make check` is green. Else it's `PARTIAL`/`BLOCKED` with the reason.
3. The unit suite runs with **no network and no credentials** — fakes at the adapter boundary.
4. On a human/3rd-party gate: record in [BLOCKERS.md](BLOCKERS.md), ask the user, move to unblocked work. Never stub around it silently.
5. Changed a behavior/rule the 3 critical docs describe? Sync them (CLAUDE.md → "keep the 3 docs in sync"). Moved/added a module? Update CLAUDE.md's Codebase Map.
