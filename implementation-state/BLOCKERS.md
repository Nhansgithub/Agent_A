# Blockers — Human / Third-Party Gates

Anything on this list **cannot be self-served by the agent**. The rule (CLAUDE.md → Human-Block
Protocol): stop that thread, record it here, ask the user, and continue with everything not blocked.

**Status legend:** `OPEN` (waiting on the user) · `RESOLVED` (supplied — record how/where) ·
`ANTICIPATED` (not needed yet, will block a later story)

---

> **Setup instructions for every gate below live in [`../SETUP-GUIDE.md`](../SETUP-GUIDE.md).**
> Two helper scripts make it concrete: `scripts/discover_ids.py` prints the Atlassian IDs to paste
> into `config/registry.yaml`, and `scripts/verify_setup.py` checks the whole configuration
> read-only. Nhan is working through the guide in parallel with the build (confirmed 2026-07-24).

## RESOLVED (by decision) — B-7 · Confluence Cloud plan blocks the publish restriction

**Blocks:** FR-15 step 1 / AD-18 — the edit restriction, and therefore Epic 6's publish transaction
and the `complete` stage. The run for PRD 1441969 is parked in `error` at `last_good_checkpoint =
publishing` (correct EH-01/AD-19 behaviour), Publishing ticket **AMS-12**.

**What happened.** The Head of Product moved AMS-12 to Done, the publish transaction started, and
step 1 failed:

```
Not enough permissions to alter ContentRestrictions on a content with ContentId <1540119> (HTTP 403)
```

**Why it is not a permissions fix.** Verified read-only against the live tenant: the agent account
already holds `restrict_content:space` **and** `administer:space` on space 360452, and the content
permission check returns `hasPermission: true` for `update`. The real cause is the plan tier —
`GET /wiki/rest/api/settings/systemInfo` returns **`"edition": "free"`**, and page restrictions are
not part of Confluence Cloud Free. On Free the API reports the gap as a permission error, which is
why the generic 403 advice is misleading (now overridden in `app/adapters/http.py`).

**Why the agent cannot self-serve it.** It is a paid plan upgrade on Nhan's Atlassian site — money
and account ownership. No permission grant, token change, or code change can enable the feature.

**Nhan's options (this is a decision, not just a task):**

1. **Upgrade the Confluence site to Standard** (Standard has a free trial). Then:
   `.venv/bin/python scripts/run_local_demo.py --admin-resume` — AD-18's ordered idempotent publish
   re-enters at `publishing` and completes restriction → move → export. This is the only path that
   demonstrates FR-15 as specified.
2. **Make the restriction optional per tenant** — a config flag so a Free-edition site skips step 1
   and still completes move + export. This *knowingly relaxes* FR-15 step 1 / AD-18, so it needs
   Nhan's explicit call; the agent must not decide to drop a spec'd requirement on its own.
3. **Leave the run parked** and treat the restriction as demonstrated-by-test only (the publish
   transaction is fully covered offline in `tests/test_publishing.py`).

**RESOLVED 2026-07-24 — Nhan chose option 2** (the config flag). `require_edit_restriction: false`
is set for `project_alpha`; the run completed via `--admin-resume` (move + export, no restriction),
and the agent posted the "not edit-restricted" notice on AMS-12. Recorded as **D-21** — this is a
knowing relaxation of FR-15 step 1 / AD-18, reversible by flipping the flag after a plan upgrade.
**The published page is editable by anyone with space access.**

---

## OPEN — remaining gates (deployment only)

Setup was completed by Nhan on 2026-07-24 and `scripts/verify_setup.py` reports **17 passed, 0
failed**. Everything needed to *run* the flow is in place; what is left is deployment.

| Gate | Status | Still needed |
|---|---|---|
| **Atlassian account, projects, folders** | ✅ Done | Jira AMS (main) + UDR (review); Confluence folders 65871 / 1474562 / 1441796. |
| **Atlassian API token** | ✅ Done | In `.env`, verified live. |
| **Atlassian IDs** | ✅ Done | In `config/registry.yaml`, all verified by `verify_setup.py`. |
| **Anthropic API key** | ✅ Done | Verified live (classifier eval + drafting + revise loop all ran). |
| **Webhook shared secret** | ✅ Done | Set. Not yet *exercised* — no endpoint is deployed to receive a signed delivery. |
| **LangSmith account + key** | ✅ Done | Key accepted by the API. |
| **DigitalOcean Droplet + Spaces** (B-4) | ⏳ Not started | The real blocker now — without it the webhook path (S6.4 full) cannot run live. SETUP-GUIDE Part 8. |
| **Container registry / CI** (B-5) | ⏳ Not started | Needed to build the image off-box (AD-21: the 1 GB Droplet must not build). |

**Naming note:** Nhan referred to the second Jira project as *"Preview"*; the PRD and this codebase
call it the **Review** project. Same thing — only its project *key* matters in config.

Everything except B-4/B-5 is unblocked and has run live. The webhook ingress layer remains the one
component verified **only** offline — see STATE.md → Next Action.

---

## ANTICIPATED (reference detail)

---

## ANTICIPATED

These are known from the planning artifacts. Each becomes `OPEN` when the build actually reaches it.
Listed in the order the build will hit them.

### B-1 · Anthropic API key
- **Blocks:** S2.3 (Classifier live), S2.4 (×3 holdout eval — the demo's one measurable gate),
  S3.1/S3.2 (Author drafting + self-critique), S4.1/4.2/4.4/4.5 (Feedback interpreter).
- **Why not self-servable:** paid API credential tied to the user's account.
- **Needed:** `ANTHROPIC_API_KEY` in the local `.env`. A key with Claude API access and enough credit
  to run the classifier eval three times over ~8 fixtures plus a few authoring runs.
- **Workaround while OPEN:** all agent code, prompts, `SKILL.md` files, and the eval harness are built
  and unit-tested against a **fake LLM client** at the injection boundary. Only *live* accuracy
  measurement and real drafting are blocked.

### B-2 · LangSmith account + API key
- **Blocks:** S1.10 (live trace verification), S6.7 (content-gating flag verification), NFR-01 evidence
  for the §12 Definition of Done ("LangSmith shows per-step latency, speed, and cost").
- **Needed:** `LANGSMITH_API_KEY` + project name. Free tier is sufficient for the demo.
- **Workaround while OPEN:** the tracing harness is built with tracing togglable via env; unit tests
  assert that every LLM call passes through the traced wrapper carrying correlation id + `review_round`.

### B-3 · Atlassian Cloud tenant — Jira + Confluence (the big one)
- **Blocks:** S1.7/S1.8 live round-trip, all of Epic 2/3/4/5 live behaviour, S6.4 end-to-end demo run.
- **Needed:**
  1. An Atlassian Cloud site (Jira + Confluence) usable for testing.
  2. An **API token** for the agent's service account (`id.atlassian.com` → Security → API tokens),
     plus that account's email and the site base URL.
  3. **Two Jira projects** — a *Main* project (tracking + publishing tickets) and a *Review* project
     (draft-review + rename-request tickets). Their project keys.
  4. A **Confluence space** with **three folders**: a watched *source* folder, a *draft/review* folder,
     and a *published* folder that is **adjacent to — not inside —** the source folder (FR-15 → FR-01).
     Their folder ids.
  5. **Account ids** for the Reviewer PM, Head of Product, and admin (may all be the same person for
     the demo).
  6. Webhooks registered for `page_created` / `page_updated` (Confluence) and `comment_created` /
     `issue_updated` (Jira), pointed at the public HTTPS endpoint with a shared secret.
- **Open verification questions this closes** (PRD §13 Q1/Q3/Q4, Spine → Deferred):
  - Does the demo Jira workflow have a legal path to a `done`-category status without a mandatory
    screen the agent cannot fill? (drives AD-13's config-declared multi-hop path)
  - Does the Confluence `page-created` payload carry the creator `accountId`, or is a follow-up
    `GET page` needed? (drives AD-12)
  - Are folders addressable by id as the v2 API expects on this instance? (AD-14)
- **Workaround while OPEN:** both adapters are built against the documented REST contracts and tested
  with a fake HTTP transport (recorded request/response shapes). Only live verification is blocked.

### B-4 · DigitalOcean Droplet + Spaces
- **Blocks:** S6.3 (litestream backup to Spaces), S6.4 (deploy + the end-to-end demo run),
  S6.5 (1 GB memory-envelope verification under real load).
- **Needed:** a Droplet (Basic / Regular SSD, 1 GB / 1 vCPU / 25 GB, Ubuntu LTS) with its IP and SSH
  access; a DO Spaces bucket + access key/secret for the SQLite WAL replica; a DNS name or IP that
  Atlassian webhooks can reach over HTTPS.
- **Note for the owner:** a powered-off Droplet is still billed (PRD §15.5).
- **Workaround while OPEN:** `deploy/` (Dockerfile, Caddyfile, swap + firewall scripts, litestream
  config, cron reconcile) is written and lint-checked locally; only provisioning and the live run are blocked.

### B-5 · Container registry / CI
- **Blocks:** S6.4 — AD-21 requires the image be built **off** the 1 GB box and pulled.
- **Needed:** either a GitHub repo with Actions enabled + a registry (GHCR/Docker Hub/DO Container
  Registry) and its credentials, or confirmation that the user will build locally and push.
- **Related:** this repo is **not a git repository yet** and Docker is **not installed on this machine**.

### B-6 · Local Docker (minor)
- **Blocks:** local image-build smoke test only.
- **Needed:** Docker Desktop (or colima) installed, if the user wants the image verified locally before
  CI. Not required if CI builds it.

---

## RESOLVED

_(none yet)_
