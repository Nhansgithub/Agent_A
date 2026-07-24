"""The shared LLM runtime — the only module that talks to Claude (AD-6, AD-20).

All six role-agents (Classifier, Ticket manager, Author, Feedback interpreter, Publisher, Error
handler) are a per-role system prompt + `SKILL.md` over *this* client, wired as graph nodes — not
separate services. That is what AD-6 means by "one shared runtime", and it is why prompt changes are
the primary quality-tuning surface rather than code changes.

Two properties are structural rather than conventional:

* **Every call is traced** (NFR-01). The span wraps the request inside this method, so there is no
  code path to Claude that skips it. `tests/test_llm_client.py` also asserts no other module imports
  the Anthropic SDK.
* **The model id comes from config** (AD-17, AD-4). Never a literal at a call site — changing the
  classifier model invalidates the holdout eval, so it has to be a visible config change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.tracing import NullTracer, Tracer
from app.domain.errors import AgentError

#: Claude prices per million tokens, for the cost figure NFR-01 requires on every trace. Approximate
#: and pinned here rather than fetched: a rough, always-present cost signal beats an exact one that
#: needs a network call on the 1 GB box. Update alongside the Stack table.
_PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-fable-5": (3.0, 15.0),
}
_DEFAULT_PRICING = (3.0, 15.0)


@dataclass(frozen=True, slots=True)
class LlmResponse:
    """One completion, plus the numbers NFR-01 requires."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    stop_reason: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        input_rate, output_rate = _PRICING_PER_MTOK.get(self.model, _DEFAULT_PRICING)
        return (self.input_tokens * input_rate + self.output_tokens * output_rate) / 1_000_000


@dataclass(frozen=True, slots=True)
class CallMetadata:
    """What ties a call to a run (AD-20).

    `review_round` rides on every span because the redraft loop is uncapped: observability is the
    guardrail, so a run quietly accumulating rounds and cost has to be visible (NFR-09).
    """

    correlation_id: str
    prd_id: str
    agent_role: str
    review_round: int = 0
    tenant: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "prd_id": self.prd_id,
            "agent_role": self.agent_role,
            "review_round": self.review_round,
            "tenant": self.tenant,
            **self.extra,
        }


class LlmClient:
    """One Anthropic client, shared by every role-agent."""

    __slots__ = ("_client", "_max_tokens", "_tracer", "_trace_content")

    def __init__(
        self,
        api_key: str,
        *,
        tracer: Tracer | None = None,
        trace_content: bool = False,
        max_tokens: int = 8192,
        client: Any = None,
    ) -> None:
        """
        Args:
            api_key: resolved from `system.anthropic_api_key_ref` (AD-4).
            tracer: the AD-20 tracing seam. Defaults to log-only.
            trace_content: AD-20 content gating. False = metadata-only traces; prompt and completion
                text are not egressed to LangSmith.
            client: injected in tests so the suite runs with no key and no network.
        """
        self._tracer = tracer or NullTracer()
        self._trace_content = trace_content
        self._max_tokens = max_tokens
        self._client = client or self._build_client(api_key)

    @staticmethod
    def _build_client(api_key: str) -> Any:
        if not api_key:
            raise AgentError(
                message="No Anthropic API key is configured.",
                suggested_fix=(
                    "Set the variable named by system.anthropic_api_key_ref in .env "
                    "(see SETUP-GUIDE.md Part 3)."
                ),
                operation="llm.init",
            )
        import anthropic  # imported here so the module loads without the SDK configured

        return anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        metadata: CallMetadata,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LlmResponse:
        """Run one completion, traced.

        Args:
            model: **from config** (AD-17) — never a literal at the call site.
            system: the role's system prompt, typically assembled with its `SKILL.md`.
            prompt: the user-turn content.
            metadata: correlation id, prd_id, agent role, and `review_round` for the trace.
        """
        span_name = f"{metadata.agent_role}.complete"

        async with self._tracer.span(span_name, **metadata.as_dict()) as span:
            if self._trace_content:
                # AD-20: only attached when the tenant has opted in to content tracing.
                span.inputs.update({"system": system, "prompt": prompt, "model": model})
            else:
                span.inputs.update(
                    {"model": model, "prompt_chars": len(prompt), "system_chars": len(system)}
                )

            try:
                message = await self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens or self._max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                    **({"temperature": temperature} if temperature is not None else {}),
                )
            except Exception as exc:
                raise self._normalize(exc, model, metadata) from exc

            response = LlmResponse(
                text=_extract_text(message),
                model=model,
                input_tokens=_usage(message, "input_tokens"),
                output_tokens=_usage(message, "output_tokens"),
                latency_ms=span.latency_ms,
                stop_reason=str(getattr(message, "stop_reason", "") or ""),
            )

            # NFR-01: latency, tokens, and cost on every span, whatever the content-gating setting.
            span.record(
                latency_ms=round(response.latency_ms, 1),
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                total_tokens=response.total_tokens,
                cost_usd=round(response.cost_usd, 6),
                stop_reason=response.stop_reason,
            )
            if self._trace_content:
                span.record(completion=response.text)
            else:
                span.record(completion_chars=len(response.text))

            return response

    @staticmethod
    def _normalize(exc: Exception, model: str, metadata: CallMetadata) -> AgentError:
        """Normalize an SDK failure into the one error type the Error handler consumes (AD-19)."""
        name = type(exc).__name__
        fixes = {
            "AuthenticationError": (
                "The Anthropic API key was rejected. Regenerate it at console.anthropic.com and "
                "update ANTHROPIC_API_KEY in .env (SETUP-GUIDE.md Part 3)."
            ),
            "RateLimitError": (
                "Anthropic is rate-limiting this key. Wait and reply to resume, or raise the "
                "account's rate limit."
            ),
            "NotFoundError": (
                f"Model {model!r} was not found. Check the model ids under system.models in "
                "config/registry.yaml (AD-17 pins them there)."
            ),
        }
        return AgentError(
            message=f"Claude call failed for {metadata.agent_role} ({name}): {exc}",
            suggested_fix=fixes.get(
                name, "Check the Anthropic status page and the service logs, then reply to resume."
            ),
            operation=f"llm.{metadata.agent_role}",
            retryable=name in {"RateLimitError", "APIConnectionError", "InternalServerError"},
            context={"model": model, "prd_id": metadata.prd_id},
        )


def _extract_text(message: Any) -> str:
    """Pull the text out of a Claude message, whose content is a list of typed blocks."""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(str(text))
    return "".join(parts)


def _usage(message: Any, field_name: str) -> int:
    usage = getattr(message, "usage", None)
    if usage is None:
        return 0
    value = getattr(usage, field_name, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(field_name)
    return int(value or 0)
