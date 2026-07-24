"""Story 1.10 — LangSmith tracing harness for all LLM calls (AD-20, NFR-01, NFR-09)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.llm import CallMetadata, LlmClient, LlmResponse
from app.agents.tracing import NullTracer, Span, build_tracer
from app.domain.errors import AgentError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RecordingTracer:
    """Captures spans so tests can assert what would have reached LangSmith."""

    def __init__(self) -> None:
        self.spans: list[Span] = []

    @asynccontextmanager
    async def span(self, name: str, **metadata):
        current = Span(name=name, metadata=metadata)
        self.spans.append(current)
        try:
            yield current
        except Exception as exc:
            current.error = str(exc)
            raise


class FakeAnthropic:
    """Stands in for `anthropic.AsyncAnthropic`. No key, no network."""

    def __init__(self, *, text: str = "drafted content", error: Exception | None = None) -> None:
        self._text, self._error = text, error
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return SimpleNamespace(
            content=[SimpleNamespace(text=self._text, type="text")],
            usage=SimpleNamespace(input_tokens=1200, output_tokens=800),
            stop_reason="end_turn",
        )


def metadata(**overrides) -> CallMetadata:
    base = {
        "correlation_id": "corr-1",
        "prd_id": "page-1",
        "agent_role": "author",
        "review_round": 2,
        "tenant": "tenant_one",
    }
    return CallMetadata(**{**base, **overrides})


def build(**kwargs) -> tuple[LlmClient, RecordingTracer, FakeAnthropic]:
    tracer = RecordingTracer()
    fake = FakeAnthropic(**{k: v for k, v in kwargs.items() if k in {"text", "error"}})
    client = LlmClient(
        "test-key",
        tracer=tracer,
        trace_content=kwargs.get("trace_content", False),
        client=fake,
    )
    return client, tracer, fake


# ---------------------------------------------------------------------------------------------
# AC 1: every LLM call is traced with latency, tokens, cost, correlation id, and review_round.
# ---------------------------------------------------------------------------------------------


async def test_a_call_produces_exactly_one_span() -> None:
    client, tracer, _ = build()

    await client.complete(model="claude-sonnet-5", system="s", prompt="p", metadata=metadata())

    assert len(tracer.spans) == 1
    assert tracer.spans[0].name == "author.complete"


@pytest.mark.parametrize(
    "measurement", ["latency_ms", "input_tokens", "output_tokens", "total_tokens", "cost_usd"]
)
async def test_span_records_every_nfr01_measurement(measurement: str) -> None:
    client, tracer, _ = build()

    await client.complete(model="claude-sonnet-5", system="s", prompt="p", metadata=metadata())

    assert measurement in tracer.spans[0].outputs, f"NFR-01 requires {measurement} on every trace"


async def test_span_carries_the_correlation_id_and_review_round() -> None:
    """NFR-09 — the guardrail on the uncapped redraft loop is observability, so both must ride along."""
    client, tracer, _ = build()

    await client.complete(
        model="claude-sonnet-5", system="s", prompt="p", metadata=metadata(review_round=7)
    )

    span_metadata = tracer.spans[0].metadata
    assert span_metadata["correlation_id"] == "corr-1"
    assert span_metadata["review_round"] == 7
    assert span_metadata["prd_id"] == "page-1"
    assert span_metadata["agent_role"] == "author"


async def test_cost_is_derived_from_the_model_and_token_counts() -> None:
    client, tracer, _ = build()

    response = await client.complete(
        model="claude-sonnet-5", system="s", prompt="p", metadata=metadata()
    )

    # 1200 in @ $3/Mtok + 800 out @ $15/Mtok
    assert response.cost_usd == pytest.approx((1200 * 3 + 800 * 15) / 1_000_000)
    assert tracer.spans[0].outputs["cost_usd"] > 0


def test_an_unknown_model_still_produces_a_cost_estimate() -> None:
    """A missing price entry must not silently report zero spend."""
    assert LlmResponse(text="", model="some-future-model", input_tokens=1_000_000).cost_usd > 0


async def test_a_failing_call_is_still_traced() -> None:
    """An untraced failure is an invisible failure — the span must close either way."""
    client, tracer, _ = build(error=RuntimeError("boom"))

    with pytest.raises(AgentError):
        await client.complete(model="claude-sonnet-5", system="s", prompt="p", metadata=metadata())

    assert len(tracer.spans) == 1
    assert tracer.spans[0].error is not None


# ---------------------------------------------------------------------------------------------
# AD-20 content gating — the seam toward metadata-only tracing.
# ---------------------------------------------------------------------------------------------


async def test_content_is_not_traced_by_default() -> None:
    """The demo traces non-confidential test PRDs only; this flag is what makes that meaningful."""
    client, tracer, _ = build(trace_content=False)

    await client.complete(
        model="claude-sonnet-5",
        system="secret system prompt",
        prompt="confidential PRD body",
        metadata=metadata(),
    )

    span = tracer.spans[0]
    serialized = str(span.inputs) + str(span.outputs)
    assert "confidential PRD body" not in serialized
    assert "secret system prompt" not in serialized
    assert span.inputs["prompt_chars"] == len("confidential PRD body")


async def test_measurements_are_recorded_even_when_content_is_gated() -> None:
    """Gating content must not gate the cost/latency signal NFR-01 requires."""
    client, tracer, _ = build(trace_content=False)

    await client.complete(model="claude-sonnet-5", system="s", prompt="p", metadata=metadata())

    assert tracer.spans[0].outputs["total_tokens"] == 2000


async def test_content_is_traced_when_the_tenant_opts_in() -> None:
    client, tracer, _ = build(trace_content=True, text="the drafted guide")

    await client.complete(
        model="claude-sonnet-5", system="s", prompt="the PRD", metadata=metadata()
    )

    assert tracer.spans[0].inputs["prompt"] == "the PRD"
    assert tracer.spans[0].outputs["completion"] == "the drafted guide"


# ---------------------------------------------------------------------------------------------
# AD-6 / AD-17: one shared runtime, model id from config.
# ---------------------------------------------------------------------------------------------


async def test_the_model_is_passed_through_from_config() -> None:
    """AD-17 — pinned in config; a literal at a call site would silently invalidate the eval."""
    client, _, fake = build()

    await client.complete(model="claude-opus-4-8", system="s", prompt="p", metadata=metadata())

    assert fake.calls[0]["model"] == "claude-opus-4-8"


async def test_the_system_prompt_is_sent_as_a_system_parameter() -> None:
    client, _, fake = build()

    await client.complete(
        model="claude-sonnet-5", system="You are the Author.", prompt="p", metadata=metadata()
    )

    assert fake.calls[0]["system"] == "You are the Author."
    assert fake.calls[0]["messages"] == [{"role": "user", "content": "p"}]


def test_only_the_llm_module_imports_the_anthropic_sdk() -> None:
    """AD-6 / NFR-01 — 100% tracing holds only if there is one door to Claude.

    Matches import *statements*, not the substring: `anthropic_api_key_ref` is a legitimate config
    field name and must not trip this.
    """
    import re

    import_pattern = re.compile(r"^\s*(?:import\s+anthropic|from\s+anthropic)", re.MULTILINE)
    offenders = [
        path.relative_to(PROJECT_ROOT)
        for path in (PROJECT_ROOT / "app").rglob("*.py")
        if path.name != "llm.py" and import_pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"only app/agents/llm.py may import the Anthropic SDK; found: {offenders}"


async def test_a_missing_api_key_fails_with_setup_guidance() -> None:
    with pytest.raises(AgentError, match="No Anthropic API key"):
        LlmClient("")


# ---------------------------------------------------------------------------------------------
# AD-19: SDK failures normalize into the single AgentError the Error handler consumes.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exception_name", "expected_fix", "retryable"),
    [
        ("AuthenticationError", "Regenerate it", False),
        ("RateLimitError", "rate-limiting", True),
        ("NotFoundError", "config/registry.yaml", False),
        ("APIConnectionError", "status page", True),
    ],
)
async def test_sdk_failures_normalize_with_an_actionable_fix(
    exception_name: str, expected_fix: str, retryable: bool
) -> None:
    error_type = type(exception_name, (Exception,), {})
    client, _, _ = build(error=error_type("upstream said no"))

    with pytest.raises(AgentError) as caught:
        await client.complete(model="claude-sonnet-5", system="s", prompt="p", metadata=metadata())

    assert expected_fix in caught.value.suggested_fix
    assert caught.value.retryable is retryable
    assert caught.value.context["prd_id"] == "page-1"


# ---------------------------------------------------------------------------------------------
# Tracer selection.
# ---------------------------------------------------------------------------------------------


def test_langsmith_disabled_falls_back_to_log_only_tracing() -> None:
    tracer = build_tracer(enabled=False, project="p", api_key="key", env={})
    assert isinstance(tracer, NullTracer)


def test_langsmith_enabled_without_a_key_degrades_rather_than_crashing() -> None:
    """Missing observability must never stop the flow from running."""
    assert isinstance(build_tracer(enabled=True, project="p", api_key=None, env={}), NullTracer)


def test_langsmith_enabled_with_a_key_selects_the_langsmith_tracer() -> None:
    from app.agents.tracing import LangSmithTracer

    env: dict[str, str] = {}
    tracer = build_tracer(enabled=True, project="leapxpert", api_key="lsv2_x", env=env)

    assert isinstance(tracer, LangSmithTracer)
    assert env["LANGSMITH_API_KEY"] == "lsv2_x"


async def test_a_langsmith_outage_does_not_fail_the_run() -> None:
    """Losing observability is bad; dropping a PRD because a metrics backend is down is worse."""
    from app.agents.tracing import LangSmithTracer

    def exploding_run_tree(**_kwargs):
        raise RuntimeError("LangSmith unreachable")

    tracer = LangSmithTracer("p", run_tree_factory=exploding_run_tree)
    client = LlmClient("k", tracer=tracer, client=FakeAnthropic())

    response = await client.complete(
        model="claude-sonnet-5", system="s", prompt="p", metadata=metadata()
    )

    assert response.text == "drafted content"


async def test_the_null_tracer_still_yields_a_usable_span() -> None:
    """Tracing is structural: disabling LangSmith must not remove the span."""
    async with NullTracer().span("test", correlation_id="c") as span:
        span.record(input_tokens=1)
    assert span.outputs["input_tokens"] == 1
