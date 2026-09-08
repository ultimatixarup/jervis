"""The launchd agents that keep Jervis running without a terminal.

PLAN.md §4 Phase 7 calls for a `.plist.template` with substitutions. This generates the
plist with `plistlib` instead: a repo path containing an apostrophe or an ampersand
would silently produce malformed XML under sed, and `plutil -lint` would then reject
something no one had touched. plistlib escapes by construction, and the result is
still an ordinary file on disk that can be read and linted.

Two agents, because they fail independently and only one is essential:
  com.arup.jervis           the brain on localhost:7777
  com.arup.jervis.telegram  the bot, only if Telegram is configured
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BRAIN_LABEL = "com.arup.jervis"
TELEGRAM_LABEL = "com.arup.jervis.telegram"
LAUNCH_AGENTS = Path("~/Library/LaunchAgents").expanduser()

# launchd starts an agent with almost no environment: no shell profile, no PATH beyond
# the system default. Homebrew has to be named explicitly or `uv` is not found, and the
# failure looks like the agent crash-looping for no reason.
HOMEBREW_PATHS = ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin")
SYSTEM_PATHS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")

# Restarting instantly forever turns a config mistake into a busy loop.
THROTTLE_SECONDS = 10
MAX_LOG_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class Agent:
    label: str
    arguments: list[str]
    working_directory: Path
    log: Path

    @property
    def plist_path(self) -> Path:
        return LAUNCH_AGENTS / f"{self.label}.plist"

    @property
    def target(self) -> str:
        return f"gui/{os.getuid()}/{self.label}"


def launch_path() -> str:
    """PATH for the agent: Homebrew first, then the system."""
    return ":".join([*HOMEBREW_PATHS, *SYSTEM_PATHS])


def uv_binary() -> str:
    """An absolute path, because launchd resolves ProgramArguments[0] itself."""
    found = shutil.which("uv")
    if found:
        return found
    for prefix in HOMEBREW_PATHS:
        candidate = Path(prefix) / "uv"
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("uv is not on PATH; run scripts/setup.sh")


def brain_agent(repo: Path, home: Path) -> Agent:
    return Agent(
        label=BRAIN_LABEL,
        arguments=[uv_binary(), "run", "jervis", "serve"],
        working_directory=repo,
        log=home / "logs" / "brain.log",
    )


def telegram_agent(repo: Path, home: Path) -> Agent:
    return Agent(
        label=TELEGRAM_LABEL,
        arguments=[uv_binary(), "run", "jervis-telegram"],
        working_directory=repo,
        log=home / "logs" / "telegram.log",
    )


def plist_for(agent: Agent) -> dict[str, Any]:
    return {
        "Label": agent.label,
        "ProgramArguments": agent.arguments,
        "WorkingDirectory": str(agent.working_directory),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_SECONDS,
        "StandardOutPath": str(agent.log),
        "StandardErrorPath": str(agent.log),
        "ProcessType": "Background",
        "EnvironmentVariables": {
            "PATH": launch_path(),
            "HOME": str(Path.home()),
            # Unbuffered, or a crash loses the very output that explains it.
            "PYTHONUNBUFFERED": "1",
        },
    }


def write_plist(agent: Agent) -> Path:
    agent.plist_path.parent.mkdir(parents=True, exist_ok=True)
    agent.log.parent.mkdir(parents=True, exist_ok=True)
    with agent.plist_path.open("wb") as handle:
        plistlib.dump(plist_for(agent), handle)
    return agent.plist_path


def rotate_log(path: Path, max_bytes: int = MAX_LOG_BYTES) -> None:
    """Keep one previous log. launchd appends forever otherwise."""
    try:
        if path.exists() and path.stat().st_size >= max_bytes:
            path.replace(path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


# --- talking to launchctl -------------------------------------------------------------


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args], capture_output=True, text=True, check=False, timeout=30
    )


def is_installed(agent: Agent) -> bool:
    return agent.plist_path.is_file()


def is_loaded(agent: Agent) -> bool:
    return _launchctl("print", agent.target).returncode == 0


def pid_of(agent: Agent) -> int | None:
    result = _launchctl("print", agent.target)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("pid = "):
            try:
                return int(stripped.split("=", 1)[1])
            except ValueError:
                return None
    return None


def describe(agent: Agent) -> str:
    if not is_installed(agent):
        return "not installed"
    if not is_loaded(agent):
        return "installed but not loaded"
    pid = pid_of(agent)
    return f"running (pid {pid})" if pid else "loaded but not running"
