"""The upstream Uber Eats server, run as a child process.

Jervis owns the single child, so there is one browser session and one cookie file. The
upstream `ubereats_checkout` is never re-exposed: the only route to placing an order is
`place_order`, which checks the price first.

The session is held inside one long-lived task. stdio_client and ClientSession are
anyio context managers holding cancel scopes, and anyio requires the task that entered
a scope to exit it - the same constraint CLAUDE.md records for the brain's pool.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

log = logging.getLogger("jervis.ubereats.child")

PACKAGE = "@striderlabs/mcp-ubereats@0.2.1"
COMMAND = "npx"
ARGS = ("-y", PACKAGE)


class ChildUnavailable(RuntimeError):
    """The upstream server could not be started or reached."""


class BrowserMissing(ChildUnavailable):
    """patchright's Chromium has not been downloaded yet."""


# The upstream server answers with a boxed ASCII banner telling you to run
# `npx playwright install` - which is the wrong command, since it uses patchright.
BROWSER_HINT = (
    "Uber Eats needs its browser downloaded first. Run this once (about 150MB):\n"
    "    npx patchright install chromium"
)


def _looks_like_a_missing_browser(text: str) -> bool:
    return "Executable doesn" in text or "playwright install" in text


class Child:
    def __init__(self, command: str = COMMAND, args: tuple[str, ...] = ARGS) -> None:
        self.command = command
        self.args = args
        self._session: ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self.error: str = ""

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._serve(), name="ubereats-child")
        await self._ready.wait()
        if self._session is None:
            raise ChildUnavailable(
                f"could not start {self.command} {' '.join(self.args)}: {self.error}"
            )

    async def _serve(self) -> None:
        try:
            async with AsyncExitStack() as stack:
                params = StdioServerParameters(command=self.command, args=list(self.args))
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self._session = session
                self._ready.set()
                await self._stop.wait()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.exception("upstream Uber Eats server failed")
        finally:
            self._ready.set()
            self._session = None

    async def restart(self) -> None:
        """Bring the child back up.

        The upstream server loads cookies when it creates its browser context, so a
        session written after it started is invisible to it until it restarts.
        """
        await self.stop()
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self.error = ""
        await self.start()

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            await task

    async def call(self, name: str, args: dict[str, Any] | None = None) -> str:
        if self._session is None:
            await self.start()
        session = self._session
        if session is None:
            raise ChildUnavailable(self.error or "the Uber Eats server is not running")
        result = await session.call_tool(name, args or {})
        text = "\n".join(
            block.text
            for block in getattr(result, "content", [])
            if isinstance(block, types.TextContent)
        ).strip()
        if getattr(result, "is_error", False):
            if _looks_like_a_missing_browser(text):
                raise BrowserMissing(BROWSER_HINT)
            raise ChildUnavailable(text or f"{name} failed")
        return text or "(no output)"
