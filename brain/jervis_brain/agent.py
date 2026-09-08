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

from . import honesty, prompts, tracing
from .config import Config
from .events import (
    ConfirmationRequested,
    EventSink,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnEvent,
    discard,
)
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
    summarise,
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
    """A turn suspended waiting on a spoken yes.

    `results` and `remaining` are what make a *parallel* round resumable. A model can
    ask for several tools in one message - "delete these five things" is one message
    with five tool_use blocks - and the API requires every one of them to be answered
    in the next message. Resuming only the confirmed call leaves its siblings dangling
    and every later request in that session fails with a 400.
    """

    pending: PendingConfirmation
    messages: list[dict[str, Any]]
    tool_use_id: str
    tool_calls: list[str]
    rounds_used: int
    # Results for the blocks already executed before the pause.
    results: list[dict[str, Any]] = field(default_factory=list)
    # The blocks still to run, the pending one first.
    remaining: list[dict[str, Any]] = field(default_factory=list)
    performed_action: bool = False


@dataclass
class _Round:
    """What one pass over a message's tool_use blocks produced."""

    results: list[dict[str, Any]] | None  # None means the turn suspended
    performed_action: bool

    @property
    def suspended(self) -> bool:
        return self.results is None


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
        channel: prompts.Channel = prompts.Channel.VOICE,
    ) -> None:
        self.client = client
        self.pool = pool
        self.guard = guard
        self.memory = memory
        self.config = config
        # Asking System Events for the frontmost app costs an osascript round trip per
        # turn; tests turn it off so they neither pay it nor touch the desktop.
        self.include_frontmost = include_frontmost
        # On the Agent rather than per call: a resumed turn has to use the channel of
        # the turn it resumes, and threading it through _Paused buys nothing.
        self.channel = channel
        self._capture = config.tracing.capture_content
        self._paused: dict[str, _Paused] = {}

    # --- public API ------------------------------------------------------------------

    async def ask(
        self, text: str, session_id: str | None = None, *, on_event: EventSink = discard
    ) -> Turn:
        session_id = session_id or uuid.uuid4().hex

        paused = self._paused.get(session_id)
        if paused is not None:
            if paused.pending.expired(self.config.permissions.confirm_window_seconds):
                # PLAN.md §4 Phase 2 scenario 4: a late "yes" is a fresh utterance, not
                # an approval. Drop the pending call rather than run it.
                log.info("confirmation for %s expired; treating input as new", paused.pending.tool)
                del self._paused[session_id]
            else:
                return await self._resume(
                    session_id, paused, approved=is_confirmation(text), on_event=on_event
                )

        messages = self.memory.history(session_id)
        messages.append({"role": "user", "content": text})
        with tracing.turn_span(
            session_id, text, channel=self.channel.value, capture_content=self._capture
        ) as span:
            turn = await self._run(
                session_id, messages, tool_calls=[], rounds_used=0, on_event=on_event
            )
            tracing.record_tool_calls(span, turn.tool_calls)
            return turn

    async def confirm(self, text: str, session_id: str, *, on_event: EventSink = discard) -> Turn:
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
        with tracing.turn_span(
            session_id, text, channel=self.channel.value, capture_content=self._capture
        ) as span:
            turn = await self._resume(
                session_id, paused, approved=is_confirmation(text), on_event=on_event
            )
            tracing.record_tool_calls(span, turn.tool_calls)
            return turn

    # --- the loop ---------------------------------------------------------------------

    async def _resume(
        self,
        session_id: str,
        paused: _Paused,
        *,
        approved: bool,
        on_event: EventSink = discard,
    ) -> Turn:
        """Answer the outstanding confirmation and finish the round it interrupted.

        The round may hold several tool calls; the rest still have to run (or be
        refused) before the model can be given anything, because the API demands a
        result for every tool_use in the message.
        """
        del self._paused[session_id]
        messages = paused.messages
        tool_calls = list(paused.tool_calls)

        round_ = await self._dispatch(
            session_id,
            paused.remaining,
            messages,
            tool_calls=tool_calls,
            rounds_used=paused.rounds_used,
            on_event=on_event,
            results=paused.results,
            confirmed_first=approved,
            performed_action=paused.performed_action,
        )
        if round_.suspended:
            return self._pause_turn(session_id, tool_calls)

        messages.append({"role": "user", "content": round_.results})
        return await self._run(
            session_id,
            messages,
            tool_calls=tool_calls,
            rounds_used=paused.rounds_used,
            on_event=on_event,
            performed_action=round_.performed_action,
        )

    def _pause_turn(self, session_id: str, tool_calls: list[str]) -> Turn:
        paused = self._paused[session_id]
        # Deliberately not persisted with the dangling tool_use turn: MemoryStore
        # strips it, and the in-memory _Paused carries the real state.
        self._persist(session_id, paused.messages)
        return Turn(
            reply=f"{paused.pending.summary}. Shall I go ahead?",
            session_id=session_id,
            pending_confirmation=paused.pending.summary,
            tool_calls=tool_calls,
        )

    async def _dispatch(
        self,
        session_id: str,
        blocks: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        *,
        tool_calls: list[str],
        rounds_used: int,
        on_event: EventSink,
        results: list[dict[str, Any]] | None = None,
        confirmed_first: bool | None = None,
        performed_action: bool = False,
    ) -> _Round:
        """Run one message's worth of tool calls, or suspend on the first that needs a yes.

        `confirmed_first` applies only to blocks[0], which is how a resumed round
        carries the answer back to the exact call that asked for it.
        """
        collected = list(results or [])

        for index, block in enumerate(blocks):
            qualified = from_wire_name(str(block["name"]))
            args = dict(block.get("input") or {})
            call_id = str(block["id"])
            confirmed = confirmed_first if index == 0 else None

            if confirmed is None:
                self._emit(
                    on_event,
                    ToolStarted(
                        call_id=call_id, tool=qualified, summary=summarise(qualified, args)
                    ),
                )
            meta = self.pool.meta_for(qualified)
            started = time.monotonic()
            with tracing.tool_span(
                qualified,
                args,
                tier=str((meta or {}).get("x-jervis-tier", "")),
                capture_content=self._capture,
            ) as span:
                try:
                    outcome = await self.guard.execute(
                        qualified,
                        args,
                        meta,
                        self.pool.call,
                        session_id=session_id,
                        confirmed=confirmed,
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
                        tool_use_id=call_id,
                        tool_calls=tool_calls,
                        rounds_used=rounds_used,
                        results=collected,
                        remaining=list(blocks[index:]),
                        performed_action=performed_action,
                    )
                    # No ToolFinished here on purpose: the call has not run. It arrives
                    # once the answer comes back. See events.py.
                    self._emit(
                        on_event,
                        ConfirmationRequested(
                            call_id=call_id, tool=needs.tool, summary=needs.summary
                        ),
                    )
                    tracing.record_confirmation_requested(span, needs.summary)
                    return _Round(results=None, performed_action=performed_action)
                except BlockedByPolicy as blocked:
                    outcome = ToolOutcome(str(blocked), is_error=True)
                except Exception as exc:
                    log.exception("tool %s failed", qualified)
                    outcome = ToolOutcome(f"{type(exc).__name__}: {exc}", is_error=True)

                tracing.record_outcome(
                    span,
                    ok=not outcome.is_error,
                    detail=outcome.text,
                    confirmed=confirmed,
                    capture_content=self._capture,
                )

            self._emit(
                on_event,
                ToolFinished(
                    call_id=call_id,
                    tool=qualified,
                    ok=not outcome.is_error,
                    detail=outcome.text[:200],
                    duration_ms=int((time.monotonic() - started) * 1000),
                ),
            )
            # A declined call never ran, so it is not something Jervis did.
            # confirmed is None for an ordinary call, True when approved, False when
            # refused - only the last of those must not be recorded.
            if confirmed is not False:
                tool_calls.append(qualified)
            if not outcome.is_error and confirmed is not False and self._is_action(qualified):
                performed_action = True
            collected.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": outcome.content(),
                    "is_error": outcome.is_error,
                }
            )

        return _Round(results=collected, performed_action=performed_action)

    async def _run(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        tool_calls: list[str],
        rounds_used: int,
        on_event: EventSink = discard,
        performed_action: bool | None = None,
    ) -> Turn:
        if performed_action is None:
            performed_action = any(self._is_action(name) for name in tool_calls)
        stopped_early = False

        while True:
            if rounds_used >= self.config.max_tool_rounds:
                stopped_early = True
                log.warning("hit max_tool_rounds=%s", self.config.max_tool_rounds)
                break

            response = await self._create(messages, on_event=on_event)
            content = [self._block_to_dict(b) for b in response.content]
            messages.append({"role": "assistant", "content": content})

            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if not tool_uses:
                break

            rounds_used += 1
            round_ = await self._dispatch(
                session_id,
                tool_uses,
                messages,
                tool_calls=tool_calls,
                rounds_used=rounds_used,
                on_event=on_event,
                performed_action=performed_action,
            )
            if round_.suspended:
                return self._pause_turn(session_id, tool_calls)
            performed_action = round_.performed_action
            messages.append({"role": "user", "content": round_.results})

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

    @staticmethod
    def _emit(sink: EventSink, event: TurnEvent) -> None:
        """Hand an event to the caller. A broken renderer must never abort a turn."""
        try:
            sink(event)
        except Exception:
            log.debug("event sink raised on %r", event, exc_info=True)

    def _is_action(self, qualified: str) -> bool:
        """True for tools that change something, so an honesty check applies."""
        meta = self.pool.meta_for(qualified) or {}
        return meta.get("x-jervis-tier") in (Tier.WRITE.value, Tier.CONFIRM.value)

    def _persist(self, session_id: str, messages: Sequence[dict[str, Any]]) -> None:
        self.memory.replace_history(session_id, list(messages)[-20:])

    async def _create(
        self, messages: list[dict[str, Any]], *, on_event: EventSink = discard
    ) -> Any:
        system = prompts.build_system(
            self._recall_for(messages),
            persona_name=self.config.persona_name,
            include_frontmost=self.include_frontmost,
            channel=self.channel,
        )
        tools = self.pool.anthropic_tools()

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            with tracing.llm_span(self.config.model, capture_content=self._capture) as span:
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
                        async for chunk in stream.text_stream:
                            self._emit(on_event, TextDelta(chunk))
                        response = await stream.get_final_message()
                        tracing.record_response(span, response, capture_content=self._capture)
                        return response
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
