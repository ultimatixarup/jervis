"""Which credential source Jervis will use."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jervis_brain import credentials
from jervis_brain.credentials import detect


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(credentials, "CREDENTIALS_DIR", tmp_path / "no-profiles")


def test_nothing_configured(tmp_path: Path) -> None:
    result = detect(tmp_path / "absent.env")
    assert not result.ok
    assert result.source == "none"


def test_an_exported_key_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert detect(tmp_path / "absent.env").source == "environment"


def test_an_empty_exported_key_is_a_trap_not_a_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty ANTHROPIC_API_KEY still occupies its precedence slot and shadows
    every other source, so it must be reported, not ignored."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-in-the-file\n")
    result = detect(tmp_path / ".env")
    assert not result.ok
    assert "shadows" in result.detail


def test_an_auth_token_counts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "oauth-token")
    assert detect(tmp_path / "absent.env").ok


def test_a_key_in_the_env_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("# comment\nANTHROPIC_API_KEY=sk-ant-abc\n")
    result = detect(tmp_path / ".env")
    assert result.source == "env file"
    assert result.ok


def test_an_empty_key_in_the_env_file_is_not_a_credential(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=\n")
    assert not detect(tmp_path / ".env").ok


def test_an_oauth_profile_counts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`ant auth login` stores a profile the SDK picks up with no key anywhere."""
    profiles = tmp_path / "credentials"
    profiles.mkdir()
    (profiles / "default.json").write_text("{}")
    monkeypatch.setattr(credentials, "CREDENTIALS_DIR", profiles)

    result = detect(tmp_path / "absent.env")
    assert result.ok
    assert result.source == "oauth profile"
    assert "default" in result.detail


def test_the_env_file_beats_a_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Matches the SDK's own precedence: a key shadows a profile."""
    profiles = tmp_path / "credentials"
    profiles.mkdir()
    (profiles / "default.json").write_text("{}")
    monkeypatch.setattr(credentials, "CREDENTIALS_DIR", profiles)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-abc\n")

    assert detect(tmp_path / ".env").source == "env file"


def test_no_secret_value_is_ever_returned(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-super-secret-value\n")
    result = detect(tmp_path / ".env")
    assert "super-secret" not in result.detail
    assert "super-secret" not in result.source


def test_empty_credentials_are_dropped_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shipped .env has every key blank; loading it must not shadow a profile."""
    from jervis_brain.config import drop_empty_credentials

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "   ")
    monkeypatch.setenv("PLAID_ENV", "sandbox")

    drop_empty_credentials()

    assert "ANTHROPIC_API_KEY" not in os.environ
    assert "ELEVENLABS_API_KEY" not in os.environ
    assert os.environ["PLAID_ENV"] == "sandbox", "non-credential values are untouched"


def test_a_real_key_survives(monkeypatch: pytest.MonkeyPatch) -> None:
    from jervis_brain.config import drop_empty_credentials

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    drop_empty_credentials()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-real"


def test_loading_the_shipped_env_leaves_an_oauth_profile_usable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End to end: blank .env plus a profile must resolve to the profile."""
    from jervis_brain.config import load_config

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    profiles = tmp_path / "credentials"
    profiles.mkdir()
    (profiles / "default.json").write_text("{}")
    monkeypatch.setattr(credentials, "CREDENTIALS_DIR", profiles)

    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=\n")
    monkeypatch.setattr("jervis_brain.config.DEFAULT_HOME", tmp_path)

    load_config(tmp_path / "absent.yaml", load_env=True)
    assert detect(env_file).source == "oauth profile"
