"""The Claude tool-use loop.

A manual loop rather than the SDK's beta tool runner: a confirm-tier call has to
suspend the turn, hand a question back to the caller, and resume on a *later* HTTP
request. The runner owns its loop and cannot be paused across process boundaries
like that.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from . import honesty, prompts
from .config import Config
from .mcp_client import MCPClientPool, from_wire_name, to_wire_name
from .memory import MemoryStore
from .permissions import (
    BlockedByPolicy,
    Guard,
    NeedsConfirmation,
    PendingConfirmation,
    Tier,
    ToolOutcome,
    is_confirmation,
)

log = logging.getLogger("jervis.agent")

MAX_RETRIES = 4
BASE_BACKOFF_SECONDS = 1.0


class AnthropicLike(Protocol):
    """Just the surface the agent uses, so tests can supply a fake."""

    @property
    def messages(self) -> Any: ...


@dataclass
class Turn:
    """The result of one exchange."""

    reply: str
    session_id: str
    pending_confirmation: str | None = None
    tool_calls: list[str] = field(default_factory=list)
    stopped_early: bool = False

    @property
    def awaiting_confirmation(self) -> bool:
        return self.pending_confirmation is not None


@dataclass
class _Paused:
    """A turn suspended waiting on a spoken yes."""

    pending: PendingConfirmation
    messages: list[dict[str, Any]]
    tool_use_id: str
    tool_calls: list[str]
    rounds_used: int


class Agent:
    def __init__(
        self,
        client: AnthropicLike,
        pool: MCPClientPool,
        guard: Guard,
        memory: MemoryStore,
        config: Config,
        *,
        include_frontmost: bool = True,
    ) -> None:
        self.client = client
        self.pool = pool
        self.guard = guard
        self.memory = memory
        self.config = config
        # Asking System Events for the frontmost app costs an osascript round trip per
        # turn; tests turn it off so they neither pay it nor touch the desktop.
        self.include_frontmost = include_frontmost
        self._paused: dict[str, _Paused] = {}

    # --- public API ------------------------------------------------------------------

    async def ask(self, text: str, session_id: str | None = None) -> Turn:
        session_id = session_id or uuid.uuid4().hex

        paused = self._paused.get(session_id)
        if paused is not None:
            if paused.pending.expired(self.config.permissions.confirm_window_seconds):
                # PLAN.md §4 Phase 2 scenario 4: a late "yes" is a fresh utterance, not
                # an approval. Drop the pending call rather than run it.
                log.info("confirmation for %s expired; treating input as new", paused.pending.tool)
                del self._paused[session_id]
            else:
                return await self._resume(session_id, paused, approved=is_confirmation(text))

        messages = self.memory.history(session_id)
        messages.append({"role": "user", "content": text})
        return await self._run(session_id, messages, tool_calls=[], rounds_used=0)

    async def confirm(self, text: str, session_id: str) -> Turn:
        """Answer an outstanding confirmation. Anything but a yes aborts."""
        paused = self._paused.get(session_id)
        if paused is None:
            return Turn(reply="There is nothing waiting to be confirmed.", session_id=session_id)
        if paused.pending.expired(self.config.permissions.confirm_window_seconds):
            del self._paused[session_id]
            return Turn(
                reply="That confirmation timed out, so I did nothing. Ask me again if you like.",
                session_id=session_id,
            )
        return await self._resume(session_id, paused, approved=is_confirmation(text))

    # --- the loop ---------------------------------------------------------------------

    async def _resume(self, session_id: str, paused: _Paused, *, approved: bool) -> Turn:
        del self._paused[session_id]
        outcome = await self.guard.execute(
            paused.pending.tool,
            paused.pending.args,
            self.pool.meta_for(paused.pending.tool),
            self.pool.call,
            session_id=session_id,
            confirmed=approved,
        )
        tool_calls = list(paused.tool_calls)
        if approved:
            tool_calls.append(paused.pending.tool)
        messages = [
            *paused.messages,
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": paused.tool_use_id,
                        "content": outcome.content(),
                        "is_error": outcome.is_error,
                    }
                ],
            },
        ]
        return await self._run(
            session_id, messages, tool_calls=tool_calls, rounds_used=paused.rounds_used
        )

    async def _run(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        tool_calls: list[str],
        rounds_used: int,
    ) -> Turn:
        performed_action = any(self._is_action(name) for name in tool_calls)
        stopped_early = False

        while True:
            if rounds_used >= self.config.max_tool_rounds:
                stopped_early = True
                log.warning("hit max_tool_rounds=%s", self.config.max_tool_rounds)
                break

            response = await self._create(messages)
            content = [self._block_to_dict(b) for b in response.content]
            messages.append({"role": "assistant", "content": content})

            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if not tool_uses:
                break

            rounds_used += 1
            results: list[dict[str, Any]] = []
            for block in tool_uses:
                qualified = from_wire_name(str(block["name"]))
                args = dict(block.get("input") or {})
                try:
                    outcome = await self.guard.execute(
                        qualified,
                        args,
                        self.pool.meta_for(qualified),
                        self.pool.call,
                        session_id=session_id,
                    )
                except NeedsConfirmation as needs:
                    self._paused[session_id] = _Paused(
                        pending=PendingConfirmation(
                            tool=needs.tool,
                            args=needs.tool_args,
                            summary=needs.summary,
                            reason="",
                            created_at=_now(),
                        ),
                        messages=messages,
                        tool_use_id=str(block["id"]),
                        tool_calls=tool_calls,
                        rounds_used=rounds_used,
                    )
                    self._persist(session_id, messages)
                    return Turn(
                        reply=f"{needs.summary}. Shall I go ahead?",
                        session_id=session_id,
                        pending_confirmation=needs.summary,
                        tool_calls=tool_calls,
                    )
                except BlockedByPolicy as blocked:
                    outcome = ToolOutcome(str(blocked), is_error=True)
                except Exception as exc:
                    log.exception("tool %s failed", qualified)
                    outcome = ToolOutcome(f"{type(exc).__name__}: {exc}", is_error=True)

                tool_calls.append(qualified)
                if not outcome.is_error and self._is_action(qualified):
                    performed_action = True
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": outcome.content(),
                        "is_error": outcome.is_error,
                    }
                )
            messages.append({"role": "user", "content": results})

        reply = _text_of(messages[-1]) if messages else ""
        reply = honesty.enforce(reply, performed_an_action=performed_action)
        if stopped_early:
            reply = (
                f"{reply} I stopped after {self.config.max_tool_rounds} steps without "
                "finishing - ask me again if you want me to keep going."
            ).strip()

        self._persist(session_id, messages)
        return Turn(
            reply=reply,
            session_id=session_id,
            tool_calls=tool_calls,
            stopped_early=stopped_early,
        )

    # --- helpers ----------------------------------------------------------------------

    def _is_action(self, qualified: str) -> bool:
        """True for tools that change something, so an honesty check applies."""
        meta = self.pool.meta_for(qualified) or {}
        return meta.get("x-jervis-tier") in (Tier.WRITE.value, Tier.CONFIRM.value)

    def _persist(self, session_id: str, messages: Sequence[dict[str, Any]]) -> None:
        self.memory.replace_history(session_id, list(messages)[-20:])

    async def _create(self, messages: list[dict[str, Any]]) -> Any:
        system = prompts.build_system(
            self._recall_for(messages),
            persona_name=self.config.persona_name,
            include_frontmost=self.include_frontmost,
        )
        tools = self.pool.anthropic_tools()

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                async with self.client.messages.stream(
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                    system=system,
                    tools=tools,
                    messages=messages,
                    thinking={"type": "adaptive"},
                    output_config={"effort": self.config.effort},
                ) as stream:
                    return await stream.get_final_message()
            except (anthropic.RateLimitError, anthropic.OverloadedError) as exc:
                last_error = exc
                delay = BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 0.5)
                log.warning("%s; retrying in %.1fs", type(exc).__name__, delay)
                await asyncio.sleep(delay)
        raise RuntimeError(
            f"Anthropic API unavailable after {MAX_RETRIES} attempts"
        ) from last_error

    def _recall_for(self, messages: Sequence[dict[str, Any]]) -> list[Any]:
        for message in reversed(messages):
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                return self.memory.recall(str(message["content"]))
        return []

    @staticmethod
    def _block_to_dict(block: Any) -> dict[str, Any]:
        if isinstance(block, dict):
            return block
        dumped = block.model_dump(exclude_none=True)
        return {k: v for k, v in dumped.items() if k not in {"citations"}}


def _now() -> float:
    return time.monotonic()


def _text_of(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"
    ).strip()


__all__ = ["Agent", "Turn", "to_wire_name"]
