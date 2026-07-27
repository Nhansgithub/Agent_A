# PRD — LeapXpert Agent B: internal Knowledge Base + Slack Q&A

**Status:** Draft (stub, created with story S-B0, 2026-07-27). Amend as Epic 7 lands — each story that
changes a behavior updates the matching `FR-Bxx` here (same rule as the Agent A PRD).
**Related:** Agent A PRD (`prd-LeapXpert_AgentA-2026-07-23`); Architecture Spine **AD-27…AD-32**;
decisions **D-41…D-46**; backlog **Epic 7** (S-B0…S-B9).

## 1. Purpose

Agent B turns the product team's knowledge — PRDs, PM designs, and the end-user UserDocs produced by
Agent A — into an **internal knowledge base** that is (a) navigable as a linked graph (Obsidian-style),
and (b) queryable in natural language from Slack. Confluence already *stores* these documents; Agent B
adds what Confluence lacks: inter-document linking, a graph view, and conversational retrieval.

## 2. Users & interface

Internal product/eng teams. Two surfaces, no bespoke GUI:
- a **browsable vault** (a Quartz static site) at an internal URL — read, search, explore the graph;
- a **Slack bot user** — ask questions, get cited answers.

## 3. Scope (demo)

**In scope:** one curated Confluence space; a scheduled pull → a linked Obsidian vault; Quartz publish;
local RAG; Slack Q&A. **Out of scope (later seams):** multi-space / multi-tenant KBs; per-user access
control; dedicated Figma/Drive design ingestion + multimodal (designs arrive as a Confluence **text**
folder, D-41); editing the vault (it is a read-only projection, AD-28).

## 4. Functional requirements

- **FR-B01 — Curated pull.** On a schedule, pull every page under the configured include-folders of the
  space, skipping the exclude-folders (the draft/review folder), and convert each to a Markdown note with
  YAML frontmatter (`title, page_id, space, doc_type, parent_id, source_url, pulled_at`). *(S-B1)*
- **FR-B02 — Idempotent sync.** Re-pull is content-hash based: unchanged pages are untouched; changed
  pages are rewritten/re-indexed; pages removed from Confluence are tombstoned. *(S-B1, S-B4)*
- **FR-B03 — Deterministic linking.** Add hierarchy links (parent/children) and restore inter-page
  references that survived conversion, as `[[wikilinks]]`. No false edges. *(S-B2)*
- **FR-B04 — Curation (LLM).** Generate per-topic MOC hub notes + a tag taxonomy; propose additional
  "related" links **quarantined** to a `related_suggested:` block, never inlined. *(S-B3)*
- **FR-B05 — Publish.** Render the vault to a read-only Quartz site (graph view, backlinks, search) at
  the configured URL. *(S-B5)*
- **FR-B06 — Retrieval.** Vector search (local embeddings) + graph expansion over the vault. *(S-B6)*
- **FR-B07 — Slack Q&A.** A bot user answers in-thread with **citations**, and **refuses** when nothing
  clears the score floor. Captures 👍/👎. *(S-B7)*
- **FR-B08 — Traceability.** Every LLM call (curation + answering) is traced (AD-20); every answer is
  logged. *(S-B3, S-B6, S-B7)*

## 5. Non-functional

- **NFR-B01 — 1 GB envelope (AD-21):** the heavy pull/embed is a short-lived job; local embeddings via
  fastembed (no torch); only the Slack query path stays resident.
- **NFR-B02 — No egress of the corpus:** embeddings are computed locally (D-43).
- **NFR-B03 — Boundaries (AD-27):** `agent_b` mirrors AD-1/2/6; enforced by import-linter.
- **NFR-B04 — Config isolation (AD-4):** all ids/creds in the `agent_b:` registry block; secrets are env
  refs only.

## 6. Configuration

The `agent_b:` block of `config/registry.yaml`: `space_key`, `confluence_credentials_ref`,
`include_folder_ids`, `exclude_folder_ids`, `folder_types`, `vault_dir`, `database_path`, `schedule_cron`,
`curator_model`, `answerer_model`, `embeddings`, `rag`, `publish`, `slack`.

## 7. Human / third-party gates (see implementation-state/BLOCKERS.md)

- **B-9** Slack app + tokens — for live Q&A.
- **B-10** designs Confluence folder id — for the designs portion of the pull.
- **B-4** deploy access + `agent.poetroastery.com` DNS — for the live Quartz URL.

## 8. Relationship to Agent A

Agent B consumes Agent A's output (the published UserDoc, via the Confluence pull) and **replaces**
Agent A's FR-15 `.md` export, which was always a stub toward exactly this SSG step (D-44 / S-B8).
