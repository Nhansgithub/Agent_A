---
title: "PRD-to-User-Document Automation Agent Flow"
status: draft
created: 2026-07-23
updated: 2026-07-24
source: "adopted from PRD_Agent_Flow.md (repo root), v0.2 demo scope"
---

# PRD: PRD-to-User-Document Automation Agent Flow

**Version:** 0.3 (Demo scope — gate-fix reconciliation)
**Status:** Draft — ready for build
**Author:** Product owner (you), specification compiled with assistant
**Last updated:** 23 July 2026
**Primary reader:** The engineer(s) who will build this system
**Deployment target:** DigitalOcean Droplet — Basic / Regular SSD, 1 GB RAM / 1 vCPU / 25 GB SSD ($6/mo), Ubuntu LTS. **See §15 for the memory-constraint warning — this is a deliberately tight box and the build must account for it.**

---

## 1. Executive Summary

We are building an **automation agent flow** that watches a company Confluence space for finalized PRDs, and — with a human Product Manager (PM) and Head of Product kept firmly in the loop via Jira — automatically produces, reviews, revises, approves, and publishes an **end-user-facing help document** (a public feature/product guide) derived from each PRD.

The system is not a single monolithic agent. It is a set of small, independently-triggered, mostly-stateless flows ("the Flow") coordinated by an explicit shared state store. Jira and Confluence are the *entire* human interface — there is no separate UI. The system is **multi-tenant**: one running service handles multiple projects, routing each incoming event to the correct project's configuration (Jira project, Confluence spaces/folders, PM, Head of Product, admin).

This PRD specifies the **demo scope**: single provider (Anthropic Claude API), SQLite state, one-PRD-at-a-time processing, and a full happy-path plus defined error/edge handling. It is intentionally built with clean seams (swappable prompts, skill files, config registry, repository-pattern state layer) so it can be hardened and scaled after the demo proves out.

---

## 2. Problem Statement

- **Who is affected:** Product teams that write PRDs but lack the time to hand-craft polished, user-facing help documentation for every feature or product release.
- **Current state:** User documentation is written manually, lags behind PRDs, and depends on a PM/writer's availability. Review cycles are ad-hoc and easy to drop.
- **Cost of inaction:** Delayed or missing user documentation; inconsistent quality; PM time spent on drafting rather than judgment.
- **What this solves:** The agent does the drafting and revision labor and drives the review loop; the humans (PM, Head of Product) contribute only judgment and approval — through tools they already use.

---

## 3. Goals & Success Metrics

| Goal | Metric | Target (demo) |
|------|--------|---------------|
| Produce a usable user doc from a PRD with no manual drafting | A demo PRD flows end-to-end to a published `.md` with only PM/Head of Product judgment inputs | 1 successful full run |
| Keep humans in control at every gate | No document is published without an explicit Head of Product "Done" transition | 100% of runs gated |
| Observability of cost/latency | LangSmith traces show per-step latency, token cost, and speed | All LLM steps traced |
| Modifiability | Swapping the PM/Head of Product/admin, or pointing at a new project, requires config edits only — no code change | Verified by adding a 2nd project via config |
| **Classifier accuracy (counter-metric)** | Zero classifier false-positives / false-negatives on the labeled **holdout** fixture set (FR-03). Per architecture AD-17: the dev fixture set tunes the prompt, the holdout set is the acceptance bar — no train-on-test. | 0 FP / 0 FN on the holdout fixtures |

**"Usable" (demo definition):** a UserDoc draft the PM PASSes — i.e. transitions the Review ticket to Done (FR-12) — **without demanding a structural rewrite**. Remaining feedback is refinement, not "start over."

**Explicit non-goals for the demo** (see §5.3): the SSG (Static Site Generator) publish/deploy step, RAG over an existing doc corpus, true parallel multi-tenant execution, and a fixed doc template.

---

## 4. Actors & Roles

| Actor | Type | Responsibility |
|-------|------|----------------|
| **Reviewer PM (per project)** | Human | Fixed in config (`pm_account_id`). Owns the UserDoc draft-review/feedback loop: reviews each draft, gives structured feedback, and signals PASS by transitioning the **Review ticket** to **Done** (the sole approval signal). |
| **Uploading PM (page creator)** | Human | The author of the `final_PRD_...` page, taken at runtime from the Confluence page-creator field (**not** from config). Involved only when a page is mislabeled: receives the rename-request task (FR-02a) as a **separate** ticket in the Review project. May or may not be the same person as the Reviewer PM. |
| **Head of Product (per project)** | Human | Final gate. Approves publishing by transitioning the UserDoc Publishing ticket to **Done**. Fixed in config. |
| **Admin / Developer ("me", per project)** | Human | Receives error escalations; fixes underlying issues; signals the agent to resume. Fixed in config. |
| **The Agent Flow** | System | All detection, drafting, revision, ticket/comment management, publishing, and error surfacing. Composed of multiple role-specialized agents (see §6.3). |

---

## 5. Scope

### 5.1 In Scope (Demo)
- Multi-tenant service with a per-project **config registry** (routing + identities + locations).
- Confluence **page-created webhook** ingestion; title-gate + LLM confirmation that a page is a finalized PRD.
- The full happy-path flow (§6.1) including the PM revision loop and the Head of Product publish gate.
- UserDoc generated as **Markdown**, saved to **server storage** for a later SSG step; and mirrored as a Confluence page for review/publishing.
- Defined **error handling**, **edge cases**, and an **admin resume** mechanism.
- **LangSmith** monitoring of latency, speed, and cost.
- **SKILL.md** files per agent role; one lightweight self-critique loop in the authoring step.

### 5.2 Out of Scope (Demo)
- The SSG build/deploy step itself (we only produce and store the `.md`).
- RAG / vector store for house-style consistency.
- True concurrent multi-project execution (design must *allow* it; demo runs one PRD at a time, queued).
- A fixed user-doc template (agent decides structure per PRD via prompt/skill).
- Multi-approver logic for publishing (single Head of Product approver only).

### 5.3 Future Considerations (post-demo, do not build now, but leave seams)
- Swap SQLite → Postgres (via the repository layer) for parallel multi-tenant.
- Add RAG over existing published docs for style/terminology consistency.
- Build the SSG publish/deploy step downstream of the stored `.md`.
- Optional per-feature (not whole-product) PRDs at scale.

---

## 6. Functional Requirements

### 6.0 Terminology
- **PRD page** — the source document in Confluence, title format `final_PRD_<feature/product name>`.
- **User Document (UserDoc)** — the derived end-user help/onboarding guide. Public-facing feature/product guide. Canonical short form used throughout this doc: **UserDoc**.
- **SSG (Static Site Generator)** — the downstream tool that would build the public help site from the exported Markdown. Out of demo scope (§5.2); the demo only produces and stores the `.md`.
- **Source folder** — the **watched** Confluence folder where finalized PRDs are uploaded (`confluence_source_folder_id`); FR-01 detection listens here.
- **Draft/review folder** — the Confluence folder where UserDoc drafts are posted for the review loop (`confluence_draft_folder_id`).
- **Published-UserDocs folder** — a dedicated Confluence folder for approved/published UserDocs (`confluence_published_folder_id`), sitting **adjacent to (next to), not inside,** the source folder so published output is never re-ingested by detection (FR-15 → FR-01).
- **Reviewer PM** — the config-fixed PM (`pm_account_id`) who owns the UserDoc draft-review/feedback loop and the PASS (Done) gate.
- **Uploading PM** — the creator of the `final_PRD_...` page (from the Confluence page-creator field, resolved at runtime); receives rename-request tasks only (FR-02a). May differ from the Reviewer PM.
- **Main project** — the primary Jira project for main tickets/tasks (PRD-tracking ticket + publishing ticket live here).
- **Review project** — the secondary Jira project used *only* for the Reviewer PM drafting/review loop (and Uploading-PM rename requests).
- **State store** — the internal table tracking each PRD's stage (see §10).

### 6.1 Happy-path flow (end to end)

**FR-01 — Detect new PRD.**
The system SHALL receive Confluence `page-created` webhook events for the configured source folder(s). On each event it SHALL determine which project (tenant) the event belongs to via the config registry (source folder → project).
- **Self-ingestion guard:** Because published UserDocs are moved to the **Published-UserDocs folder** *outside* the watched source folder (FR-15), the agent's own output is never re-detected here. As defense-in-depth, detection SHALL **also** exclude the agent's own pages via a label/path check. The exact exclusion-guard mechanism is deferred to Architecture (§13.1).

> **Amendment 2026-07-25 (FR-01a — admission is once-per-PRD; rename churn is ignored).** A page's
> id is stable across renames, so a PRD already taken into the flow keeps receiving `page-updated`
> events every time its name is changed — and since each rename bumps the Confluence version (a new
> dedupe key), version-dedup alone does not stop it. The system SHALL re-process a source-page event
> for an **already-admitted** run **only** while that run is parked awaiting a corrected re-upload
> (FR-02a title mismatch, or EH-07 Classifier reject). Once the PRD has advanced past detection
> (drafted, in review, publishing, complete, or errored), a later rename/edit of the source page is
> **ignored** — it produces no new ticket, draft, or re-detection. Toggling the name back and forth,
> however many times, cannot make the agent re-catch the same PRD. Also: a page created **anywhere in
> the space but not in the source folder** is refused at admission (not merely declined by detection
> afterward), so it never leaves a dead run in the store.

**FR-02 — Title gate.**
The system SHALL treat a page as a candidate PRD only if its title matches the pattern `final_PRD_<name>`. (Demo-agreed exact convention.)
- **FR-02a — Title mismatch:** If a page lands in the source folder but the title does NOT match, the system SHALL create a **small rename-request task ticket in the Review project**, assigned to the **Uploading PM** — the page creator, taken from the Confluence page-creator field, **not** the config Reviewer PM — asking them to confirm it is a PRD and rename it to `final_PRD_...`. This rename-request ticket is **entirely separate** from the draft-review ticket (FR-06). The system SHALL NOT process the page further until a matching page-created/`page-updated` event arrives. Assigning the Uploading PM requires a Confluence→Jira identity mapping (see §13 Q4 / §13.1). (See §8 edge handling for the rename re-trigger.)

**FR-03 — LLM PRD confirmation.**
For title-matching pages, the Classifier agent SHALL read the page content and confirm it is genuinely a finalized PRD. If it is not, behave as FR-02a (ask the Uploading PM to confirm/correct).
- **Decision rubric — ACCEPT** (all must hold): the page has substantive prose (not just headings or placeholders); it describes a product/feature to be built (problem, solution/requirements, and/or scope); and it reads as a completed document rather than a stub. **REJECT** if the page is: empty or near-empty; an unfilled template (headings with placeholder / `TODO` / `Lorem`-style text); a non-PRD document that merely happens to match the title (meeting notes, a design doc, a scratchpad); or otherwise junk / mislabeled.
- **Labeled fixture set + accuracy bar:** the demo build SHALL ship a small **labeled fixture set** of ACCEPT and REJECT example pages (e.g. 3–5 each: a real finalized PRD, an empty page, a bare template, a mislabeled non-PRD). The Classifier SHALL be **correct on 100% of these demo fixtures — 0 false-positives and 0 false-negatives** — as the acceptance bar for this gate (this is the §3 counter-metric).

**FR-04 — Locate or create the PRD-tracking ticket (Main project).**
The system SHALL search Jira (across the project(s) per config) for an existing ticket that references this PRD (by PRD name/link). 
- If found: SHALL transition it to **Done**.
- If not found: SHALL create a new ticket for this PRD. Per demo decision, when the agent must create it, the ticket is created **at the top of the Main project hierarchy** (not as a subtask), and SHALL be transitioned to **Done**.
- Note: a human-created tracking ticket may live anywhere; the search must therefore not assume a fixed location.
- **Transition guard:** before transitioning, the agent SHALL **skip the transition if the ticket is already Done**, and SHALL resolve the **legal transition path** from the ticket's current status at runtime — it must NOT assume a direct-to-Done transition always exists (see §13 Q1 / §13.1).

**FR-05 — Generate the first UserDoc draft.**
An **authoring agent** SHALL read the PRD and produce a first draft of the UserDoc (an end-user onboarding/help guide for the whole product in the demo). 
- Structure and quality are governed by the agent's **system prompt + context + SKILL.md**, not a hardcoded template. The agent decides and tailors the best structure per PRD.
- The authoring step SHALL include one lightweight **self-critique pass** (draft → critique against skill file → single revision) before publishing the draft.
- **Acceptance oracle (explicit):** FR-05 is satisfied when a self-critiqued draft is produced and posted (FR-06). The **content-quality acceptance of the UserDoc is solely the human PM PASS gate (FR-12)** — the self-critique pass is a *drafting aid*, NOT an acceptance gate, and does not by itself signal done-ness.

**FR-06 — Publish draft to Confluence (Review location) + create Review ticket.**
The system SHALL create the UserDoc as a Confluence page in the configured **draft/review folder**, and create a **Review ticket in the Review project**, assigned to the project's **PM**, linking to the draft page.

**FR-07 — Request review (with framing).**
On the Review ticket, the agent SHALL post a comment that:
- Tags the PM (`@<PM>`) so they are notified.
- Requests feedback in the **exact structured format** (§6.2).
- Explicitly asks the PM to *"please put yourself in the users' shoes"* (not the engineer's POV).
- States clearly that the **only** way to finalize/pass the draft is for the PM to **transition the Review ticket to Done themselves**; that feedback added after moving to Done is not processed; and that they must not ask the agent to change the status on their behalf.

**FR-08 — PM clarification sub-loop (conditional, bounded triggers).**
The agent SHALL post a clarifying question on the Review ticket (tagging the Reviewer PM) and **wait** for the answer before drafting/redrafting **only** when one of these enumerated triggers holds:
1. The PRD uses a feature name, term, or acronym that is **defined nowhere** on the page and whose meaning materially changes the doc.
2. Two parts of the PRD **directly contradict** each other about a user-facing behavior.
3. A user-facing flow the doc must describe is **left incomplete** in the PRD (a required step or outcome is missing).
4. The PM's own feedback is **internally contradictory** or points to a section that does not exist.

Outside these enumerated cases the agent SHALL **proceed without asking**, filling trivial gaps with a reasonable, stated assumption. This bounded list — not a subjective "truly blocked" judgment — is the trigger.

**FR-09 — Ingest PM feedback.**
On a new PM comment on the Review ticket (via webhook), the agent SHALL parse the feedback.
- If the PM used the structured format (§6.2), proceed to FR-11.
- If the PM used **plain language**, proceed to FR-10.

**FR-10 — Structure-confirmation sub-loop (plain-language feedback).**
When feedback is unstructured, the agent SHALL:
1. Convert it into the structured format itself.
2. Comment back (tagging the PM) with the structured version and ask: *"You didn't feedback following the format so I curated it like this — is this what you mean?"*
3. **Wait** for the PM's confirming reply before applying any changes.
Only upon confirmation does it proceed to FR-11.

> **Amendment 2026-07-25 (FR-10a — conversational review loop).** The review loop is a **conversation with memory**, not a sequence of isolated one-shot interpretations. When interpreting any PM comment, the agent SHALL be given the **recent review-ticket transcript** (labelled PM vs agent) and, while awaiting a confirmation, the **exact restatement it proposed**. Consequently the PM's reply to the confirmation question is handled by intent, not just yes/no:
> - **affirm** (*"yes"*) → apply the restatement as-is;
> - **affirm with an adjustment** (*"yes, but drop the last point"*) → apply the restatement **edited per the adjustment**, in the same turn, without a second confirmation for a small unambiguous change;
> - **reject with a new direction** (*"no, I meant the intro"*) → treat as fresh feedback (restate-and-confirm or apply); the direction is never discarded;
> - **bare reject** (*"no"*) → the agent SHALL **acknowledge on the ticket and ask what to change instead** (tagging the PM), returning to `awaiting_review` for another round. A rejection must **never** be a silent dead-end.
>
> This preserves EH-08 (still blocks on a human; never fabricates the answer) and AD-16 (the interpreter's output stays a typed decision; routing stays deterministic) — only the interpreter's *input* gains conversation context. It also fixes a latent defect: `awaiting_structure_confirm → awaiting_review` was not a legal state-machine edge, so a *"no"* raised an internal error; that edge is now part of the §9 machine.

**FR-11 — Apply feedback → new draft.**
The agent SHALL revise the UserDoc per the (confirmed) structured feedback, update the Confluence draft page, and post a comment (tagging the Reviewer PM) summarizing what changed, then re-request review per FR-07's framing. The loop (FR-07 → FR-11) repeats **uncapped for the demo** until PASS. This is safe because **each round requires a fresh human PM feedback comment** — the loop cannot spin autonomously. The guardrail is **observability, not a hard cap**: the `review_round` counter (§10) and per-round token cost are surfaced in LangSmith (NFR-01 / NFR-09) so runaway cost stays visible.

**FR-12 — Detect PASS (Reviewer PM gate model).**
Once the Review ticket exists, the Reviewer PM has exactly two productive moves:
- **(a) Leave feedback** (a comment) → the agent runs the revise loop (FR-09 → FR-11) and re-requests review.
- **(b) Transition the Review ticket `In Progress → Done`** → the agent SHALL interpret the **Done transition as the sole PASS / approval / finalize signal** (webhook).

If the PM does **neither**, the run **stays parked at `awaiting_review` indefinitely — there is no timeout.** This is an explicit demo decision and a documented limitation. Non-Done terminal transitions (Rejected / Won't Do / Duplicate) and reassignment are handled in EH-09.

**FR-13 — Confirm pass + create Publishing ticket.**
On PASS, the agent SHALL:
1. Post a confirmation comment on the Review ticket (tagging the PM).
2. Create a **UserDoc Publishing ticket in the Main project**, reported to / assigned for approval by the **Head of Product**, linking to the passed UserDoc, requesting approval to publish to production.

**FR-14 — Head of Product publish gate.**
The agent SHALL wait for the **Publishing ticket to transition to Done** by the Head of Product (webhook) — mirroring the Reviewer PM gate (FR-12), **Done = approve → publish** is the sole approval signal. Until then, nothing is published. If the Head of Product takes **no action**, the run **stays parked at `awaiting_publish_approval` indefinitely — there is no timeout** (explicit demo decision; documented limitation). Non-Done terminal transitions and reassignment are handled in EH-09.

**FR-15 — Publish on approval.**
When the Publishing ticket is marked Done, the agent SHALL:
1. **Apply Confluence edit restrictions** to the UserDoc (restrict *who may edit* the page, to prevent casual post-approval edits). This is an access restriction — **NOT a content freeze and NOT version pinning**: space admins and the agent account retain edit rights, and Confluence continues to version the page normally.
2. **Move** the final UserDoc page to the dedicated **Published-UserDocs folder** (`confluence_published_folder_id`), which sits **adjacent to (next to), not inside,** the watched source `final_PRD` folder — so the published output is never re-ingested by detection (FR-01).
3. **Export** the final UserDoc as a **Markdown file** to **server storage** (the running server's disk), for the later SSG step.
4. Mark the flow **Complete** in the state store.

> **Amendment 2026-07-27 (FR-15a — step 3 `.md` export retired).** Step 3 above (export the final
> UserDoc to a server-disk `.md`) is **removed**. Its sole purpose was to feed a later static-site step;
> that consumer is now **Agent B** (Epic 7), which ingests the published UserDoc by **pulling the
> Confluence space** into its own vault — so a redundant `.md` copy on the agent's disk serves nothing.
> On publish the agent now does **restrict → move → mark complete**. The `md_export_dir` config field
> and the `md_export_path` / `md_exported_at` state fields are **deprecated**: no longer written, kept
> nullable so the live DB needs no rebuild (see DECISION-LOG **D-44**). The storage→Markdown converter
> itself lives on — Agent B's pull uses it.

> **Amendment 2026-07-26 (FR-16 — draft-deletion detection & human-gated recovery).** If the UserDoc
> **draft page** is deleted (moved to trash) while a run is in flight, the agent SHALL detect it and
> **ask the Reviewer PM before doing anything** — it must **never auto-recover**, because a deletion
> may be deliberate.
> 1. **Detect robustly.** Detection keys on the page's real *status*, not the webhook's label: a page
>    event whose id is a run's own draft (`userdoc_page_id`) which is now `trashed`/missing is treated
>    as a deletion — even if the Confluence Automation rule fired a generic *page-updated* rather than
>    *page-trashed*. (A normal edit of the agent's own draft is ignored; a trashed page that is not any
>    run's draft is ignored.)
> 2. **Ask, don't act.** Post a question on the Review ticket, **@-mentioning the Reviewer PM**: *"the
>    draft was deleted — was that intentional? Reply ‘restore’ and I'll bring it back exactly as it
>    was, or ‘leave it’ to keep it deleted."* Park the run awaiting the answer
>    (`pending_gate = PM_DELETION_DECISION`); the agent touches nothing until the PM replies.
> 3. **Recover only on a confirmed mistake.** When the PM replies, interpret their intent (a typed
>    RESTORE / LEAVE / UNCLEAR decision, routed deterministically per AD-16):
>    - **RESTORE** → restore the page from trash in place (same id, so the ticket link survives); if
>      that fails, **recreate** a new page holding the **exact latest content** (read back from the
>      still-readable trashed page), repointing `userdoc_page_id`; confirm to the PM. If the run had
>      **errored** because the page was gone, re-enter at `last_good_checkpoint` now that it is back.
>    - **LEAVE** → acknowledge and leave the page deleted; the agent does not restore it.
>    - **UNCLEAR** → re-ask; **never guess** between restore and leave.
> This is a human-gated loop in the spirit of EH-08 / AD-16 (block on a human, never fabricate the
> answer). Consequently the **publish transaction never auto-recovers** either: if the draft is
> missing/trashed at publish time, the agent **refuses to publish** and raises an actionable error
> (restore it, or reply ‘restore’ on the Review ticket, then resume) rather than silently restoring a
> page a human deleted.

> **Amendment 2026-07-26 (FR-17 — inline-comment feedback channel).** A reviewer MAY give feedback by
> leaving a **Confluence inline comment** on the UserDoc draft (highlighting a passage and commenting on
> it), as an alternative to commenting on the Jira Review ticket. When one appears on a run's draft while
> it is under review, the agent SHALL:
> 1. **Detect and read it.** A Confluence *Page commented* Automation rule delivers the comment id; the
>    agent reads the comment through the adapter to get its author, body, and the **highlighted passage**
>    it anchors to (the "section"). The trigger also fires for page-level (footer) comments and comments
>    on other pages — those are read and ignored; only an inline comment on a tracked draft is acted on.
> 2. **Restate it on the Review ticket, tagging the exact commenter.** Post a comment on the draft's Jira
>    **Review ticket**, **@-mentioning the person who left the inline comment** (their Atlassian account,
>    *not* the configured Reviewer PM), noting the section it concerns, and restating it in the
>    `Section / Issue / Suggested change` format.
> 3. **Propose a solution when none was given.** If the reviewer named a problem but no fix, the agent
>    **proposes a concrete one itself** and says so, rather than leaving the suggested change blank.
> 4. **Confirm, then hand off to the conversation.** Ask the commenter whether the restatement captures
>    what they meant and park awaiting confirmation (`AWAITING_STRUCTURE_CONFIRM`). From there the
>    conversation-aware Feedback interpreter (FR-10 amendment) drives the back-and-forth — confirm,
>    adjust, or clarify — and finalizes the change, addressing the **same commenter** throughout. No
>    draft edit happens until they confirm (EH-08 spirit); the loop never fabricates their reply (AD-16).

### 6.2 PM structured feedback format

The agent requests, and internally uses, this exact structure (one block per point):

```
Section: <which part of the UserDoc>
Issue: <what's wrong / missing / unclear>
Suggested change: <what the PM wants instead>
```

The agent SHALL accept multiple such blocks in one comment. If the PM writes plain language instead, FR-10 applies.

### 6.3 Agent roles (the Flow's internal composition)

The Flow is decomposed into role-specialized agents, each with its own **SKILL.md**, system prompt, and toolset. Minimum set for the demo:

| Agent role | Job | Key tools |
|---|---|---|
| **Classifier** | Confirm a page is a real finalized PRD (FR-03) | Confluence read |
| **Ticket manager** | Search/update/create Jira tickets & transitions (FR-04, FR-06, FR-13) | Jira API |
| **Author** | Draft & revise the UserDoc, incl. self-critique (FR-05, FR-11) | Confluence read/write |
| **Feedback interpreter** | Parse structured/plain feedback; run structure-confirmation loop; judge when to ask clarifying questions (FR-08, FR-09, FR-10) | Jira comments |
| **Publisher** | Lock, move (FR-15; `.md` export retired — FR-15a) | Confluence API |
| **Error handler** | Surface errors to Jira + admin, manage resume (see §8) | Jira comments, state store |

Roles may be implemented as separate prompt/skill configurations over a shared agent runtime rather than separate services. All agents run on the **Anthropic Claude API**.

---

## 7. Non-Functional Requirements

| ID | Category | Requirement | Threshold / Note | Priority |
|----|----------|-------------|------------------|----------|
| NFR-01 | Observability | All LLM steps traced in **LangSmith** with latency, speed, token cost. **Data-governance note:** the demo traces **non-confidential test PRDs** only; before production, add a redaction/retention policy or a content-gating switch for confidential/unreleased content (post-demo item). | 100% of LLM calls | Must |
| NFR-02 | Modifiability | Reviewers/assignees (PM, Head of Product, admin) and all project locations are config-only changes | No code edits to swap | Must |
| NFR-03 | Portability | State access via a **repository layer** so SQLite → Postgres is a single-module change | No logic touches raw SQL | Must |
| NFR-04 | Idempotency | Every webhook handler is idempotent; genuine duplicate deliveries (common in Jira/Confluence) must not double-process, **yet a real rename / `page-updated` MUST be able to re-enter the flow** (EH-04). Dedupe key = **event id + a monotonic content/version marker** (e.g. Confluence page version) — NOT entity/page id alone — so duplicate deliveries are suppressed while a legitimate update is not. Exact key mechanism deferred to Architecture (§13.1). | Dedupe by (event id + version marker) | Must |
| NFR-05 | Isolation of config | No project-specific literal (project key, space key, folder id, account id) appears anywhere outside config | grep-clean | Must |
| NFR-06 | Concurrency | Demo processes one PRD at a time (queue); design must not preclude later parallelism | Serial queue | Should |
| NFR-07 | Portability of the runtime | Packaged as a single Docker image; per-project instance = image + its `.env`; nothing project-specific baked into the image. Target host: DigitalOcean Droplet, Ubuntu LTS (§15). | 12-factor style | Should |
| NFR-08 | Resilience | Transient API failures retried with backoff before escalating to the error path | e.g. 3 retries | Should |
| NFR-09 | Cost control | Anthropic API usage visible per run via LangSmith. No loop can spin autonomously: the clarification/structure loops (FR-08/FR-10) block on a human reply, and the redraft loop (FR-11) is **uncapped for the demo but requires a fresh human PM feedback comment each round**. Guardrail is observability — `review_round` count + per-round token cost surfaced in LangSmith — not a hard cap. | `review_round` + cost visible per run | Should |
| NFR-10 | Licensing hygiene | If LangGraph is used, only the MIT-licensed core library is used (self-built FastAPI wrapper); no dependency on the licensed `langgraph-api` server product | Avoids license cost | Should |
| NFR-11 | Memory footprint | The running service SHALL operate within a 1 GB RAM host. Keep the container lean; do not co-locate memory-heavy services (e.g. Postgres) on the same Droplet for the demo. See §15 for build-time mitigations. | Runs stable on 1 GB | Must |

---

## 8. Error Handling, Edge Cases & Resume

**EH-01 — Error surfacing.**
On any error mid-flow (API failure after retries, ambiguous state, unexpected data), the agent SHALL:
1. Post a comment on the **relevant Jira ticket** describing the error in plain language.
2. Include a **suggested fix action**.
3. **Tag the admin** (`@<admin>` from config).
4. **Explicitly state how the admin should reply to resume** (see EH-02).
5. Log the error (with correlation id) for LangSmith/observability.

**EH-02 — Admin resume mechanism.**
The admin fixes the underlying issue, then **replies to the agent's error comment** on that ticket with a keyword — `@agent resume` (or `fixed`). On detecting this reply (webhook), the agent SHALL **re-run the failed step from the last good checkpoint** in the state store (not restart the whole flow). The error comment itself must tell the admin exactly this ("Reply `@agent resume` on this comment once fixed and I'll retry from where I stopped").

**EH-03 — Title mismatch.** See FR-02a (rename-request task to the Uploading PM in the Review project).

**EH-04 — Rename re-trigger.** After the Uploading PM renames a mislabeled page to `final_PRD_...`, the resulting Confluence `page-updated`/`page-created` event SHALL re-enter the flow at FR-02. This is **consistent with NFR-04**: because the dedupe key is **event id + content/version marker** (not entity/page id alone), the rename arrives as a *new* version and is **not** suppressed as a duplicate, while genuine duplicate deliveries of the *same* version still are. The earlier rename-request task must not cause duplicate processing.

**EH-05 — Concurrency.** If multiple valid PRDs arrive close together, they SHALL be **queued and processed one at a time** (demo). The state store tracks queued vs in-progress.

**EH-06 — Late feedback after Done.** Per FR-07 framing, any PM feedback added *after* the Review ticket is Done is **not processed**. The pass is final at the Done transition.

**EH-07 — Ambiguous/empty PRD.** If the Classifier cannot confirm a real PRD (empty, template, junk), treat as FR-02a (ask the Uploading PM to confirm/correct) rather than guessing.

**EH-08 — Clarification/structure loops never auto-advance.** FR-08 and FR-10 both **block on a human reply**; the agent must never fabricate the PM's answer or proceed on assumption in these two gates.

**EH-09 — Non-Done gate transitions & indefinite stalls (out of demo scope).** At both human gates (Review ticket, FR-12; Publishing ticket, FR-14), only the transition **to Done** is handled. **Non-Done terminal transitions** (Rejected / Won't Do / Duplicate), **reassignment** of the ticket, and the case where the human **never acts** are **out of demo scope**: the run simply **parks** at its current gate stage (`awaiting_review` or `awaiting_publish_approval`) with **no timeout and no auto-escalation**. This is an explicit demo decision and a documented limitation, not an oversight. (Distinct from EH-06: feedback added *after* Done is still ignored — the pass is final at the Done transition.)

---

## 9. System Design Notes (guidance, not prescription)

- **Trigger layer:** A self-built **FastAPI** service receives Confluence and Jira webhooks. (If LangGraph is used for orchestration, only its MIT core is used behind this wrapper — no licensed server product; see NFR-10.)
- **Routing:** Every inbound event is mapped to a tenant via the **config registry** before any work happens.
- **State:** An explicit **state store** (SQLite for demo) records, per PRD: identifiers, current **stage**, review round count, pending gate, last-good checkpoint, and dedupe keys. All access via a **repository layer** (NFR-03).
- **Stages (explicit, not inferred):** e.g. `detected → confirmed → prd_ticket_done → drafted → awaiting_review → awaiting_clarification → awaiting_structure_confirm → revising → passed → awaiting_publish_approval → publishing → complete → error`.
- **Skills:** Each agent role loads its **SKILL.md** (role, context, quality bar, do/don't). The demo build SHOULD author these skill files as part of delivery — they need not be perfect; they are the primary quality-tuning surface and will be iterated.
- **Self-critique:** One draft→critique→revise pass in the Author agent (FR-05).
- **Deferred by design:** RAG, Postgres, SSG deploy, parallel tenancy — seams left open, not implemented.

---

## 10. Data / State Requirements

Minimum per-PRD state record:

| Field | Purpose |
|---|---|
| `prd_id` | Stable key (Confluence page id) |
| `project_id` / tenant | Which config applies |
| `stage` | Explicit lifecycle stage (§9) |
| `review_ticket_key` | Review project ticket |
| `prd_tracking_ticket_key` | Main project ticket |
| `publishing_ticket_key` | Main project publishing ticket |
| `userdoc_page_id` | Confluence draft/final page |
| `review_round` | Loop counter (metrics) |
| `pending_gate` | What human action is awaited |
| `last_good_checkpoint` | For EH-02 resume |
| `dedupe_keys` | Processed event ids (NFR-04) |
| `md_export_path` | Where the final `.md` was written |
| `timestamps` | created/updated/completed |

---

## 11. Config Registry (per project) — the "easy to mod" surface

Each project/tenant is one config entry. Example fields (values illustrative):

```yaml
project_alpha:
  # routing — folders (source is WATCHED; published is separate and adjacent to source)
  confluence_source_folder_id: "..."        # WATCHED by FR-01 detection
  confluence_draft_folder_id: "..."         # UserDoc drafts for the review loop
  confluence_published_folder_id: "..."     # approved UserDocs; ADJACENT to (not inside) source — never re-ingested
  jira_main_project_key: "MAIN"
  jira_review_project_key: "REV"
  jira_epic_id: "..."                  # if/when subtasking is used
  # identities (fixed for demo)
  pm_account_id: "..."                 # Reviewer PM: draft-review/feedback loop + PASS gate
  head_of_product_account_id: "..."
  admin_account_id: "..."              # "me" for error escalation
  # NOTE: the Uploading PM (page creator) is NOT config — it is taken from the Confluence
  #       page-creator field at runtime and mapped (Confluence account -> Jira account)
  #       to assign the rename-request task (FR-02a). Mapping mechanism: see §13.1.
  # infra
  md_export_dir: "/data/userdocs/alpha"
  # credentials by reference (not inline secrets)
  jira_credentials_ref: "env:ALPHA_JIRA"
  confluence_credentials_ref: "env:ALPHA_CONF"
```

Swapping a reviewer, or onboarding a new project, is an edit here — **no code change** (NFR-02, NFR-05).

---

## 12. Definition of Done (for the demo build)

- [ ] A page titled `final_PRD_<name>` created in the configured source folder triggers the flow automatically.
- [ ] A mislabeled page instead produces a rename-request task to the Uploading PM (page creator) in the Review project, as a separate ticket.
- [ ] The PRD-tracking ticket is found-and-Done'd, or created-and-Done'd at top hierarchy.
- [ ] A first UserDoc draft (self-critiqued) is published to the draft folder and a Review ticket is created and assigned to the Reviewer PM.
- [ ] The review request comment tags the Reviewer PM, requests the structured format, includes the "users' shoes" framing, and states the Done-only pass rule.
- [ ] Plain-language feedback triggers the structure-confirmation loop and blocks until the PM confirms.
- [ ] Structured (or confirmed) feedback produces a revised draft + change summary + re-request; loop repeats uncapped.
- [ ] The agent asks clarifying questions only when truly blocked, and waits for the answer.
- [ ] Review ticket → Done is detected as PASS; agent confirms and creates the Publishing ticket reported to the Head of Product.
- [ ] Publishing ticket → Done triggers edit-restriction + move to the Published-UserDocs folder (adjacent to source) + `.md` export to server storage; flow marked Complete.
- [ ] Any injected error posts an error+fix+admin-tag comment with resume instructions; `@agent resume` re-runs the failed step from checkpoint.
- [ ] LangSmith shows per-step latency, speed, and cost for the run.
- [ ] Swapping the PM/Head of Product/admin or adding a second project is done via config only.

---

## 13. Open Questions / Assumptions to confirm at build time

| # | Item | Current assumption |
|---|------|--------------------|
| 1 | Exact Jira workflow transitions | Assumes a direct path to "Done" exists on the relevant issue types; verify no mandatory intermediate states. **Guard (FR-04):** skip if already Done; resolve the legal transition path from current status at runtime. Handed to Architecture (§13.1). |
| 2 | Confluence folder model | Assumes the space uses a folder/parent model addressable by id via API; confirm folder vs parent-page. |
| 3 | Webhook reliability | Assumes Confluence/Jira webhooks are enabled and can reach the Droplet's public HTTPS endpoint (§15.4); a polling fallback is out of demo scope but noted. |
| 4 | Uploading-PM identity & mapping | The **Uploading PM** is taken from the Confluence page-creator field on the webhook (verify it's present) and must be **mapped Confluence-account → Jira-account** to assign the rename-request task (FR-02a). The **Reviewer PM**, Head of Product & admin come from config. Mapping mechanism handed to Architecture (§13.1). |
| 5 | Markdown fidelity | Confluence storage format → Markdown conversion via a standard library; minor formatting loss acceptable for demo. |
| 6 | Secrets handling | Credentials injected via environment references, never inline in config or code. |

### 13.1 Deferred to Architecture

The following are explicitly handed to the Architecture step — they shape the build but are design decisions, not product requirements:

- **Dedupe key mechanism** — the exact form of `event id + monotonic content/version marker` (NFR-04, EH-04).
- **Detection-exclusion guard** — the precise label/path check that keeps the agent's own published pages out of FR-01 detection (FR-15; §6.0 Published-UserDocs folder).
- **Checkpoint / resume granularity** — the definition of a resumable "step" and checkpoint for EH-02; recommended granularity is **per-stage**, using the §9 stage list.
- **Confluence→Jira identity mapping** — how a Confluence page-creator account resolves to a Jira account to assign the Uploading PM the rename task (FR-02a; §13 Q4).
- **Jira transition legality / paths** — resolving legal workflow transitions to Done at runtime, incl. skip-if-already-Done (FR-04; §13 Q1).
- **Confluence folder-vs-parent model** — whether folders are addressable by id or modeled as parent pages (§13 Q2), affecting source/draft/published folder handling.

---

## 14. Appendix — Architectural decisions carried in from discovery

- **Flow, not a single agent:** decomposed, independently-triggered, mostly-stateless units coordinated by an explicit state store. Chosen because Jira/Confluence already hold most authoritative state; the trade-off is that the state contract must be explicit and idempotent (owned by us, not a framework).
- **Multi-tenant single service:** one deployment routes by project via config, rather than N duplicated deployments. Container-per-tenant remains a fallback if isolation is ever needed.
- **Licensing:** build a thin FastAPI wrapper; if LangGraph is used, use only its MIT core, avoiding the Elastic-licensed server product so the system runs on a VPS with no license cost.
- **Demo-first:** quality behaviors (doc structure, tone) live in prompts + SKILL.md and are tuned post-demo; heavier techniques (RAG, orchestration frameworks) deferred until a real need appears.

---

## 15. Deployment & Infrastructure (target environment)

### 15.1 Host — locked decision
- **Provider / type:** DigitalOcean Droplet, **Basic** plan, **Regular SSD** disk.
- **Size:** **1 GB RAM / 1 vCPU / 25 GB SSD** (~$6/month). Locked by the product owner.
- **Image / OS:** **Ubuntu LTS** (latest Long-Term Support release). Plain Ubuntu, or the Marketplace "Docker on Ubuntu" 1-Click image if the builder prefers Docker pre-installed — either is acceptable.
- **Deploy artifact:** the single Docker image from NFR-07, run on the Droplet.

### 15.2 ⚠️ Memory-constraint warning — read before building
This is a **deliberately small box (1 GB RAM)**, chosen for cost. It is sufficient for the single-tenant, one-PRD-at-a-time demo, but the build MUST respect it. The builder should expect and design around the following:

- **Do not build the Docker image on the Droplet.** A Docker build on 1 GB can OOM (run out of memory) and fail. Build the image elsewhere — locally, in CI (e.g. GitHub Actions), or via DigitalOcean's registry — and **pull** the finished image onto the Droplet. This is the single most important mitigation.
- **Add a swap file** (e.g. 1–2 GB) on the Droplet as a safety cushion against transient memory spikes. Standard, cheap, and expected on a 1 GB host.
- **Keep the container lean.** Slim Python base image (e.g. `python:*-slim`), no unnecessary system packages, single worker process for the demo (do not spin up many Uvicorn/Gunicorn workers — each consumes RAM and the workload is serial anyway).
- **Do NOT co-locate a database server.** SQLite (an in-process file, not a server) is fine here and is the demo default (NFR-03). If/when the system moves to Postgres for multi-tenant scale, Postgres should run on a **separate** Droplet or a managed database instance, **not** on this 1 GB box.
- **Watch the LLM-payload memory.** Large PRDs held in memory + the HTTP/agent stack are the main consumers here. One PRD at a time keeps this bounded; the serial queue (NFR-06) is also a memory-safety measure, not only a concurrency choice.

### 15.3 If 1 GB proves too tight
Resizing a Droplet up (e.g. to the 2 GB / $12 tier) is a non-destructive, few-minute operation in the DigitalOcean panel — RAM/CPU can be increased and later decreased. So the 1 GB start is **reversible**: treat OOM symptoms during the **§12 end-to-end run / Definition of Done** as the signal to bump the size, not as a redesign trigger. The build should not hard-code any assumption that only 1 GB will ever be available.

### 15.4 Webhook reachability (host-level requirement)
Because detection is webhook-driven (FR-01) and Atlassian must reach the Droplet over the public internet:
- The Droplet needs a **public URL reachable by Jira/Confluence webhooks**, served over **HTTPS** (Atlassian webhooks require a valid TLS endpoint). Simplest path: a reverse proxy (nginx/Caddy) terminating TLS in front of the FastAPI service, with a domain or the Droplet's public IP + a certificate (Caddy auto-provisions Let's Encrypt certs with near-zero config).
- **Firewall:** expose only what's needed (443 for webhooks, 22 for SSH); keep the FastAPI port bound to localhost behind the proxy.
- **Webhook auth:** validate incoming webhooks (shared secret / signature check) so the public endpoint can't be spoofed — a security "Must," not a nice-to-have, since the endpoint triggers real Jira/Confluence writes.

### 15.5 Billing awareness (for the product owner)
- A **powered-off Droplet is still billed** at full rate (DigitalOcean reserves the resources). To stop charges, destroy it (optionally snapshot first).
- **Backups** are an optional add-on at ~20% of the Droplet's monthly price — reasonable insurance once real data (the state DB, exported `.md` files) lives on the box.
- The exported `.md` files and the SQLite state file live on the Droplet's disk — factor them into any snapshot/backup policy so a rebuild doesn't lose in-flight workflow state.
