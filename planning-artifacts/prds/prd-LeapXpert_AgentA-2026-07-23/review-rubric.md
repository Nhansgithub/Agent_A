# PRD Quality Review — PRD-to-User-Document Automation Agent Flow

*Calibration applied: backend multi-agent automation service, no GUI (Jira/Confluence are the entire human interface), demo-scope but **chain-top** — feeds Architecture → Epics & Stories → implementation. Judged as an internal/technical capability spec. Consumer-style User Journeys are NOT expected and their absence is not penalized. Per the stakes, testability of FRs and term/ID consistency are graded unforgivingly.*

## Overall verdict

This is a well-shaped, substantive chain-top build spec: it carries a clear thesis (automate the drafting/revision labor of user docs while humans keep judgment via tools they already use), honest and load-bearing non-goals (§5.2/§5.3), product-specific NFRs with real thresholds (§7), and a genuinely locked deployment story (§15). It is the right shape for a headless automation service and will feed architecture and stories with a solid procedural spine. It grades **thin on Done-ness**, though, because its core *autonomous* judgments ship with no test oracle — the PRD classifier (FR-03 "genuinely a finalized PRD") and the "truly blocked" clarification heuristic (FR-08) are unfalsifiable as written, and the primary output (the UserDoc) has no stated acceptance mechanism. The human-gate mechanism that is the product's whole point specifies only the happy "Done" transition, leaving rejection, abandonment, and indefinite stalls silently unhandled with no timeouts; and several excerpt artifacts (missing §§6.3–6.6, a dangling "§Phase 5/6" reference, an undefined "SSG") betray incomplete reconciliation on adoption and will trip downstream extraction.

## Decision-readiness — adequate

The PRD makes and states its decisions rather than burying them. The deployment target is explicitly *locked* (§15.1, header). §14 is a genuine architectural-decisions appendix that names trade-offs with what was given up, not just what was chosen — e.g. "Flow, not a single agent … the trade-off is that the state contract must be explicit and idempotent (owned by us, not a framework)," and "Multi-tenant single service … Container-per-tenant remains a fallback if isolation is ever needed." The licensing decision (MIT LangGraph core only, avoiding the Elastic-licensed server) is concrete and reasoned (NFR-10, §14). The Open Questions (§13) are genuinely open — build-time verification items each paired with a *Current assumption*, not rhetorical questions answered in the next sentence.

What keeps this from *strong*: a few decisions that a chain-top spec could pin are punted wholesale to "build time" (§13 Q1 exact Jira transitions, Q2 Confluence folder-vs-parent model) even though they materially shape the ticket-manager and publisher agents. And one cost-relevant decision is left un-adjudicated: FR-11 declares the redraft loop "repeats with **no cap** until PASS" on a demo that is otherwise fixated on cost ($6/mo, 1 GB, LangSmith cost tracing) with no round/cost ceiling or metric to bound it.

### Findings
- **medium** No-cap redraft loop is an un-adjudicated cost decision (§6.1 FR-11; §3) — FR-11 sets "no cap until PASS" with no round or cost ceiling and no metric tracking cumulative spend, on a demo whose entire framing is cost/latency-conscious. *Fix:* state the decision explicitly — either "unbounded by design, cost accepted" or add a soft round cap / cost budget surfaced via the `review_round` counter and LangSmith, and note the counter-behavior.
- **low** Chain-top verifications deferred to build-time (§13 Q1/Q2) that could be pinned now — exact Jira transition path to Done and folder-vs-parent model both shape agent design. *Fix:* resolve before Architecture, or explicitly flag them as architecture-phase inputs so they are not silently assumed.

## Substance over theater — strong

The content is earned, not furniture. There is **no persona theater** — §4 Actors & Roles is functional, and every role drives real FRs (PM → FR-02a/07/09/12; Head of Product → FR-14; Admin → EH-01/02; Agent Flow → the rest). There is **no innovation/differentiation theater** — no differentiation section was invented to fill a template. The NFRs are product-specific with concrete thresholds, not boilerplate: NFR-04 "Dedupe by event/entity id," NFR-08 "e.g. 3 retries," NFR-10 licensing hygiene, NFR-11 "Runs stable on 1 GB." The Executive Summary (§1) is specific to this product (Confluence-watching, Jira-in-the-loop, multi-tenant, SQLite demo) and could not be swapped into another PRD unchanged. The heavy §15 deployment detail is not padding — it is load-bearing because the 1 GB box is a locked constraint.

### Findings
- **low** Self-critique pass reads slightly checkbox-like (§6.1 FR-05) — "draft → critique against skill file → single revision" is asserted as a quality mechanism, but with no measurable improvement criterion and against a not-yet-written SKILL.md it is closer to a ritual than a verified control. *Fix:* state what the critique checks against and what it would reject, even loosely, so it is more than a claimed pass.

## Strategic coherence — adequate

The PRD has a real thesis and the features serve one arc: detect finalized PRD → author draft → human-gated review loop → Head-of-Product publish gate → publish/export. Scope kind is coherent (problem-solving: remove the drafting/review-driving labor; defer RAG/SSG/parallelism as seams). The success metrics mostly validate the thesis (human-in-control: "100% of runs gated"; modifiability: "adding a 2nd project via config").

The coherence gap is in measurement. There are **no counter-metrics** despite Success Metrics existing (§3), and the headline metric — "a demo PRD flows end-to-end to a published `.md`" for a **usable** user doc — never defines "usable." As written, the demo can score its top metric (1 successful full run) while emitting a hollow or wrong document, because nothing measures output quality and no guardrail metric (e.g. false-classification rate, doc-quality bar, human-edit volume) is named. For a thesis that is fundamentally "the machine drafts well enough that humans only judge," the absence of any quality-side metric undercuts the thesis validation.

### Findings
- **medium** No counter-metrics and an undefined "usable" success bar (§3) — the top goal can be met by a single happy-path run regardless of output quality; nothing guards against a hollow success. *Fix:* add at least one counter-metric (e.g. classifier false-positive/false-negative on a small labeled set, or "PM required ≤ N revision rounds") and define what "usable" means for the demo.

## Done-ness clarity — thin

The *procedural* spine is genuinely testable — roughly 12 of 15 FRs have verifiable consequences (FR-01 route by config; FR-02 title regex; FR-06 page+ticket created and assigned; FR-07 comment contains the four required elements; FR-12/13/14 transition-driven; FR-15 lock/move/export/mark-complete), and the DoD (§12) sharpens many into a checklist. That is real and to the PRD's credit.

But this is the dimension downstream story creation leans on hardest, and the product's **value-producing, autonomous judgments have no test oracle**. FR-03 requires the classifier to "confirm it is genuinely a finalized PRD (not an empty page, template, or mislabeled doc)" — "genuinely" is doing enormous unspecified work, with no examples, no accuracy target, and no way for a story author or QA to verify correctness, even though this gate decides whether the entire flow starts (a false positive drafts from a non-PRD; a false negative nags a PM about a real one). FR-08 fires only when the agent "genuinely does not understand an important point or is truly blocked … not for trivial assumptions" — untestable as stated. FR-05's UserDoc is the primary output, yet its acceptance is never declared: quality is *implicitly* gated by the PM's PASS, which is a legitimate design, but the PRD never says "the human PASS **is** the acceptance criterion for FR-05," leaving FR-05's own done-ness ambiguous. Finally, EH-02's resume "re-runs the failed step from the last good checkpoint," but neither "step" nor "checkpoint" granularity is defined (per-FR? per-stage? per-API-call?), and FR-15 is a four-action step (lock, move, export, mark complete) whose re-run could double-apply unless each sub-action is idempotent — which is asserted nowhere.

### Findings
- **high** Core autonomous judgments have no acceptance criteria or test oracle (§6.1 FR-03, FR-08) — "genuinely a finalized PRD" and "truly blocked" are unfalsifiable; FR-03 gates flow entry with real false-positive/false-negative cost, and downstream stories/QA have nothing to verify against. *Fix:* give the classifier a concrete decision rubric + a small labeled fixture set (accept/reject examples) and an accuracy expectation for the demo; give FR-08 a bounded trigger definition (e.g. "ask only when a named required input is absent from the PRD," enumerated).
- **medium** UserDoc acceptance mechanism unstated (§6.1 FR-05) — the primary output's "done" is never defined; human PASS is the implicit oracle but is not declared as such. *Fix:* state explicitly that FR-05's acceptance for the demo is "a self-critiqued draft is produced and posted; content quality is accepted solely via the PM PASS gate (FR-12)."
- **medium** Resume checkpoint/step granularity undefined; multi-action step double-apply risk (§8 EH-02; §6.1 FR-15; §10) — "re-run the failed step from the last good checkpoint" is unspecified, and FR-15's four sub-actions could double-apply on re-run. *Fix:* define what a checkpoint is (recommend per-stage from §9's stage list) and require each FR-15 sub-action to be individually idempotent/skippable-if-done.

## Scope honesty — adequate

Omissions are largely explicit and do real work. §5.2 Out of Scope (SSG deploy, RAG, true concurrency, fixed template, multi-approver) and §5.3 Future Considerations (with "leave seams") are clear and prevent gold-plating; §3 restates the non-goals; §13 surfaces assumptions in table form. The open-items density (6 open questions, all build-time verifications) is appropriate for demo stakes.

The honesty gap is that §8 — a section that explicitly sets out to cover edges (title mismatch, rename re-trigger, concurrency, *late feedback after Done*, ambiguous PRD, loop-gating) — is silent on the two most obvious human-gate edges. Both gates are specified only for the happy **Done** transition (FR-12, FR-14); what the agent does when the PM or Head of Product moves the ticket to a *non-Done* terminal status (Rejected / Won't Do / Duplicate), reassigns it, or simply never acts, is undefined. There is no timeout, reminder, or SLA anywhere, so a run can sit in `awaiting_review` or `awaiting_publish_approval` indefinitely — an ironic silent stall for a system whose value prop is "documentation doesn't lag." These omissions are not declared as non-goals; the reader is left to infer them.

### Findings
- **high** Only the happy "Done" transition is handled at both human gates; rejection, abandonment, and stalls are silently unhandled (§6.1 FR-12/FR-14; §8) — non-Done terminal transitions have no defined behavior and there are no timeouts/SLAs, so the flow can deadlock; §8 covers lesser edges but not these, so the omission reads as an oversight rather than a decision. *Fix:* either add EH cases for non-Done gate transitions and a stall/timeout escalation to the admin, or explicitly declare "only the Done transition is handled; other transitions and stalls are out of demo scope" so the omission is honest.

## Downstream usability — adequate

This matters more here (chain-top). The core is extractable: FR-01–15, NFR-01–11, and EH-01–08 are each contiguous and unique; §6.0 provides a Terminology block; §10 gives an explicit state schema and §11 a config schema; sections mostly stand alone. Absence of UJs is correct for a headless service and is not a defect.

What will trip source-extraction: (1) the Functional Requirements section numbering jumps **6.2 → 6.7** with no 6.3–6.6 — a reader cannot tell whether four requirement subsections were dropped or the numbering is inherited from the source doc, which is exactly the doubt a build spec must not create; (2) §15.3 references "**§Phase 5/6**" but this PRD defines no phases — a dangling cross-reference proving the doc was excerpted from a larger plan (`PRD_Agent_Flow.md`) and not fully reconciled; (3) "**SSG**" is used repeatedly (§5.1/5.2/5.3, FR-15, §9) and named as a non-goal but never expanded or defined in §6.0 — an architect must guess "Static Site Generator"; (4) minor glossary drift ("UserDoc"/"User Document", "Head of Product"/"Head-of-Product").

### Findings
- **medium** FR section numbering gap 6.2 → 6.7 (§6) — no §§6.3–6.6; reader cannot tell if requirements are missing. *Fix:* renumber 6.7 to 6.3 (or restore the missing subsections if content was dropped on adoption).
- **medium** Dangling cross-reference "§Phase 5/6" (§15.3) — no phases exist in this document. *Fix:* replace with a concrete anchor (e.g. "during the §12 end-to-end run") or add the referenced phase structure.
- **low** "SSG" never expanded or defined (§5, FR-15, §9) — central to the publish story and a named non-goal. *Fix:* expand on first use and add to §6.0 Terminology.
- **low** Glossary drift — "UserDoc"/"User Document" and "Head of Product"/"Head-of-Product" used interchangeably. *Fix:* pick one canonical form each and normalize.

## Shape fit — strong

The PRD matches its product type well. It is a capability/technical spec — Actors & Roles, SHALL-form Functional Requirements, cross-cutting NFRs, Error Handling/Edge/Resume, Data/State, Config Registry, DoD, Deployment — which is exactly right for a headless multi-agent service where Jira/Confluence are the interface. It is neither over-formalized (no forced UJs, no persona theater) nor under-formalized (rich FRs/NFRs/edge handling/DoD). SMs are operational (1 full run, 100% gated), appropriate for an internal automation tool. The §12 DoD acts as an acceptance checklist that partially compensates for thin per-FR acceptance criteria — a good shape choice. §9 correctly labels its implementation guidance "guidance, not prescription."

### Findings
- *(none material)* — minor: some implementation detail lives inline in FRs and §9/§14 rather than a separate `addendum.md`, but for an adopted chain-top build spec with no addendum this is acceptable and arguably useful.

## Mechanical notes
- **ID continuity:** FR-01–15, NFR-01–11, EH-01–08 are contiguous and unique. But **§6 subsection numbering skips 6.3–6.6** (jumps 6.2 → 6.7) — the one real continuity break.
- **Broken cross-reference:** "§Phase 5/6" (§15.3) resolves to nothing — no phases defined in this PRD. All other cross-refs checked resolve (§6.2, §6.7, §8, §10, §15.4).
- **Undefined term:** "SSG" used throughout, never expanded; not in §6.0.
- **Glossary drift:** "UserDoc" vs "User Document"; "Head of Product" vs "Head-of-Product".
- **Assumptions convention:** no inline `[ASSUMPTION: …]` tags or an Assumptions Index; assumptions are instead captured in the §13 Open-Questions table. This is a legitimate alternative form for an externally-authored/adopted PRD — noted, not penalized.
- **Required sections:** all expected sections for a chain-top technical capability spec are present (scope, FRs, NFRs, error/edge, data/state, config, DoD, deployment). No UJ/persona sections — correct for this shape.
