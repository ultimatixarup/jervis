"""One real call to the Anthropic API, to catch SDK or API drift.

Skipped by default. `scripts/test.sh --live` runs it. It costs a few hundred tokens
and exists to fail loudly the day the streaming or tool-use shape the agent depends
on changes underneath it - everything else in the suite talks to a fake.
"""

from __future__ import annotations

import os

import anthropic
import pytest
from anthropic.types import ToolParam

from jervis_brain.config import load_config

pytestmark = [pytest.mark.live, pytest.mark.asyncio]

TOOL: ToolParam = {
    "name": "macos__list_dir",
    "description": "List the contents of a directory.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
}


@pytest.fixture(autouse=True)
def _needs_a_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY is not set")


async def test_the_agents_request_shape_is_still_accepted() -> None:
    """Exactly the call agent._create makes, against the real API."""
    config = load_config()
    client = anthropic.AsyncAnthropic()

    async with client.messages.stream(
        model=config.model,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": "You are a terse assistant.",
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": "Use a tool when one fits."},
        ],
        tools=[TOOL],
        messages=[{"role": "user", "content": "List what is in /tmp. Use the tool."}],
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
    ) as stream:
        message = await stream.get_final_message()

    assert message.stop_reason in {"tool_use", "end_turn"}
    blocks = [b for b in message.content if b.type == "tool_use"]
    assert blocks, f"expected a tool call, got {[b.type for b in message.content]}"
    assert blocks[0].name == "macos__list_dir"
    assert isinstance(blocks[0].input, dict)
    # The agent relies on model_dump to replay assistant turns.
    assert blocks[0].model_dump(exclude_none=True)["type"] == "tool_use"


async def test_a_dotted_tool_name_is_rejected() -> None:
    """Why mcp_client rewrites `macos.list_dir` to `macos__list_dir`."""
    client = anthropic.AsyncAnthropic()
    with pytest.raises(anthropic.BadRequestError):
        await client.messages.create(
            model=load_config().model,
            max_tokens=64,
            tools=[{**TOOL, "name": "macos.list_dir"}],
            messages=[{"role": "user", "content": "hi"}],
        )
