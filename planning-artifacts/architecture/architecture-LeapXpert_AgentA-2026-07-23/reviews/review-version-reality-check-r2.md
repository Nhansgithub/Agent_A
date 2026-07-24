# Reviewer Gate — Version / Reality Check (r2, 2026-07-24)

**Lens:** finalize_reviewer #1 — every committed version web-researched, not asserted from training data. **Verdict: PASS.**

## One-line verdict
Every Stack row is web-verified current; the two r2 dependency changes (drop `langgraph-checkpoint-sqlite`, add `langgraph-checkpoint` base + `litestream`) are confirmed real, licensed, compatible, and correctly pinned.

## r1 rows (unchanged) — still valid
Python 3.12, FastAPI 0.136.3, Uvicorn 0.51.0, LangGraph 1.2.9, anthropic 0.117.0, langsmith 0.10.9, markdownify 1.2.3, Caddy 2.11.4 (CVE floor ≥2.11.4), Jira v3, Confluence v2/v1, stdlib sqlite3. All verified 2026-07-23; no newer breaking pin found affecting the design.

## r2 changes — verified 2026-07-24
- **DROPPED `langgraph-checkpoint-sqlite` 3.1.0.** Correct consequence of the AD-11 rewrite: the cross-webhook `SqliteSaver` durable checkpointer is gone. This also **retires the r1 LICENSE-file caveat** (GitHub disc. 5210) — the flagged package is no longer a dependency. CONFIRMED.
- **ADD `langgraph-checkpoint` (base) 4.1.1 — MIT.** Web-verified on PyPI (released 2026-05-22, MIT). Provides `InMemorySaver`/`MemorySaver` in `langgraph.checkpoint.memory` (alias since v1.0). It is a **required transitive dependency of `langgraph` core**, whose recent pin is `langgraph-checkpoint>=3.0.1,<5` (LangChain setup docs) — so **4.1.1 is in range**. CONFIRMED compatible.
- **ADD `litestream` 0.5.15 — Apache-2.0.** Web-verified (GitHub releases, latest 2026-07-21). Streams SQLite WAL to S3-compatible storage incl. DigitalOcean Spaces. **Floor `>=0.5.4` is justified**: 0.5.4 auto-disables aws-chunked encoding, fixing the documented DO Spaces upload failures (AWS SDK Go v2 ≥1.73). CONFIRMED, pin correct.

## Notes (info, not blocking)
- **`InMemorySaver` "not for production" caveat is satisfied by design.** LangChain docs say to use `InMemorySaver` only for testing/debugging and `PostgresSaver` for production *durability*. Here it is used **only as in-invocation control-flow state**, never as the durable store (durability = the repository SQLite record + litestream). So the caveat does not apply — this is the correct use, and the spine says so explicitly (AD-6/AD-11).
- **APScheduler** (AD-22 alternative) is intentionally **not pinned** in Stack — consistent with the existing httpx treatment (pin exact version at build). No unverified version asserted.
- **Build-time confirm** (carry as open item): pin the exact `langgraph-checkpoint` patch that `langgraph` 1.2.9 resolves; 4.1.1 is current and in-range.
