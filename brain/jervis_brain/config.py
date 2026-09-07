"""Load ``~/.jervis/config.yaml`` and ``~/.jervis/.env``."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

DEFAULT_HOME = Path("~/.jervis").expanduser()


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value)))


CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ELEVENLABS_API_KEY")


def drop_empty_credentials() -> None:
    """Remove credential variables that were loaded as empty strings.

    An empty ANTHROPIC_API_KEY is worse than an absent one: it still occupies its slot
    in the SDK's precedence order and shadows an `ant auth login` profile. The example
    .env ships every key blank, so loading it verbatim would break exactly the setup
    that needs no key at all.
    """
    for name in CREDENTIAL_VARS:
        if name in os.environ and not os.environ[name].strip():
            del os.environ[name]


@dataclass(frozen=True)
class VoiceConfig:
    """The voice section, as the brain sees it.

    The voice package owns these settings and reads the same file itself; the brain
    only carries them so `jervis status` can show them. Unknown keys are ignored
    rather than raising, so adding a voice setting never breaks the brain.
    """

    wake_model: str = "hey_jarvis"
    wake_threshold: float = 0.6
    wake_cooldown_seconds: float = 2.0
    stt_model: str = "mlx-community/whisper-small.en-mlx"
    tts_backend: str = "say"
    tts_voice: str = "Daniel"
    silence_ms: int = 700
    max_utterance_ms: int = 15_000

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> VoiceConfig:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass(frozen=True)
class ServerConfig:
    name: str
    enabled: bool = True
    module: str = ""
    env: str | None = None

    @property
    def import_module(self) -> str:
        return self.module or f"jervis_mcp_{self.name}"


@dataclass(frozen=True)
class PermissionsConfig:
    confirm_window_seconds: int = 60
    extra_destructive_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class HttpConfig:
    host: str = "127.0.0.1"
    port: int = 7777


@dataclass(frozen=True)
class Paths:
    home: Path = DEFAULT_HOME
    audit_log: Path = DEFAULT_HOME / "audit.jsonl"
    memory_db: Path = DEFAULT_HOME / "memory.db"
    logs: Path = DEFAULT_HOME / "logs"


@dataclass(frozen=True)
class Config:
    model: str = "claude-sonnet-5"
    max_tool_rounds: int = 12
    max_tokens: int = 8000
    effort: str = "low"
    persona_name: str = "Jervis"
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    servers: tuple[ServerConfig, ...] = ()
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    paths: Paths = field(default_factory=Paths)

    @property
    def enabled_servers(self) -> tuple[ServerConfig, ...]:
        return tuple(s for s in self.servers if s.enabled)


def load_config(path: str | Path | None = None, *, load_env: bool = True) -> Config:
    """Read config.yaml, falling back to defaults for anything absent."""
    config_path = Path(path) if path else DEFAULT_HOME / "config.yaml"
    raw: dict[str, Any] = {}
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text()) or {}

    if load_env:
        # Secrets live beside the config, never in the repo (PLAN.md §6).
        load_dotenv(DEFAULT_HOME / ".env", override=False)
        drop_empty_credentials()

    paths_raw = raw.get("paths") or {}
    home = _expand(paths_raw.get("home", str(DEFAULT_HOME)))
    paths = Paths(
        home=home,
        audit_log=_expand(paths_raw.get("audit_log", str(home / "audit.jsonl"))),
        memory_db=_expand(paths_raw.get("memory_db", str(home / "memory.db"))),
        logs=_expand(paths_raw.get("logs", str(home / "logs"))),
    )

    voice_raw = raw.get("voice") or {}
    perms_raw = raw.get("permissions") or {}
    http_raw = raw.get("http") or {}

    servers = tuple(
        ServerConfig(
            name=name,
            enabled=bool((spec or {}).get("enabled", True)),
            module=(spec or {}).get("module", ""),
            env=(spec or {}).get("env"),
        )
        for name, spec in (raw.get("servers") or {}).items()
    )

    defaults = Config()
    return Config(
        model=raw.get("model", defaults.model),
        max_tool_rounds=int(raw.get("max_tool_rounds", defaults.max_tool_rounds)),
        max_tokens=int(raw.get("max_tokens", defaults.max_tokens)),
        effort=raw.get("effort", defaults.effort),
        persona_name=raw.get("persona_name", defaults.persona_name),
        voice=VoiceConfig.from_raw(voice_raw),
        servers=servers,
        permissions=PermissionsConfig(
            confirm_window_seconds=int(
                perms_raw.get("confirm_window_seconds", defaults.permissions.confirm_window_seconds)
            ),
            extra_destructive_patterns=tuple(perms_raw.get("extra_destructive_patterns") or ()),
        ),
        http=HttpConfig(
            host=http_raw.get("host", defaults.http.host),
            port=int(http_raw.get("port", defaults.http.port)),
        ),
        paths=paths,
    )
