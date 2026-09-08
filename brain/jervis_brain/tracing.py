"""OpenTelemetry tracing, in the GenAI semantic conventions, shipped to LangSmith.

Three spans per turn, which is what makes a trace readable:

    invoke_agent jervis          (chain)   one whole exchange
      chat <model>               (llm)     one model call, with token usage
      execute_tool macos.list_dir (tool)   one tool call, with tier and outcome
      chat <model>                         the next round, after tool results

LangSmith ingests OTLP directly, so nothing LangChain-specific is needed. It reads
`langsmith.span.kind` to classify a run, `gen_ai.*` for model and usage, and
`langsmith.trace.session_id` to group a conversation - so a Telegram thread and a REPL
session appear as separate, continuous sessions.

**Content is not captured by default.** A prompt here contains directory listings,
message contents and, once Phase 5 lands, bank balances; a tool argument is often a
path from someone's home directory. Turning `capture_content` on sends all of that to
a third party, so it is a deliberate opt-in rather than something you get by enabling
tracing.

When tracing is disabled the OpenTelemetry API returns a no-op tracer, so the
instrumentation costs an attribute lookup and nothing else.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

log = logging.getLogger("jervis.tracing")

# LangSmith's OTLP collector. The exporter appends nothing, so /v1/traces is explicit.
LANGSMITH_ENDPOINT = "https://api.smith.langchain.com/otel/v1/traces"
LANGSMITH_EU_ENDPOINT = "https://eu.api.smith.langchain.com/otel/v1/traces"

SYSTEM = "anthropic"
# `langsmith.trace.session_id` is NOT a conversation thread: in LangSmith a "tracer
# session" is a *project*, so that attribute wants an existing project UUID and returns
# 404 "tracer session not found" for anything else. The project comes from the
# Langsmith-Project header instead. Threads are grouped from metadata, which is where
# the conversation id belongs.
THREAD_KEYS = ("session_id", "thread_id", "conversation_id")
AGENT_NAME = "jervis"
SERVICE_NAME = "jervis-brain"

_configured = False
# Set by configure(). Kept here rather than only in OpenTelemetry's global so tests can
# swap a provider in without reaching into the API's private state - doing that leaves
# a proxy pointing at itself and recurses forever.
_provider: Any = None


@dataclass(frozen=True)
class TracingConfig:
    enabled: bool = False
    endpoint: str = LANGSMITH_ENDPOINT
    project: str = "jervis"
    # Off on purpose. See the module docstring: this is prompts, file paths, message
    # bodies and balances leaving the machine.
    capture_content: bool = False
    api_key_var: str = "LANGSMITH_API_KEY"

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_var, "").strip()

    @property
    def usable(self) -> bool:
        return self.enabled and bool(self.api_key)


def configure(config: TracingConfig) -> bool:
    """Install the exporter. Returns whether tracing actually ended up on."""
    global _configured
    if _configured or not config.enabled:
        return _configured
    if not config.api_key:
        log.warning(
            "tracing is enabled but %s is not set, so nothing will be sent",
            config.api_key_var,
        )
        return False

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create({"service.name": SERVICE_NAME, "service.namespace": "jervis"})
    )
    exporter = OTLPSpanExporter(
        endpoint=config.endpoint,
        headers={"x-api-key": config.api_key, "Langsmith-Project": config.project},
    )
    # Batched and off the hot path: a slow or dead collector must never delay a turn.
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    globals()["_provider"] = provider
    _configured = True
    log.info("tracing to %s, project %r", config.endpoint, config.project)
    return True


def shutdown() -> None:
    """Flush pending spans. Called when the brain stops."""
    provider = _provider or trace.get_tracer_provider()
    flush = getattr(provider, "shutdown", None)
    if flush is not None:
        try:
            flush()
        except Exception:
            log.debug("tracer shutdown failed", exc_info=True)


def tracer() -> trace.Tracer:
    if _provider is not None:
        return _provider.get_tracer("jervis.brain")  # type: ignore[no-any-return]
    return trace.get_tracer("jervis.brain")


def _set(span: Span, key: str, value: Any) -> None:
    if value is not None:
        span.set_attribute(key, value)


# --- the three span shapes -------------------------------------------------------------


@contextmanager
def turn_span(
    session_id: str, text: str, *, channel: str, capture_content: bool = False
) -> Iterator[Span]:
    """The root span: one exchange, from question to answer."""
    with tracer().start_as_current_span(f"invoke_agent {AGENT_NAME}", kind=SpanKind.CLIENT) as span:
        _set(span, "langsmith.span.kind", "chain")
        _set(span, "langsmith.trace.name", f"{channel} turn")
        # All three keys, because LangSmith's Threads view accepts any of them and
        # which one it prefers has changed before.
        for key in THREAD_KEYS:
            _set(span, f"langsmith.metadata.{key}", session_id)
        _set(span, "gen_ai.operation.name", "invoke_agent")
        _set(span, "gen_ai.agent.name", AGENT_NAME)
        _set(span, "langsmith.metadata.channel", channel)
        if capture_content:
            _set(span, "gen_ai.prompt.0.role", "user")
            _set(span, "gen_ai.prompt.0.content", text)
        yield span


@contextmanager
def llm_span(model: str, *, capture_content: bool = False) -> Iterator[Span]:
    """One model call."""
    with tracer().start_as_current_span(f"chat {model}", kind=SpanKind.CLIENT) as span:
        _set(span, "langsmith.span.kind", "llm")
        _set(span, "gen_ai.operation.name", "chat")
        _set(span, "gen_ai.system", SYSTEM)
        _set(span, "gen_ai.request.model", model)
        del capture_content  # content is attached by record_response, if at all
        yield span


def record_response(span: Span, response: Any, *, capture_content: bool = False) -> None:
    """Attach model, usage and finish reason once the call has returned."""
    usage = getattr(response, "usage", None)
    if usage is not None:
        inputs = getattr(usage, "input_tokens", None)
        outputs = getattr(usage, "output_tokens", None)
        _set(span, "gen_ai.usage.input_tokens", inputs)
        _set(span, "gen_ai.usage.output_tokens", outputs)
        if isinstance(inputs, int) and isinstance(outputs, int):
            _set(span, "gen_ai.usage.total_tokens", inputs + outputs)
    _set(span, "gen_ai.response.model", getattr(response, "model", None))
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason:
        span.set_attribute("gen_ai.response.finish_reasons", [str(stop_reason)])
    if capture_content:
        text = "".join(
            getattr(b, "text", "") for b in getattr(response, "content", []) or []
        ).strip()
        if text:
            _set(span, "gen_ai.completion.0.role", "assistant")
            _set(span, "gen_ai.completion.0.content", text)


@contextmanager
def tool_span(
    name: str, args: dict[str, Any], *, tier: str = "", capture_content: bool = False
) -> Iterator[Span]:
    """One tool call. `gen_ai.tool.name` is what marks the run as a tool in LangSmith."""
    with tracer().start_as_current_span(f"execute_tool {name}", kind=SpanKind.INTERNAL) as span:
        _set(span, "langsmith.span.kind", "tool")
        _set(span, "gen_ai.operation.name", "execute_tool")
        _set(span, "gen_ai.tool.name", name)
        if tier:
            _set(span, "langsmith.metadata.tier", tier)
        if capture_content and args:
            import json

            _set(span, "tool_arguments", json.dumps(args, default=str)[:4000])
        yield span


def record_outcome(
    span: Span,
    *,
    ok: bool,
    detail: str = "",
    confirmed: bool | None = None,
    capture_content: bool = False,
) -> None:
    _set(span, "langsmith.metadata.ok", ok)
    if confirmed is not None:
        _set(span, "langsmith.metadata.confirmed", confirmed)
    if not ok:
        # The message is the useful half of a failure, and it is Jervis's own text
        # rather than the user's data, so it is recorded regardless of capture_content.
        span.set_status(Status(StatusCode.ERROR, detail[:200]))
        _set(span, "langsmith.metadata.error", detail[:200])
    elif capture_content and detail:
        _set(span, "gen_ai.completion.0.content", detail[:4000])


def record_confirmation_requested(span: Span, summary: str) -> None:
    """A confirm-tier call that suspended. It has not run, and may never."""
    span.add_event("gen_ai.tool.confirmation_requested", {"summary": summary[:200]})
    _set(span, "langsmith.metadata.awaiting_confirmation", True)


def record_tool_calls(span: Span, tool_calls: Sequence[str]) -> None:
    if tool_calls:
        _set(span, "langsmith.metadata.tool_calls", ", ".join(tool_calls))
        _set(span, "langsmith.metadata.tool_call_count", len(tool_calls))
