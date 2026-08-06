# BLOCKERS — human / third-party gates

> Anything here **cannot be self-served by the coding agent** — it needs a credential, an account, a paid
> resource, deployment access, or a live human action. The rule (CLAUDE.md → Human-Block Protocol): stop
> that thread, record it here, ask the user, and continue with everything not blocked. Never stub around
> a gate silently.
>
> **Status legend:** `OPEN` (waiting on the user) · `RESOLVED` (how/where recorded) · `ANTICIPATED`
> (not needed yet). Setup steps for every gate live in [../SETUP-GUIDE.md](../SETUP-GUIDE.md);
> `scripts/verify_setup.py` checks the whole live config read-only.

---

## OPEN

### B-4 · Deployment access (Droplet + Automation rules)
**Blocks:** BACKLOG **S-01** (FR-17 live), **S-02** (FR-16 live), **S-03** (prove webhook publish last-mile), **S-B5** (Quartz URL — also needs an `agent.poetroastery.com` A record + a Caddy route), **S-04** (nightly KB-site publish CI — needs the two repo secrets in step 6).
**Why not self-servable:** requires SSH/deploy access to the DigitalOcean Droplet and org-admin rights in
Confluence to register Automation rules — both are the owner's to perform.
**Exact steps for the human:**
1. Register the outstanding Confluence Automation rules against the live endpoint (`https://poetroastery.com`): the *Page commented* rule (FR-17, SETUP-GUIDE Part 7c) and the *Page trashed* rule (FR-16). Each sends a distinct Custom-data body.
2. Redeploy the Droplet on the current image (CI builds off-box; the box only pulls).
3. Walk one fresh `final_PRD_*` run through both gates **without touching the draft** to close S-03.
4. **For S-B7 (Agent B Slack bot):** creds are already in `.env` (B-9) and the channel is wired. Build the Agent B image off-box (`docker build -f deploy/Dockerfile.agent_b -t ghcr.io/nhansgithub/agent_b_bot:latest . && docker push …`), ensure `/opt/agent/config/registry.yaml`'s `agent_b:` block matches the local one, then run `./deploy/agent_b.sh` — it seeds the vault+index, starts the `agent-b` container (Socket Mode, no inbound port), and installs the nightly pull cron. Test by DM/@-mention in `C0BL3KQSK1S`.
5. **For S-B5 (KB site):** add an `agent.poetroastery.com` A record → the Droplet; build the site OFF-box with `deploy/build_site.sh` (clones pinned Quartz, stages the vault, `npx quartz build`); place the output at `/opt/agent/data/site`; the `agent.poetroastery.com` block in `deploy/Caddyfile` then serves it read-only over auto-TLS (D-45). The build code + Caddy route already landed (S-B5); this step is DNS + the off-box build + serve.
6. **For S-04 (automated nightly KB publish):** add two GitHub **Actions repo secrets** (Settings → Secrets and variables → Actions) — `DROPLET_HOST` (the box's IP/hostname, e.g. `143.198.218.143`) and `DROPLET_SSH_KEY` (a **private** SSH deploy key whose public half is in the box's `root@…:~/.ssh/authorized_keys`). Then `.github/workflows/publish-site.yml` refreshes the site nightly (04:00 UTC, after the pull) and on manual dispatch (Actions → *Publish KB site* → *Run workflow*). Until both secrets exist the workflow **no-ops with a warning** — no failing runs. This is the same box-access gate as S-B5, just for CI rather than a laptop (D-53).
**Note:** a powered-off Droplet is still billed (PRD §15.5). Agent B on the 1 GB box shares RAM with Agent A + Caddy — watch memory (swap cushions it; resize to 2 GB if it thrashes).

### B-9 · Slack workspace + Agent B app credentials — ⚙️ CREDS SUPPLIED (2026-07-27); live deploy pending B-4
**Blocks:** BACKLOG **S-B7** (live Slack Q&A). The code + offline tests are **not** blocked — only the live bot is.
**Status:** the owner created the Slack app and put the three `AGENTB_SLACK_*` keys in `.env`; the channel id `C0BL3KQSK1S` is wired to `agent_b.slack.allowed_channel_ids` in `config/registry.yaml`. What remains is running the bot **on the box** — see **B-4** (build `deploy/Dockerfile.agent_b`, run `deploy/agent_b.sh`). Confirm the bot scopes/events from Part 9 are set and the bot is `/invite`d to the channel.
**Why not self-servable:** creating a Slack app, installing it to the workspace, and issuing a bot token, an
app-level token (Socket Mode), and the signing secret are workspace-admin actions tied to a real Slack org —
no code or existing credential can mint them.
**Exact steps for the human:**
1. Create a Slack app (from scratch) in the target workspace; set its name/avatar so it appears as the "Agent B" user.
2. Enable **Socket Mode**; add bot scopes (`app_mentions:read`, `chat:write`, `im:history`, `im:read`, `im:write`, `reactions:read`); generate an **app-level token** with `connections:write`.
3. Install to the workspace; copy the **Bot User OAuth token**, the **app-level token**, and the **signing secret**.
4. Put them in `/opt/agent/.env` + the local `.env` as `AGENTB_SLACK_BOT_TOKEN` / `AGENTB_SLACK_APP_TOKEN` / `AGENTB_SLACK_SIGNING_SECRET`; set `agent_b.slack.allowed_channel_ids` in `registry.yaml`.
**Full beginner-friendly walk-through:** [SETUP-GUIDE.md](../SETUP-GUIDE.md) → **Part 9** (just paste me the 3 tokens + channel id).

### B-10 · Designs Confluence folder id — ✅ RESOLVED (2026-07-27)
**Was:** the designs folder id for the KB pull. **Resolved:** folder id `1999113` supplied and wired into
`config/registry.yaml` — added to `agent_b.include_folder_ids` with `folder_types: {"1999113": "design"}`.
Nothing further; the crawler now includes it on the next pull.

### B-8 · Dedicated "UserDoc Agent" Atlassian account (attribution enhancement)
**Blocks:** nothing spec'd — an enhancement so agent-created tickets show the agent (not a person) as Creator.
**Why not self-servable:** minting an Atlassian user + granting product access is an org-admin action that
consumes a licensed seat; no token or code change can do it.
**Steps:** SETUP-GUIDE Part 1 ("Recommended…"). Invite `userdoc-agent@…`, grant Jira+Confluence access to
the Main/Review projects, create its API token, put it in `/opt/agent/.env` and local `.env` under
`ALPHA_JIRA_*` / `ALPHA_CONF_*` (no `registry.yaml` change), then re-run `scripts/verify_setup.py`.

---

## RESOLVED (by decision — kept for context)

### B-7 · Confluence Free tenant has no page restrictions → FR-15 step 1 skipped
The live site is Confluence Cloud **Free**, which does not support page edit-restrictions, so the publish
transaction's step 1 (AD-18) 403s. **Owner chose (D-21)** to make the restriction optional per tenant:
`require_edit_restriction: false` for `project_alpha`. The flow completes move + export and the agent posts
a "not edit-restricted" notice on the Publishing ticket. **The published page is editable by anyone with
space access.** Reversible: flip the flag to `true` after upgrading to Standard.

---

## RESOLVED (supplied)

All build-time setup gates are satisfied — `scripts/verify_setup.py` reports green. In place and verified
live: the Atlassian tenant (Jira Main **AMS** + Review **UDR**; Confluence source/draft/published folders),
the Atlassian API token, the Anthropic API key, the LangSmith key, the webhook shared secret, and the
Droplet + CI/registry (image `ghcr.io/nhansgithub/agent_a`, built by GitHub Actions).

---

## ANTICIPATED

- **Off-box backup credentials (litestream → DO Spaces)** — only needed if the owner enables AD-23 backup (currently off by choice). Requires a Spaces bucket + access key/secret.
- Any future story that needs a new third-party service, a paid tier, or a live human action becomes an `OPEN` entry here the moment the build reaches it.
