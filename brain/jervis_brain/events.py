"""Events a turn emits while it is still running.

A turn used to be strictly request in, answer out, which is fine over HTTP and useless
at a prompt: a turn that calls three tools shows nothing at all for several seconds.
These let a caller watch one happen without changing what `Agent.ask` returns.

**Ordering.** Every `ToolStarted` is followed by exactly one `ToolFinished` with the
same `call_id` - except when the turn suspends for confirmation. Then it is followed by
`ConfirmationRequested` carrying that same `call_id`, and the matching `ToolFinished`
arrives on the *resumed* turn, from `Agent.confirm`. That asymmetry is unavoidable:
`ToolStarted` is emitted before the permission guard runs, which is where the request
for confirmation comes from.

**Streamed text is not the reply.** `TextDelta`s carry every assistant message in the
turn, including the "let me check your Downloads" that precedes a tool call, whereas
`Turn.reply` is only the last one. `honesty.enforce` can also rewrite the final text
after the fact - it depends on whether an action actually ran, which is not known until
the round finishes. A renderer must therefore treat the deltas as progress and
`Turn.reply` as the answer. `render.ConsoleRenderer.finish` does exactly that.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class TextDelta:
    """A fragment of assistant text, as it arrives."""

    text: str


@dataclass(frozen=True)
class ToolStarted:
    """A tool call is about to be dispatched."""

    call_id: str
    tool: str
    summary: str


@dataclass(frozen=True)
class ToolFinished:
    """A tool call is done, whether it worked or not.

    Blocked-by-policy and outright failures both arrive here with `ok=False`; a
    separate event would buy a renderer nothing and cost every consumer a branch.
    """

    call_id: str
    tool: str
    ok: bool
    detail: str
    duration_ms: int


@dataclass(frozen=True)
class ConfirmationRequested:
    """The turn has suspended, waiting on a yes."""

    call_id: str
    tool: str
    summary: str


TurnEvent = TextDelta | ToolStarted | ToolFinished | ConfirmationRequested
EventSink = Callable[[TurnEvent], None]


def discard(_event: TurnEvent) -> None:
    """The default sink. A no-op default beats `| None` at five call sites."""
    return None
