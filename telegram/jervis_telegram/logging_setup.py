"""Keep the bot token out of the logs.

Every Bot API call puts the token in the URL, and httpx logs the URL at INFO. That
writes the token, in plaintext, into ~/.jervis/logs/telegram.log - a file that gets
tailed, copied into bug reports and swept up by backups. Anyone holding it controls
the bot, which has a line to this Mac.

Two layers, because the first is easy to undo by accident: httpx is quietened, and a
filter redacts the token from anything that reaches a handler regardless of source.
"""

from __future__ import annotations

import logging

REDACTED = "bot<token redacted>"


class RedactToken(logging.Filter):
    """Replace the token wherever it appears in a record."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.token:
            return True
        if isinstance(record.msg, str) and self.token in record.msg:
            record.msg = record.msg.replace(f"bot{self.token}", REDACTED)
            record.msg = record.msg.replace(self.token, "<token redacted>")
        if record.args:
            record.args = tuple(_scrub(a, self.token) for a in _as_tuple(record.args))
        return True


def _as_tuple(args: object) -> tuple[object, ...]:
    return args if isinstance(args, tuple) else (args,)


def _scrub(value: object, token: str) -> object:
    if isinstance(value, str) and token in value:
        return value.replace(f"bot{token}", REDACTED).replace(token, "<token redacted>")
    return value


def install(token: str) -> None:
    """Quieten httpx and redact the token everywhere else."""
    # httpx logs the full request URL at INFO, token and all.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    redactor = RedactToken(token)
    root = logging.getLogger()
    root.addFilter(redactor)
    for handler in root.handlers:
        handler.addFilter(redactor)
