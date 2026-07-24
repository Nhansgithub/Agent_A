# LeapXpert_AgentA — PRD-to-UserDoc Automation Agent Flow

A multi-tenant Python service that turns a finalized PRD in Confluence into a published, human-approved
end-user help document — with Jira and Confluence as the **entire** human interface. There is no GUI.

```
Confluence page-created ──▶ detect ─▶ classify ─▶ draft ─▶ [Reviewer PM review loop]
                                                       ─▶ PASS ─▶ [Head of Product gate]
                                                              ─▶ restrict + move + export .md
```

Two human gates hold the whole flow: the agent **detects** a human moving a ticket to Done, and never
moves a gate ticket itself. A run with no human action parks indefinitely and consumes nothing.

## Where things are

| | |
|---|---|
| **Project contract — read first** | [CLAUDE.md](CLAUDE.md) |
| **Build state / resume pointer** | [implementation-state/STATE.md](implementation-state/STATE.md) |
| **Story backlog & status** | [implementation-state/EPIC-STORY-TRACKER.md](implementation-state/EPIC-STORY-TRACKER.md) |
| **What's waiting on a human** | [implementation-state/BLOCKERS.md](implementation-state/BLOCKERS.md) |
| **Requirements (source of truth)** | [planning-artifacts/](planning-artifacts/) — PRD, Architecture Spine, solution design, epics |

## Developing

Requires Python 3.12 (matching the `python:3.12-slim` deploy base).

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest          # no network, no credentials required
.venv/bin/python -m ruff check .
```

The test suite enforces the architecture, not just behaviour: `tests/test_architecture_boundaries.py`
runs the import-linter contracts (AD-1 inward-only dependencies — only adapters open HTTP sockets, only
the repository runs SQL), and `tests/test_stack_and_licensing.py` pins the Stack table and fails the
build if the Elastic-licensed `langgraph-api` server product ever enters the dependency tree (NFR-10).

## Running

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In production FastAPI binds to localhost behind Caddy, which terminates TLS on :443 — a single Uvicorn
worker on a 1 GB DigitalOcean Droplet (AD-21). The image is built **off** the box and pulled; building
on 1 GB can OOM.
