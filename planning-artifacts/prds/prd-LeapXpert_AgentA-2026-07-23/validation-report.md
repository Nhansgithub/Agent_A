# Validation Report — PRD-to-User-Document Automation Agent Flow

- **PRD:** `_bmad-output/planning-artifacts/prds/prd-LeapXpert_AgentA-2026-07-23/prd.md`
- **Rubric:** `.claude/skills/bmad-prd/assets/prd-validation-checklist.md`
- **Run at:** 2026-07-23T11:34:42Z
- **Grade:** Fair

## Overall verdict

This is a well-shaped, substantive chain-top build spec: it carries a clear thesis (automate the drafting/revision labor of user docs while humans keep judgment via the tools they already use), honest and load-bearing non-goals (§5.2/§5.3), product-specific NFRs with real thresholds (§7), and a genuinely locked deployment story (§15). It is the right shape for a headless automation service where Jira and Confluence are the entire interface, and its procedural spine (roughly 12 of 15 FRs) is testable and will feed architecture and stories cleanly.

It grades **Fair** because the two dimensions the stakes make unforgiving both bend. **Done-ness is thin**: the product's core *autonomous* judgments ship with no test oracle — the PRD classifier (FR-03 "genuinely a finalized PRD") and the "truly blocked" clarification heuristic (FR-08) are unfalsifiable, and the primary output (the UserDoc) has no stated acceptance mechanism. And the human-gate mechanism that is the product's whole point specifies only the happy "Done" transition, leaving rejection, abandonment, and indefinite stalls silently unhandled with no timeouts.

The adversarial pass sharpened rather than shifted the picture, surfacing concrete build-biters the rubric only implied: an idempotency-vs-rename-re-trigger contradiction (NFR-04 ↔ EH-04); a self-ingestion loop where publishing moves the finished doc into the very folder the webhook watches (FR-15 ↔ FR-01); a "PM who created the page" identity that contradicts "PM fixed in config"; and unacknowledged egress of confidential unreleased-PRD content to LangSmith. Several excerpt artifacts (missing §§6.3–6.6, a dangling "§Phase 5/6" reference, an undefined "SSG") show the adoption from `PRD_Agent_Flow.md` was not fully reconciled.

## Dimension verdicts
- Decision-readiness — adequate
- Substance over theater — strong
- Strategic coherence — adequate
- Done-ness clarity — thin
- Scope honesty — adequate
- Downstream usability — adequate
- Shape fit — strong

## Findings by severity

### Critical (0)
*None.*

### High (2)

**[Done-ness | Adversarial]** — Core autonomous LLM judgments have no acceptance criteria or test oracle (§6.1 FR-03, FR-08)
FR-03 asks the classifier to confirm a page is "genuinely a finalized PRD" with no definition, examples, accuracy bar, or verification path — and this gate decides whether the entire flow fires (a false positive drafts a public doc from non-PRD content; a false negative nags a PM about a correct doc). FR-08 fires when the agent is "truly blocked … not for trivial assumptions," equally unfalsifiable. Downstream stories/QA have nothing to verify against.
Fix: give the classifier a concrete decision rubric + a small labeled fixture set (accept/reject examples) and a demo accuracy expectation; give FR-08 a bounded, enumerated trigger definition.

**[Scope honesty | Adversarial]** — Only the happy "Done" transition is handled at both human gates; rejection, abandonment, and stalls silently unhandled (§6.1 FR-12/FR-14; §8)
Both gates are specified only for the transition *to Done*. Non-Done terminal transitions (Rejected / Won't Do / Duplicate), reassignment, and "the human never acts" are undefined, and there are no timeouts/SLAs anywhere — so a run can deadlock in `awaiting_review` or `awaiting_publish_approval` indefinitely. §8 handled lesser edges (even "late feedback after Done"), so this reads as oversight, not a declared decision — ironic for a system whose pitch is "documentation stops lagging."
Fix: add EH cases for non-Done gate transitions and a stall/timeout escalation to the admin (reuse the EH-01 error-comment channel), or explicitly declare these out of demo scope.

### Medium (10)

**[Strategic coherence]** — No counter-metrics and an undefined "usable" success bar (§3)
The top goal ("a demo PRD flows end-to-end to a published `.md`" for a *usable* doc) never defines "usable," and no counter/guardrail metric exists. The demo can score its headline metric with a single run while emitting a hollow or wrong document.
Fix: add at least one counter-metric (classifier false-positive/negative on a small labeled set, or "PM required ≤ N revision rounds") and define "usable" for the demo.

**[Done-ness]** — UserDoc acceptance mechanism unstated (§6.1 FR-05)
The primary output's "done" is never declared; human PASS is the implicit oracle but is never stated as such, leaving FR-05's own done-ness ambiguous.
Fix: state explicitly that FR-05's acceptance is "a self-critiqued draft is produced and posted; content quality is accepted solely via the PM PASS gate (FR-12)."

**[Done-ness | Adversarial]** — Resume checkpoint/step granularity undefined; multi-action step double-apply risk (§8 EH-02; §6.1 FR-15; §10)
"Re-run the failed step from the last good checkpoint" leaves "step" and "checkpoint" undefined; FR-15 is four actions (lock, move, export, mark complete) that could double-apply on re-run absent stated idempotency.
Fix: define checkpoint granularity (recommend per-stage from §9's stage list) and require each FR-15 sub-action to be individually idempotent/skippable-if-done.

**[Decision-readiness | Adversarial]** — Unbounded redraft loop with no round/cost ceiling (§6.1 FR-11 vs §7 NFR-09; §3)
FR-11's "no cap until PASS" sits on a cost-obsessed demo (locked $6/mo box, LangSmith cost tracing) and in tension with NFR-09 "no runaway loops," whose parenthetical pointedly covers only the clarification/structure loops. *(Reconciled from the adversarial reviewer's "high": the redraft loop needs a PM comment each round, so it cannot spin autonomously — the real defect is the absence of a round/cost ceiling or metric.)*
Fix: add a soft round/cost ceiling surfaced via the `review_round` counter and LangSmith, or explicitly accept unbounded cost as a decision.

**[Downstream usability | Adversarial]** — FR section numbering jumps 6.2 → 6.7 (§6)
No §§6.3–6.6; a reader cannot tell whether four requirement subsections were dropped during the excerpt from `PRD_Agent_Flow.md` or the numbering is inherited.
Fix: confirm no content is missing and renumber 6.7 → 6.3 (or restore the subsections).

**[Downstream usability | Adversarial]** — Dangling cross-reference "§Phase 5/6" (§15.3)
No phases exist in this document — a fossil from the larger source plan, unreconciled on adoption.
Fix: repoint to a concrete anchor (e.g. "during the §12 end-to-end run") and sweep for other fossils.

**[Adversarial]** — Idempotency vs rename re-trigger unreconciled (§7 NFR-04 vs §8 EH-04)
NFR-04 "dedupe by entity id" collides with EH-04's requirement that the *same page id* re-enter the flow after rename: dedupe-by-entity suppresses the reprocess; dedupe-by-event double-processes on genuine duplicate delivery. EH-04 asserts the two coexist without saying how.
Fix: specify the dedupe key (event id + a monotonic content/version marker) so deliberate reprocessing-on-update survives dedupe.

**[Adversarial]** — Publishing moves the doc into the watched folder → self-ingestion (§6.1 FR-15 vs FR-01/FR-02a)
FR-15 moves the final UserDoc to the same folder as the source PRD, which FR-01 watches; the UserDoc title is not `final_PRD_...`, so FR-02 routes it to FR-02a — spawning a rename-request ticket nagging the PM about the doc just published.
Fix: exclude the agent's own published pages from detection (tag/label or path guard), or publish outside the watched set.

**[Adversarial]** — "PM who created the page" contradicts "PM fixed in config" (§6.1 FR-02a vs §4; §13 Q4)
§4 fixes the PM in config; FR-02a assigns the rename task to the page creator (from the Confluence creator field). A non-PM author breaks the model, and clean Confluence→Jira account-id mapping is assumed, not established.
Fix: route rename tasks to the config PM by default; use the creator only as CC, and specify the identity-mapping source.

**[Adversarial]** — LangSmith mandate egresses confidential content to a third-party SaaS (§7 NFR-01)
100% LLM-call tracing sends PRDs/UserDocs for unreleased features to an external vendor; no data-governance, redaction, or retention note.
Fix: add a data-governance note (allowed data classes, retention, redaction) or gate tracing content behind a policy switch.

### Low (7)

**[Substance | Adversarial]** — Self-critique pass asserted as a control with no measurable criterion (§6.1 FR-05; §9)
Same model, one pass, against a SKILL.md that §9 admits does not exist yet — no measured improvement or rejection threshold. A ritual dressed as a control.
Fix: define what the critique checks and rejects; log a before/after signal to LangSmith.

**[Downstream usability]** — "SSG" never expanded or defined (§5, §6.1 FR-15, §9)
Central to the publish story and a named non-goal, yet never spelled out.
Fix: expand on first use and add to §6.0 Terminology.

**[Downstream usability]** — Glossary drift (§6.0 and throughout)
"UserDoc"/"User Document"; "Head of Product"/"Head-of-Product" used interchangeably.
Fix: pick one canonical form each and normalize.

**[Decision-readiness]** — Chain-top verifications deferred to build-time (§13 Q1/Q2)
Exact Jira transition path and folder-vs-parent model both shape agent design yet are punted wholesale.
Fix: resolve before Architecture, or explicitly flag them as architecture-phase inputs.

**[Adversarial]** — "Lock" via page restrictions overstates the guarantee (§6.1 FR-15)
Confluence restrictions govern who may edit, not content freeze; space admins and the agent account retain edit rights; no version pinning.
Fix: reword to the actual guarantee, or add explicit version pinning if a true freeze is required.

**[Adversarial]** — Headline success metric is a sample size of one (§3 vs §12)
"1 successful full run" declares victory on a single happy path; the §12 DoD is far more demanding.
Fix: align the success metric with the DoD (include at least one error/resume path).

**[Adversarial]** — FR-04 transitions to Done without checking state or legality (§6.1 FR-04; §13 Q1)
A ticket already Done, or one with no direct-to-Done transition from its current state (the §13 Q1 risk), will error or no-op; FR-04 assumes the transition always applies.
Fix: guard the transition (skip if already Done; resolve the legal transition path at runtime).

## Mechanical notes
- **ID continuity:** FR-01–15, NFR-01–11, EH-01–08 contiguous and unique — but §6 subsection numbering skips 6.3–6.6 (jumps 6.2 → 6.7), the one real break.
- **Broken cross-reference:** "§Phase 5/6" (§15.3) resolves to nothing; all other cross-refs checked resolve (§6.2, §6.7, §8, §10, §15.4).
- **Undefined term:** "SSG" used throughout, never expanded; absent from §6.0.
- **Glossary drift:** "UserDoc" vs "User Document"; "Head of Product" vs "Head-of-Product".
- **Assumptions convention:** no inline `[ASSUMPTION: …]` tags / Assumptions Index; assumptions live in the §13 Open-Questions table — a legitimate alternative for an adopted PRD. Noted, not penalized.
- **Required sections:** all expected sections for a chain-top technical capability spec are present; no UJ/persona sections — correct for this headless shape.

## Reviewer files
- `review-rubric.md`
- `review-adversarial-general.md`
