"""Who may talk to Jervis. This is the whole security model, so it fails closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from jervis_telegram.settings import NotConfigured, Settings, load, load_env


def test_an_empty_allowlist_answers_nobody() -> None:
    """A bot is reachable by anyone who learns its handle, and Jervis runs shell
    commands. Empty must mean nobody, never everybody."""
    settings = Settings(token="t")
    assert not settings.may_talk(1)
    assert not settings.may_talk(0)
    with pytest.raises(NotConfigured, match="answer nobody"):
        settings.require_usable()


def test_only_listed_accounts_are_allowed() -> None:
    settings = Settings(token="t", allowed_user_ids=frozenset({42}))
    assert settings.may_talk(42)
    assert not settings.may_talk(43)
    settings.require_usable()


def test_a_missing_token_says_how_to_get_one() -> None:
    with pytest.raises(NotConfigured, match="BotFather"):
        Settings(allowed_user_ids=frozenset({1})).require_usable()


def test_the_empty_allowlist_message_says_where_to_get_an_id() -> None:
    with pytest.raises(NotConfigured, match="userinfobot"):
        Settings(token="t").require_usable()


def test_config_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    (tmp_path / "c.yaml").write_text(
        "telegram:\n"
        "  allowed_user_ids: [111, 222]\n"
        "  poll_timeout: 10\n"
        "  drop_pending_on_start: false\n"
        "http:\n"
        "  host: 127.0.0.1\n"
        "  port: 7777\n"
    )
    settings = load(tmp_path / "c.yaml")
    assert settings.token == "123:abc"
    assert settings.allowed_user_ids == frozenset({111, 222})
    assert settings.poll_timeout == 10
    assert settings.drop_pending_on_start is False
    assert settings.brain_url == "http://127.0.0.1:7777"


def test_the_brain_url_follows_the_http_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    (tmp_path / "c.yaml").write_text("http:\n  host: 0.0.0.0\n  port: 9000\n")
    assert load(tmp_path / "c.yaml").brain_url == "http://0.0.0.0:9000"


def test_no_config_file_is_defaults_and_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    settings = load(tmp_path / "absent.yaml")
    assert settings.allowed_user_ids == frozenset()
    with pytest.raises(NotConfigured):
        settings.require_usable()


def test_the_shipped_example_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    example = Path(__file__).parents[2] / "config.example.yaml"
    settings = load(example)
    assert settings.allowed_user_ids == frozenset(), "the example must not allow anyone"


def test_load_env_skips_empty_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank line in .env must not shadow a real exported value."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-the-shell")
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=\n")
    load_env(tmp_path / ".env")
    import os

    assert os.environ["TELEGRAM_BOT_TOKEN"] == "from-the-shell"
