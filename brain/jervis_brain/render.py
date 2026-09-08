"""Turning a running turn into something worth watching at a prompt."""

from __future__ import annotations

from collections.abc import Callable

import click

from .agent import Turn
from .events import (
    ConfirmationRequested,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnEvent,
)

Writer = Callable[[str], None]


def _echo(text: str) -> None:
    click.echo(text, nl=False)


class ConsoleRenderer:
    """An `EventSink` that prints a turn as it happens.

    Instances are callable, so they can be passed straight to `Agent.ask(on_event=...)`
    with no adapter.
    """

    def __init__(self, write: Writer = _echo, *, colour: bool = True) -> None:
        self._write = write
        self._colour = colour
        self._streamed: list[str] = []
        self._at_line_start = True
        self._open_calls: set[str] = set()

    # --- sink ------------------------------------------------------------------------

    def __call__(self, event: TurnEvent) -> None:
        match event:
            case TextDelta(text=text):
                self._delta(text)
            case ToolStarted(call_id=call_id, tool=tool, summary=summary):
                self._open_calls.add(call_id)
                self._line(f"· {summary or tool}", fg="bright_black")
            case ToolFinished(call_id=call_id, tool=tool, ok=ok, detail=detail, duration_ms=ms):
                self._open_calls.discard(call_id)
                if ok:
                    self._line(f"  ✓ {tool} ({ms}ms)", fg="bright_black")
                else:
                    self._line(f"  ✗ {tool}: {_first_line(detail)}", fg="red")
            case ConfirmationRequested(call_id=call_id):
                # The tool has not run. _resume closes this line when the answer lands.
                self._open_calls.discard(call_id)

    # --- output ----------------------------------------------------------------------

    def _delta(self, text: str) -> None:
        if not text:
            return
        self._streamed.append(text)
        # Deltas are written raw and unstyled: wrapping each one in a colour would emit
        # a reset sequence per token, and a partial word is not a place to break style.
        self._write(text)
        self._at_line_start = text.endswith("\n")

    def _line(self, text: str, *, fg: str | None = None) -> None:
        if not self._at_line_start:
            self._write("\n")
        styled = click.style(text, fg=fg) if (fg and self._colour) else text
        self._write(styled + "\n")
        self._at_line_start = True

    # --- end of turn -------------------------------------------------------------------

    def finish(self, turn: Turn) -> None:
        """Print the settled answer, but only if it is not what was already streamed.

        Two things make the reply diverge from the stream: `honesty.enforce` can strip a
        claim no tool backs up, and hitting the round limit appends a note. Both depend
        on what happened during the turn, so neither can be known while streaming. In
        the ordinary case this prints nothing, because the stream already said it.
        """
        streamed = "".join(self._streamed).strip()
        reply = turn.reply.strip()
        if reply and reply != streamed:
            self._line(reply, fg="cyan" if not streamed else "yellow")
        elif not self._at_line_start:
            self._write("\n")
            self._at_line_start = True
        self.reset()

    def reset(self) -> None:
        self._streamed.clear()
        self._open_calls.clear()

    @property
    def streamed_text(self) -> str:
        return "".join(self._streamed)


def _first_line(text: str) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= 120 else line[:117] + "..."
