"""Signing in. No browser is launched and no password goes anywhere near this."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jervis_mcp_ubereats import signin


def test_the_helper_exists_and_is_runnable() -> None:
    assert signin.HELPER.is_file()
    assert signin.HELPER.name == "login.js"


def test_the_helper_opens_a_visible_browser() -> None:
    """The whole reason this exists: the upstream server is headless-only, so a
    sign-in anywhere else can never reach it."""
    source = signin.HELPER.read_text()
    assert "headless: false" in source
    assert "headless: true" not in source


def test_the_helper_never_types_anything() -> None:
    """It opens a window and waits. Filling a field is how a password would be
    handled, so no input API may appear at all - checked against code, since the
    comments legitimately talk about passwords."""
    code = "\n".join(
        line
        for line in signin.HELPER.read_text().splitlines()
        if not line.lstrip().startswith(("*", "//", "/*"))
    )
    for api in (".fill(", ".type(", "keyboard", "press("):
        assert api not in code, f"the sign-in helper uses {api!r}"


def test_the_helper_waits_for_the_cookies_the_upstream_server_recognises() -> None:
    """auth.ts getAuthState checks exactly these names; drifting apart means saving a
    session the server then reports as signed out."""
    source = signin.HELPER.read_text()
    for cookie in ("uev2.id", "sid", "uev2.tok", "jwt-session"):
        assert f'"{cookie}"' in source


def test_the_helper_writes_where_the_upstream_server_reads() -> None:
    source = signin.HELPER.read_text()
    assert '".strider", "ubereats"' in source
    assert '"cookies.json"' in source


def test_the_session_file_is_written_owner_only() -> None:
    """It is a bearer token for the account, not a preference file."""
    assert "0o600" in signin.HELPER.read_text()


# --- reading the session -----------------------------------------------------------------


def test_no_file_is_not_signed_in(tmp_path: Path) -> None:
    session = signin.read_session(tmp_path / "absent.json")
    assert not session.exists
    assert not session.looks_signed_in


def test_an_empty_array_is_not_signed_in(tmp_path: Path) -> None:
    """What `logout` leaves behind, and what was sitting there while login 'worked'."""
    path = tmp_path / "cookies.json"
    path.write_text("[]")
    session = signin.read_session(path)
    assert session.exists
    assert session.count == 0
    assert not session.looks_signed_in


def test_cookies_mean_signed_in(tmp_path: Path) -> None:
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps([{"name": "sid", "value": "x", "domain": ".ubereats.com"}]))
    session = signin.read_session(path)
    assert session.looks_signed_in
    assert session.count == 1
    assert session.modified_at > 0


def test_a_corrupt_file_is_not_signed_in(tmp_path: Path) -> None:
    path = tmp_path / "cookies.json"
    path.write_text("{not json")
    assert not signin.read_session(path).looks_signed_in


def test_read_session_never_returns_cookie_values(tmp_path: Path) -> None:
    """Nothing in Jervis needs the value, so nothing in Jervis carries it."""
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps([{"name": "sid", "value": "super-secret-token"}]))
    session = signin.read_session(path)
    assert "super-secret-token" not in repr(session)


def test_a_missing_helper_explains_itself(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(signin, "HELPER", tmp_path / "nope" / "login.js")
    with pytest.raises(signin.SignInUnavailable, match="missing"):
        signin.check_helper()


def test_missing_dependencies_give_the_install_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = tmp_path / "node" / "login.js"
    helper.parent.mkdir(parents=True)
    helper.write_text("// stub")
    monkeypatch.setattr(signin, "HELPER", helper)
    with pytest.raises(signin.SignInUnavailable, match="npm install"):
        signin.check_helper()


def test_a_missing_node_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(signin.shutil, "which", lambda _name: None)
    with pytest.raises(signin.SignInUnavailable, match="node is not installed"):
        signin.check_helper()
