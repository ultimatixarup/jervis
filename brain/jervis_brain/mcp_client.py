"""Spawns the MCP servers, aggregates their tools, routes calls back to them."""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from .config import Config, ServerConfig
from .permissions import ToolOutcome

# Anthropic tool names must match ^[a-zA-Z0-9_-]{1,128}$ - a dot is rejected. PLAN.md
# §4 Phase 2 writes qualified names as `macos.run_shell`, which stays the canonical
# form everywhere a human reads one (config, audit log, tests, e2e scenarios); only
# the name sent over the wire is rewritten, and converted straight back on the way in.
WIRE_SEPARATOR = "__"
ANTHROPIC_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def to_wire_name(qualified: str) -> str:
    return qualified.replace(".", WIRE_SEPARATOR)


def from_wire_name(wire: str) -> str:
    return wire.replace(WIRE_SEPARATOR, ".", 1)


@dataclass(frozen=True)
class RemoteTool:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def qualified(self) -> str:
        return f"{self.server}.{self.name}"

    @property
    def wire_name(self) -> str:
        return to_wire_name(self.qualified)

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "name": self.wire_name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ServerStartupError(RuntimeError):
    """A configured server could not be started."""


def _blocks_from(result: types.CallToolResult) -> tuple[str, list[dict[str, Any]]]:
    """Convert an MCP result into Anthropic content blocks plus a text summary."""
    blocks: list[dict[str, Any]] = []
    texts: list[str] = []
    for block in result.content:
        if isinstance(block, types.TextContent):
            blocks.append({"type": "text", "text": block.text})
            texts.append(block.text)
        elif isinstance(block, types.ImageContent):
            # MCP already carries image data base64-encoded, which is exactly the
            # shape an Anthropic image block wants.
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block.mime_type,
                        "data": block.data,
                    },
                }
            )
            texts.append(f"[{block.mime_type} image]")
        else:
            texts.append(f"[unsupported content: {getattr(block, 'type', 'unknown')}]")
            blocks.append({"type": "text", "text": texts[-1]})
    return "\n".join(texts), blocks


class MCPClientPool:
    """One stdio subprocess per enabled server, for the lifetime of the brain."""

    def __init__(self, servers: Sequence[ServerConfig], *, python: str | None = None) -> None:
        self.servers = tuple(servers)
        self.python = python or sys.executable
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, RemoteTool] = {}
        self.failures: dict[str, str] = {}
        self._supervisor: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()

    @classmethod
    def from_config(cls, config: Config) -> MCPClientPool:
        return cls(config.enabled_servers)

    async def __aenter__(self) -> MCPClientPool:
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()

    async def start(self) -> None:
        """Bring every enabled server up and wait until its tools are known."""
        if self._supervisor is not None:
            return
        self._supervisor = asyncio.create_task(self._serve(), name="jervis-mcp-pool")
        await self._ready.wait()

    async def _serve(self) -> None:
        """Own every subprocess for its whole lifetime, inside one task.

        stdio_client and ClientSession are anyio context managers holding cancel
        scopes, and anyio requires a scope to be exited by the task that entered it.
        An AsyncExitStack that one task enters and another closes raises "attempted to
        exit cancel scope in a different task" - which is exactly what happens when
        setup and teardown land in different tasks. Keeping the stack inside this one
        long-lived task makes that structurally impossible.
        """
        try:
            async with AsyncExitStack() as stack:
                for server in self.servers:
                    try:
                        await self._start_one(stack, server)
                    except Exception as exc:
                        # One broken server must not take the whole agent down; the
                        # rest of Jervis stays usable and /health reports `failures`.
                        self.failures[server.name] = f"{type(exc).__name__}: {exc}"
                self._ready.set()
                await self._stop.wait()
        finally:
            # Never leave start() waiting on a supervisor that has already died.
            self._ready.set()

    async def _start_one(self, stack: AsyncExitStack, server: ServerConfig) -> None:
        params = StdioServerParameters(command=self.python, args=["-m", server.import_module])
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._sessions[server.name] = session

        listed = await session.list_tools()
        for tool in listed.tools:
            remote = RemoteTool(
                server=server.name,
                name=tool.name,
                description=tool.description or tool.name,
                input_schema=tool.input_schema,
                meta=dict(tool.meta or {}),
            )
            self._tools[remote.qualified] = remote

    async def stop(self) -> None:
        self._stop.set()
        supervisor, self._supervisor = self._supervisor, None
        if supervisor is not None:
            await supervisor
        self._sessions.clear()
        self._tools.clear()

    # --- tools ---------------------------------------------------------------------

    @property
    def tools(self) -> list[RemoteTool]:
        return sorted(self._tools.values(), key=lambda t: t.qualified)

    def tool(self, qualified: str) -> RemoteTool | None:
        return self._tools.get(qualified)

    def meta_for(self, qualified: str) -> dict[str, Any] | None:
        tool = self._tools.get(qualified)
        return tool.meta if tool else None

    def anthropic_tools(self) -> list[dict[str, Any]]:
        return [tool.to_anthropic() for tool in self.tools]

    # --- calling -------------------------------------------------------------------

    async def call(self, qualified: str, args: dict[str, Any]) -> ToolOutcome:
        server_name, _, tool_name = qualified.partition(".")
        session = self._sessions.get(server_name)
        if session is None:
            return ToolOutcome(f"No such server: {server_name}", is_error=True)
        if qualified not in self._tools:
            return ToolOutcome(f"No such tool: {qualified}", is_error=True)

        # call_tool only returns InputRequiredResult when allow_input_required=True,
        # which Jervis never sets: a server must not be able to prompt the user
        # directly, bypassing the permission guard.
        result = await session.call_tool(tool_name, args)
        text, blocks = _blocks_from(result)
        return ToolOutcome(text=text, blocks=blocks, is_error=bool(result.is_error))
