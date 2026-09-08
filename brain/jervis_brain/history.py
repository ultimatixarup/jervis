"""Line editing and history for the REPL.

Importing `readline` is the whole trick: `click.prompt` calls `input()`, which picks up
readline's editing and history automatically once the module is imported.

Nothing here raises. A missing history file is the normal first run, and an unwritable
one is not a reason to refuse to start - same rule as the audit log.

Deliberately no tab completion. macOS ships a libedit-backed readline whose bind syntax
differs from GNU readline, and completion is the one feature that reliably breaks on it.
History and editing need no bind strings at all.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("jervis.history")

MAX_ENTRIES = 1000


@contextmanager
def readline_history(path: Path, max_entries: int = MAX_ENTRIES) -> Iterator[None]:
    """Load history on the way in, save it on the way out."""
    try:
        import readline
    except ImportError:  # pragma: no cover - readline is in the stdlib on macOS/Linux
        log.debug("readline unavailable; no history or line editing")
        yield
        return

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        readline.read_history_file(path)
    except (OSError, PermissionError):
        log.debug("no readable history at %s", path)

    readline.set_history_length(max_entries)
    try:
        yield
    finally:
        try:
            readline.write_history_file(path)
        except (OSError, PermissionError):
            log.debug("could not write history to %s", path)
