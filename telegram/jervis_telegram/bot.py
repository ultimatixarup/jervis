"""The Telegram loop: read a message, ask the brain, reply.

A client of the brain's HTTP endpoint, exactly like the voice loop. The permission
guard, the audit log and the confirm tier are all unchanged and unaware of Telegram -
this only decides how a question arrives and how an answer is shown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from .api import Callback, Message, TelegramAPI, TelegramError, Update
from .settings import Settings

log = logging.getLogger("jervis.telegram.bot")

CONFIRM_YES = "jervis:yes"
CONFIRM_NO = "jervis:no"
CONFIRM_BUTTONS = [("Yes, go ahead", CONFIRM_YES), ("No", CONFIRM_NO)]

HELP = """\
Just say what you want.

/help    this
/status  what Jervis can reach
/whoami  your Telegram id, for the allowlist
/new     forget this conversation and start fresh
"""

REFUSED = (
    "I don't take instructions from this account.\n\n"
    "If this is you, add {user_id} to telegram.allowed_user_ids in ~/.jervis/config.yaml."
)


@dataclass
class Pending:
    """A confirm-tier action waiting on a tap."""

    chat_id: int
    message_id: int
    summary: str


@dataclass
class Bot:
    api: TelegramAPI
    settings: Settings
    brain: Any = None
    _pending: dict[int, Pending] = field(default_factory=dict)
    stopped: bool = False

    def __post_init__(self) -> None:
        if self.brain is None:
            self.brain = httpx.Client(timeout=300.0)

    # --- the loop ---------------------------------------------------------------------

    def run(self, max_updates: int | None = None) -> None:
        handled = 0
        while not self.stopped:
            try:
                updates = self.api.poll(self.settings.poll_timeout)
            except (TelegramError, httpx.HTTPError):
                # A dropped connection is normal on a laptop that sleeps. Keep going.
                log.warning("poll failed; retrying", exc_info=True)
                continue
            for update in updates:
                self.handle(update)
                handled += 1
                if max_updates is not None and handled >= max_updates:
                    return

    def handle(self, update: Update) -> None:
        if not self.settings.may_talk(update.user_id):
            self.refuse(update)
            return
        try:
            if isinstance(update, Callback):
                self.on_callback(update)
            else:
                self.on_message(update)
        except Exception:
            log.exception("failed handling update %s", update.update_id)
            self.say(update.chat_id, "Something went wrong at my end. It is in the log.")

    def refuse(self, update: Update) -> None:
        """Say no, and say it once - without telling a stranger what Jervis can do."""
        log.warning(
            "refused user_id=%s username=%r chat=%s",
            update.user_id,
            getattr(update, "username", ""),
            update.chat_id,
        )
        if isinstance(update, Callback):
            self.api.answer_callback(update.callback_id, "Not authorised.")
            return
        self.say(update.chat_id, REFUSED.format(user_id=update.user_id))

    # --- messages ---------------------------------------------------------------------

    def on_message(self, message: Message) -> None:
        text = message.text.strip()
        if message.is_command and self.command(message, text):
            return

        # A typed "yes" answers an outstanding question just as a tapped button does.
        if message.chat_id in self._pending:
            self.resolve(message.chat_id, text)
            return

        self.api.typing(message.chat_id)
        self.exchange(message.chat_id, "/ask", text)

    def command(self, message: Message, text: str) -> bool:
        command = text.split()[0].lstrip("/").split("@")[0].lower()
        match command:
            case "start" | "help":
                self.say(message.chat_id, HELP)
            case "whoami":
                self.say(message.chat_id, f"Your Telegram id is {message.user_id}.")
            case "status":
                self.say(message.chat_id, self.status())
            case "new":
                self._pending.pop(message.chat_id, None)
                self.exchange(message.chat_id, "/ask", "Forget what we were doing.")
            case _:
                return False
        return True

    def status(self) -> str:
        try:
            health = self.brain.get(f"{self.settings.brain_url}/health").json()
        except (httpx.HTTPError, ValueError):
            return "I can't reach my brain. Is `jervis serve` running?"
        tools = health.get("tools") or []
        failures = health.get("server_failures") or {}
        lines = [f"Model: {health.get('model')}", f"Tools: {len(tools)}"]
        if failures:
            lines.append(f"Not working: {', '.join(failures)}")
        return "\n".join(lines)

    # --- confirmations -----------------------------------------------------------------

    def on_callback(self, callback: Callback) -> None:
        pending = self._pending.get(callback.chat_id)
        if pending is None:
            self.api.answer_callback(callback.callback_id, "That has already been settled.")
            return
        answer = "yes" if callback.data == CONFIRM_YES else "no"
        self.api.answer_callback(callback.callback_id)
        # Take the buttons away so the same action cannot be confirmed twice.
        self.api.edit(
            callback.chat_id,
            pending.message_id,
            f"{pending.summary}\n\n— {'yes' if answer == 'yes' else 'no'}",
        )
        self.resolve(callback.chat_id, answer)

    def resolve(self, chat_id: int, answer: str) -> None:
        del self._pending[chat_id]
        self.api.typing(chat_id)
        self.exchange(chat_id, "/confirm", answer)

    # --- talking to the brain ----------------------------------------------------------

    def exchange(self, chat_id: int, path: str, text: str) -> None:
        try:
            response = self.brain.post(
                f"{self.settings.brain_url}{path}",
                json={"text": text, "session_id": session_for(chat_id)},
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError:
            log.exception("brain call failed")
            self.say(
                chat_id,
                "I can't reach my brain. Start it with `jervis serve`, or "
                "`scripts/start.sh --telegram` to run both.",
            )
            return

        reply = str(body.get("reply") or "").strip() or "(nothing to say)"
        summary = body.get("pending_confirmation")
        if summary:
            message_id = self.api.send(chat_id, reply, buttons=CONFIRM_BUTTONS)
            self._pending[chat_id] = Pending(chat_id, message_id, reply)
            return
        self.say(chat_id, _with_tools(reply, body.get("tool_calls") or []))

    def say(self, chat_id: int, text: str) -> None:
        try:
            self.api.send(chat_id, text)
        except (TelegramError, httpx.HTTPError):
            log.exception("could not send to %s", chat_id)

    def close(self) -> None:
        self.brain.close()
        self.api.close()


def session_for(chat_id: int) -> str:
    """One conversation per chat, kept apart from the REPL's and the voice loop's."""
    return f"telegram:{chat_id}"


def _with_tools(reply: str, tool_calls: list[str]) -> str:
    """Note what was used, so an answer is not indistinguishable from a guess."""
    if not tool_calls:
        return reply
    used = ", ".join(dict.fromkeys(tool_calls))
    return f"{reply}\n\n· {used}"
