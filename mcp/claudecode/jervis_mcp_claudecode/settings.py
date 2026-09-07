"""What Claude Code is allowed to do, and where.

Read from ~/.jervis/config.yaml so the limits belong to Arup, not to whatever the
model decides to ask for. No tool argument can widen any of this.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path("~/.jervis/config.yaml").expanduser()

# Read-only work. `ask` runs with exactly these and nothing else.
READ_ONLY_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep")

# Editing work. Note what is absent: Bash. Under --permission-mode acceptEdits a
# tool that is not auto-approved is *denied* in non-interactive mode rather than
# prompting, so leaving Bash out means Claude Code can edit files but cannot run
# arbitrary commands until Arup allow-lists specific ones in config.
EDIT_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep", "Edit", "Write", "NotebookEdit")

# Denied even if something later adds them to an allow list. Deny beats allow.
ALWAYS_DENIED: tuple[str, ...] = (
    "Bash(rm:*)",
    "Bash(sudo:*)",
    "Bash(git push:*)",
    "Bash(curl:*)",
    "Bash(ssh:*)",
    "WebFetch",
)

BLOCKED_PATH_ROOTS: tuple[str, ...] = ("~/.ssh", "~/Library/Keychains", "/System")


@dataclass(frozen=True)
class Settings:
    projects: tuple[Path, ...] = ()
    model: str = ""
    max_turns: int = 30
    ask_timeout_seconds: int = 120
    task_timeout_seconds: int = 600
    extra_allowed_tools: tuple[str, ...] = field(default=())

    def allowed_tools(self, *, editing: bool) -> tuple[str, ...]:
        base = EDIT_TOOLS if editing else READ_ONLY_TOOLS
        return (*base, *self.extra_allowed_tools) if editing else base


def _expand(value: str) -> Path:
    return Path(os.path.realpath(os.path.expandvars(os.path.expanduser(value))))


def load_settings(path: Path | None = None) -> Settings:
    config_path = path or CONFIG_PATH
    raw: dict[str, Any] = {}
    if config_path.is_file():
        raw = (yaml.safe_load(config_path.read_text()) or {}).get("claudecode") or {}

    projects = tuple(_expand(p) for p in (raw.get("projects") or ["~/code"]))
    defaults = Settings()
    return Settings(
        projects=projects,
        model=str(raw.get("model") or defaults.model),
        max_turns=int(raw.get("max_turns", defaults.max_turns)),
        ask_timeout_seconds=int(raw.get("ask_timeout_seconds", defaults.ask_timeout_seconds)),
        task_timeout_seconds=int(raw.get("task_timeout_seconds", defaults.task_timeout_seconds)),
        extra_allowed_tools=tuple(raw.get("extra_allowed_tools") or ()),
    )


def is_blocked(path: Path) -> bool:
    roots = [_expand(root) for root in BLOCKED_PATH_ROOTS]
    return any(path == root or root in path.parents for root in roots)


def resolve_project(name_or_path: str, settings: Settings) -> Path:
    """Turn a spoken project name into a directory, or refuse.

    Accepts a bare name ("ideap"), matched against the basenames of the configured
    roots' children, or a path that must sit inside one of those roots. Anything else
    is refused - Claude Code gets filesystem access to wherever it is started, so this
    is the boundary that matters.
    """
    if not settings.projects:
        raise ValueError(
            "No project directories are configured. Add claudecode.projects to "
            "~/.jervis/config.yaml."
        )

    candidate = (
        _expand(name_or_path) if "/" in name_or_path or name_or_path.startswith("~") else None
    )

    if candidate is None:
        matches = [
            child
            for root in settings.projects
            if root.is_dir()
            for child in sorted(root.iterdir())
            if child.is_dir() and child.name.lower() == name_or_path.strip().lower()
        ]
        if not matches:
            known = ", ".join(sorted(p.name for p in list_projects(settings))) or "none"
            raise ValueError(f"I don't know a project called {name_or_path!r}. I know: {known}.")
        candidate = matches[0]

    if is_blocked(candidate):
        raise ValueError(f"{candidate} is inside a blocked location. This is blocked by policy.")
    if not any(candidate == root or root in candidate.parents for root in settings.projects):
        allowed = ", ".join(str(p) for p in settings.projects)
        raise ValueError(
            f"{candidate} is outside the directories Claude Code may touch ({allowed})."
        )
    if not candidate.is_dir():
        raise ValueError(f"{candidate} is not a directory.")
    return candidate


def list_projects(settings: Settings) -> list[Path]:
    found: list[Path] = []
    for root in settings.projects:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                found.append(child)
    return found
