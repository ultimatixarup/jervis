"""The Claude Code tools, without ever spending money on a real session."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from jervis_mcp_claudecode import server as srv
from jervis_mcp_claudecode.settings import Settings

SUCCESS = {
    "is_error": False,
    "result": "The failing test was a stale fixture. Fixed in tests/test_x.py.",
    "session_id": "abc-123",
    "num_turns": 4,
    "total_cost_usd": 0.41,
    "permission_denials": [],
    "subtype": "success",
}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "code"
    (root / "ideap").mkdir(parents=True)
    monkeypatch.setattr(srv, "_settings", lambda: Settings(projects=(root,)))
    return root


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture the argv Claude Code would have been started with."""
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(SUCCESS), "")

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    monkeypatch.setattr(srv.shutil, "which", lambda _name: "/usr/local/bin/claude")
    return calls


# --- the flags that matter ----------------------------------------------------------


def test_no_permission_bypass_flag_appears_anywhere_in_the_source() -> None:
    """A voice agent must never be able to hand Claude Code a blank cheque."""
    source = (
        Path(srv.__file__).read_text()
        + Path(srv.__file__.replace("server.py", "settings.py")).read_text()
    )
    for flag in (
        "dangerously-skip-permissions",
        "dangerously_skip_permissions",
        "bypassPermissions",
        "--add-dir",
    ):
        assert flag not in source, f"{flag} must not appear in this server"


def test_ask_runs_read_only(workspace: Path, recorded: list[list[str]]) -> None:
    srv.ask("what does this do?", "ideap")
    argv = recorded[0]
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    allowed = argv[argv.index("--allowedTools") + 1 : argv.index("--disallowedTools")]
    assert allowed == ["Read", "Glob", "Grep"]


def test_run_task_can_edit_but_not_run_commands(workspace: Path, recorded: list[list[str]]) -> None:
    srv.run_task("fix the failing test", "ideap")
    argv = recorded[0]
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    allowed = argv[argv.index("--allowedTools") + 1 : argv.index("--disallowedTools")]
    assert "Edit" in allowed
    assert not any(t.startswith("Bash") for t in allowed)


def test_the_deny_list_is_always_passed(workspace: Path, recorded: list[list[str]]) -> None:
    srv.run_task("do something", "ideap")
    argv = recorded[0]
    denied = argv[argv.index("--disallowedTools") + 1 :]
    for pattern in ("Bash(rm:*)", "Bash(sudo:*)", "Bash(git push:*)"):
        assert pattern in denied


def test_it_runs_inside_the_project(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, json.dumps(SUCCESS), "")

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    monkeypatch.setattr(srv.shutil, "which", lambda _name: "/usr/local/bin/claude")
    srv.ask("hello", "ideap")
    assert seen["cwd"] == str(workspace / "ideap")


def test_a_session_can_be_resumed(workspace: Path, recorded: list[list[str]]) -> None:
    srv.run_task("and now add a test", "ideap", session_id="abc-123")
    assert "--resume" in recorded[0]
    assert recorded[0][recorded[0].index("--resume") + 1] == "abc-123"


def test_the_model_is_only_passed_when_configured(
    workspace: Path, recorded: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    srv.ask("q", "ideap")
    assert "--model" not in recorded[0]

    monkeypatch.setattr(
        srv, "_settings", lambda: Settings(projects=(workspace,), model="claude-opus-5")
    )
    srv.ask("q", "ideap")
    assert recorded[1][recorded[1].index("--model") + 1] == "claude-opus-5"


# --- results ------------------------------------------------------------------------


def test_the_answer_carries_the_cost_and_session(
    workspace: Path, recorded: list[list[str]]
) -> None:
    out = srv.run_task("fix it", "ideap")
    assert "stale fixture" in out
    assert "$0.41" in out
    assert "4 turns" in out
    assert "session abc-123" in out


def test_permission_denials_are_reported_not_swallowed(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {**SUCCESS, "permission_denials": [{"tool_name": "Bash"}]}
    monkeypatch.setattr(
        srv.subprocess,
        "run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 0, json.dumps(payload), ""),
    )
    monkeypatch.setattr(srv.shutil, "which", lambda _name: "/usr/local/bin/claude")
    out = srv.run_task("run the tests", "ideap")
    assert "wanted to use Bash" in out
    assert "extra_allowed_tools" in out


def test_an_error_result_says_so(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {**SUCCESS, "is_error": True, "subtype": "error_max_turns"}
    monkeypatch.setattr(
        srv.subprocess,
        "run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 0, json.dumps(payload), ""),
    )
    monkeypatch.setattr(srv.shutil, "which", lambda _name: "/usr/local/bin/claude")
    assert "reported a problem" in srv.run_task("x", "ideap")


def test_a_timeout_tells_you_to_check_git_status(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(argv: list[str], **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(srv.subprocess, "run", boom)
    monkeypatch.setattr(srv.shutil, "which", lambda _name: "/usr/local/bin/claude")
    with pytest.raises(srv.ToolError, match="git status"):
        srv.run_task("something slow", "ideap")


def test_unparseable_output_is_an_error(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        srv.subprocess,
        "run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 0, "not json", ""),
    )
    monkeypatch.setattr(srv.shutil, "which", lambda _name: "/usr/local/bin/claude")
    with pytest.raises(srv.ToolError, match="Could not read"):
        srv.ask("q", "ideap")


def test_a_missing_cli_is_explained(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srv.shutil, "which", lambda _name: None)
    with pytest.raises(srv.ToolError, match="not on PATH"):
        srv.ask("q", "ideap")


def test_an_unknown_project_is_a_tool_error(workspace: Path) -> None:
    with pytest.raises(srv.ToolError, match="don't know a project"):
        srv.ask("q", "nonsense")


# --- the read-only helpers ----------------------------------------------------------


def test_list_code_projects(workspace: Path) -> None:
    assert "ideap" in srv.list_code_projects()


def test_list_code_projects_when_there_are_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srv, "_settings", lambda: Settings(projects=(Path("/nowhere"),)))
    assert "No projects found" in srv.list_code_projects()


def test_review_changes_on_a_clean_repo(workspace: Path) -> None:
    project = workspace / "ideap"
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    assert "no uncommitted changes" in srv.review_changes("ideap")


def test_review_changes_lists_edits(workspace: Path) -> None:
    project = workspace / "ideap"
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    (project / "new.py").write_text("x = 1\n")
    assert "new.py" in srv.review_changes("ideap")


def test_review_changes_outside_a_repo(workspace: Path) -> None:
    assert "not a git repository" in srv.review_changes("ideap")


# --- over stdio ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tiers_survive_the_wire() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "jervis_mcp_claudecode"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
    tiers = {t.name: (t.meta or {}).get("x-jervis-tier") for t in listed.tools}
    assert tiers == {
        "list_code_projects": "read",
        "ask": "read",
        "run_task": "confirm",
        "review_changes": "read",
    }
    read_only = [t for t in listed.tools if t.name in {"ask", "review_changes"}]
    assert all(t.annotations and t.annotations.read_only_hint for t in read_only)
    assert all(isinstance(t, types.Tool) for t in listed.tools)


def test_the_prompt_goes_on_stdin_not_argv(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--allowedTools/--disallowedTools are variadic and eat a trailing positional."""
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["argv"] = argv
        seen["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(argv, 0, json.dumps(SUCCESS), "")

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    monkeypatch.setattr(srv.shutil, "which", lambda _name: "/usr/local/bin/claude")

    question = "what does honesty.py do?"
    srv.ask(question, "ideap")
    assert seen["input"] == question
    assert question not in seen["argv"], "a positional prompt is swallowed by the tool lists"
    # The last flag is variadic, so nothing may follow its values.
    assert seen["argv"][-1] in srv.ALWAYS_DENIED
