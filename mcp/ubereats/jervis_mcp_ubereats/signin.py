"""Signing in to Uber Eats, in a browser you can see.

The upstream server's login is a dead end: it drives a headless browser and its
`ubereats_login` only hands back a URL, so a session established anywhere else never
reaches it. This runs a small Node helper that opens the browser it *does* use, waits
while Arup signs in himself, and writes the cookies where that server reads them.

Detached on purpose. Signing in takes as long as it takes - finding a password, a code
from a text - and a tool call that blocks for five minutes outlives the HTTP timeouts
between Telegram, the brain and back. So the helper is launched and left to it; the
next `status` call notices the cookies and reloads.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "node" / "login.js"
COOKIE_FILE = Path("~/.strider/ubereats/cookies.json").expanduser()
DEFAULT_TIMEOUT_SECONDS = 300


class SignInUnavailable(RuntimeError):
    """The helper cannot run."""


@dataclass(frozen=True)
class Session:
    """What the cookie file says, without reading any cookie values."""

    exists: bool
    count: int
    modified_at: float

    @property
    def looks_signed_in(self) -> bool:
        # The upstream server treats any non-empty array as a stored session.
        return self.exists and self.count > 0


def read_session(path: Path | None = None) -> Session:
    target = path or COOKIE_FILE
    if not target.is_file():
        return Session(False, 0, 0.0)
    try:
        cookies = json.loads(target.read_text() or "[]")
    except (OSError, json.JSONDecodeError):
        return Session(True, 0, target.stat().st_mtime)
    count = len(cookies) if isinstance(cookies, list) else 0
    return Session(True, count, target.stat().st_mtime)


def check_helper() -> None:
    if shutil.which("node") is None:
        raise SignInUnavailable("node is not installed. `brew install node`, then try again.")
    if not HELPER.is_file():
        raise SignInUnavailable(f"the sign-in helper is missing at {HELPER}")
    if not (HELPER.parent / "node_modules").is_dir():
        raise SignInUnavailable(
            "the sign-in helper's dependencies are not installed. Run:\n"
            f"    cd {HELPER.parent} && npm install"
        )


def start(timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> subprocess.Popen[bytes]:
    """Open the sign-in window and return without waiting."""
    check_helper()
    return subprocess.Popen(
        ["node", str(HELPER), str(timeout_seconds)],
        cwd=str(HELPER.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # outlives this tool call
        env={**os.environ},
    )
