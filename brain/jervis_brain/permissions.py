"""Tier classification, the confirmation flow, and the audit log.

Every tool call in Jervis passes through `Guard.execute`. Nothing else may call a
server directly.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .safety import destructive_match, find_blocked_reference, is_blocked_path

# Tool `meta` keys the servers set (see jervis_mcp_macos.tiers).
TIER_KEY = "x-jervis-tier"
ESCALATION_KEY = "x-jervis-escalates-on"

MAX_AUDIT_BYTES = 50 * 1024 * 1024
MAX_AUDIT_ARG_CHARS = 2000

CONFIRMATION_WORDS: frozenset[str] = frozenset(
    {"yes", "yeah", "yep", "confirm", "confirmed", "do it", "go ahead", "please do"}
)


class Tier(StrEnum):
    READ = "read"
    WRITE = "write"
    CONFIRM = "confirm"
    BLOCKED = "blocked"


_ORDER = {Tier.READ: 0, Tier.WRITE: 1, Tier.CONFIRM: 2, Tier.BLOCKED: 3}


def at_least(a: Tier, b: Tier) -> Tier:
    return a if _ORDER[a] >= _ORDER[b] else b


class NeedsConfirmation(Exception):
    """A confirm-tier call is waiting on a spoken yes."""

    def __init__(self, summary: str, tool: str, tool_args: dict[str, Any]) -> None:
        super().__init__(summary)
        self.summary = summary
        self.tool = tool
        self.tool_args = tool_args


class BlockedByPolicy(Exception):
    """A blocked-tier call. The model is told; nothing is executed."""


@dataclass
class ToolOutcome:
    """What a tool produced.

    `blocks` carries the raw Anthropic content blocks so a tool that returns an image
    (macos.screenshot) reaches the model as an image and not as a description of one.
    A failing tool is an outcome with `is_error`, not an exception: the model needs to
    read the error and try something else.
    """

    text: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    is_error: bool = False

    def content(self) -> Any:
        return self.blocks if self.blocks else self.text


@dataclass(frozen=True)
class Decision:
    tier: Tier
    reason: str = ""

    @property
    def needs_confirmation(self) -> bool:
        return self.tier is Tier.CONFIRM

    @property
    def blocked(self) -> bool:
        return self.tier is Tier.BLOCKED


# --- classification -----------------------------------------------------------------

# Argument names that carry a filesystem path, for the blocked-path check.
_PATH_ARGS = (
    "path",
    "cwd",
    "root",
    "directory",
    "dir",
    "file",
    "destination",
    "src",
    "dst",
    "project",
)
# Argument names that carry free text which may name a path or a command.
_TEXT_ARGS = ("cmd", "command", "script", "query")


def classify(
    tool: str,
    args: dict[str, Any],
    meta: dict[str, Any] | None = None,
    *,
    extra_destructive_patterns: Sequence[str] = (),
) -> Decision:
    """Decide the effective tier for one call.

    Starts from the tier the server declared, then raises it if the *arguments* are
    dangerous. It never lowers a declared tier.
    """
    meta = meta or {}
    declared_raw = meta.get(TIER_KEY)
    try:
        declared = Tier(str(declared_raw))
    except ValueError:
        # An untiered tool is a bug in that server. Refusing is the safe reading:
        # silently defaulting to `write` would let a new tool run unannounced.
        return Decision(Tier.BLOCKED, f"{tool} declares no tier, so it cannot be run")

    if declared is Tier.BLOCKED:
        return Decision(Tier.BLOCKED, f"{tool} is blocked by policy")

    for name in _PATH_ARGS:
        value = args.get(name)
        if isinstance(value, str) and value and is_blocked_path(value, args.get("cwd")):
            return Decision(Tier.BLOCKED, f"{value} is inside a blocked location")

    for name in _TEXT_ARGS:
        value = args.get(name)
        if not isinstance(value, str) or not value:
            continue
        reference = find_blocked_reference(value, args.get("cwd"))
        if reference is not None:
            return Decision(Tier.BLOCKED, f"{reference} is inside a blocked location")

    escalation = meta.get(ESCALATION_KEY)
    if escalation == "destructive-command":
        for name in _TEXT_ARGS:
            value = args.get(name)
            if isinstance(value, str):
                pattern = destructive_match(value, extra_destructive_patterns)
                if pattern is not None:
                    return Decision(
                        at_least(declared, Tier.CONFIRM),
                        f"matches the destructive pattern {pattern}",
                    )
    elif escalation == "overwrites-existing-file":
        path = args.get("path")
        if args.get("mode") == "overwrite" and isinstance(path, str):
            expanded = Path(os.path.expanduser(path))
            if expanded.exists():
                return Decision(at_least(declared, Tier.CONFIRM), f"{expanded} already exists")

    return Decision(declared, f"{tool} is a {declared.value} action")


def is_confirmation(text: str) -> bool:
    """PLAN.md §4 Phase 2: yes / confirm / do it / go ahead. Anything else aborts."""
    cleaned = text.strip().lower().rstrip(".!").strip()
    return cleaned in CONFIRMATION_WORDS


# --- audit log ----------------------------------------------------------------------


class AuditEntry(BaseModel):
    """One line of ~/.jervis/audit.jsonl."""

    timestamp: str
    session_id: str
    tool: str
    args: dict[str, Any]
    tier: str
    reason: str = ""
    confirmed: bool | None = None
    ok: bool = True
    result_summary: str = ""
    error: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_AUDIT_ARG_CHARS:
        return value[:MAX_AUDIT_ARG_CHARS] + f"... [{len(value):,} chars]"
    return value


class AuditLog:
    """Append-only JSONL. Writing must never break a tool call, so nothing raises."""

    def __init__(self, path: str | Path, max_bytes: int = MAX_AUDIT_BYTES) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes

    def write(self, entry: AuditEntry) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed()
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(entry.model_dump_json() + "\n")
        except Exception:
            # A logging failure must never abort a tool call, and a tool failure must
            # never lose its audit line. Both directions are tested.
            pass

    def _rotate_if_needed(self) -> None:
        if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
            return
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = self.path.with_name(f"{self.path.stem}-{stamp}{self.path.suffix}")
        # Two rotations inside the same second would otherwise rename onto the same
        # name, and Path.rename silently replaces - losing a whole archive of the
        # audit trail. Never overwrite an existing archive.
        counter = 1
        while target.exists():
            target = self.path.with_name(f"{self.path.stem}-{stamp}-{counter}{self.path.suffix}")
            counter += 1
        self.path.rename(target)

    def entries(self) -> list[AuditEntry]:
        """Read the log back. Used by tests and `jervis status`."""
        if not self.path.exists():
            return []
        out: list[AuditEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(AuditEntry.model_validate_json(line))
        return out


# --- the guard ----------------------------------------------------------------------

ToolRunner = Callable[[str, dict[str, Any]], Awaitable[ToolOutcome]]


@dataclass
class PendingConfirmation:
    tool: str
    args: dict[str, Any]
    summary: str
    reason: str
    created_at: float

    def expired(self, window_seconds: float, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) - self.created_at > window_seconds


def _shorten(value: str, limit: int = 110) -> str:
    """Shorten for speaking aloud, keeping the part that identifies the thing.

    For a path that is the *end* - truncating a long temp path from the left leaves
    ".../pytest-of-arup/test_scenario..." and drops the filename, which is precisely
    what someone needs to hear before saying yes.
    """
    if len(value) <= limit:
        return value
    if "/" in value:
        parts = value.rstrip("/").split("/")
        tail = "/".join(parts[-2:]) if len(parts) > 2 else parts[-1]
        candidate = f".../{tail}"
        return candidate if len(candidate) <= limit else "..." + tail[-(limit - 3) :]
    return value[: limit - 3] + "..."


def summarise(tool: str, args: dict[str, Any]) -> str:
    """One line, read back aloud before a confirm-tier action."""
    interesting = {k: v for k, v in args.items() if v not in (None, "", [], {})}
    for key in ("path", "cmd", "command", "script", "to", "url", "name"):
        if key in interesting:
            return f"{tool}: {_shorten(str(interesting[key]))}"
    if not interesting:
        return tool
    rendered = ", ".join(f"{k}={v}" for k, v in list(interesting.items())[:3])
    return f"{tool}: {rendered}"


class Guard:
    """Classifies, confirms, executes and audits. The only path to a tool."""

    def __init__(
        self,
        audit: AuditLog,
        *,
        confirm_window_seconds: int = 60,
        extra_destructive_patterns: Sequence[str] = (),
    ) -> None:
        self.audit = audit
        self.confirm_window_seconds = confirm_window_seconds
        self.extra_destructive_patterns = tuple(extra_destructive_patterns)

    def classify(self, tool: str, args: dict[str, Any], meta: dict[str, Any] | None) -> Decision:
        return classify(
            tool, args, meta, extra_destructive_patterns=self.extra_destructive_patterns
        )

    async def execute(
        self,
        tool: str,
        args: dict[str, Any],
        meta: dict[str, Any] | None,
        run: ToolRunner,
        *,
        session_id: str,
        confirmed: bool | None = None,
    ) -> ToolOutcome:
        """Run one tool call.

        Raises NeedsConfirmation for a confirm-tier call that has not been approved,
        and BlockedByPolicy for a blocked one. Both are audited before raising.
        """
        decision = self.classify(tool, args, meta)

        if decision.blocked:
            self._record(session_id, tool, args, decision, ok=False, error=decision.reason)
            raise BlockedByPolicy(f"This action is blocked by policy: {decision.reason}")

        if decision.needs_confirmation and confirmed is not True:
            if confirmed is False:
                self._record(
                    session_id,
                    tool,
                    args,
                    decision,
                    confirmed=False,
                    ok=False,
                    error="user declined",
                )
                return ToolOutcome("The user declined. Nothing was done.")
            raise NeedsConfirmation(summarise(tool, args), tool, args)

        started = time.monotonic()
        try:
            outcome = await run(tool, args)
        except Exception as exc:
            self._record(
                session_id,
                tool,
                args,
                decision,
                confirmed=confirmed,
                ok=False,
                error=str(exc),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise
        self._record(
            session_id,
            tool,
            args,
            decision,
            confirmed=confirmed,
            ok=not outcome.is_error,
            result_summary=outcome.text[:200],
            error=outcome.text[:200] if outcome.is_error else None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return outcome

    def _record(
        self,
        session_id: str,
        tool: str,
        args: dict[str, Any],
        decision: Decision,
        *,
        confirmed: bool | None = None,
        ok: bool = True,
        result_summary: str = "",
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self.audit.write(
            AuditEntry(
                timestamp=datetime.now(UTC).isoformat(),
                session_id=session_id,
                tool=tool,
                args={k: _truncate(v) for k, v in args.items()},
                tier=decision.tier.value,
                reason=decision.reason,
                confirmed=confirmed,
                ok=ok,
                result_summary=result_summary,
                error=error,
                duration_ms=duration_ms,
            )
        )


__all__ = [
    "AuditEntry",
    "AuditLog",
    "BlockedByPolicy",
    "Decision",
    "Guard",
    "NeedsConfirmation",
    "PendingConfirmation",
    "Tier",
    "ToolOutcome",
    "classify",
    "is_confirmation",
    "summarise",
]
