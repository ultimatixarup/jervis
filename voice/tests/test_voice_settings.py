"""Reading the voice section of the config."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jervis_voice.settings import load, load_env


def test_defaults_when_there_is_no_file(tmp_path: Path) -> None:
    config, base_url = load(tmp_path / "absent.yaml")
    assert config.wake_model == "hey_jarvis"
    assert config.tts_backend == "say"
    assert base_url == "http://127.0.0.1:7777"


def test_the_shipped_example_parses() -> None:
    """config.example.yaml is what setup.sh installs, so it must load."""
    config, base_url = load(Path(__file__).parents[2] / "config.example.yaml")
    assert config.wake_model == "hey_jarvis"
    assert config.wake_threshold == 0.6
    assert config.stt_model.startswith("mlx-community/")
    assert config.tts_voice == "Daniel"
    assert config.limits.silence_ms == 700
    assert config.limits.max_ms == 15_000
    assert base_url == "http://127.0.0.1:7777"


def test_the_confirm_window_is_taken_from_the_permissions_section(tmp_path: Path) -> None:
    """It must match the brain's, or Jervis listens for a yes the brain forgot about."""
    (tmp_path / "c.yaml").write_text("permissions:\n  confirm_window_seconds: 30\n")
    config, _ = load(tmp_path / "c.yaml")
    assert config.confirm_window_seconds == 30


def test_the_shipped_confirm_windows_agree() -> None:
    """The two halves read the same file; this pins that they stay in step."""
    from jervis_brain.config import load_config

    example = Path(__file__).parents[2] / "config.example.yaml"
    voice_config, _ = load(example)
    brain_config = load_config(example, load_env=False)
    assert voice_config.confirm_window_seconds == brain_config.permissions.confirm_window_seconds


def test_overrides(tmp_path: Path) -> None:
    (tmp_path / "c.yaml").write_text(
        "voice:\n"
        "  wake_model: jervis\n"
        "  wake_threshold: 0.8\n"
        "  wake_cooldown_seconds: 4\n"
        "  tts_backend: elevenlabs\n"
        "  tts_voice: Fiona\n"
        "  silence_ms: 400\n"
        "  max_utterance_ms: 9000\n"
        "http:\n"
        "  host: 0.0.0.0\n"
        "  port: 8123\n"
    )
    config, base_url = load(tmp_path / "c.yaml")
    assert config.wake_model == "jervis"
    assert config.wake_threshold == 0.8
    assert config.wake_cooldown_seconds == 4
    assert config.tts_backend == "elevenlabs"
    assert config.limits.silence_ms == 400
    assert config.limits.max_ms == 9000
    assert base_url == "http://0.0.0.0:8123"


def test_an_empty_voice_section_is_defaults(tmp_path: Path) -> None:
    (tmp_path / "c.yaml").write_text("voice:\nhttp:\n")
    config, base_url = load(tmp_path / "c.yaml")
    assert config.wake_model == "hey_jarvis"
    assert base_url == "http://127.0.0.1:7777"


# --- .env loading ---------------------------------------------------------------------


def test_load_env_sets_missing_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    (tmp_path / ".env").write_text("# a comment\n\nELEVENLABS_API_KEY=abc123\nMALFORMED\n")
    load_env(tmp_path / ".env")
    assert os.environ["ELEVENLABS_API_KEY"] == "abc123"


def test_load_env_does_not_override_the_real_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "from-the-shell")
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=from-the-file\n")
    load_env(tmp_path / ".env")
    assert os.environ["ELEVENLABS_API_KEY"] == "from-the-shell"


def test_load_env_tolerates_a_missing_file(tmp_path: Path) -> None:
    load_env(tmp_path / "nothing-here")
