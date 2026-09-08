"""The launchd agents."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

from jervis_brain import daemon
from jervis_brain.daemon import (
    BRAIN_LABEL,
    TELEGRAM_LABEL,
    Agent,
    brain_agent,
    describe,
    launch_path,
    plist_for,
    rotate_log,
    telegram_agent,
    write_plist,
)

REPO = Path("/Users/someone/code/jervis")
HOME = Path("/Users/someone/.jervis")


def test_the_brain_agent_runs_the_server() -> None:
    agent = brain_agent(REPO, HOME)
    assert agent.label == BRAIN_LABEL
    assert agent.arguments[1:] == ["run", "jervis", "serve"]
    assert Path(agent.arguments[0]).is_absolute(), "launchd resolves argv[0] itself"
    assert agent.working_directory == REPO
    assert agent.log == HOME / "logs" / "brain.log"


def test_the_two_agents_are_separate() -> None:
    """They fail independently; only the brain is essential."""
    assert brain_agent(REPO, HOME).label != telegram_agent(REPO, HOME).label
    assert telegram_agent(REPO, HOME).label == TELEGRAM_LABEL
    assert brain_agent(REPO, HOME).log != telegram_agent(REPO, HOME).log


def test_the_path_names_homebrew() -> None:
    """launchd gives an agent no shell profile; without this, `uv` is not found and
    the agent crash-loops for no visible reason."""
    path = launch_path()
    assert "/opt/homebrew/bin" in path
    assert path.index("/opt/homebrew/bin") < path.index("/usr/bin")


def test_the_plist_restarts_but_does_not_busy_loop() -> None:
    plist = plist_for(brain_agent(REPO, HOME))
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["ThrottleInterval"] >= 10, "a config mistake must not become a busy loop"
    assert plist["ProcessType"] == "Background"


def test_output_is_unbuffered() -> None:
    """A crash must not lose the output that explains it."""
    assert plist_for(brain_agent(REPO, HOME))["EnvironmentVariables"]["PYTHONUNBUFFERED"] == "1"


def test_both_streams_go_to_the_log() -> None:
    plist = plist_for(brain_agent(REPO, HOME))
    assert plist["StandardOutPath"] == plist["StandardErrorPath"]
    assert plist["StandardOutPath"].endswith("brain.log")


def test_write_plist_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon, "LAUNCH_AGENTS", tmp_path / "LaunchAgents")
    agent = brain_agent(REPO, tmp_path / "jervis")
    path = write_plist(agent)
    assert path.is_file()
    with path.open("rb") as handle:
        assert plistlib.load(handle)["Label"] == BRAIN_LABEL


@pytest.mark.macos
def test_the_generated_plist_passes_plutil(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon, "LAUNCH_AGENTS", tmp_path / "LaunchAgents")
    path = write_plist(brain_agent(REPO, tmp_path / "jervis"))
    subprocess.run(["plutil", "-lint", str(path)], check=True, capture_output=True)


@pytest.mark.macos
def test_an_awkward_repo_path_still_produces_valid_xml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why this is generated with plistlib and not sed: a path with an ampersand or an
    apostrophe would silently produce malformed XML under substitution."""
    monkeypatch.setattr(daemon, "LAUNCH_AGENTS", tmp_path / "LaunchAgents")
    awkward = Path("/Users/someone/code/R&D <jervis> \"quoted\" 'apostrophe'")
    path = write_plist(brain_agent(awkward, tmp_path / "jervis"))
    subprocess.run(["plutil", "-lint", str(path)], check=True, capture_output=True)
    with path.open("rb") as handle:
        assert plistlib.load(handle)["WorkingDirectory"] == str(awkward)


def test_writing_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon, "LAUNCH_AGENTS", tmp_path / "LaunchAgents")
    agent = brain_agent(REPO, tmp_path / "jervis")
    first = write_plist(agent).read_bytes()
    assert write_plist(agent).read_bytes() == first


def test_the_log_directory_is_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon, "LAUNCH_AGENTS", tmp_path / "LaunchAgents")
    home = tmp_path / "jervis"
    write_plist(brain_agent(REPO, home))
    assert (home / "logs").is_dir(), "launchd will not create it and the agent will fail"


# --- log rotation ---------------------------------------------------------------------


def test_rotate_keeps_one_previous_log(tmp_path: Path) -> None:
    log = tmp_path / "brain.log"
    log.write_text("x" * 100)
    rotate_log(log, max_bytes=50)
    assert not log.exists()
    assert (tmp_path / "brain.log.1").read_text() == "x" * 100


def test_a_small_log_is_left_alone(tmp_path: Path) -> None:
    log = tmp_path / "brain.log"
    log.write_text("small")
    rotate_log(log, max_bytes=1000)
    assert log.read_text() == "small"


def test_rotating_a_missing_log_is_harmless(tmp_path: Path) -> None:
    rotate_log(tmp_path / "never-existed.log")


# --- status ---------------------------------------------------------------------------


def test_describe_an_uninstalled_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon, "LAUNCH_AGENTS", tmp_path / "nothing-here")
    assert describe(brain_agent(REPO, tmp_path)) == "not installed"


def test_describe_an_installed_but_unloaded_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(daemon, "LAUNCH_AGENTS", tmp_path / "LaunchAgents")
    agent = brain_agent(REPO, tmp_path / "jervis")
    write_plist(agent)
    monkeypatch.setattr(daemon, "is_loaded", lambda _a: False)
    assert describe(agent) == "installed but not loaded"


def test_describe_a_running_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon, "LAUNCH_AGENTS", tmp_path / "LaunchAgents")
    agent = brain_agent(REPO, tmp_path / "jervis")
    write_plist(agent)
    monkeypatch.setattr(daemon, "is_loaded", lambda _a: True)
    monkeypatch.setattr(daemon, "pid_of", lambda _a: 4321)
    assert describe(agent) == "running (pid 4321)"


def test_pid_is_parsed_from_launchctl_output(monkeypatch: pytest.MonkeyPatch) -> None:
    sample = "state = running\n\tpid = 12345\n\tprogram = /opt/homebrew/bin/uv\n"

    def fake(*_args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, sample, "")

    monkeypatch.setattr(daemon, "_launchctl", fake)
    assert daemon.pid_of(Agent("l", [], Path("/"), Path("/tmp/x.log"))) == 12345


def test_no_pid_when_launchctl_does_not_know_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(*_args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 1, "", "could not find service")

    monkeypatch.setattr(daemon, "_launchctl", fake)
    assert daemon.pid_of(Agent("l", [], Path("/"), Path("/tmp/x.log"))) is None
