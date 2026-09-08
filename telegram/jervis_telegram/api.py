"""A thin Telegram Bot API client.

Raw HTTP rather than a bot framework: Jervis needs six methods, the API is plain JSON,
and the rest of this repo already talks HTTP with httpx directly (see
`voice/jervis_voice/brain.py`). A framework would bring its own event loop and
dispatcher to sit alongside the one the brain already owns.

Long polling, not webhooks. A webhook needs a public HTTPS endpoint pointing at a
laptop behind NAT; `getUpdates` needs nothing but an outbound connection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("jervis.telegram.api")

BASE = "https://api.telegram.org"

# Telegram caps a message at 4096 characters.
MAX_MESSAGE = 4096


class TelegramError(RuntimeError):
    """The Bot API said no."""


@dataclass(frozen=True)
class Message:
    """An incoming message, reduced to what Jervis cares about."""

    update_id: int
    chat_id: int
    user_id: int
    text: str
    username: str = ""

    @property
    def is_command(self) -> bool:
        return self.text.startswith("/")


@dataclass(frozen=True)
class Callback:
    """A tapped inline button."""

    update_id: int
    callback_id: str
    chat_id: int
    user_id: int
    message_id: int
    data: str
    username: str = ""


Update = Message | Callback


@dataclass
class TelegramAPI:
    token: str
    client: Any = None
    timeout: float = 65.0
    _offset: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not self.token:
            raise TelegramError(
                "No TELEGRAM_BOT_TOKEN. Create a bot with @BotFather in Telegram, then "
                "put the token in ~/.jervis/.env."
            )
        if self.client is None:
            self.client = httpx.Client(timeout=self.timeout)

    # --- plumbing --------------------------------------------------------------------

    def call(self, method: str, **params: Any) -> Any:
        response = self.client.post(f"{BASE}/bot{self.token}/{method}", json=params)
        if response.status_code == 401:
            raise TelegramError("Telegram rejected the bot token. Check TELEGRAM_BOT_TOKEN.")
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('description', body)}")
        return body.get("result")

    def close(self) -> None:
        self.client.close()

    # --- reading ---------------------------------------------------------------------

    def me(self) -> dict[str, Any]:
        return dict(self.call("getMe"))

    def poll(self, timeout: int = 50) -> list[Update]:
        """Long-poll for updates. Blocks up to *timeout* seconds, then returns."""
        raw = self.call(
            "getUpdates",
            offset=self._offset or None,
            timeout=timeout,
            allowed_updates=["message", "callback_query"],
        )
        updates: list[Update] = []
        for item in raw or []:
            self._offset = max(self._offset, int(item["update_id"]) + 1)
            parsed = parse_update(item)
            if parsed is not None:
                updates.append(parsed)
        return updates

    def drop_pending(self) -> None:
        """Discard anything queued while Jervis was off.

        Acting on a message sent hours ago, out of context, is worse than ignoring it -
        especially when the message might be "delete the old logs".
        """
        raw = self.call("getUpdates", offset=-1, timeout=0)
        for item in raw or []:
            self._offset = max(self._offset, int(item["update_id"]) + 1)

    # --- writing ---------------------------------------------------------------------

    def send(self, chat_id: int, text: str, buttons: list[tuple[str, str]] | None = None) -> int:
        """Send a message, returning its id so it can be edited later."""
        params: dict[str, Any] = {"chat_id": chat_id, "text": _clip(text)}
        if buttons:
            params["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in buttons]
                ]
            }
        result = self.call("sendMessage", **params)
        return int(result["message_id"])

    def edit(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
    ) -> None:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": _clip(text),
        }
        params["reply_markup"] = (
            {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in buttons]
                ]
            }
            if buttons
            else {"inline_keyboard": []}
        )
        try:
            self.call("editMessageText", **params)
        except TelegramError as exc:
            # "message is not modified" is routine when a status line has not changed.
            if "not modified" not in str(exc):
                raise

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Stop the button's spinner. Telegram sulks if this is never sent."""
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    def typing(self, chat_id: int) -> None:
        try:
            self.call("sendChatAction", chat_id=chat_id, action="typing")
        except (TelegramError, httpx.HTTPError):
            log.debug("could not send typing indicator", exc_info=True)


def parse_update(item: dict[str, Any]) -> Update | None:
    """Turn one raw update into a Message or Callback, or None if it is neither."""
    update_id = int(item["update_id"])

    if "callback_query" in item:
        query = item["callback_query"]
        message = query.get("message") or {}
        return Callback(
            update_id=update_id,
            callback_id=str(query["id"]),
            chat_id=int(message.get("chat", {}).get("id", 0)),
            user_id=int(query.get("from", {}).get("id", 0)),
            message_id=int(message.get("message_id", 0)),
            data=str(query.get("data", "")),
            username=str(query.get("from", {}).get("username", "")),
        )

    message = item.get("message")
    if not message:
        return None
    text = message.get("text")
    if not text:
        # Photos, stickers, voice notes: nothing Jervis can act on yet.
        return None
    return Message(
        update_id=update_id,
        chat_id=int(message["chat"]["id"]),
        user_id=int(message.get("from", {}).get("id", 0)),
        text=str(text),
        username=str(message.get("from", {}).get("username", "")),
    )


def _clip(text: str) -> str:
    if len(text) <= MAX_MESSAGE:
        return text or "(nothing to say)"
    return text[: MAX_MESSAGE - 20] + "\n… (truncated)"
