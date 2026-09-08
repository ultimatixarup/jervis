"""REPL history. Nothing here may stop the REPL starting."""

from __future__ import annotations

from pathlib import Path

from jervis_brain.history import readline_history


def test_a_missing_history_file_is_the_normal_first_run(tmp_path: Path) -> None:
    with readline_history(tmp_path / "nested" / "repl_history"):
        pass


def test_entries_round_trip(tmp_path: Path) -> None:
    """Assert the round trip, not the file bytes.

    macOS ships a libedit-backed readline that writes its own format - a
    `_HiStOrY_V2_` header and `\\040` for spaces - so grepping the file for the
    typed line fails on macOS and passes on GNU readline.
    """
    import readline

    path = tmp_path / "repl_history"
    typed = "what's on my desktop"
    with readline_history(path):
        readline.add_history(typed)
    assert path.exists()

    readline.clear_history()
    with readline_history(path):
        recalled = [
            readline.get_history_item(i)
            for i in range(1, readline.get_current_history_length() + 1)
        ]
    assert typed in recalled


def test_an_unwritable_location_is_survived(tmp_path: Path) -> None:
    """A read-only home must not stop you talking to Jervis."""
    blocker = tmp_path / "a-file-not-a-dir"
    blocker.write_text("x")
    with readline_history(blocker / "repl_history"):
        pass


def test_history_is_capped(tmp_path: Path) -> None:
    import readline

    with readline_history(tmp_path / "h", max_entries=5):
        assert readline.get_history_length() == 5
