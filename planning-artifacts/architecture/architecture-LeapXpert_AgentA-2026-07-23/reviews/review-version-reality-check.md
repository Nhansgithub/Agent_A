# Reviewer lens: Web / version reality-check

**Method:** verify every committed decision was web-researched or reality-checked rather than asserted from training data — current versions, that each named tech exists and fits.

**Verdict:** PASS. Every Stack row was web-verified on 2026-07-23 (sources in `.memlog.md`). No version asserted from memory. Minor notes below.

## Verified
- **LangGraph MIT core `langgraph` 1.2.9** — PyPI License Expression = MIT (SPDX). The critical NFR-10 claim holds: `langgraph-api` (the server behind `langgraph dev`/`build`) is Elastic License 2.0; the documented OSS-compliant path is exactly "use the library + build your own FastAPI serving layer," which AD-6 mandates. Strongest possible confirmation of the bound decision.
- **FastAPI 0.136.3**, **Uvicorn 0.51.0**, **Anthropic SDK 0.117.0**, **LangSmith 0.10.9**, **markdownify 1.2.3**, **Caddy 2.11.4** — all confirmed current on PyPI / release pages. Caddy 2.11.4 also fixes CVE-2026-27585/6/7/8; pinning `>=2.11.4` is correct.
- **langgraph-checkpoint-sqlite 3.1.0** — MIT per PyPI; provides `SqliteSaver`/`AsyncSqliteSaver`. Fits the "same SQLite file via repository" reconciliation (AD-11).
- **Jira Cloud REST v3** — `GET /issue/{key}/transitions` returns only currently-legal transitions (validates AD-13); v3 comment bodies require ADF (captured in AD-7 + Conventions).
- **Confluence Cloud REST v2 + v1** — v2 folders are first-class by id; v2 `parentId` 500s on folder parents; v1 move endpoint and v1 content-restriction endpoints are the working paths (validates AD-14, AD-18).
- **Shared Atlassian accountId across Jira/Confluence within one org** — confirmed (validates AD-12).

## Notes to carry (non-blocking)
1. **langgraph-checkpoint-sqlite LICENSE-file caveat:** the 3.1.0 sdist is reported (GitHub discussion #5210) to omit the LICENSE file though PyPI declares MIT and it sits in the MIT `langgraph` monorepo. Intent is MIT; recommend a quick legal sign-off before production. Already recorded as an assumption.
2. **FastAPI:** a newer unnamed release appeared on PyPI dated 2026-07-16; spine pins 0.136.3 and says "pin exact patch at build" — correct posture.
3. **Python 3.12** cleanly satisfies every dependency's floor (FastAPI/uvicorn/langsmith need ≥3.10; anthropic supports 3.9–3.14). Good conservative choice for the slim base.
4. **Confluence webhook event names / registration mechanism** could not be fully confirmed headless (instance-dependent) — correctly carried as an open question, and it does not touch any boundary.
