"""Which Anthropic credential Jervis will actually use.

There is more than one way to authenticate, and the SDK resolves them in a fixed
order. Checking only for ANTHROPIC_API_KEY makes a perfectly good OAuth profile look
like a broken setup, so everything that reports on credentials asks here instead.

Nothing in this module reads a secret's value - only whether a source exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("ANTHROPIC_CONFIG_DIR", "~/.config/anthropic")).expanduser()
CREDENTIALS_DIR = CONFIG_DIR / "credentials"
ENV_FILE = Path("~/.jervis/.env").expanduser()


@dataclass(frozen=True)
class Credential:
    source: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.source != "none"


def _env_file_has_key(path: Path) -> bool:
    if not path.is_file():
        return False
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("ANTHROPIC_API_KEY=") and stripped.split("=", 1)[1].strip():
            return True
    return False


def detect(env_file: Path | None = None) -> Credential:
    """Report the credential the SDK would pick, in the SDK's own precedence order."""
    # An exported key wins over everything, including an empty one - an empty
    # ANTHROPIC_API_KEY still occupies its slot and authenticates with nothing.
    if "ANTHROPIC_API_KEY" in os.environ:
        if os.environ["ANTHROPIC_API_KEY"].strip():
            return Credential("environment", "ANTHROPIC_API_KEY is exported")
        return Credential(
            "none",
            "ANTHROPIC_API_KEY is exported but empty, which shadows every other source; unset it",
        )
    if os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip():
        return Credential("environment", "ANTHROPIC_AUTH_TOKEN is exported")

    target = env_file or ENV_FILE
    if _env_file_has_key(target):
        return Credential("env file", f"ANTHROPIC_API_KEY in {target}")

    profiles = sorted(CREDENTIALS_DIR.glob("*.json")) if CREDENTIALS_DIR.is_dir() else []
    if profiles:
        names = ", ".join(p.stem for p in profiles)
        return Credential("oauth profile", f"`ant auth login` profile: {names}")

    return Credential("none", "no API key and no OAuth profile")
