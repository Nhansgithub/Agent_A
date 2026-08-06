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

## Epic 7 — Agent B: internal Knowledge Base + Slack Q&A

> A monorepo sibling package `agent_b/` that projects a **curated** Confluence space into a git-backed,
> Obsidian-compatible Markdown vault (linked + graph-navigable), publishes it read-only at a URL, and
> answers questions over it in Slack. Reuses Agent A's adapters/LLM/config/tracing by injection. This
> epic uses the `S-Bx` sub-namespace for readability. Locked decisions: **D-41…D-46**. New architectural
> rules: **AD-27…AD-32**. Product spec: the Agent B PRD (`planning-artifacts/prds/prd-LeapXpert_AgentB-2026-07-27/`).
>
> **Agent B design invariants** (hold in every story): the vault is a **read-only projection** (humans
> edit Confluence, not the vault — vault edits are overwritten on the next pull); organization is
> **metadata (MOC + tags), never physical file moves**; every scheduled run is **idempotent**
> (content-hash change detection); LLM link suggestions are **quarantined** (never inlined into prose);
> every LLM call is **traced** (AD-20); Agent B has **its own SQLite store**, separate from Agent A's.

### S-B0 · Scaffolding & boundaries   [DONE]
**Intent:** a buildable, boundary-enforced `agent_b/` package wired into CI — no behavior yet.
**Acceptance criteria:**
- [x] `agent_b/` package created; `pyproject.toml` uses `root_packages=["app","agent_b"]`, `packages.find` includes `agent_b*`; import-linter contracts mirror AD-1/2/6 for agent_b (sqlite3 only in `agent_b.repository`; anthropic only in `agent_b.agents`; httpx only in adapters; `agent_b.config` is a leaf).
- [x] `AgentBConfig` pydantic schema (frozen, `extra="forbid"`, secrets by `env:` ref only); loads from a new `agent_b:` block in `registry.yaml`; an absent block → `None` (Agent A unaffected — the registry loader ignores unknown top-level keys, [registry.py:49](app/config/registry.py#L49)).
- [x] Agent B SQLite schema created (`documents, links, llm_cache, qa_log, pull_runs`) via a repository skeleton (open/close, WAL pragmas) — no live data yet.
- [x] Docs: Agent B PRD stub + Spine **AD-27…AD-32** + DECISION-LOG **D-41…D-46** + CLAUDE.md Codebase Map rows for `agent_b/`.
- [x] tests added (config load + env-ref validation + repo schema); `make check` green (**560 passed, 7/7 contracts**).
**Notes / pointers:** reuse patterns from [app/config/schema.py](app/config/schema.py), [app/repository/database.py](app/repository/database.py). New deps: none (uses pydantic/PyYAML/stdlib sqlite3 already present).

### S-B1 · Curated space import (bootstrap)   [DONE]
**Intent:** the curated folder set (PRD source + published UserDocs + designs, excluding in-flight drafts) becomes an Obsidian vault of `.md` notes with YAML frontmatter.
**Acceptance criteria:**
- [x] A new Confluence read verb lists all pages under a folder/space (the adapter had none): `ConfluenceAdapter.list_descendant_pages` (recursive tree walk + cursor pagination + sub-folder exclusion). Agent B pulls pages under `include_folder_ids`, skips `exclude_folder_ids`.
- [x] Each page → `notes/<page_id>-<slug>.md` with frontmatter (`title, page_id, space, doc_type, parent_id, source_url`); `doc_type` from `folder_types` (prd|userdoc|design|other); body via existing [storage_to_markdown](app/adapters/markdown.py). `pulled_at` is recorded in the SQLite row, **kept out of the note** so unchanged content is byte-identical.
- [x] **Idempotent**: a second run over unchanged content produces byte-identical notes; a content hash is recorded per doc and the file is left untouched.
- [x] `confluence-md` permitted for the one-shot bootstrap only; the incremental path uses the adapter (D-41).
- [x] tests (a fake Confluence tree → vault layout, frontmatter, draft exclusion, idempotency; + the adapter verb's pagination/exclude over a fake transport); `make check` green (**566 passed, 7/7 contracts**).
**Note:** image **binary** download to `assets/` is **deferred to S-B4** — B1 preserves image *references* via the converter; no binary fetch yet.
**Notes / pointers:** `app/adapters/confluence.py` (new list verb), `agent_b/pipeline/{crawler,convert,writer}.py`. **Live designs pull gated on B-10** (designs folder id); PRD+UserDoc pull uses existing `ALPHA_CONF` creds.

### S-B2 · Deterministic linker (tiers 1–2)   [DONE]
**Intent:** the safe edges — hierarchy + restored references — as `[[wikilinks]]`, with no LLM.
**Acceptance criteria:**
- [x] Tier-1 hierarchy: a per-note **"## Related"** block renders `[[parent]]` + `[[children]]` links (chosen over frontmatter-only — inline wikilinks feed the Obsidian graph). Parent = a page whose stored id is a known document.
- [x] Tier-2: Confluence internal links surviving conversion as `[label](Page Title)` are resolved title→note and rewritten `[[note|label]]`; external (URL) and ambiguous (shared-title) links left as-is (**no false edges**, AD-30).
- [x] `links` table records each edge (kind=hierarchy|restored, source=deterministic); the pass **re-derives from stored `base_content`**, so re-run is byte-identical and the edge set is stable.
- [x] tests (fixture cross-links → correct `[[..]]`, zero false edges, idempotent); `make check` green (**569 passed, 7/7 contracts**).

### S-B3 · LLM curation — MOC, tags, suggested links (tier 3)   [DONE]
**Intent:** "clean organization" delivered as metadata — MOC hub notes + a tag taxonomy + quarantined suggested links.
**Acceptance criteria:**
- [x] Librarian agent ([agent_b/agents/librarian/](agent_b/agents/librarian/) + `SKILL.md`, over the shared `LlmClient`, traced, model from config): MOC hub notes (`notes/moc-<topic>.md`) grouping related docs; a controlled tag taxonomy on frontmatter `tags:`.
- [x] Tier-3 suggested links written **only** to a labelled "Suggested (AI — unverified)" line in the "## Related" block — **never inlined into prose** (AD-30); recorded as `source=llm` edges, kept distinct from deterministic edges.
- [x] LLM decisions cached by corpus hash (`llm_cache`); an unchanged corpus is **not re-sent to the model** (asserted); parsing drops unknown/self/duplicate ids so a hallucination can't enter the graph.
- [x] tests (a fake LLM → MOC created, tags applied, suggestions quarantined + not inlined, cached); `make check` green (**572 passed, 7/7 contracts**).

### S-B4 · Incremental sync + deletion reconcile + schedule   [DONE]
**Intent:** turn the bootstrap into a vault maintained on a schedule.
**Acceptance criteria:**
- [x] Change detection re-processes only changed pages (hash diff, `agent_b/pipeline/sync.py`); deletions are tombstoned (`repo.tombstone_documents`: note removed, `links` cleaned, `documents` row flagged `deleted_at` and dropped from the live index). A re-added page un-tombstones (counts as an add). Rename drops the stale-slug note.
- [x] A cron entry ([deploy/agent_b_pull.cron](deploy/agent_b_pull.cron), mirrors [deploy/reconcile.cron](deploy/reconcile.cron)) runs `scripts/run_agent_b_pull.py` at `schedule_cron` (default nightly 03:00); a `pull_runs` row records counts + `ok`/`error` per run (`run_pull`); a git commit per touched run versions the vault (`GitVault`, injected `VaultVcs` seam — no empty commit on an unchanged pull).
- [x] tests ([tests/test_agent_b_sync.py](../tests/test_agent_b_sync.py): run1→run2 add/change/delete/rename deltas, tombstone cleans links + index, re-add un-tombstones, pull-run ledger + commit, error path, real `GitVault`); `make check` green (**580 passed, 7/7 contracts**).
**Note:** image **binary** download to `assets/` moved to its own story **S-B10** (needs a shared-transport binary-fetch verb — a different boundary; D-47).

### S-B10 · Image binary download → `assets/`   [DONE]
**Intent:** vault notes render their Confluence images offline, not just preserve the reference.
**Acceptance criteria:**
- [x] `AtlassianClient.download(path) -> bytes` (shared transport, same retry/`AgentError` path) + additive `ConfluenceAdapter.list_attachments`/`download_attachment`; `agent_b/pipeline/assets.py` writes a page's **image** attachments to `vault/assets/<page_id>/<filename>` and `render_note` rewrites `![alt](file)` → `![alt](../assets/<page_id>/file)` (external URLs untouched), deterministically (D-50).
- [x] Idempotent (skip unchanged bytes) and incremental (only added/changed pages, off S-B4's change detection via an injected `AssetFetcher` in `sync_vault`/`run_pull`); tombstoning a page removes its `assets/<page_id>/` dir.
- [x] tests ([tests/test_agent_b_assets.py](../tests/test_agent_b_assets.py): adapter verbs over a fake client, image-only + idempotent fetch, ref rewrite, sync wiring — fetch only for changed pages + tombstone removal); `make check` green (**609 passed, 7/7 contracts**).
**Notes / pointers:** wired into `scripts/run_agent_b_pull.py` (`--no-assets` to skip). Was the S-B1 deferral, re-homed out of S-B4 per D-47.

### S-B5 · Quartz publish → internal URL   [DONE (code); live URL gated → B-4]
**Intent:** the browsable Obsidian-style graph at a URL.
**Acceptance criteria:**
- [x] Quartz config renders the vault (`render_quartz_config`: SPA + `ObsidianFlavoredMarkdown`/`CrawlLinks` transformers + `ContentIndex`/sitemap → graph view, backlinks, search); build runs **off-box** ([deploy/build_site.sh](deploy/build_site.sh) clones pinned Quartz → stages → `npx quartz build`); a new `agent.poetroastery.com` block in [deploy/Caddyfile](deploy/Caddyfile) serves it read-only, **no auth** (D-45). `baseUrl` comes from `PublishConfig`, never a literal (AD-4).
- [x] `[[links]]` resolve (staged byte-for-byte, wikilinks preserved); the graph renders (Quartz defaults); `related_suggested` is **visually distinct** — the linker now emits AI suggestions as a `> [!tip] Suggested (AI — unverified)` **Obsidian callout** (a titled box in Obsidian + Quartz), with a shipped `custom.scss` styling it (D-48).
- [x] Code + config + build script land and are unit-checked offline ([tests/test_agent_b_publish.py](../tests/test_agent_b_publish.py): config injection, staging mirror, callout render); **live URL blocked on deploy access + DNS (B-4)**. `make check` green (**585 passed, 7/7 contracts**).
**Notes / pointers:** `agent_b/pipeline/publish.py`, `scripts/build_agent_b_site.py`, `deploy/build_site.sh`, `deploy/Caddyfile`. The Node/Quartz build never runs on the 1 GB box (AD-21).

### S-B6 · RAG index (vector, local embeddings)   [DONE]
**Intent:** grounded retrieval over the vault.
**Acceptance criteria:**
- [x] Vector store: chunks embedded with a local ONNX model (`fastembed`, D-43), stored as float32 BLOBs in a `chunks` table in Agent B's own SQLite store — **numpy cosine, not `sqlite-vec`** (D-49: this runtime has no loadable-extension support). Incremental: re-embeds only pages whose `content_hash` changed (hash-diff from S-B4); tombstoned pages' chunks are dropped.
- [x] Retrieval (`agent_b/rag/retriever.py`) = vector cosine + graph expansion along deterministic `[[links]]`; the Answerer agent (`agent_b/agents/answerer` + `SKILL.md`, model from config, traced) answers via `LlmClient` with **inline `[n]` citations** (note + Confluence URL) and **refuses** ("I don't have a doc on that") when the top score < `min_score` **or** the passages don't support an answer.
- [x] Every answer traced (AD-20, via `CallMetadata`) + logged to `qa_log` (`agent_b/qa.py` orchestration; 👍/👎 recorded via `set_qa_feedback`).
- [x] tests ([tests/test_agent_b_rag.py](../tests/test_agent_b_rag.py): fixture vault + fake embedder/LLM → right note retrieved, incremental skip, graph-only neighbour, unanswerable → refusal without an LLM call, qa_log); `make check` green (**592 passed, 1 skipped, 7/7 contracts**).
**Notes / pointers:** deps `fastembed` + `numpy` in the `agent_b` extra (D-43/D-49, AD-21). AD-32 contract extended to `agent_b.rag`/`agent_b.qa`. Nightly index refresh wired into `scripts/run_agent_b_pull.py`.

### S-B7 · Slack bot Q&A   [DONE (code); live bot gated → B-9]
**Intent:** the demo surface — ask the bot, get cited answers.
**Acceptance criteria:**
- [x] Socket-Mode app (`agent_b/slack/app.py`, `slack_bolt` imported lazily); the transport-agnostic `SlackQaHandler` responds to DM + @mention **only in `allowed_channel_ids`** (DMs always), answers in-thread with a **Sources** section of note links, and records 👍/👎 reactions to `qa_log` (`record_feedback`). Socket Mode auto-acks receipt < 3s; the grounded answer posts after.
- [x] Secrets (bot token, app token, signing secret) by `env:` ref (AD-4) — resolved in `scripts/run_agent_b_slack.py`, never read in the handler.
- [x] Code + tests land offline ([tests/test_agent_b_slack.py](../tests/test_agent_b_slack.py): allow-list, grounded reply+sources, refusal has no sources, thumbs feedback, empty-question ignore — **no `slack_bolt` imported**); **live bot blocked on Slack app + tokens (B-9)**. `make check` green (**597 passed, 7/7 contracts**).
**Notes / pointers:** `slack-bolt` **declared** in the `agent_b` extra for CI/deploy but **not installed locally** (pip is deny-ruled in this env); the handler needs no install, so the offline suite covers all behaviour. AD-32 extended to `agent_b.slack`.

### S-B8 · Retire Agent A's `.md` export   [DONE]
**Intent:** remove the now-redundant FR-15 step-3 export (Agent B captures the published UserDoc via the pull).
**Acceptance criteria:**
- [x] The Publisher no longer writes `.md`; `PublishResult.md_export_path`/`exported` + `_write_export` + the export step removed; `publish()` no longer takes `prd_id`/`page_title`/`export_done`/`existing_md_path`; the publishing-handler note is "published and moved…".
- [x] `md_export_dir` config now optional/ignored; `md_export_path`/`md_exported_at` state + DB columns **deprecated** (never written, kept nullable — **no live-DB rebuild**) (D-44).
- [x] PRD **FR-15 amended** (FR-15a, dated 2026-07-27); DECISION-LOG **D-44** marked implemented; author `SKILL.md` + `markdown.py` docstrings updated; tests adjusted; `make check` green (**602 passed, 7/7 contracts**).
- [x] Sequenced after S-B1 (proven — Agent B ingests the published UserDoc).
**Notes / pointers:** the storage→Markdown converter lives on (Agent B's pull uses it).

### S-B9 · Q&A eval harness   [DONE]
**Intent:** a measurable answer-quality gate, mirroring the classifier's discipline.
**Acceptance criteria:**
- [x] A golden set (question → expected source notes / expected-refusal) template under [fixtures/agent_b/](../fixtures/agent_b/) (`golden.example.json` + a README on filling in real page ids); `scripts/run_agent_b_eval.py` indexes the vault then reports **source recall** + **refusal correctness** (API-gated, mirroring [classifier evaluation.py](app/agents/classifier/evaluation.py)); `agent_b/eval.py` is the pure harness over an injected `answer_question`.
- [x] Target bar defined + recorded (`TARGET_REFUSAL_ACCURACY = 1.0`, `TARGET_SOURCE_RECALL = 0.8` — a wrong refusal is the Q&A false-negative). Offline `make check` green (**603 passed, 7/7 contracts**): [tests/test_agent_b_eval.py](../tests/test_agent_b_eval.py) covers recall/refusal scoring, a source miss, a wrong refusal, a fabrication, and the golden-set loader.
**Notes / pointers:** the real golden set (real page ids) is filled in against the live KB (B-4/B-9); AD-32 extended to `agent_b.eval`.

---

### S-04 · Automated nightly KB-site publish (CI)   [DONE (code); live run gated → B-4]
**Intent:** the public Agent B KB site refreshes itself every night off the freshly-pulled vault, instead
of drifting stale until someone runs `deploy/site.sh` by hand.
**Acceptance criteria:**
- [x] A scheduled workflow ([.github/workflows/publish-site.yml](../.github/workflows/publish-site.yml)) runs nightly (04:00 UTC, after the 03:00 box pull cron) and on manual dispatch; it executes the existing [deploy/site.sh](../deploy/site.sh) on the GitHub runner — rsync vault down → `npx quartz build` → rsync site up → reload Caddy.
- [x] The Node/Quartz build never runs on the 1 GB box (AD-21); the box only serves the pre-built static files, read-only, no auth (D-45).
- [x] Missing deploy secrets → the job **no-ops with a `::warning::`** (no nightly red X) rather than failing; the live run is gated on the `DROPLET_HOST` + `DROPLET_SSH_KEY` repo secrets (B-4).
- [x] Docs synced: DECISION-LOG **D-53**, [deploy/README.md](../deploy/README.md), CLAUDE.md Codebase Map, BLOCKERS **B-4**; `make check` green (workflow + docs only — no app/test change).
**Notes / pointers:** reuses `deploy/site.sh` + `deploy/build_site.sh` verbatim (which call `scripts/build_agent_b_site.py` — ensure that file is not deleted). Live run needs a deploy key trusted by the box in repo Actions secrets — the same droplet-access gate as S-B5 (B-4). **No PRD/Spine change:** this honors AD-21 + S-B5 and adds no new product behavior or boundary (D-53 records the why).

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
