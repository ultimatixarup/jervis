"""Who is allowed to talk to Jervis, and where the brain is.

The allowlist is the whole security model. A Telegram bot is reachable by anyone who
learns its @handle, and Jervis can run shell commands, read files and move things to
the Trash. So an empty allowlist means *nobody*, not everybody - the one default that
is safe to get wrong.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path("~/.jervis/config.yaml").expanduser()
ENV_PATH = Path("~/.jervis/.env").expanduser()
TOKEN_VAR = "TELEGRAM_BOT_TOKEN"


class NotConfigured(RuntimeError):
    """Something only Arup can supply is missing."""


@dataclass(frozen=True)
class Settings:
    token: str = ""
    allowed_user_ids: frozenset[int] = field(default_factory=frozenset)
    brain_url: str = "http://127.0.0.1:7777"
    poll_timeout: int = 50
    # Skip anything that arrived while Jervis was off. Acting on an hours-old
    # instruction out of context is worse than ignoring it.
    drop_pending_on_start: bool = True

    def may_talk(self, user_id: int) -> bool:
        return user_id in self.allowed_user_ids

    def require_usable(self) -> None:
        if not self.token:
            raise NotConfigured(
                f"No {TOKEN_VAR}. Message @BotFather in Telegram, send /newbot, then put "
                f"the token it gives you in ~/.jervis/.env as {TOKEN_VAR}=..."
            )
        if not self.allowed_user_ids:
            raise NotConfigured(
                "telegram.allowed_user_ids is empty, so Jervis would answer nobody - "
                "which is deliberate, because a bot anyone can find would otherwise be "
                "a shell on your Mac. Message @userinfobot in Telegram to get your "
                "numeric id, then add it to ~/.jervis/config.yaml:\n"
                "  telegram:\n"
                "    allowed_user_ids: [123456789]"
            )


def load_env(path: Path | None = None) -> None:
    """Minimal .env reader. Empty values are skipped so they cannot shadow real ones."""
    target = path or ENV_PATH
    if not target.is_file():
        return
    for line in target.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if value.strip():
            os.environ.setdefault(key.strip(), value.strip())


def load(path: Path | None = None) -> Settings:
    config_path = path or CONFIG_PATH
    raw: dict[str, Any] = {}
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text()) or {}

    telegram = raw.get("telegram") or {}
    http = raw.get("http") or {}
    defaults = Settings()

    host = http.get("host", "127.0.0.1")
    port = int(http.get("port", 7777))

    return Settings(
        token=os.environ.get(TOKEN_VAR, "").strip(),
        allowed_user_ids=frozenset(int(i) for i in (telegram.get("allowed_user_ids") or ())),
        brain_url=str(telegram.get("brain_url") or f"http://{host}:{port}"),
        poll_timeout=int(telegram.get("poll_timeout", defaults.poll_timeout)),
        drop_pending_on_start=bool(
            telegram.get("drop_pending_on_start", defaults.drop_pending_on_start)
        ),
    )
