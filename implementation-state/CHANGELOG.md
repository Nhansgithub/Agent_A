# CHANGELOG — what shipped, newest first

> **Purpose:** a compact, append-only record of completed work, so history is available without git
> archaeology and without bloating [NOW.md](NOW.md). One entry per story (or per milestone). Keep each
> line to *what changed + the evidence* — the *why* lives in [DECISION-LOG.md](DECISION-LOG.md), the
> *what/how* in the code and the 3 critical docs.
>
> Format: `YYYY-MM-DD · S-xx <title> — <result / evidence>`. Add new entries at the **top**.

---

## Agile iteration (post-build)

2026-07-28 · **Agent B conversation memory** (D-52) — the bot now remembers a conversation so follow-ups resolve ("why?", "the second one", "tell me more"). `qa_log` gains a `conversation_key` (guarded `ALTER TABLE` migration — no live-DB rebuild) + `recent_qa(key, limit=6)`; the Slack handler keys a DM by its channel and each channel thread by its root, loads the last ~6 turns, and passes them to the Answerer (a "Recent conversation" prompt block + rewritten SKILL). Memory only helps it *understand* the message — doc facts still come only from retrieved passages (AD-30). `qa.answer_question` retries retrieval with recent turns folded in **only** when the current message alone matched nothing (no dilution for self-contained questions). Also: DM replies now post **inline** (channels still thread), and source links use canonical `/wiki/spaces/<KEY>/pages/<id>`. `make check` green (619 passed, 7/7 contracts).

2026-07-28 · **Agent B live-tuning** — (1) Site name is now config-driven (`PublishConfig.site_title`); the build patches Quartz's `pageTitle` (was the default "Quartz 4") → "Knowledge Base". (2) **Answerer made conversational** (D-51): no more cold "I don't have a doc on that." — it always calls the model with the passages **+ a catalog of all docs**, so it greets, lists/suggests docs, warmly guides when nothing matched, and **replies in the user's language**, while still never fabricating doc facts (AD-30). Refusal (= no grounded hits ⇒ no Sources) still drives eval/logging; `SKILL.md` rewritten. Also fixed the site rollout: unstyled page (was overwriting Quartz's config → now patch only baseUrl), sidebar-less layout (`custom.scss` clobbered Quartz's `@use "./base.scss"` → now appended), homepage 404 (added `content/index.md`), and the Slack bot silence (missing `aiohttp` for async Socket Mode). `make check` green (612 passed, 7/7 contracts).

2026-07-27 · **Agent B go-live wiring + deploy plumbing** — wired the supplied Slack channel `C0BL3KQSK1S` (`agent_b.slack.allowed_channel_ids`) and designs folder `1999113` (`include_folder_ids` + `folder_types: design`) into `config/registry.yaml`, and **fixed a load-blocking bug**: the live registry still had `embeddings.store` (removed by D-49) which the schema now rejects → replaced with `chunk_chars`/`chunk_overlap`. Added the missing Agent B deployment path: `deploy/Dockerfile.agent_b` (separate image so Agent A stays lean — carries the `agent_b` extra + git for the vault commits), `deploy/agent_b.sh` (seeds vault+index, starts the Socket-Mode bot container capped at 512m, installs the nightly pull cron), a docker-based `deploy/agent_b_pull.cron`, and a deploy/README "Agent B" section. B-10 resolved, B-9 creds supplied; box execution remains gated on B-4 (droplet SSH). `make check` green (609 passed, 7/7 contracts).

2026-07-27 · **S-B10** Agent B image assets — the last Epic 7 story. `AtlassianClient.download(path) -> bytes` (a binary read on the shared transport, same retry/`AgentError` path; Agent A uses none) + additive `ConfluenceAdapter.list_attachments`/`download_attachment`. `agent_b/pipeline/assets.py` writes a page's image attachments to `vault/assets/<page_id>/` and `render_note` deterministically rewrites `![alt](file)` → `![alt](../assets/<page_id>/file)` (external URLs untouched, D-50). Idempotent (skip unchanged bytes), incremental (only added/changed pages via an injected `AssetFetcher` in `sync_vault`/`run_pull`, off S-B4), tombstone removes `assets/<page_id>/`. Wired into the pull script (`--no-assets`). Evidence: **609 passed, 1 skipped** (+7), 7/7 contracts. **Epic 7 code-complete.**

2026-07-27 · **S-B8** Retire Agent A's FR-15 `.md` export — the Publisher now does **restrict → move** only (`PublishResult` lost `md_export_path`/`exported`, `_write_export` gone, `publish()` slimmed); Agent B captures the published UserDoc via its pull, so the server-disk copy was redundant. `md_export_dir` config → optional/ignored; `md_export_path`/`md_exported_at` state + DB columns deprecated (never written, kept nullable — no live-DB rebuild, D-44). PRD amended **FR-15a** (dated); D-44 marked implemented; author `SKILL.md` + `markdown.py` docstrings updated; publishing tests adjusted. Evidence: **602 passed, 1 skipped**, 7/7 contracts.

2026-07-27 · **S-B9** Agent B Q&A eval harness — `agent_b/eval.py`: a pure harness (over an injected `answer_question`) scoring **source recall** (were the expected notes retrieved/cited?) + **refusal correctness** (refuse the unanswerable, answer the answerable — a wrong refusal is the Q&A false-negative), mirroring the classifier's 0-FP/0-FN discipline. Bar recorded in code: refusal accuracy = 1.0, source recall ≥ 0.8. Golden-set template + README under `fixtures/agent_b/` (real page ids filled in against the live KB, B-4/B-9); live gate `scripts/run_agent_b_eval.py` indexes then scores, exits non-zero below bar. AD-32 extended to `agent_b.eval`. Evidence: **603 passed, 1 skipped** (+6), 7/7 contracts.

2026-07-27 · **S-B7** Agent B Slack Q&A (code; live bot gated → B-9) — `agent_b/slack/`: a transport-agnostic `SlackQaHandler` (normalized event → `qa.answer_question` → in-thread reply with a *Sources* section of note links; 👍/👎 reactions → `qa_log`), with a channel allow-list (DMs always, mentions only in `allowed_channel_ids`). The Socket-Mode bolt wiring (`app.py`) imports `slack_bolt` **lazily**, so the offline suite ([tests/test_agent_b_slack.py](../tests/test_agent_b_slack.py), fake events, no bolt) covers all behaviour. `slack-bolt` declared in the `agent_b` extra (not installed locally — pip deny-ruled). Live entrypoint `scripts/run_agent_b_slack.py` resolves tokens from `env:` refs (AD-4). AD-32 extended to `agent_b.slack`. Evidence: **597 passed, 1 skipped** (+5), 7/7 contracts.

2026-07-27 · **S-B6** Agent B RAG index — grounded, citing, refusing Q&A over the vault. New `agent_b/rag/` (chunker → `fastembed` embedder → `index_vault` → `retrieve`): chunks stored as float32 BLOBs + **numpy cosine** in Agent B's own SQLite (`chunks` table), **not sqlite-vec** (D-49 — no loadable-extension support in the runtime). Incremental by `content_hash` (S-B4 hash-diff). Retrieval = vector cosine + graph expansion along deterministic `[[links]]`; the Answerer agent (`agent_b/agents/answerer` + `SKILL.md`, model from config, traced AD-20) answers with inline `[n]` citations + note/Confluence-URL sources and **refuses** below `min_score` or when unsupported (AD-30). `agent_b/qa.py` orchestrates retrieve→answer→`qa_log`. Nightly re-index wired into the pull script. Deps `fastembed`+`numpy` in a new `agent_b` extra (kept out of Agent A's envelope, AD-21); AD-32 contract extended to `agent_b.rag`/`.qa`. Evidence: **592 passed, 1 skipped** (+8), 7/7 contracts, licensing clean.

2026-07-27 · **S-B5** Agent B Quartz publish (code; live URL gated → B-4) — `agent_b/pipeline/publish.py`: `render_quartz_config` (config-driven `baseUrl`, AD-4; SPA + Obsidian/CrawlLinks transformers + ContentIndex → graph/backlinks/search), `render_custom_css`, `stage_content` (vault `notes/` → Quartz `content/` byte-for-byte, a faithful mirror so tombstoned notes don't linger). Off-box build via [deploy/build_site.sh](deploy/build_site.sh) (pinned Quartz clone → `npx quartz build`; never on the 1 GB box, AD-21) + [scripts/build_agent_b_site.py](../scripts/build_agent_b_site.py). New `agent.poetroastery.com` block in [deploy/Caddyfile](deploy/Caddyfile) serves it read-only, no auth (D-45). `related_suggested` made visually distinct: the linker now renders AI suggestions as a `> [!tip] Suggested (AI — unverified)` Obsidian callout (D-48) instead of a bullet. Evidence: **585 tests green** (+5), 7/7 contracts. Live URL blocked on B-4 (DNS A record + off-box build + serve).

2026-07-27 · **S-B4** Agent B incremental sync + deletion reconcile + schedule — `agent_b/pipeline/sync.py`: `sync_vault` change-detects via the stored `content_hash` (only added/changed pages rewritten; unchanged corpus byte-identical) and reconciles deletions by tombstoning (`repo.tombstone_documents` removes the note, drops edges, flags `deleted_at`, keeps the row so a re-add un-tombstones); the writer now drops the stale-slug note on a rename. `run_pull` wraps it as the nightly job — a `pull_runs` ledger row (counts + `ok`/`error`), optional LLM curation overlay, link, and one git commit per touched run via an injected `VaultVcs` (`GitVault`; no empty commit when unchanged). New: [deploy/agent_b_pull.cron](deploy/agent_b_pull.cron) (nightly 03:00, mirrors reconcile.cron) + [scripts/run_agent_b_pull.py](../scripts/run_agent_b_pull.py) entrypoint. Image-binary → `assets/` split out to S-B10 (D-47). Evidence: **580 tests green** (+8), 7/7 contracts.

2026-07-27 · **S-B3** Agent B LLM curation — the Librarian agent (`agent_b/agents/librarian`, own `SKILL.md`, over the shared traced `LlmClient`, model from config) proposes tags, MOC hubs, and suggested cross-links. Tags → frontmatter; suggestions → a labelled "Suggested (AI — unverified)" line in the Related block (never inlined, AD-30), recorded as `source=llm` edges; MOC hub notes written to `notes/moc-*.md`. Decisions cached by corpus hash (`llm_cache`) so an unchanged corpus isn't re-billed; parser drops unknown/self ids. Evidence: **572 tests green** (+3), 7/7 contracts.

2026-07-27 · **S-B2** Agent B deterministic linker — hierarchy links (a per-note "## Related" block with `[[parent]]`/`[[children]]`) + restored references (a surviving `[label](Page Title)` whose title matches exactly one doc → `[[note|label]]`); external/ambiguous links left untouched (no false edges, AD-30). Edges recorded in `links`; the pass re-derives from stored `base_content` so it is byte-identical on re-run. `build_vault` = import + link. Evidence: **569 tests green** (+3), 7/7 contracts.

2026-07-27 · **S-B1** Agent B curated space import — added `ConfluenceAdapter.list_descendant_pages` (recursive folder-tree crawl with cursor pagination + sub-folder exclusion; additive, Agent A untouched) and the `agent_b/pipeline` (crawl → convert → write): each curated page → `notes/<id>-<slug>.md` with deterministic YAML frontmatter, drafts excluded, idempotent re-pull (content-hash → byte-identical). Evidence: **566 tests green** (+6), 7/7 contracts. Image *binary* download to `assets/` deferred to S-B4.

2026-07-27 · **S-B0** Agent B scaffolding & boundaries — stood up the `agent_b/` monorepo sibling: `AgentBConfig` schema + `agent_b:` registry block (AD-4 mirror, a leaf), and Agent B's own SQLite store (`documents`/`links`/`llm_cache`/`qa_log`/`pull_runs`, AD-32). import-linter extended to `root_packages=[app, agent_b]` with 2 new contracts (AD-27 leaf, AD-32 SQL isolation). Docs synced: Agent B PRD stub, Spine AD-27…AD-32, decisions D-41…D-46, CLAUDE Codebase Map. Evidence: **560 tests green** (+12), ruff clean, **7/7 contracts kept**. (Epic 7 in progress.)

---

## Milestone: initial build (Epics 1–6) — completed & live-verified · through 2026-07-26

The full product was built as 39 stories across 6 epics and verified live against a real Atlassian
tenant + the Claude API. Highlights (detail in git history and the code):

- **Epic 1 — Foundation & deployable skeleton (10 stories):** layered module skeleton with import-linter
  contracts; per-tenant config registry + env-ref secrets (grep-clean); repository + single SQLite store
  with the `stage` machine; webhook ingress (HMAC validate → dedupe → route); `processed_events`
  idempotency; Jira + Confluence adapters (ADF, v2/v1, retry, `AgentError`); in-invocation LangGraph
  orchestrator + serial queue; LangSmith tracing harness.
- **Epic 2 — Detection & confirmation (8):** source-folder detection + title gate; **Classifier** with a
  held-out fixture eval that **passed 0-FP / 0-FN ×3 live**; tracking-ticket find-or-create → Done;
  rename-request path + clean re-entry; self-ingestion defense-in-depth; cross-org identity fallback.
- **Epic 3 — Authoring & draft publication (5):** **Author** first draft + one self-critique pass;
  idempotent self-stamped draft publish to the draft folder; Review ticket + framed review-request comment.
- **Epic 4 — Review & revision loop (6):** typed `FeedbackDecision` + deterministic routing; structured-
  feedback revise loop (`review_round++`, uncapped, fresh-comment-gated); PASS detection on the PM's Done;
  structure-confirmation + bounded 4-trigger clarification sub-loops; late-feedback / non-Done handling.
- **Epic 5 — Approval & publishing (3):** confirm PASS + Publishing ticket for the Head of Product; the
  publish gate; the ordered idempotent publish transaction (restrict / move / export / complete).
- **Epic 6 — Resilience & ops (7):** error surfacing + admin resume from checkpoint; reconciliation &
  liveness sweep; off-box backup artifact; deploy to the 1 GB Droplet + memory-envelope hardening;
  config-only modifiability; content-gating observability flag.

**Live milestones:** the end-to-end flow reached `stage = complete` against the real tenant (detect →
classify → tracking ticket → draft → 2 human feedback rounds → PM PASS → Head-of-Product approval → move
+ export). Deployed at `https://poetroastery.com`; image built off-box by CI and pulled (AD-21).

### Post-readiness hardening (owner requests, 2026-07-25 → 07-26)

- **Conversational review loop** (FR-10a) — the Feedback interpreter reads the review-ticket transcript + pending restatement; replies handled by intent (confirm / adjust / redirect / bare-reject). (D-30)
- **Rename-churn guard + source-folder admission gate** (FR-01a / AD-24) — an already-admitted PRD isn't re-caught on later renames; stray-space pages refused at the door. (D-35)
- **Draft-deletion detection + human-gated recovery** (FR-16 / AD-25) — a deleted draft is detected on real status, the PM is asked before any recovery, and an errored run self-heals on restore. (D-36, D-38) *Live activation pending — see BACKLOG S-02.*
- **Tracking-ticket search fix** (FR-04 / D-39) — don't adopt another run's same-named ticket; typed marker search excludes `agent-generated`.
- **Inline-comment feedback channel** (FR-17 / AD-26) — a Confluence inline comment on a draft is read via the adapter, restated on the Review ticket @-mentioning the exact commenter, and handed to the existing conversation loop. (D-40) *Live activation pending — see BACKLOG S-01.*

> The workflow reset to this agile system (new `CLAUDE.md` brain + `NOW`/`BACKLOG`/`CHANGELOG`/`BLOCKERS`
> + retained `DECISION-LOG`) happened on 2026-07-26, replacing the build-phase `STATE`/`EPIC-STORY-TRACKER`
> /`SESSION-LOG` docs.
