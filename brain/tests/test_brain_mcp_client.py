"""The MCP client pool."""

from __future__ import annotations

import sys

import pytest

from jervis_brain.config import ServerConfig
from jervis_brain.mcp_client import (
    ANTHROPIC_TOOL_NAME,
    MCPClientPool,
    RemoteTool,
    from_wire_name,
    to_wire_name,
)

pytestmark = pytest.mark.asyncio


async def test_name_round_trip() -> None:
    assert to_wire_name("macos.run_shell") == "macos__run_shell"
    assert from_wire_name("macos__run_shell") == "macos.run_shell"
    # Only the first separator is the namespace boundary.
    assert from_wire_name("macos__do__thing") == "macos.do__thing"
    assert from_wire_name(to_wire_name("macos.do__thing")) == "macos.do__thing"


async def test_every_wire_name_is_accepted_by_the_api() -> None:
    async with MCPClientPool((ServerConfig(name="macos"),), python=sys.executable) as pool:
        assert not pool.failures
        for tool in pool.tools:
            assert ANTHROPIC_TOOL_NAME.match(tool.wire_name), tool.wire_name


async def test_tools_are_namespaced_and_carry_their_tier() -> None:
    async with MCPClientPool((ServerConfig(name="macos"),), python=sys.executable) as pool:
        names = [t.qualified for t in pool.tools]
        assert "macos.run_shell" in names
        assert pool.meta_for("macos.move_to_trash") == {"x-jervis-tier": "confirm"}
        assert pool.meta_for("macos.nope") is None
        assert pool.tool("macos.list_dir") is not None


async def test_anthropic_tool_shape() -> None:
    async with MCPClientPool((ServerConfig(name="macos"),), python=sys.executable) as pool:
        tool = next(t for t in pool.anthropic_tools() if t["name"] == "macos__list_dir")
        assert set(tool) == {"name", "description", "input_schema"}
        assert tool["input_schema"]["type"] == "object"


async def test_calls_are_routed_to_the_right_server() -> None:
    async with MCPClientPool((ServerConfig(name="macos"),), python=sys.executable) as pool:
        outcome = await pool.call("macos.run_shell", {"cmd": "echo routed"})
        assert outcome.text.strip() == "routed"
        assert not outcome.is_error


async def test_unknown_server_and_tool_are_errors_not_crashes() -> None:
    async with MCPClientPool((ServerConfig(name="macos"),), python=sys.executable) as pool:
        missing_server = await pool.call("nope.thing", {})
        assert missing_server.is_error and "No such server" in missing_server.text
        missing_tool = await pool.call("macos.nope", {})
        assert missing_tool.is_error and "No such tool" in missing_tool.text


async def test_a_tool_error_comes_back_as_an_error_outcome() -> None:
    async with MCPClientPool((ServerConfig(name="macos"),), python=sys.executable) as pool:
        outcome = await pool.call("macos.read_file", {"path": "/definitely/not/here"})
        assert outcome.is_error
        assert "not a file" in outcome.text


async def test_an_image_result_becomes_an_image_block() -> None:
    """macos.screenshot returns a PNG; it must reach the model as an image."""
    async with MCPClientPool((ServerConfig(name="macos"),), python=sys.executable) as pool:
        outcome = await pool.call("macos.screenshot", {})
        if outcome.is_error and "Screen Recording" in outcome.text:
            pytest.skip("Screen Recording not granted")
        image = next(b for b in outcome.blocks if b["type"] == "image")
        assert image["source"]["media_type"] == "image/png"
        assert image["source"]["type"] == "base64"


async def test_a_broken_server_is_recorded_not_fatal() -> None:
    pool = MCPClientPool(
        (ServerConfig(name="macos"), ServerConfig(name="nonexistent")), python=sys.executable
    )
    async with pool:
        assert "nonexistent" in pool.failures
        assert any(t.server == "macos" for t in pool.tools), "the healthy server still works"


async def test_starting_twice_is_harmless() -> None:
    pool = MCPClientPool((ServerConfig(name="macos"),), python=sys.executable)
    async with pool:
        before = len(pool.tools)
        await pool.start()
        assert len(pool.tools) == before


async def test_remote_tool_naming() -> None:
    tool = RemoteTool(server="mail", name="send_mail", description="d", input_schema={})
    assert tool.qualified == "mail.send_mail"
    assert tool.wire_name == "mail__send_mail"
