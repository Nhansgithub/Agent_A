# NOW — the one file to read first

> **Purpose:** in ~30 seconds, tell the next agent *what is the state of play* and *what to do next*.
> Keep it short. Update it at every story boundary (in the same step as a green `make check`), never as
> a later cleanup pass. If this file and reality disagree, this file is the bug.

**Last updated:** 2026-07-27
**Phase:** **Epic 7 — Agent B is CODE-COMPLETE.** All stories S-B0…S-B10 done and green. Agent A is
code-complete and live. Only **live-activation gates** remain (human/3rd-party), no code.

---

## Active Story

*(none — Epic 7 is code-complete. Next work is either activating the live gates below, or a new
requirement — write it as a story in [BACKLOG.md](BACKLOG.md) first.)*

## ▶ Next Action — going live (config wired 2026-07-27; box execution is B-4)

Config is done: Slack channel `C0BL3KQSK1S` + designs folder `1999113` wired into `config/registry.yaml`
(the load-blocking `embeddings.store` bug fixed too), and the Agent B deploy plumbing landed
(`deploy/Dockerfile.agent_b`, `deploy/agent_b.sh`, docker pull-cron, README). **Remaining = run it on the
box** (needs droplet SSH — B-4; cannot be done from the build env):

1. Push `master` → CI rebuilds Agent A `:latest` (ships S-B8/S-B10 app changes) → `./deploy/redeploy.sh`.
2. Build the Agent B image off-box: `docker build -f deploy/Dockerfile.agent_b -t ghcr.io/nhansgithub/agent_b_bot:latest . && docker push …`; mirror the `agent_b:` block into `/opt/agent/config/registry.yaml`.
3. `./deploy/agent_b.sh` — seeds vault+index, starts the `agent-b` Slack bot, installs the nightly cron.
4. (Optional) S-B5 site: A record + `deploy/build_site.sh` + serve; S-B9: fill `fixtures/agent_b/golden.json` + `run_agent_b_eval.py`.

- **Live activation gated (human/3rd-party):** B-4 (droplet SSH + DNS). B-9/B-10 creds are supplied. See [BLOCKERS.md](BLOCKERS.md).
- **Live activation gated (human/3rd-party):** S-B7 live → B-9 (Slack app+tokens); S-B1 designs pull →
  B-10 (designs folder id); S-B5 live URL → B-4 (deploy + DNS). See [BLOCKERS.md](BLOCKERS.md); setup
  walk-throughs in [SETUP-GUIDE.md](../SETUP-GUIDE.md) Parts 9–10.

## Snapshot of where things stand

- **Agent A:** unchanged behaviour and green (one additive Confluence read verb from B1; Agent A calls it not).
- **Agent B:** **S-B0…S-B10 done — code-complete.** Own SQLite store + enforced boundaries
  (AD-27/AD-32); curated pull → idempotent Obsidian vault, **linked** + **LLM-curated**, **maintained
  on a schedule** (S-B4), **image assets mirrored** (S-B10), **Quartz-publishable** (S-B5, URL gated
  B-4), **RAG-queryable** (S-B6), answerable **over Slack** (S-B7, bot gated B-9), **eval-gated** (S-B9).
  S-B8 retired Agent A's `.md` export (D-44). `make check` green at **609 passed, 1 skipped, 7/7 contracts**.
- **Locked calls:** monorepo sibling; vault = read-only Obsidian **projection**; organization = MOC + tags
  (no physical moves); local embeddings via **fastembed** + **sqlite-vec**; Quartz at a **public** URL;
  **designs = a text folder** in the same pull.

## Standing rules for whoever picks this up

1. `CLAUDE.md` → Non-Negotiable Invariants hold in every commit — extended to `agent_b` (AD-27…AD-32).
2. A story is Done only when its acceptance criteria are met **and** `make check` is green. Else `PARTIAL`/`BLOCKED` with the reason.
3. The unit suite runs with **no network and no credentials** — fakes at the boundary (fake Confluence tree/transport, fake LLM, fake Slack).
4. On a human/3rd-party gate: record in [BLOCKERS.md](BLOCKERS.md), ask the user, move to unblocked work.
5. Changed a behavior/rule the 3 critical docs describe? Sync them. Moved/added a module? Update CLAUDE.md's Codebase Map.
