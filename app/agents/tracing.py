"""LangSmith tracing for every LLM call (AD-20, NFR-01, NFR-09).

NFR-01 requires **100%** of LLM calls traced with latency, tokens, and cost. That is a structural
property here, not a discipline: tracing lives *inside* the single LLM client (`app/agents/llm.py`),
which is the only module permitted to import the Anthropic SDK. There is no code path that reaches
Claude without passing through a span.

Every span carries the run's **correlation id** and **`review_round`** (NFR-09). The redraft loop is
uncapped by design — it cannot self-spin, since each round needs a fresh human comment — so
observability *is* the guardrail. `review_round` plus per-round token cost is what makes a run that
is quietly costing money visible.

**Content gating (AD-20).** `trace_content` on the tenant config governs whether prompt and
completion *text* rides along with a trace. It defaults to false: timing, token counts, and cost are
always recorded, but document bodies are not egressed. This is the seam toward the production
redaction/retention policy, which stays deferred — and the reason the demo can only trace
non-confidential test PRDs is that this flag, not redaction, is what exists today.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class Span:
    """One traced LLM call. Outputs are attached by the caller before the span closes."""

    name: str
    metadata: dict[str, Any]
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.perf_counter)
    error: str | None = None

    @property
    def latency_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000

    def record(self, **outputs: Any) -> None:
        self.outputs.update(outputs)


class Tracer(Protocol):
    """The tracing seam. Structural, so a test can capture spans without a LangSmith account."""

    def span(self, name: str, **metadata: Any) -> Any: ...


class NullTracer:
    """Records timing and tokens to the application log only.

    The default when LangSmith is not configured. Deliberately still a *tracer* rather than a no-op
    branch in the client: if disabling tracing removed the span entirely, the "100% of calls traced"
    property would depend on configuration instead of on structure.
    """

    __slots__ = ()

    @asynccontextmanager
    async def span(self, name: str, **metadata: Any) -> AsyncIterator[Span]:
        current = Span(name=name, metadata=metadata)
        try:
            yield current
        except Exception as exc:
            current.error = str(exc)
            raise
        finally:
            logger.info(
                "llm_call name=%s latency_ms=%.0f tokens_in=%s tokens_out=%s "
                "correlation_id=%s review_round=%s%s",
                name,
                current.latency_ms,
                current.outputs.get("input_tokens"),
                current.outputs.get("output_tokens"),
                metadata.get("correlation_id"),
                metadata.get("review_round"),
                f" error={current.error}" if current.error else "",
            )


class LangSmithTracer:
    """Posts each call to LangSmith as an `llm` run (AD-20).

    A tracing failure must never fail the flow — losing observability is bad, dropping a PRD because
    a metrics backend was down is worse. Every LangSmith interaction is therefore best-effort and
    logged rather than raised.
    """

    __slots__ = ("_project", "_run_tree_factory")

    def __init__(self, project: str, *, run_tree_factory: Any = None) -> None:
        self._project = project
        self._run_tree_factory = run_tree_factory or _default_run_tree

    @asynccontextmanager
    async def span(self, name: str, **metadata: Any) -> AsyncIterator[Span]:
        current = Span(name=name, metadata=metadata)
        run = None
        try:
            run = self._run_tree_factory(
                name=name,
                run_type="llm",
                project_name=self._project,
                inputs=current.inputs,
                extra={"metadata": metadata},
            )
            run.post()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("could not start LangSmith run for %s: %s", name, exc)
            run = None

        try:
            yield current
        except Exception as exc:
            current.error = str(exc)
            raise
        finally:
            if run is not None:
                try:
                    run.end(outputs=current.outputs, error=current.error)
                    run.patch()
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("could not finish LangSmith run for %s: %s", name, exc)


def _default_run_tree(**kwargs: Any) -> Any:
    from langsmith.run_trees import RunTree

    return RunTree(**kwargs)


def build_tracer(
    *, enabled: bool, project: str, api_key: str | None, env: dict[str, str] | None = None
) -> Tracer:
    """Choose a tracer from config.

    Returns a `NullTracer` when LangSmith is disabled or unconfigured — the call is still traced to
    the log, so the "every call passes through a span" property holds either way.
    """
    if not enabled:
        return NullTracer()
    if not api_key:
        logger.warning(
            "LangSmith is enabled but no API key resolved; falling back to log-only tracing. "
            "See SETUP-GUIDE.md Part 5."
        )
        return NullTracer()

    import os

    (env if env is not None else os.environ)["LANGSMITH_API_KEY"] = api_key
    return LangSmithTracer(project)
