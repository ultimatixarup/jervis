"""GenAI spans, and what they must never carry."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from jervis_brain import tracing
from jervis_brain.tracing import (
    LANGSMITH_ENDPOINT,
    TracingConfig,
    llm_span,
    record_outcome,
    record_response,
    record_tool_calls,
    tool_span,
    turn_span,
)


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "_provider", provider)
    yield exporter


def attrs(exporter: InMemorySpanExporter, name_starts: str) -> dict[str, Any]:
    span = next(s for s in exporter.get_finished_spans() if s.name.startswith(name_starts))
    return dict(span.attributes or {})


# --- shapes LangSmith reads -------------------------------------------------------------


def test_the_turn_span_is_a_chain_with_a_session(spans: InMemorySpanExporter) -> None:
    with turn_span("telegram:42", "what's on my desktop", channel="text"):
        pass
    a = attrs(spans, "invoke_agent")
    assert a["langsmith.span.kind"] == "chain"
    assert a["gen_ai.operation.name"] == "invoke_agent"
    assert a["langsmith.metadata.channel"] == "text"


def test_the_conversation_is_grouped_by_metadata_not_trace_session_id(
    spans: InMemorySpanExporter,
) -> None:
    """`langsmith.trace.session_id` means the *project* - a "tracer session" - so a
    conversation id there is a 404 "tracer session not found", and a non-UUID is a 422.
    A local OTLP receiver accepts either happily; only the real endpoint rejects them.
    Threads are grouped from metadata instead.
    """
    with turn_span("telegram:42", "hi", channel="text"):
        pass
    a = attrs(spans, "invoke_agent")
    assert "langsmith.trace.session_id" not in a
    for key in ("session_id", "thread_id", "conversation_id"):
        assert a[f"langsmith.metadata.{key}"] == "telegram:42"


def test_the_llm_span_carries_model_and_usage(spans: InMemorySpanExporter) -> None:
    class Usage:
        input_tokens, output_tokens = 120, 34

    class Response:
        usage = Usage()
        model = "claude-sonnet-5"
        stop_reason = "end_turn"
        content: ClassVar[list[Any]] = []

    with llm_span("claude-sonnet-5") as span:
        record_response(span, Response())

    a = attrs(spans, "chat ")
    assert a["langsmith.span.kind"] == "llm"
    assert a["gen_ai.operation.name"] == "chat"
    assert a["gen_ai.system"] == "anthropic"
    assert a["gen_ai.request.model"] == "claude-sonnet-5"
    assert a["gen_ai.usage.input_tokens"] == 120
    assert a["gen_ai.usage.output_tokens"] == 34
    assert a["gen_ai.usage.total_tokens"] == 154
    assert a["gen_ai.response.finish_reasons"] == ("end_turn",)


def test_a_response_without_usage_does_not_explode(spans: InMemorySpanExporter) -> None:
    class Bare:
        pass

    with llm_span("m") as span:
        record_response(span, Bare())
    assert "gen_ai.usage.input_tokens" not in attrs(spans, "chat ")


def test_the_tool_span_is_a_tool(spans: InMemorySpanExporter) -> None:
    """gen_ai.tool.name is what makes LangSmith classify the run as a tool."""
    with tool_span("macos.list_dir", {"path": "~/Desktop"}, tier="read") as span:
        record_outcome(span, ok=True, detail="3 items")
    a = attrs(spans, "execute_tool")
    assert a["gen_ai.tool.name"] == "macos.list_dir"
    assert a["langsmith.span.kind"] == "tool"
    assert a["langsmith.metadata.tier"] == "read"
    assert a["langsmith.metadata.ok"] is True


def test_a_failed_tool_marks_the_span_errored(spans: InMemorySpanExporter) -> None:
    with tool_span("macos.read_file", {}) as span:
        record_outcome(span, ok=False, detail="not a file: /x")
    finished = next(s for s in spans.get_finished_spans() if s.name.startswith("execute_tool"))
    assert finished.status.status_code.name == "ERROR"
    assert "not a file" in (finished.status.description or "")


def test_a_confirmed_call_records_it(spans: InMemorySpanExporter) -> None:
    with tool_span("macos.move_to_trash", {}) as span:
        record_outcome(span, ok=True, detail="Moved.", confirmed=True)
    assert attrs(spans, "execute_tool")["langsmith.metadata.confirmed"] is True


def test_a_suspended_call_is_visible_as_awaiting(spans: InMemorySpanExporter) -> None:
    with tool_span("macos.move_to_trash", {}) as span:
        tracing.record_confirmation_requested(span, "bin report.pdf")
    a = attrs(spans, "execute_tool")
    assert a["langsmith.metadata.awaiting_confirmation"] is True


def test_tool_calls_are_summarised_on_the_turn(spans: InMemorySpanExporter) -> None:
    with turn_span("s", "hi", channel="text") as span:
        record_tool_calls(span, ["macos.list_dir", "macos.read_file"])
    a = attrs(spans, "invoke_agent")
    assert a["langsmith.metadata.tool_call_count"] == 2


# --- privacy ------------------------------------------------------------------------------


def test_content_is_not_captured_by_default(spans: InMemorySpanExporter) -> None:
    """Prompts hold directory listings and message bodies; tool args hold home paths.
    None of that leaves the machine unless capture_content is explicitly on."""
    with turn_span("s", "read my private diary", channel="text"):
        pass
    with tool_span("macos.read_file", {"path": "/Users/arup/diary.txt"}):
        pass

    turn = attrs(spans, "invoke_agent")
    tool = attrs(spans, "execute_tool")
    assert "gen_ai.prompt.0.content" not in turn
    assert "tool_arguments" not in tool
    assert "diary" not in str(turn) and "diary" not in str(tool)


def test_content_is_captured_when_asked_for(spans: InMemorySpanExporter) -> None:
    with turn_span("s", "hello there", channel="text", capture_content=True):
        pass
    with tool_span("macos.read_file", {"path": "/tmp/x"}, capture_content=True):
        pass
    assert attrs(spans, "invoke_agent")["gen_ai.prompt.0.content"] == "hello there"
    assert "/tmp/x" in attrs(spans, "execute_tool")["tool_arguments"]


def test_an_error_message_is_recorded_even_without_content_capture(
    spans: InMemorySpanExporter,
) -> None:
    """A failure is Jervis's own words, not the user's data, and it is the whole
    point of looking at a trace."""
    with tool_span("macos.read_file", {}) as span:
        record_outcome(span, ok=False, detail="not a file: /x", capture_content=False)
    assert "not a file" in attrs(spans, "execute_tool")["langsmith.metadata.error"]


# --- configuration --------------------------------------------------------------------------


def test_disabled_by_default() -> None:
    assert TracingConfig().enabled is False
    assert TracingConfig().capture_content is False
    assert TracingConfig().endpoint == LANGSMITH_ENDPOINT


def test_not_usable_without_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert TracingConfig(enabled=True).usable is False


def test_usable_with_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_fake")
    assert TracingConfig(enabled=True).usable is True


def test_configure_without_a_key_does_not_turn_tracing_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setattr(tracing, "_configured", False)
    assert tracing.configure(TracingConfig(enabled=True)) is False


def test_configure_is_a_no_op_when_disabled() -> None:
    assert tracing.configure(TracingConfig(enabled=False)) is False


def test_instrumentation_is_free_when_tracing_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No provider means a no-op tracer, so the spans cost nothing."""
    monkeypatch.setattr(tracing, "_provider", None)
    with turn_span("s", "x", channel="text") as span:
        assert not span.is_recording()
