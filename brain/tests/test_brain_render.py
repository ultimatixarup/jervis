"""Turning turn events into terminal output."""

from __future__ import annotations

from jervis_brain.agent import Turn
from jervis_brain.events import (
    ConfirmationRequested,
    TextDelta,
    ToolFinished,
    ToolStarted,
)
from jervis_brain.render import ConsoleRenderer


class Capture:
    def __init__(self) -> None:
        self.chunks: list[str] = []

    def __call__(self, text: str) -> None:
        self.chunks.append(text)

    @property
    def text(self) -> str:
        return "".join(self.chunks)


def renderer() -> tuple[ConsoleRenderer, Capture]:
    out = Capture()
    return ConsoleRenderer(write=out, colour=False), out


def test_deltas_are_written_as_they_arrive() -> None:
    render, out = renderer()
    for chunk in ("Ten ", "past ", "four."):
        render(TextDelta(chunk))
    assert out.text == "Ten past four."
    assert render.streamed_text == "Ten past four."


def test_a_tool_call_gets_a_start_and_a_finish_line() -> None:
    render, out = renderer()
    render(ToolStarted(call_id="t1", tool="macos.list_dir", summary="macos.list_dir: ~/Desktop"))
    render(
        ToolFinished(
            call_id="t1", tool="macos.list_dir", ok=True, detail="3 items", duration_ms=120
        )
    )
    lines = out.text.splitlines()
    assert lines[0] == "· macos.list_dir: ~/Desktop"
    assert lines[1] == "  ✓ macos.list_dir (120ms)"


def test_a_failure_shows_why() -> None:
    render, out = renderer()
    render(ToolStarted(call_id="t1", tool="macos.read_file", summary="macos.read_file: /x"))
    render(
        ToolFinished(
            call_id="t1",
            tool="macos.read_file",
            ok=False,
            detail="not a file: /x\nsecond line",
            duration_ms=5,
        )
    )
    assert "✗ macos.read_file: not a file: /x" in out.text
    assert "second line" not in out.text, "only the first line of an error belongs inline"


def test_a_tool_line_never_lands_mid_sentence() -> None:
    """Text and tool lines interleave; a tool line must start on its own line."""
    render, out = renderer()
    render(TextDelta("Let me look."))
    render(ToolStarted(call_id="t1", tool="macos.list_dir", summary="macos.list_dir: ~"))
    assert out.text.startswith("Let me look.\n·")


def test_finish_stays_quiet_when_the_stream_already_said_it() -> None:
    render, out = renderer()
    render(TextDelta("Ten past four."))
    render.finish(Turn(reply="Ten past four.", session_id="s"))
    assert out.text.strip() == "Ten past four."


def test_finish_prints_a_reply_that_was_never_streamed() -> None:
    """What StubAgent does, and what a paused turn does."""
    render, out = renderer()
    render.finish(Turn(reply="Shall I go ahead?", session_id="s"))
    assert "Shall I go ahead?" in out.text


def test_finish_prints_a_corrected_reply() -> None:
    """honesty.enforce rewrites after the fact; the correction must be visible."""
    render, out = renderer()
    render(TextDelta("Done. I've deleted it."))
    render.finish(Turn(reply="I should be clear: I did not actually do that.", session_id="s"))
    assert "did not actually do that" in out.text


def test_finish_resets_for_the_next_turn() -> None:
    render, _ = renderer()
    render(TextDelta("first"))
    render.finish(Turn(reply="first", session_id="s"))
    assert render.streamed_text == ""


def test_a_confirmation_request_prints_nothing_itself() -> None:
    """The reply carries the question; the event only closes the open tool line."""
    render, out = renderer()
    render(ToolStarted(call_id="t1", tool="macos.move_to_trash", summary="bin it"))
    before = out.text
    render(ConfirmationRequested(call_id="t1", tool="macos.move_to_trash", summary="bin it"))
    assert out.text == before


def test_an_empty_delta_is_ignored() -> None:
    render, out = renderer()
    render(TextDelta(""))
    assert out.text == ""
