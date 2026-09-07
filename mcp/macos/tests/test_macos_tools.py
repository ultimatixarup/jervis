"""In-process tests for the tool functions.

The stdio tests in test_macos_server.py prove the wiring; these prove the behaviour,
with sharper failures and without a subprocess (which coverage cannot see into).
Side-effecting helpers are stubbed so the suite never touches the real clipboard,
volume or notification centre.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jervis_mcp_macos import server as srv
from jervis_mcp_macos.safety import BlockedByPolicy


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every subprocess and osascript call instead of making it."""
    recorded: list[Any] = []

    def fake_run(argv: list[str], **kwargs: Any) -> str:
        recorded.append(("run", argv, kwargs))
        return ""

    def fake_osascript(script: str, **kwargs: Any) -> str:
        recorded.append(("osascript", script, kwargs))
        return "ok"

    monkeypatch.setattr(srv, "_run", fake_run)
    monkeypatch.setattr(srv, "_osascript", fake_osascript)
    return recorded


# --- shell -------------------------------------------------------------------------


def test_run_shell_captures_stdout_and_stderr(tmp_path: Path) -> None:
    assert srv.run_shell("echo out; echo err >&2", cwd=str(tmp_path)) == "out\nerr"


def test_run_shell_reports_the_exit_code() -> None:
    assert srv.run_shell("exit 7").startswith("exit 7")


def test_run_shell_says_so_when_there_is_no_output() -> None:
    assert srv.run_shell("true") == "(no output)"


def test_run_shell_rejects_a_missing_cwd(tmp_path: Path) -> None:
    with pytest.raises(srv.ToolFailure, match="cwd does not exist"):
        srv.run_shell("pwd", cwd=str(tmp_path / "nope"))


def test_run_shell_times_out() -> None:
    with pytest.raises(srv.ToolFailure, match="timed out"):
        srv.run_shell("sleep 5", timeout=1)


def test_run_shell_refuses_blocked_paths() -> None:
    with pytest.raises(BlockedByPolicy):
        srv.run_shell("cat ~/.ssh/id_rsa")


def test_run_applescript_refuses_blocked_paths() -> None:
    with pytest.raises(BlockedByPolicy):
        srv.run_applescript('do shell script "ls ~/Library/Keychains"')


def test_run_applescript_returns_the_result(calls: list[Any]) -> None:
    assert srv.run_applescript("return 1 + 1") == "ok"
    assert calls[0][0] == "osascript"


# --- apps --------------------------------------------------------------------------


def test_open_app(calls: list[Any]) -> None:
    assert srv.open_app("Safari") == "Opened Safari."
    assert calls[0][1] == ["open", "-a", "Safari"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://x"])
def test_open_url_rejects_other_schemes(url: str, calls: list[Any]) -> None:
    with pytest.raises(srv.ToolFailure, match="http"):
        srv.open_url(url)
    assert not calls


def test_open_url_accepts_https(calls: list[Any]) -> None:
    srv.open_url("https://example.com")
    assert calls[0][1] == ["open", "https://example.com"]


# --- files -------------------------------------------------------------------------


def test_list_dir_lists_directories_first(tmp_path: Path) -> None:
    (tmp_path / "b_dir").mkdir()
    (tmp_path / "a_file.txt").write_text("x")
    lines = srv.list_dir(str(tmp_path)).splitlines()
    assert "2 items" in lines[0]
    assert lines[1].strip() == "b_dir/"
    assert "a_file.txt" in lines[2]


def test_list_dir_on_an_empty_directory(tmp_path: Path) -> None:
    assert srv.list_dir(str(tmp_path)).endswith("is empty.")


def test_list_dir_rejects_a_file(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("x")
    with pytest.raises(srv.ToolFailure, match="not a directory"):
        srv.list_dir(str(target))


def test_read_file_rejects_a_directory(tmp_path: Path) -> None:
    with pytest.raises(srv.ToolFailure, match="not a file"):
        srv.read_file(str(tmp_path))


def test_read_file_replaces_undecodable_bytes(tmp_path: Path) -> None:
    target = tmp_path / "binary.bin"
    target.write_bytes(b"ok\xff\xfe")
    assert srv.read_file(str(target)).startswith("ok")


def test_write_file_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "atomic.txt"
    srv.write_file(str(target), "one")
    srv.write_file(str(target), "two", mode="overwrite")
    assert target.read_text() == "two"
    assert [p.name for p in tmp_path.iterdir()] == ["atomic.txt"]


def test_write_file_rejects_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(srv.ToolFailure, match="directory does not exist"):
        srv.write_file(str(tmp_path / "nope" / "f.txt"), "x")


def test_write_file_append_creates_then_appends(tmp_path: Path) -> None:
    target = tmp_path / "log.txt"
    srv.write_file(str(target), "a", mode="append")
    srv.write_file(str(target), "b", mode="append")
    assert target.read_text() == "ab"


def test_search_files_filters_blocked_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    home = str(Path.home())
    monkeypatch.setattr(
        srv, "_run", lambda *_a, **_k: f"{home}/.ssh/id_rsa\n{home}/Desktop/notes.txt\n"
    )
    result = srv.search_files("notes")
    assert "notes.txt" in result
    assert ".ssh" not in result


def test_search_files_reports_no_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srv, "_run", lambda *_a, **_k: "\n")
    assert "No Spotlight results" in srv.search_files("zzzz")


def test_search_files_caps_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srv, "_run", lambda *_a, **_k: "\n".join(f"/tmp/f{i}" for i in range(50)))
    result = srv.search_files("f", limit=10)
    assert "[showing 10 of 50]" in result


def test_move_to_trash_rejects_a_missing_file(tmp_path: Path, calls: list[Any]) -> None:
    with pytest.raises(srv.ToolFailure, match="nothing at"):
        srv.move_to_trash(str(tmp_path / "ghost"))
    assert not calls


def test_move_to_trash_uses_finder_not_rm(tmp_path: Path, calls: list[Any]) -> None:
    target = tmp_path / "doomed.txt"
    target.write_text("x")
    srv.move_to_trash(str(target))
    kind, script, _ = calls[0]
    assert kind == "osascript"
    assert "Finder" in script and "delete POSIX file" in script
    assert "rm " not in script


def test_move_to_trash_escapes_quotes_in_the_path(tmp_path: Path, calls: list[Any]) -> None:
    target = tmp_path / 'we"ird.txt'
    target.write_text("x")
    srv.move_to_trash(str(target))
    assert '\\"' in calls[0][1]


# --- desktop -----------------------------------------------------------------------


def test_notify_escapes_quotes(calls: list[Any]) -> None:
    srv.notify('a "quoted" title', "body \\ with backslash")
    script = calls[0][1]
    assert '\\"quoted\\"' in script
    assert "\\\\" in script


def test_clipboard_get_reports_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srv, "_run", lambda *_a, **_k: "")
    assert srv.clipboard_get() == "(clipboard is empty)"


def test_clipboard_set_pipes_via_stdin(calls: list[Any]) -> None:
    srv.clipboard_set("hello")
    _, argv, kwargs = calls[0]
    assert argv == ["pbcopy"]
    assert kwargs["stdin"] == "hello"


@pytest.mark.parametrize("region", ["1,2,3", "a,b,c,d", "1,2,3,4,5", ""])
def test_screenshot_rejects_a_bad_region(region: str, calls: list[Any]) -> None:
    if region == "":
        pytest.skip("empty region means full screen, tested separately")
    with pytest.raises(srv.ToolFailure, match="x,y,width,height"):
        srv.screenshot(region)
    assert not calls


def test_screenshot_explains_a_permission_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> str:
        raise srv.ToolFailure("could not create image from display")

    monkeypatch.setattr(srv, "_run", boom)
    with pytest.raises(srv.ToolFailure, match="Screen Recording"):
        srv.screenshot()


@pytest.mark.parametrize("pct", [-1, 101, 1000])
def test_set_volume_rejects_out_of_range(pct: int, calls: list[Any]) -> None:
    with pytest.raises(srv.ToolFailure, match="between 0 and 100"):
        srv.set_volume(pct)
    assert not calls


def test_set_volume_accepts_the_boundaries(calls: list[Any]) -> None:
    srv.set_volume(0)
    srv.set_volume(100)
    assert len(calls) == 2


def test_system_info_degrades_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> str:
        raise srv.ToolFailure("nope")

    monkeypatch.setattr(srv, "_run", boom)
    monkeypatch.setattr(srv, "_osascript", boom)
    info = json.loads(srv.system_info())
    # Disk and hostname come from the stdlib, so they survive every subprocess failing.
    assert info["battery"] == "unavailable"
    assert "unavailable" in info["volume"]
    assert "unavailable" in info["wifi"]
    assert "GB" in info["disk_free"]
    assert info["hostname"]


def test_system_info_parses_a_real_battery_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        srv,
        "_run",
        lambda argv, **_k: (
            "Now drawing from 'Battery Power'\n -InternalBattery-0 87%; discharging; 4:21\n"
            if argv[0] == "pmset"
            else "Current Wi-Fi Network: Home\n"
        ),
    )
    monkeypatch.setattr(srv, "_osascript", lambda *_a, **_k: "42")
    info = json.loads(srv.system_info())
    assert "87%" in info["battery"]
    assert info["wifi"] == "Home"
    assert info["volume"] == "42"


def test_toggle_dnd_explains_the_missing_shortcut(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> str:
        raise srv.ToolFailure("shortcut not found")

    monkeypatch.setattr(srv, "_run", boom)
    with pytest.raises(srv.ToolFailure, match="no scriptable Focus API"):
        srv.toggle_do_not_disturb()


def test_toggle_dnd_runs_the_named_shortcut(calls: list[Any]) -> None:
    srv.toggle_do_not_disturb("My Focus Toggle")
    assert calls[0][1] == ["shortcuts", "run", "My Focus Toggle"]


# --- the _run helper itself --------------------------------------------------------


def test_run_reports_a_missing_binary() -> None:
    with pytest.raises(srv.ToolFailure, match="not available"):
        srv._run(["definitely-not-a-real-binary-xyz"])


def test_run_surfaces_stderr_on_failure() -> None:
    with pytest.raises(srv.ToolFailure, match="boom"):
        srv._run(["/bin/sh", "-c", "echo boom >&2; exit 1"])


def test_run_times_out() -> None:
    with pytest.raises(srv.ToolFailure, match="timed out"):
        srv._run(["/bin/sleep", "5"], timeout=0.5)
