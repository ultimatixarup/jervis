"""Read the voice section of ~/.jervis/config.yaml.

Read directly rather than through jervis_brain so the voice package stays independent
of the brain package - they talk over HTTP, not imports.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .loop import VoiceConfig
from .vad import RecordingLimits

CONFIG_PATH = Path("~/.jervis/config.yaml").expanduser()
ENV_PATH = Path("~/.jervis/.env").expanduser()


def load_env(path: Path | None = None) -> None:
    """Minimal .env loader - the voice package has no dotenv dependency."""
    target = path or ENV_PATH
    if not target.is_file():
        return
    for line in target.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        # An empty value is not a credential; setting one shadows better sources
        # (an `ant auth login` profile, for one) instead of falling through to them.
        if value.strip():
            os.environ.setdefault(key.strip(), value.strip())


def load(path: Path | None = None) -> tuple[VoiceConfig, str]:
    """Return the voice config and the brain's base URL."""
    config_path = path or CONFIG_PATH
    raw: dict[str, Any] = {}
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text()) or {}

    voice = raw.get("voice") or {}
    http = raw.get("http") or {}
    permissions = raw.get("permissions") or {}
    defaults = VoiceConfig()

    config = VoiceConfig(
        wake_model=str(voice.get("wake_model") or defaults.wake_model),
        wake_threshold=float(voice.get("wake_threshold", defaults.wake_threshold)),
        wake_cooldown_seconds=float(
            voice.get("wake_cooldown_seconds", defaults.wake_cooldown_seconds)
        ),
        stt_model=str(voice.get("stt_model") or defaults.stt_model),
        tts_backend=str(voice.get("tts_backend") or defaults.tts_backend),
        tts_voice=str(voice.get("tts_voice") or defaults.tts_voice),
        # The spoken confirmation window has to match the brain's, or Jervis will keep
        # listening for a yes the brain has already given up on.
        confirm_window_seconds=int(
            permissions.get("confirm_window_seconds", defaults.confirm_window_seconds)
        ),
        limits=RecordingLimits(
            silence_ms=int(voice.get("silence_ms", RecordingLimits().silence_ms)),
            max_ms=int(voice.get("max_utterance_ms", RecordingLimits().max_ms)),
        ),
    )
    host = http.get("host", "127.0.0.1")
    port = int(http.get("port", 7777))
    return config, f"http://{host}:{port}"
