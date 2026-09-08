"""A stand-in for anthropic.AsyncAnthropic that replays a scripted conversation.

Real `anthropic.types` are used for the response blocks so the agent exercises the
same attribute access, `model_dump` behaviour and block shapes as it would in
production - a hand-rolled stub would hide exactly the drift the live contract test
exists to catch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage


def text(value: str) -> TextBlock:
    return TextBlock(type="text", text=value)


def tool_use(name: str, tool_input: dict[str, Any], block_id: str = "tu_1") -> ToolUseBlock:
    return ToolUseBlock(type="tool_use", id=block_id, name=name, input=tool_input)


def _message(blocks: Sequence[Any]) -> Message:
    return Message(
        id="msg_fake",
        type="message",
        role="assistant",
        model="claude-sonnet-5",
        content=list(blocks),
        stop_reason="tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn",
        usage=Usage(input_tokens=1, output_tokens=1),
    )


# Deliberately not one chunk per block: the real API delivers text in fragments, and
# a consumer that only works when each block arrives whole is a consumer that has not
# been tested.
DELTA_CHARS = 7


class _FakeStream:
    def __init__(self, blocks: Sequence[Any]) -> None:
        self._blocks = blocks

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    @property
    def text_stream(self) -> AsyncIterator[str]:
        """Mirrors anthropic's AsyncMessageStream.text_stream: text deltas only."""
        return self._deltas()

    async def _deltas(self) -> AsyncIterator[str]:
        for block in self._blocks:
            if getattr(block, "type", None) != "text":
                continue
            text_value = block.text
            for start in range(0, len(text_value), DELTA_CHARS):
                yield text_value[start : start + DELTA_CHARS]

    async def get_final_message(self) -> Message:
        return _message(self._blocks)


class _Messages:
    def __init__(self, owner: FakeAnthropic) -> None:
        self._owner = owner

    def stream(self, **kwargs: Any) -> _FakeStream:
        self._owner.requests.append(kwargs)
        if not self._owner.script:
            raise AssertionError(
                "the agent asked for another completion but the script is exhausted; "
                f"{len(self._owner.requests)} request(s) made"
            )
        turn = self._owner.script.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return _FakeStream(turn)


class FakeAnthropic:
    """Replays `script`, one entry per model turn."""

    def __init__(self, script: Sequence[Sequence[Any] | Exception]) -> None:
        self.script: list[Any] = list(script)
        self.requests: list[dict[str, Any]] = []

    @property
    def messages(self) -> _Messages:
        return _Messages(self)

    @property
    def last_request(self) -> dict[str, Any]:
        return self.requests[-1]

    @property
    def tools_offered(self) -> list[str]:
        return [t["name"] for t in self.last_request.get("tools", [])]
