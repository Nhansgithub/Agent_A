"""FastAPI application + the single public webhook entrypoint.

AD-8 ingress order: validate signature -> dedupe -> route -> work.
Filled in by Story 1.4 (validation) and Story 1.6 (routing).
"""

from fastapi import FastAPI

app = FastAPI(title="PRD-to-UserDoc Automation Agent Flow", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Used by the deploy smoke test (Story 6.4)."""
    return {"status": "ok"}
