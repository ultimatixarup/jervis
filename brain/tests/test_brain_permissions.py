"""The permission guard. PLAN.md §5 requires 100% coverage of permissions.py."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from jervis_brain.permissions import (
    AuditEntry,
    AuditLog,
    BlockedByPolicy,
    Guard,
    NeedsConfirmation,
    PendingConfirmation,
    Tier,
    ToolOutcome,
    at_least,
    classify,
    is_confirmation,
    summarise,
)

READ = {"x-jervis-tier": "read"}
WRITE = {"x-jervis-tier": "write"}
CONFIRM = {"x-jervis-tier": "confirm"}
SHELL = {"x-jervis-tier": "write", "x-jervis-escalates-on": "destructive-command"}
OVERWRITE = {"x-jervis-tier": "write", "x-jervis-escalates-on": "overwrites-existing-file"}


# --- classification -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args", "meta", "expected"),
    [
        ("macos.list_dir", {"path": "~/Desktop"}, READ, Tier.READ),
        ("macos.read_file", {"path": "~/Desktop/a.txt"}, READ, Tier.READ),
        ("macos.clipboard_get", {}, READ, Tier.READ),
        ("macos.open_app", {"name": "Safari"}, WRITE, Tier.WRITE),
        ("macos.notify", {"title": "hi"}, WRITE, Tier.WRITE),
        ("macos.set_volume", {"pct": 30}, WRITE, Tier.WRITE),
        ("macos.move_to_trash", {"path": "~/Desktop/a.txt"}, CONFIRM, Tier.CONFIRM),
        # shell: benign stays write, destructive escalates
        ("macos.run_shell", {"cmd": "ls -la"}, SHELL, Tier.WRITE),
        ("macos.run_shell", {"cmd": "git status"}, SHELL, Tier.WRITE),
        ("macos.run_shell", {"cmd": "rm -rf build"}, SHELL, Tier.CONFIRM),
        ("macos.run_shell", {"cmd": "sudo reboot"}, SHELL, Tier.CONFIRM),
        ("macos.run_shell", {"cmd": "git push --force"}, SHELL, Tier.CONFIRM),
        ("macos.run_applescript", {"script": 'tell app "Finder" to activate'}, SHELL, Tier.WRITE),
        # blocked paths win over everything
        ("macos.read_file", {"path": "~/.ssh/id_rsa"}, READ, Tier.BLOCKED),
        ("macos.list_dir", {"path": "~/Library/Keychains"}, READ, Tier.BLOCKED),
        ("macos.run_shell", {"cmd": "cat ~/.ssh/id_rsa"}, SHELL, Tier.BLOCKED),
        ("macos.write_file", {"path": "/System/x", "content": "y"}, OVERWRITE, Tier.BLOCKED),
        # claudecode: a project argument is a path, and blocked roots win
        ("claudecode.ask", {"question": "q", "project": "jervis"}, READ, Tier.READ),
        ("claudecode.run_task", {"instruction": "i", "project": "jervis"}, CONFIRM, Tier.CONFIRM),
        ("claudecode.run_task", {"instruction": "i", "project": "~/.ssh"}, CONFIRM, Tier.BLOCKED),
        ("claudecode.ask", {"question": "q", "project": "/System/Library"}, READ, Tier.BLOCKED),
        # a tool with no declared tier cannot run at all
        ("mystery.tool", {}, {}, Tier.BLOCKED),
        ("mystery.tool", {}, None, Tier.BLOCKED),
        ("mystery.tool", {}, {"x-jervis-tier": "banana"}, Tier.BLOCKED),
        # a server may declare blocked directly
        ("bank.transfer", {}, {"x-jervis-tier": "blocked"}, Tier.BLOCKED),
    ],
)
def test_classify(tool: str, args: dict[str, Any], meta: Any, expected: Tier) -> None:
    assert classify(tool, args, meta).tier is expected


def test_write_file_overwrite_escalates_only_when_the_file_exists(tmp_path: Path) -> None:
    existing = tmp_path / "there.txt"
    existing.write_text("x")
    missing = tmp_path / "not-there.txt"

    assert (
        classify("macos.write_file", {"path": str(existing), "mode": "overwrite"}, OVERWRITE).tier
        is Tier.CONFIRM
    )
    assert (
        classify("macos.write_file", {"path": str(missing), "mode": "overwrite"}, OVERWRITE).tier
        is Tier.WRITE
    )
    assert (
        classify("macos.write_file", {"path": str(existing), "mode": "create"}, OVERWRITE).tier
        is Tier.WRITE
    )


def test_extra_destructive_patterns_from_config() -> None:
    args = {"cmd": "terraform destroy -auto-approve"}
    assert classify("macos.run_shell", args, SHELL).tier is Tier.WRITE
    assert (
        classify(
            "macos.run_shell", args, SHELL, extra_destructive_patterns=[r"terraform\s+destroy"]
        ).tier
        is Tier.CONFIRM
    )


def test_a_declared_tier_is_never_lowered() -> None:
    # A confirm-tier tool with harmless arguments stays confirm.
    assert classify("macos.move_to_trash", {"path": "/tmp/x"}, CONFIRM).tier is Tier.CONFIRM


def test_decision_helpers() -> None:
    assert classify("macos.move_to_trash", {"path": "/tmp/x"}, CONFIRM).needs_confirmation
    assert classify("x.y", {}, {}).blocked
    assert not classify("macos.list_dir", {"path": "/tmp"}, READ).needs_confirmation
    assert at_least(Tier.READ, Tier.CONFIRM) is Tier.CONFIRM
    assert at_least(Tier.BLOCKED, Tier.WRITE) is Tier.BLOCKED


DESTRUCTIVE_TOKENS = ["rm", "sudo", "kill", "killall", "mkfs", "diskutil", "shred", "srm"]


@given(
    prefix=st.text(alphabet=" abcdefghijklmnopqrstuvwxyz-/", max_size=25),
    token=st.sampled_from(DESTRUCTIVE_TOKENS),
    suffix=st.text(alphabet=" abcdefghijklmnopqrstuvwxyz-/", max_size=25),
)
@settings(max_examples=300, deadline=None)
def test_any_command_containing_a_destructive_token_needs_at_least_confirmation(
    prefix: str, token: str, suffix: str
) -> None:
    """PLAN.md §4 Phase 2 property: a destructive token always escalates."""
    cmd = f"{prefix} {token} {suffix}"
    decision = classify("macos.run_shell", {"cmd": cmd}, SHELL)
    assert decision.tier in (Tier.CONFIRM, Tier.BLOCKED), cmd


# --- confirmation words -------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["yes", "Yes", "YES ", "confirm", "Confirm.", "do it", "go ahead", "yep", "yeah"]
)
def test_confirmation_words(text: str) -> None:
    assert is_confirmation(text)


@pytest.mark.parametrize(
    "text",
    [
        "no",
        "nope",
        "cancel",
        "stop",
        "wait",
        "",
        "yes but only the first one",  # a qualified yes is not a yes
        "not yet",
        "yesterday",
        "why",
    ],
)
def test_non_confirmation_words_abort(text: str) -> None:
    assert not is_confirmation(text)


def test_pending_confirmation_expiry() -> None:
    pending = PendingConfirmation("t", {}, "s", "r", created_at=1000.0)
    assert not pending.expired(60, now=1059.0)
    assert pending.expired(60, now=1061.0)
    # The default `now` is the real clock, and a just-created entry is never expired.
    assert not PendingConfirmation("t", {}, "s", "r", time.monotonic()).expired(60)


# --- summaries ----------------------------------------------------------------------


def test_summarise_leads_with_the_interesting_argument() -> None:
    assert summarise("macos.move_to_trash", {"path": "/tmp/a"}) == "macos.move_to_trash: /tmp/a"
    assert "rm -rf" in summarise("macos.run_shell", {"cmd": "rm -rf build"})
    assert summarise("macos.system_info", {}) == "macos.system_info"
    assert summarise("x.y", {"a": 1, "b": 2}) == "x.y: a=1, b=2"
    assert summarise("x.y", {"path": "p" * 200}).endswith("...")
    # Empty values are not "interesting".
    assert summarise("x.y", {"a": None, "b": ""}) == "x.y"


# --- audit log ----------------------------------------------------------------------


def test_audit_line_schema(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    log.write(
        AuditEntry(
            timestamp="2026-01-01T00:00:00+00:00",
            session_id="s1",
            tool="macos.list_dir",
            args={"path": "~/Desktop"},
            tier="read",
            ok=True,
            result_summary="3 items",
        )
    )
    raw = json.loads((tmp_path / "audit.jsonl").read_text().strip())
    assert set(raw) == {
        "timestamp",
        "session_id",
        "tool",
        "args",
        "tier",
        "reason",
        "confirmed",
        "ok",
        "result_summary",
        "error",
        "duration_ms",
    }
    assert log.entries()[0].tool == "macos.list_dir"


def test_audit_log_rotates(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, max_bytes=500)
    entry = AuditEntry(
        timestamp="t", session_id="s", tool="x", args={}, tier="read", result_summary="y" * 200
    )
    for _ in range(6):
        log.write(entry)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert "audit.jsonl" in names
    assert any(n.startswith("audit-") and n.endswith(".jsonl") for n in names)
    # Rotation is checked before each write, so the live file can exceed max_bytes by
    # at most the length of the lines written since the last rotation. What matters is
    # that it is bounded and that no entry was lost.
    line_length = len(path.read_text().splitlines()[0]) + 1
    assert path.stat().st_size <= 500 + line_length
    kept = len(path.read_text().splitlines())
    archived = sum(
        len((tmp_path / n).read_text().splitlines()) for n in names if n != "audit.jsonl"
    )
    assert kept + archived == 6


def test_audit_log_truncates_huge_arguments(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    guard = Guard(log)
    entry_args = {"content": "z" * 10_000}

    async def run(_tool: str, _args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome("ok")

    import asyncio

    asyncio.run(guard.execute("macos.write_file", entry_args, WRITE, run, session_id="s"))
    logged = log.entries()[0].args["content"]
    assert len(logged) < 2500
    assert "10,000 chars" in logged


def test_audit_log_never_raises(tmp_path: Path) -> None:
    """A logging failure must not abort a tool call."""
    unwritable = tmp_path / "a-file-not-a-dir"
    unwritable.write_text("x")
    log = AuditLog(unwritable / "audit.jsonl")
    log.write(AuditEntry(timestamp="t", session_id="s", tool="x", args={}, tier="read"))
    assert log.entries() == []


def test_audit_log_reads_back_an_absent_file(tmp_path: Path) -> None:
    assert AuditLog(tmp_path / "nothing.jsonl").entries() == []


def test_audit_log_ignores_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text('\n{"timestamp":"t","session_id":"s","tool":"x","args":{},"tier":"read"}\n\n')
    assert len(AuditLog(path).entries()) == 1


# --- the guard ----------------------------------------------------------------------


@pytest.fixture
def guard(tmp_path: Path) -> Guard:
    return Guard(AuditLog(tmp_path / "audit.jsonl"), confirm_window_seconds=60)


async def _ok(_tool: str, _args: dict[str, Any]) -> ToolOutcome:
    return ToolOutcome("did the thing")


@pytest.mark.asyncio
async def test_read_and_write_pass_straight_through(guard: Guard) -> None:
    assert await guard.execute("macos.list_dir", {"path": "/tmp"}, READ, _ok, session_id="s")
    assert await guard.execute("macos.open_app", {"name": "Safari"}, WRITE, _ok, session_id="s")
    tiers = [e.tier for e in guard.audit.entries()]
    assert tiers == ["read", "write"]
    assert all(e.confirmed is None for e in guard.audit.entries())


@pytest.mark.asyncio
async def test_confirm_tier_pauses_and_audits_nothing_yet(guard: Guard) -> None:
    with pytest.raises(NeedsConfirmation) as excinfo:
        await guard.execute("macos.move_to_trash", {"path": "/tmp/x"}, CONFIRM, _ok, session_id="s")
    assert excinfo.value.tool == "macos.move_to_trash"
    assert excinfo.value.tool_args == {"path": "/tmp/x"}
    assert "/tmp/x" in excinfo.value.summary
    # Nothing ran, so nothing is logged as having run.
    assert guard.audit.entries() == []


@pytest.mark.asyncio
async def test_confirmed_call_runs_and_records_confirmed_true(guard: Guard) -> None:
    result = await guard.execute(
        "macos.move_to_trash", {"path": "/tmp/x"}, CONFIRM, _ok, session_id="s", confirmed=True
    )
    assert result.text == "did the thing"
    entry = guard.audit.entries()[0]
    assert entry.confirmed is True and entry.ok is True and entry.tier == "confirm"


@pytest.mark.asyncio
async def test_declined_call_does_not_run_and_records_confirmed_false(guard: Guard) -> None:
    ran = False

    async def run(_t: str, _a: dict[str, Any]) -> ToolOutcome:
        nonlocal ran
        ran = True
        return ToolOutcome("should not happen")

    result = await guard.execute(
        "macos.move_to_trash", {"path": "/tmp/x"}, CONFIRM, run, session_id="s", confirmed=False
    )
    assert not ran
    assert "declined" in result.text
    entry = guard.audit.entries()[0]
    assert entry.confirmed is False and entry.ok is False and entry.error == "user declined"


@pytest.mark.asyncio
async def test_blocked_call_raises_and_is_audited(guard: Guard) -> None:
    ran = False

    async def run(_t: str, _a: dict[str, Any]) -> ToolOutcome:
        nonlocal ran
        ran = True
        return ToolOutcome("should not happen")

    with pytest.raises(BlockedByPolicy, match="blocked by policy"):
        await guard.execute("macos.read_file", {"path": "~/.ssh/id_rsa"}, READ, run, session_id="s")
    assert not ran
    entry = guard.audit.entries()[0]
    assert entry.tier == "blocked" and entry.ok is False


@pytest.mark.asyncio
async def test_a_failing_tool_is_still_audited(guard: Guard) -> None:
    async def boom(_t: str, _a: dict[str, Any]) -> ToolOutcome:
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError, match="kaboom"):
        await guard.execute("macos.open_app", {"name": "X"}, WRITE, boom, session_id="s")
    entry = guard.audit.entries()[0]
    assert entry.ok is False and entry.error == "kaboom" and entry.duration_ms is not None


@pytest.mark.asyncio
async def test_guard_passes_config_patterns_to_classify(tmp_path: Path) -> None:
    guard = Guard(
        AuditLog(tmp_path / "a.jsonl"), extra_destructive_patterns=[r"terraform\s+destroy"]
    )
    with pytest.raises(NeedsConfirmation):
        await guard.execute(
            "macos.run_shell", {"cmd": "terraform destroy"}, SHELL, _ok, session_id="s"
        )


@pytest.mark.asyncio
async def test_result_summary_is_capped(guard: Guard) -> None:
    async def chatty(_t: str, _a: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome("x" * 5000)

    await guard.execute("macos.list_dir", {"path": "/tmp"}, READ, chatty, session_id="s")
    assert len(guard.audit.entries()[0].result_summary) == 200


@pytest.mark.asyncio
async def test_an_erroring_tool_is_an_outcome_not_an_exception(guard: Guard) -> None:
    """The model must be able to read a tool error and try something else."""

    async def failing(_t: str, _a: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome("no such file", is_error=True)

    outcome = await guard.execute(
        "macos.read_file", {"path": "/tmp/x"}, READ, failing, session_id="s"
    )
    assert outcome.is_error
    entry = guard.audit.entries()[0]
    assert entry.ok is False and entry.error == "no such file"


def test_tool_outcome_content_prefers_blocks() -> None:
    assert ToolOutcome("text only").content() == "text only"
    blocks = [{"type": "text", "text": "hi"}]
    assert ToolOutcome("hi", blocks=blocks).content() == blocks


@pytest.mark.parametrize(
    ("value", "must_contain"),
    [
        (
            "/private/var/folders/tq/bm0fpjvj56191z0hkwzvh2g80000gn/T/pytest-of-arup/"
            "test_scenario_2_delete_then_ye0/report.pdf",
            "report.pdf",
        ),
        ("/Users/arup/Desktop/Q4 numbers final FINAL v3 (revised).xlsx", ".xlsx"),
    ],
)
def test_summarise_keeps_the_filename_of_a_long_path(value: str, must_contain: str) -> None:
    """You cannot approve what you cannot hear."""
    summary = summarise("macos.move_to_trash", {"path": value})
    assert must_contain in summary
    assert len(summary) < 160
