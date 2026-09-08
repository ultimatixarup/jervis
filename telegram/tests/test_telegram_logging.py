"""The bot token must never reach a log file.

Every Bot API call carries the token in the URL, and httpx logs URLs at INFO. Without
this the token lands in ~/.jervis/logs/telegram.log in plaintext.
"""

from __future__ import annotations

import logging

from jervis_telegram.logging_setup import RedactToken, install

TOKEN = "8930444067:AAFVfakefakefakefakefakefakefakefake"


def record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord("httpx", logging.INFO, __file__, 1, msg, args or None, None)


def test_a_url_containing_the_token_is_redacted() -> None:
    entry = record(f"HTTP Request: POST https://api.telegram.org/bot{TOKEN}/getMe 200 OK")
    RedactToken(TOKEN).filter(entry)
    assert TOKEN not in entry.getMessage()
    assert "token redacted" in entry.getMessage()


def test_the_token_is_redacted_from_format_arguments() -> None:
    entry = record("calling %s", f"https://api.telegram.org/bot{TOKEN}/getUpdates")
    RedactToken(TOKEN).filter(entry)
    assert TOKEN not in entry.getMessage()


def test_a_single_non_tuple_argument_is_handled() -> None:
    entry = logging.LogRecord("x", logging.INFO, __file__, 1, "%s", (TOKEN,), None)
    RedactToken(TOKEN).filter(entry)
    assert TOKEN not in entry.getMessage()


def test_unrelated_records_pass_through_untouched() -> None:
    entry = record("Listening for updates")
    assert RedactToken(TOKEN).filter(entry) is True
    assert entry.getMessage() == "Listening for updates"


def test_an_empty_token_is_not_a_wildcard() -> None:
    """Redacting the empty string would mangle every message."""
    entry = record("perfectly ordinary line")
    RedactToken("").filter(entry)
    assert entry.getMessage() == "perfectly ordinary line"


def test_install_quietens_httpx() -> None:
    install(TOKEN)
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


def test_install_redacts_through_a_real_handler(caplog: object) -> None:
    """End to end: what actually reaches a handler carries no token."""
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        install(TOKEN)
        logging.getLogger("jervis.test").warning(
            "POST https://api.telegram.org/bot%s/sendMessage", TOKEN
        )
        handler.flush()
        assert TOKEN not in stream.getvalue()
    finally:
        root.removeHandler(handler)
