# Setup Guide — connecting the agent to Jira, Confluence, Claude, and LangSmith

This guide takes you from "I have a Jira and Confluence account" to "the agent is configured and
verified." It assumes **no prior experience** with API tokens, account IDs, or webhooks.

You will end up with exactly two files filled in, both of which stay on your machine:

| File | Contains | Committed to git? |
|---|---|---|
| `.env` | secrets — API tokens, keys | **No.** Gitignored. Never commit it. |
| `config/registry.yaml` | IDs — project keys, folder IDs, account IDs | Yes. No secrets in here by design. |

> **Why two files?** So that adding a project or swapping a reviewer is a reviewable config change
> (NFR-02), while secrets never enter git history. The config file references secrets by *name*
> (`env:ALPHA_JIRA`), never by value.

**Time needed:** about 30–40 minutes for Parts 1–6. Parts 7–8 come later, when you deploy.

---

## Checklist

Work top to bottom. Each part tells you exactly which value it produces.

- [ ] **Part 1** — Atlassian API token
- [ ] **Part 2** — Collect your IDs (site URL, project keys, space + folder IDs, account IDs)
- [ ] **Part 3** — Anthropic API key *(you already have this — just needs placing)*
- [ ] **Part 4** — Webhook shared secret *(you generate this yourself)*
- [ ] **Part 5** — LangSmith account + API key
- [ ] **Part 6** — Fill in `.env` and `config/registry.yaml`, then verify
- [ ] **Part 7** — Register the webhooks *(after the server is reachable — Epic 6)*
- [ ] **Part 8** — DigitalOcean Droplet + Spaces *(deployment — Epic 6)*

---

## Part 1 — Create an Atlassian API token

An API token is how the agent proves it is allowed to act in your Jira and Confluence. It works like
a password, but you can revoke it without changing your own login.

**Important:** the agent acts *as the account that owns the token*. Whatever login owns this token
becomes the **Creator** of every ticket and the author of every comment and draft page — and in Jira
Cloud the Creator field can never be changed after the fact. For the demo, using your own account
works, but then every ticket looks like *you* filed it. To make it obvious to the whole team that the
flow — not a person — produced these tickets, create a dedicated agent account (see the box below).

> **Recommended for a shared team: a dedicated "UserDoc Agent" account.**
> 1. Have an Atlassian **org admin** invite a new user, e.g. `userdoc-agent@yourcompany.com`, and name
>    it **UserDoc Agent** (this name is what everyone will see in the *Reporter* / *Creator* columns).
> 2. Grant it **product access to both Jira and Confluence** — this consumes one licensed seat per
>    product (on the Free tier it counts against the ~10-user cap; on paid tiers it is a billable seat).
> 3. Add it to the **Main** and **Review** Jira projects with a role that allows: *Browse Projects,
>    Create Issues, Add Comments, Transition Issues, Assign Issues*. Give it **add/edit page** on the
>    Confluence space (plus the space-restriction permission the publisher needs for FR-15).
> 4. Log in **as that account** and create the API token (steps 1–5 below) from *its* profile.
> 5. Put that account's email + token in `.env` as the Atlassian credentials. No code or
>    `config/registry.yaml` change is needed — the agent already stamps every ticket with an
>    `agent-generated` label and no longer overrides any Reporter to a human (D-33), so a dedicated
>    account is all that is required for full "made by the agent" attribution.
>
> Bonus: a distinct agent account also makes the AD-10 self-author detection guard and the review
> transcript's speaker labelling exact, instead of relying on the token happening not to be a
> reviewer's account.

1. Go to **https://id.atlassian.com/manage-profile/security/api-tokens**
   (or: click your avatar in Jira → **Manage account** → **Security** → **Create and manage API tokens**).
2. Click **Create API token**.
   - If you are offered both "Create API token" and "Create API token with scopes", choose the plain
     **Create API token**. The scoped variety needs extra configuration this build does not use.
3. Give it a label you will recognise later, e.g. `leapxpert-agent-a`.
4. Set an expiry if prompted. Pick the longest available — a token expiring mid-demo is a confusing
   failure to debug.
5. Click **Create**, then **Copy**.

> ⚠️ **The token is shown exactly once.** Paste it somewhere safe immediately. If you lose it, delete
> the token and create a new one — you cannot view it again.

**You now have:**
- `ATLASSIAN_API_TOKEN` — the long string you copied
- `ATLASSIAN_EMAIL` — the email address you log into Atlassian with
- `ATLASSIAN_SITE_URL` — your site address, e.g. `https://yourcompany.atlassian.net`
  (take it from your browser's address bar; **no trailing slash**, and no `/jira` or `/wiki` on the end)

Both Jira and Confluence use the **same** token and the same site URL.

---

## Part 2 — Collect your IDs

The agent needs to know *where* to work: which Jira projects, which Confluence folders, and which
people. These are IDs, not names.

### The easy way: run the discovery script

Once you have the values from Part 1, this prints every ID you need in one go.

```bash
# from the project root
export ATLASSIAN_SITE_URL="https://yourcompany.atlassian.net"
export ATLASSIAN_EMAIL="you@example.com"
export ATLASSIAN_API_TOKEN="paste-your-token-here"

.venv/bin/python scripts/discover_ids.py
```

It lists your Jira projects with their keys, your Confluence spaces and folders with their IDs, and
your own account ID. Copy the values into Part 6.

To also look up your teammates' account IDs, pass their emails:

```bash
.venv/bin/python scripts/discover_ids.py --emails pm@example.com head@example.com
```

If the script fails, the manual instructions below produce the same values.

### The manual way

#### 2a. Jira project keys

A project key is the short prefix on every issue in that project — the `ABC` in `ABC-123`.

1. Open Jira → **Projects** → your project.
2. Look at any issue's key, or the URL: `.../jira/software/projects/**ABC**/boards/1`.

You need two:
- **Main project key** — where the PRD-tracking ticket and the Publishing ticket go.
- **Review project key** — where the UserDoc review ticket and rename-request tickets go.
  *(You may have named this project "Preview" — the name does not matter, only the key.)*

> These two **must be different projects**. The config loader rejects them being the same, because
> the Review project exists to keep the PM's review loop out of the main project.

#### 2b. Confluence space key

1. Open your Confluence space.
2. The URL contains it: `.../wiki/spaces/**SPACEKEY**/overview`.

#### 2c. Confluence folder IDs — the fiddly one

You have three folders: **source** (where finalized PRDs are uploaded), **draft** (where the agent
posts UserDoc drafts for review), and **published** (where approved UserDocs end up).

To get a folder's ID: **click into the folder in Confluence and read the URL.** It looks like:

```
https://yourcompany.atlassian.net/wiki/spaces/SPACEKEY/folder/1234567890
                                                               ^^^^^^^^^^
                                                               this is the folder ID
```

Do this for all three folders and note which is which.

> 🚨 **Critical layout requirement.** The **published** folder must sit **next to** the source folder,
> **never inside it**:
>
> ```
> Space
>  ├── final_PRD/          ← source (watched)
>  ├── userdoc-drafts/     ← draft
>  └── userdoc-published/  ← published  ✅ sibling of source
> ```
>
> ```
> Space
>  └── final_PRD/          ← source (watched)
>       └── published/     ← ❌ WRONG: inside source
> ```
>
> If published sits inside source, the agent detects its own published document as a new PRD and
> starts drafting again — forever. This is the system's primary self-ingestion guard. The config
> loader catches the exact-match case, but it cannot see your folder *tree*, so please check visually.

#### 2d. Atlassian account IDs

An account ID is a long string like `5b10ac8d82e05b22cc7d4ef5` or `712020:a1b2c3d4-...`. It is **not**
an email or a display name.

1. In Confluence, go to **People** (top nav) and click the person.
2. Read it from the URL: `.../wiki/people/**5b10ac8d82e05b22cc7d4ef5**`.

You need three (for the demo they can all be *you*):
- **`pm_account_id`** — the Reviewer PM. Reviews drafts, gives feedback, and signals PASS by moving
  the Review ticket to Done.
- **`head_of_product_account_id`** — approves publishing by moving the Publishing ticket to Done.
- **`admin_account_id`** — gets tagged when something breaks, and replies `@agent resume` to retry.

---

## Part 3 — Anthropic API key

You already have this. If you need another: **https://console.anthropic.com/settings/keys** →
**Create Key**. It starts with `sk-ant-`.

This one key runs all six agent roles (Classifier, Ticket manager, Author, Feedback interpreter,
Publisher, Error handler).

**You now have:** `ANTHROPIC_API_KEY`

> **Cost note.** The demo's biggest single spend is the classifier evaluation, which runs the
> Classifier three times over the labeled fixture set. That is a small number of short calls — cents,
> not dollars. Drafting a UserDoc is the larger per-run cost, and you can watch it in LangSmith
> (Part 5).

---

## Part 4 — Webhook shared secret

This is **not** something you fetch from a website. **You invent it**, then tell both sides.

Here is why it matters: the agent listens on a public internet address, and anything that reaches
that address can make it write to your Jira and Confluence. The shared secret is how the agent knows
a request genuinely came from Atlassian and not from someone who found the URL.

Generate one:

```bash
openssl rand -hex 32
```

That prints a 64-character random string. Copy it.

**You now have:** `WEBHOOK_SHARED_SECRET`

You will paste this same value in two places: into `.env` (Part 6), and into the Jira/Confluence
webhook configuration (Part 7). They must match exactly.

While you are here, generate a second one for the admin endpoint:

```bash
openssl rand -hex 32
```

**You now have:** `ADMIN_API_TOKEN` — protects the local maintenance endpoint that the scheduled
liveness check calls.

---

## Part 5 — LangSmith account and API key

LangSmith shows you what every AI call cost, how long it took, and what it produced. The demo's
definition of done requires this visibility. The **free tier is enough**.

1. Go to **https://smith.langchain.com** and sign up (Google/GitHub sign-in is fine).
2. Once in, open **Settings** (usually your avatar or the gear icon) → **API Keys**.
3. Click **Create API Key**, name it `leapxpert-agent-a`, and copy it. It starts with `lsv2_`.

**You now have:** `LANGSMITH_API_KEY`

> ⚠️ **Privacy.** Tracing sends content to LangSmith's servers. For the demo, use **non-confidential
> test PRDs only**. The config has a `trace_content: false` flag (the default) which keeps document
> bodies out of traces while still recording timing and cost.

---

## Part 6 — Fill in the two files, then verify

### 6a. Create `.env`

```bash
cp .env.example .env
```

Open `.env` and fill in. Using tenant name `project_alpha`, the prefix is `ALPHA`:

```bash
# --- Atlassian (same token and URL for both Jira and Confluence) ---
ALPHA_JIRA_BASE_URL=https://yourcompany.atlassian.net
ALPHA_JIRA_EMAIL=you@example.com
ALPHA_JIRA_API_TOKEN=paste-the-Part-1-token

ALPHA_CONF_BASE_URL=https://yourcompany.atlassian.net
ALPHA_CONF_EMAIL=you@example.com
ALPHA_CONF_API_TOKEN=paste-the-same-Part-1-token

# --- Part 4 ---
WEBHOOK_SHARED_SECRET=paste-the-first-openssl-string
ADMIN_API_TOKEN=paste-the-second-openssl-string

# --- Part 3 ---
ANTHROPIC_API_KEY=sk-ant-...

# --- Part 5 ---
LANGSMITH_API_KEY=lsv2_...
```

> **How the naming works.** `config/registry.yaml` says `jira_credentials_ref: "env:ALPHA_JIRA"`.
> The agent expands that prefix into `ALPHA_JIRA_BASE_URL`, `ALPHA_JIRA_EMAIL`, and
> `ALPHA_JIRA_API_TOKEN`. If you rename the tenant, keep the prefix and the variable names in step.

### 6b. Create `config/registry.yaml`

```bash
cp config/registry.example.yaml config/registry.yaml
```

Replace every `REPLACE_...` placeholder with your Part 2 values:

```yaml
tenants:
  project_alpha:
    confluence_source_folder_id: "1234567890"      # 2c — the WATCHED folder
    confluence_draft_folder_id: "1234567891"       # 2c
    confluence_published_folder_id: "1234567892"   # 2c — sibling of source, NOT inside it
    confluence_space_key: "SPACEKEY"               # 2b

    jira_main_project_key: "MAIN"                  # 2a
    jira_review_project_key: "REV"                 # 2a — must differ from main

    pm_account_id: "5b10ac8d82e05b22cc7d4ef5"      # 2d
    head_of_product_account_id: "..."              # 2d
    admin_account_id: "..."                        # 2d

    md_export_dir: "/data/userdocs/alpha"
    jira_credentials_ref: "env:ALPHA_JIRA"
    confluence_credentials_ref: "env:ALPHA_CONF"
```

Leave everything else at its default.

### 6c. Verify

```bash
.venv/bin/python scripts/verify_setup.py
```

This checks that your config parses, your credentials work against the real Atlassian site, the
folders and projects exist, the account IDs are real people, and your Anthropic key is accepted.
It changes nothing — every check is read-only. Fix anything it reports, then re-run until it is green.

---

## Part 7 — Register the webhooks *(after deployment)*

Webhooks are how Atlassian tells the agent something happened. They need a **public HTTPS address**,
so this comes after the server is deployed (Part 8 / Epic 6). Your address will look like
`https://your-domain-or-ip/webhooks/atlassian`.

### 7a. Jira

1. **⚙️ Settings** (top right) → **System** → **WebHooks** (under Advanced), or go directly to
   `https://yourcompany.atlassian.net/plugins/servlet/webhooks`.
2. **Create a WebHook**.
3. Fill in:
   - **Name:** `LeapXpert Agent A`
   - **URL:** your public webhook address
   - **Secret:** paste your `WEBHOOK_SHARED_SECRET` from Part 4
   - **Events:** tick **Comment → created** and **Issue → updated**
   - **JQL filter (optional but recommended):** `project in (MAIN, REV)` so you only receive events
     for the relevant projects
4. **Create**.

### 7b. Confluence

Confluence Cloud has no equivalent admin webhook screen, so use **Automation** rules instead. Create
**three** rules, all with the same *Send web request* action, differing only in the trigger:

| Rule | Trigger | Why it matters |
|---|---|---|
| **PRD created** | *Page created* | starts a run when a `final_PRD_*` page appears |
| **PRD renamed** | *Page updated* | how a mis-named PRD re-enters the flow after it's corrected (FR-02a/EH-04) |
| **Draft deleted** | *Page trashed* (a.k.a. *Page removed/deleted*) | lets the agent detect a deleted UserDoc draft and **ask the PM whether to restore it** (FR-16) |

For **each** rule:

1. **Space settings** → **Automation** → **Create rule**, pick the trigger above.
2. **Action:** *Send web request*.
   - **URL:** `https://<your-domain>/webhooks/atlassian`
   - **Method:** `POST`
   - **Web request body:** **Custom data** (not "Empty" and not the default) — paste exactly:
     ```json
     {"webhookEvent": "<EVENT>", "page": {"id": "{{page.id}}", "title": "{{page.title}}"}}
     ```
     Replace `<EVENT>` with `page_created`, `page_updated`, or `page_trashed` to match the trigger.
   - **Headers:** add `X-Webhook-Secret` = your `WEBHOOK_SHARED_SECRET`
     *(Automation cannot compute an HMAC, so the agent accepts this shared-secret header instead.)*
   - Leave *"Delay execution … until we've received a response"* **unchecked** — a drafting run takes
     a minute, and the agent acks the webhook immediately anyway.
3. Turn the rule on.

> **Why Custom data, not the default body?** Confluence Automation exposes **no page-version smart
> value at all**, and its default payload does not carry the fields the agent needs. The agent only
> needs the page **id** (and title); it resolves the authoritative version itself with one API read.
> A body of `Empty` or the default is silently dropped as unparseable.

> **Scope the rules to the source folder if you can.** The triggers are space-wide by default; adding
> a condition that limits *PRD created/renamed* to the source folder avoids the agent even seeing
> unrelated pages. It is defense-in-depth only — the agent already refuses any page outside the source
> folder and any page it created itself.

> **The Draft-deleted rule is forgiving.** The agent checks a page's real *status*, so even if your
> deletion rule fires as *page updated* (some spaces do), a trashed draft is still detected — as long
> as the rule sends the page **id**. What it does on a deletion is **ask the Reviewer PM** on the
> Review ticket whether it was intentional; it restores the draft **only** if the PM replies that it
> was a mistake. It never auto-restores a page someone may have deleted on purpose.

> **If a webhook is silently dropped** — Atlassian delivery is best-effort — the scheduled liveness
> check catches a missed *gate* transition. A missed *deletion* surfaces at publish time as an
> actionable error (the agent refuses to publish a missing draft and tells the human to restore it),
> not a silent auto-recovery.

---

## Part 8 — DigitalOcean Droplet and Spaces *(deployment)*

Needed for Epic 6 only. You can skip this until the rest works locally.

### 8a. The Droplet

1. **https://cloud.digitalocean.com** → **Create** → **Droplets**.
2. Choose: **Ubuntu LTS**, **Basic** plan, **Regular SSD**, **1 GB RAM / 1 vCPU / 25 GB** (~$6/month).
3. Add your SSH key, name it, and create.
4. Note the **public IP address**.

> 💡 **A powered-off Droplet is still billed at the full rate.** To actually stop charges you must
> destroy it (take a snapshot first if you want to keep the state).

> ⚠️ **Never build the Docker image on this Droplet.** 1 GB is not enough and the build will run out
> of memory. Build it elsewhere (CI or your laptop) and pull the finished image down.

### 8b. Spaces (off-box backup)

The agent's workflow state lives only on the Droplet's disk. If that disk is lost mid-run, redelivered
webhooks could create duplicate tickets or re-publish a document. Spaces holds a continuous replica.

1. **Create** → **Spaces Object Storage**. Pick a region and a unique bucket name.
2. Go to **API** → **Spaces Keys** → **Generate New Key**.
3. Copy both halves — the secret is shown only once.

Add to `.env`:

```bash
LITESTREAM_ACCESS_KEY_ID=...
LITESTREAM_SECRET_ACCESS_KEY=...
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Environment variable ALPHA_JIRA_API_TOKEN is not set` | Missing or misspelled in `.env`, or `.env` not created | Check the name matches the `env:` prefix in `registry.yaml` exactly |
| `401 Unauthorized` | Token wrong, expired, or email mismatched | Confirm the email is the one that *owns* the token; regenerate if unsure |
| `403 Forbidden` | The token's account lacks access to that project or space | Grant that account permission in Jira/Confluence |
| `404 Not Found` | A wrong ID in `registry.yaml` | Re-run `scripts/discover_ids.py` and compare |
| `confluence_published_folder_id must be ADJACENT to ...` | Published and source folder are the same ID | Use three distinct folders (see 2c) |
| `jira_main_project_key and jira_review_project_key must differ` | Both set to the same project | The Review project must be a separate project |
| Agent never reacts to a new page | Webhook not firing, or wrong secret | Check the Automation rule's audit log; confirm the secret matches `.env` byte for byte |
| Agent drafts endlessly from its own output | Published folder is *inside* the source folder | Move it to be a sibling (see 2c) |

---

## What each secret can do, if leaked

Worth knowing, so you treat them appropriately.

| Secret | Blast radius | If leaked |
|---|---|---|
| Atlassian API token | Full access to Jira + Confluence **as your account** | Revoke immediately at the Part 1 page |
| `ANTHROPIC_API_KEY` | Spends your Claude credit | Revoke in the Anthropic console |
| `WEBHOOK_SHARED_SECRET` | Lets someone trigger agent actions in your Atlassian | Generate a new one; update `.env` *and* both webhook configs |
| `LANGSMITH_API_KEY` | Read/write your traces | Revoke in LangSmith settings |
| Spaces keys | Read/write the state backup | Regenerate in the DigitalOcean panel |

`.env` is gitignored, so these do not enter git history — provided you keep them in `.env` and never
paste them into `config/registry.yaml` or into code. The config loader actively rejects an inline
secret to make that mistake hard.

> **One production note:** every tenant's token currently sits in this single `.env`, so its blast
> radius is *all* tenants. That is an accepted demo trade-off; production should scope and rotate
> per-tenant credentials.

---

## When you are done

Run the verifier one more time:

```bash
.venv/bin/python scripts/verify_setup.py
```

When it is green, tell me and I will wire the live integration and run the end-to-end demo. Parts 1–6
are enough to unblock nearly everything; Parts 7–8 are only needed for the final deployed run.
