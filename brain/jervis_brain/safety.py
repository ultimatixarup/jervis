"""The brain's own view of what is dangerous.

Deliberately a *separate* implementation from each MCP server's checks. PLAN.md §4
Phase 1 calls the server-side checks "defence-in-depth, not the only line"; two
independent implementations only help if they really are independent, so this module
imports nothing from the servers. `test_brain_safety_is_a_superset` asserts that this
one never flags *less* than the macOS server does, which catches drift in the only
direction that matters.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path

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
    r"\bshred\b",
    r"\bsrm\b",
)

BLOCKED_PATH_ROOTS: tuple[str, ...] = ("~/.ssh", "~/Library/Keychains", "/System")

_DATA_FIRMLINK = "/System/Volumes/Data"
_PATH_CANDIDATE = re.compile(r"(?:~|\$HOME|\$\{HOME\}|\.{1,2}/|/)[^\s'\";|&()<>`]*")


def destructive_match(text: str, extra_patterns: Sequence[str] = ()) -> str | None:
    for pattern in (*DESTRUCTIVE_PATTERNS, *extra_patterns):
        if re.search(pattern, text, re.IGNORECASE):
            return pattern
    return None


def is_destructive(text: str, extra_patterns: Sequence[str] = ()) -> bool:
    return destructive_match(text, extra_patterns) is not None


def normalise_path(path: str | Path, cwd: str | Path | None = None) -> Path:
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


def is_blocked_path(path: str | Path, cwd: str | Path | None = None) -> bool:
    target = normalise_path(path, cwd)
    roots = [normalise_path(root) for root in BLOCKED_PATH_ROOTS]
    return any(target == root or root in target.parents for root in roots)


def find_blocked_reference(text: str, cwd: str | Path | None = None) -> str | None:
    """First blocked path mentioned anywhere in *text*, or None."""
    for match in _PATH_CANDIDATE.finditer(text):
        candidate = match.group(0).rstrip(".,;:")
        if is_blocked_path(candidate, cwd):
            return candidate
    return None
