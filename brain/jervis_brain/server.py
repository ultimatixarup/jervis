"""localhost:7777 - typed input for the brain.

Exists so the brain is testable without a microphone, and so a phone client can be
added later without touching the agent (PLAN.md §1).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import anthropic
from fastapi import FastAPI
from pydantic import BaseModel, Field

from . import prompts, tracing
from .agent import Agent
from .config import Config, load_config
from .credentials import detect
from .mcp_client import MCPClientPool
from .memory import MemoryStore
from .permissions import AuditLog, Guard

log = logging.getLogger("jervis.server")


class AskRequest(BaseModel):
    text: str = Field(min_length=1)
    session_id: str = "default"


class ConfirmRequest(BaseModel):
    text: str = "yes"
    session_id: str = "default"


class TurnResponse(BaseModel):
    reply: str
    session_id: str
    pending_confirmation: str | None = None
    tool_calls: list[str] = Field(default_factory=list)


AUTH_HINTS = ("could not resolve authentication", "authentication_error", "x-api-key")


def spoken_error(exc: Exception) -> str:
    """Turn an exception into something worth hearing out loud.

    The voice loop speaks whatever comes back, so a 500 with a traceback becomes
    silence or noise. Every failure has to arrive as a sentence.
    """
    message = str(exc).lower()
    if isinstance(exc, anthropic.AuthenticationError) or any(h in message for h in AUTH_HINTS):
        return (
            "I can't sign in to Claude. Check that ANTHROPIC_API_KEY is set in the "
            "env file in your jervis folder."
        )
    if isinstance(exc, anthropic.RateLimitError | anthropic.OverloadedError):
        return "Claude is busy right now. Try me again in a moment."
    if isinstance(exc, anthropic.APIConnectionError):
        return "I can't reach Claude. It looks like the network is down."
    return "Something went wrong at my end. The details are in the log."


@dataclass
class Runtime:
    config: Config
    pool: MCPClientPool
    memory: MemoryStore
    agent: Agent


async def build_runtime(
    config: Config | None = None, *, channel: prompts.Channel = prompts.Channel.VOICE
) -> Runtime:
    config = config or load_config()
    pool = MCPClientPool.from_config(config)
    await pool.start()
    memory = MemoryStore(config.paths.memory_db)
    guard = Guard(
        AuditLog(config.paths.audit_log),
        confirm_window_seconds=config.permissions.confirm_window_seconds,
        extra_destructive_patterns=config.permissions.extra_destructive_patterns,
    )
    if config.tracing.enabled:
        tracing.configure(
            tracing.TracingConfig(
                enabled=True,
                endpoint=config.tracing.endpoint,
                project=config.tracing.project,
                capture_content=config.tracing.capture_content,
            )
        )

    credential = detect()
    if not credential.ok:
        # Not fatal - /health and the tool list still work - but it makes the failure
        # legible now rather than on the first thing Arup says out loud.
        log.error(
            "No Anthropic credentials (%s); every request will fail. Put "
            "ANTHROPIC_API_KEY in ~/.jervis/.env or run `ant auth login`.",
            credential.detail,
        )
    else:
        log.info("using credentials from the %s (%s)", credential.source, credential.detail)
    client = anthropic.AsyncAnthropic()
    return Runtime(
        config, pool, memory, Agent(client, pool, guard, memory, config, channel=channel)
    )


RuntimeFactory = Callable[[], Awaitable[Runtime]]


def create_app(
    runtime: Runtime | None = None, runtime_factory: RuntimeFactory | None = None
) -> FastAPI:
    """Build the app.

    `runtime_factory` is awaited inside the lifespan, so whatever it creates lives on
    the same event loop that serves requests. That matters: the MCP sessions hold
    anyio streams, and driving them from a different loop than the one they were
    created on deadlocks rather than erroring.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = runtime or (await runtime_factory() if runtime_factory else await build_runtime())
        app.state.runtime = state
        if state.pool.failures:
            log.error("some MCP servers failed to start: %s", state.pool.failures)
        try:
            yield
        finally:
            if runtime is None:
                await state.pool.stop()
                state.memory.close()
                # Flush whatever is still batched, or the last turn never shows up.
                tracing.shutdown()

    app = FastAPI(title="Jervis", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        state: Runtime = app.state.runtime
        return {
            "ok": not state.pool.failures,
            "model": state.config.model,
            "tools": [t.qualified for t in state.pool.tools],
            "server_failures": state.pool.failures,
        }

    async def _turn(handler: Callable[[], Awaitable[Any]], session_id: str) -> TurnResponse:
        """Run one exchange, converting any failure into something speakable."""
        try:
            turn = await handler()
        except Exception as exc:
            log.exception("turn failed")
            return TurnResponse(reply=spoken_error(exc), session_id=session_id)
        return TurnResponse(
            reply=turn.reply,
            session_id=turn.session_id,
            pending_confirmation=turn.pending_confirmation,
            tool_calls=turn.tool_calls,
        )

    @app.post("/ask")
    async def ask(request: AskRequest) -> TurnResponse:
        state: Runtime = app.state.runtime
        return await _turn(
            lambda: state.agent.ask(request.text, session_id=request.session_id),
            request.session_id,
        )

    @app.post("/confirm")
    async def confirm(request: ConfirmRequest) -> TurnResponse:
        state: Runtime = app.state.runtime
        return await _turn(
            lambda: state.agent.confirm(request.text, session_id=request.session_id),
            request.session_id,
        )

    return app


def main() -> None:
    import uvicorn

    config = load_config()
    config.paths.logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=config.http.host, port=config.http.port)


if __name__ == "__main__":
    main()
