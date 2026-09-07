"""Destructive-command detection and blocked-path enforcement.

This is the server's own line of defence. The brain's permission guard runs the same
checks independently (PLAN.md §4 Phase 1: "defence-in-depth, not the only line"), so a
bug or a bypass in one does not silently become a bypass in the other.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

# PLAN.md §4 Phase 1 lists these patterns. Three are deliberately *wider* than the plan
# text, because the plan's literal form leaves a hole that a reviewer would rightly
# flag - erring toward `confirm` costs one spoken "yes", erring the other way costs data:
#   - `kill(all)?`  - `killall` is at least as destructive as `kill`.
#   - `chmod -\w*R` - catches `chmod -Rf`, not just the exact string `chmod -R`.
#   - `git push`    - catches the `-f` short form as well as `--force`.
# One pattern is added outright: `dd ... of=` overwrites a device or file wholesale and
# belongs with `mkfs` and `diskutil`, which the plan does list.
DESTRUCTIVE_PATTERNS: tuple[str, ...] = (
    r"\brm\b",
    r"\bsudo\b",
    r"\bkill(all)?\b",
    r">\s*/dev/",
    r"\bmkfs\b",
    r"\bdiskutil\b",
    r"\bchmod\s+-\w*R",
    r"\bgit\s+push\b[^\n]*(--force|\s-[A-Za-z]*f\b)",
    r"\bdd\b[^\n]*\bof=",
)

# Never readable or writable by Jervis, at any tier (PLAN.md §0 `blocked`).
BLOCKED_PATH_ROOTS: tuple[str, ...] = ("~/.ssh", "~/Library/Keychains", "/System")

# On modern macOS the read-only system volume is firmlinked: /System/Volumes/Data/Users/me
# is the *same file* as /Users/me. Without stripping this prefix, an ordinary Desktop path
# spelled the long way would look like it lives under the blocked /System root.
_DATA_FIRMLINK = "/System/Volumes/Data"

# Anything that could start a path: ~, $HOME, an absolute path, or a relative traversal.
_PATH_CANDIDATE = re.compile(r"(?:~|\$HOME|\$\{HOME\}|\.{1,2}/|/)[^\s'\";|&()<>`]*")


class BlockedByPolicy(ToolError):
    """Raised when a call touches something Jervis is never allowed to touch.

    Subclasses the SDK's ToolError so the reason reaches the model. A bare Exception
    would be treated as a crash and the model would see only "Error executing tool X",
    which tells it nothing about *why* it was refused.
    """


def _compile(extra: Sequence[str] = ()) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in (*DESTRUCTIVE_PATTERNS, *extra)]


def destructive_match(text: str, extra_patterns: Sequence[str] = ()) -> str | None:
    """Return the pattern that flags *text* as destructive, or None."""
    for pattern in _compile(extra_patterns):
        if pattern.search(text):
            return pattern.pattern
    return None


def is_destructive(text: str, extra_patterns: Sequence[str] = ()) -> bool:
    return destructive_match(text, extra_patterns) is not None


def normalise_path(path: str | Path, cwd: str | Path | None = None) -> Path:
    """Expand ``~``, resolve symlinks and ``..``, and undo the /System firmlink.

    Works on paths that do not exist yet - ``os.path.realpath`` does not require the
    target to be present, which matters for ``write_file`` on a new file.
    """
    raw = os.path.expandvars(os.path.expanduser(str(path)))
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(cwd or Path.cwd()) / candidate
    resolved = Path(os.path.realpath(candidate))

    text = str(resolved)
    if text == _DATA_FIRMLINK:
        return Path("/")
    if text.startswith(_DATA_FIRMLINK + "/"):
        return Path(text[len(_DATA_FIRMLINK) :])
    return resolved


def blocked_roots() -> list[Path]:
    return [normalise_path(root) for root in BLOCKED_PATH_ROOTS]


def is_blocked_path(path: str | Path, cwd: str | Path | None = None) -> bool:
    """True if *path* is, or is inside, a blocked root - after full normalisation."""
    target = normalise_path(path, cwd)
    return any(target == root or root in target.parents for root in blocked_roots())


def path_candidates(text: str) -> Iterable[str]:
    """Yield substrings of *text* that could be filesystem paths."""
    for match in _PATH_CANDIDATE.finditer(text):
        yield match.group(0).rstrip(".,;:")


def find_blocked_reference(text: str, cwd: str | Path | None = None) -> str | None:
    """Return the first blocked path mentioned anywhere in *text*, or None.

    Used to stop `cat ~/.ssh/id_rsa` before a shell ever sees it. It is a text scan,
    so it is inherently approximate; it is a backstop, not the security boundary.
    """
    for candidate in path_candidates(text):
        if is_blocked_path(candidate, cwd):
            return candidate
    return None


def assert_path_allowed(path: str | Path, cwd: str | Path | None = None) -> Path:
    """Normalise *path*, raising BlockedByPolicy if it is off limits."""
    if is_blocked_path(path, cwd):
        raise BlockedByPolicy(
            f"{path} is inside a blocked location "
            f"({', '.join(BLOCKED_PATH_ROOTS)}). This action is blocked by policy."
        )
    return normalise_path(path, cwd)


def assert_command_allowed(text: str, cwd: str | Path | None = None) -> None:
    """Raise BlockedByPolicy if a command or script references a blocked path."""
    reference = find_blocked_reference(text, cwd)
    if reference is not None:
        raise BlockedByPolicy(
            f"This command references {reference}, which is inside a blocked location "
            f"({', '.join(BLOCKED_PATH_ROOTS)}). This action is blocked by policy."
        )
