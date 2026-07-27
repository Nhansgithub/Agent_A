# Agent B Q&A golden set (S-B9)

`golden.example.json` is a **template** for the answer-quality gate. Unlike the classifier fixtures
(self-contained labelled pages), the Q&A golden set references **real Confluence page ids** from the
deployed KB, so it can only be finalized once the KB is live (B-4/B-9).

## How to build the real golden set

1. Pull + index the vault at least once (`scripts/run_agent_b_pull.py`).
2. Copy `golden.example.json` → `golden.json` (gitignore or commit per the owner's call).
3. For each question, fill `expected_page_ids` with the note page id(s) that actually hold the answer
   (the `page_id` in each note's frontmatter). Mark questions the KB has no doc for with
   `"expect_refusal": true`.
4. Aim for a spread: single-doc lookups, multi-doc synthesis, and several *unanswerable* questions —
   the refusal cases are what prove the bot won't fabricate (AD-30).

## Running the gate

```bash
.venv/bin/python scripts/run_agent_b_eval.py --golden fixtures/agent_b/golden.json
```

The bar (`agent_b/eval.py`): **refusal accuracy = 1.0** and **source recall ≥ 0.8**. The harness logic
is unit-tested offline (`tests/test_agent_b_eval.py`); this live run needs an API key + the indexed vault.
