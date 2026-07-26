# BACKLOG — the prioritized story list

> **This is where work is defined.** A new requirement becomes a **story** here *before* it is coded;
> an in-flight story is tracked here until Done. It is the single place to answer "is there already a
> story for this?" and "what's next?".
>
> **A story is the definition of done.** If the acceptance criteria aren't written, the story isn't ready.
> The `S-xx` id, the intent, and the criteria are the contract the next agent (or the reviewer) holds you to.

**Status legend:** `TODO` (ready to start) · `WIP` (in progress — should also be the Active Story in
[NOW.md](NOW.md)) · `BLOCKED` (needs a human/3rd-party gate — see [BLOCKERS.md](BLOCKERS.md)) ·
`DONE` (acceptance criteria met **and** `make check` green — then move it to [CHANGELOG.md](CHANGELOG.md)).

**Ordering:** top = highest priority. Add a new story at the position its priority warrants.

### Story template — copy this for a new requirement

```
### S-XX · <short title>              [TODO | WIP | BLOCKED]
**Intent:** <one sentence — the user-visible outcome, not the implementation>
**Acceptance criteria:**
- [ ] <Given/When/Then, or a concrete checklet that is objectively verifiable>
- [ ] tests added; `make check` green
- [ ] docs synced if a behavior/rule/stack changed (CLAUDE.md → "keep the 3 docs in sync")
**Notes / pointers:** <files likely involved; the FR/AD/D-xx that governs it, if any>
```

Ids continue from the highest `S-` below. (The original build used `S1.1…S6.7` / epics; those are Done
and live in [CHANGELOG.md](CHANGELOG.md). New agile stories use a flat `S-01, S-02, …`.)

---

## Ready / open

### S-01 · Activate the FR-17 inline-comment feedback channel live   [BLOCKED]
**Intent:** a reviewer's Confluence **inline comment** on a draft actually triggers the flow in production
(today it works in code + tests but nothing delivers the event live).
**Acceptance criteria:**
- [ ] A **4th** Confluence Automation rule (*Page commented* → `webhookEvent: page_commented`) is registered against the Droplet endpoint (SETUP-GUIDE Part 7c).
- [ ] The Droplet is redeployed on the current image.
- [ ] A real inline comment on a draft under review posts a restatement on the Jira Review ticket, @-mentioning the actual commenter, and parks at `awaiting_structure_confirm`.
**Notes / pointers:** FR-17 / AD-26 / D-40. Code: `app/webhooks/` (`ConfluenceCommentEvent`, `_dispatch_comment`), `app/adapters/confluence.py` (`get_inline_comment`), `app/orchestrator` (`apply_inline_comment`). **Blocked on:** deployment access (B-4). No code change expected — this is ops/registration.

### S-02 · Activate the FR-16 draft-deletion recovery live   [BLOCKED]
**Intent:** deleting a draft page mid-flow is detected in production and the agent asks the PM before recovering.
**Acceptance criteria:**
- [ ] A *Page trashed* (or the agreed generic page) Confluence Automation rule is registered against the Droplet.
- [ ] Trashing a draft under review posts the "was that intentional?" question on the Review ticket and parks at `pending_gate = PM_DELETION_DECISION`.
- [ ] A "restore" reply recovers the page; a "leave it" reply leaves it; an unclear reply re-asks.
**Notes / pointers:** FR-16 / AD-25 / D-36,D-38. Code path is built + tested (`apply_draft_deleted` / `apply_deletion_decision`). **Blocked on:** deployment access + the Automation rule (B-4).

### S-03 · Prove the webhook-driven publish last-mile to `complete` on the Droplet   [BLOCKED]
**Intent:** close the one unproven production path — a fully webhook-driven run reaching `stage = complete`
(publish → move → export) on the box, not just via the local driver.
**Acceptance criteria:**
- [ ] One fresh `final_PRD_*` page is created in the watched folder and walked through **both** human gates **without touching the draft page**.
- [ ] The run reaches `complete` on the Droplet; the `.md` export lands in the published folder + `md_export_dir`.
- [ ] Any dead/errored prior run on the box is cleared first.
**Notes / pointers:** this is the residual of the original S6.4. The local run reached `complete`; the Droplet run once errored because a human trashed the draft between gates (now covered by S-02's recovery). **Blocked on:** deployment access (B-4).

---

## Known deferrals (intentional — not scheduled unless the owner asks)

- **Off-box SQLite backup (litestream / AD-23)** — deploy artifact is ready (`deploy/litestream.yml`); replication is **off by the owner's call**. The Droplet's SQLite is single-copy until enabled.
- **Publish edit-restriction (FR-15 step 1 / AD-18)** — skipped on the current Confluence **Free** tenant, which has no page restrictions (D-21, B-7). `require_edit_restriction: false` for `project_alpha`. Flip to `true` after any upgrade to Standard.
- **Dedicated "UserDoc Agent" Atlassian account (B-8)** — an attribution enhancement (agent-created tickets show the agent as Creator). Needs an org-admin to mint a licensed account; nothing in scope depends on it.
- Post-demo seams from the Spine's *Deferred* section: SQLite→Postgres, true parallel multi-tenancy, RAG for house style, the SSG deploy step, a fixed doc template, multi-approver publishing, LangSmith redaction/retention, zero-config cross-org identity / Jira path-search.

---

## Done

Completed stories move to [CHANGELOG.md](CHANGELOG.md) (newest first) with a one-line result. The entire
original build backlog — **Epics 1–6, 39 stories, live-verified** — is summarized there.
