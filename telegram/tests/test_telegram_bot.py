"""The bot loop, against a mocked Telegram and a mocked brain."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from jervis_telegram.api import Callback, Message, TelegramAPI, parse_update
from jervis_telegram.bot import CONFIRM_NO, CONFIRM_YES, Bot, Pending, session_for
from jervis_telegram.settings import Settings

OWNER = 4242
STRANGER = 9999


class FakeTelegram:
    """Records every Bot API call and replays canned updates."""

    def __init__(self, updates: list[list[dict[str, Any]]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.batches = list(updates or [])
        self._next_message_id = 100

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        params = json.loads(request.content or b"{}")
        self.calls.append((method, params))

        if method == "getUpdates":
            batch = self.batches.pop(0) if self.batches else []
            return httpx.Response(200, json={"ok": True, "result": batch})
        if method == "sendMessage":
            self._next_message_id += 1
            return httpx.Response(
                200, json={"ok": True, "result": {"message_id": self._next_message_id}}
            )
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "jervisbot"}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def sent(self) -> list[dict[str, Any]]:
        return [p for name, p in self.calls if name == "sendMessage"]

    def texts(self) -> list[str]:
        return [str(p.get("text", "")) for p in self.sent()]

    def named(self, method: str) -> list[dict[str, Any]]:
        return [p for name, p in self.calls if name == method]


class FakeBrain:
    """Replays scripted /ask and /confirm responses."""

    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = list(replies)
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(
                200, json={"ok": True, "model": "claude-sonnet-5", "tools": ["macos.list_dir"]}
            )
        self.requests.append((path, json.loads(request.content or b"{}")))
        if not self.replies:
            raise AssertionError(f"brain asked again with no reply scripted: {path}")
        return httpx.Response(200, json=self.replies.pop(0))


def make_bot(telegram: FakeTelegram, brain: FakeBrain, allowed: set[int] | None = None) -> Bot:
    settings = Settings(
        token="123:abc",
        allowed_user_ids=frozenset(allowed if allowed is not None else {OWNER}),
        brain_url="http://brain",
    )
    api = TelegramAPI(token="123:abc", client=httpx.Client(transport=telegram.transport()))
    return Bot(api=api, settings=settings, brain=brain.client())


def message(text: str, user_id: int = OWNER, chat_id: int = 1, update_id: int = 1) -> Message:
    return Message(update_id=update_id, chat_id=chat_id, user_id=user_id, text=text)


def callback(data: str, user_id: int = OWNER, chat_id: int = 1) -> Callback:
    return Callback(
        update_id=2,
        callback_id="cb1",
        chat_id=chat_id,
        user_id=user_id,
        message_id=101,
        data=data,
    )


# --- authorisation --------------------------------------------------------------------


def test_a_stranger_is_refused_and_the_brain_never_hears_them() -> None:
    """The load-bearing test. Jervis can run shell commands."""
    telegram, brain = FakeTelegram(), FakeBrain([])
    bot = make_bot(telegram, brain)
    bot.handle(message("delete everything on the desktop", user_id=STRANGER))

    assert brain.requests == [], "an unauthorised message must never reach the brain"
    assert "don't take instructions" in telegram.texts()[0]


def test_a_stranger_tapping_a_button_is_refused() -> None:
    telegram, brain = FakeTelegram(), FakeBrain([])
    bot = make_bot(telegram, brain)
    bot._pending[1] = Pending(chat_id=1, message_id=101, summary="bin it")
    bot.handle(callback(CONFIRM_YES, user_id=STRANGER))

    assert brain.requests == []
    assert telegram.named("answerCallbackQuery")[0]["text"] == "Not authorised."


def test_an_empty_allowlist_refuses_even_the_owner() -> None:
    telegram, brain = FakeTelegram(), FakeBrain([])
    bot = make_bot(telegram, brain, allowed=set())
    bot.handle(message("hello"))
    assert brain.requests == []


def test_the_refusal_does_not_describe_what_jervis_can_do() -> None:
    telegram, brain = FakeTelegram(), FakeBrain([])
    make_bot(telegram, brain).handle(message("hi", user_id=STRANGER))
    refusal = telegram.texts()[0].lower()
    for leak in ("shell", "tool", "macos", "trash", "file"):
        assert leak not in refusal


# --- ordinary exchanges ----------------------------------------------------------------


def test_a_question_reaches_the_brain_and_the_answer_comes_back() -> None:
    telegram = FakeTelegram()
    brain = FakeBrain([{"reply": "Two files.", "tool_calls": ["macos.list_dir"]}])
    make_bot(telegram, brain).handle(message("what's in Downloads"))

    path, body = brain.requests[0]
    assert path == "/ask"
    assert body["text"] == "what's in Downloads"
    assert body["session_id"] == session_for(1)
    assert "Two files." in telegram.texts()[0]


def test_the_tools_used_are_shown() -> None:
    """An answer should not be indistinguishable from a guess."""
    telegram = FakeTelegram()
    brain = FakeBrain([{"reply": "Two files.", "tool_calls": ["macos.list_dir", "macos.list_dir"]}])
    make_bot(telegram, brain).handle(message("what's in Downloads"))
    assert "· macos.list_dir" in telegram.texts()[0]
    assert telegram.texts()[0].count("macos.list_dir") == 1, "repeats collapse"


def test_each_chat_is_its_own_conversation() -> None:
    telegram = FakeTelegram()
    brain = FakeBrain([{"reply": "a"}, {"reply": "b"}])
    bot = make_bot(telegram, brain)
    bot.handle(message("one", chat_id=1))
    bot.handle(message("two", chat_id=2))
    assert brain.requests[0][1]["session_id"] != brain.requests[1][1]["session_id"]


def test_a_brain_that_is_down_is_explained() -> None:
    telegram = FakeTelegram()

    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    settings = Settings(token="t", allowed_user_ids=frozenset({OWNER}), brain_url="http://brain")
    api = TelegramAPI(token="t", client=httpx.Client(transport=telegram.transport()))
    bot = Bot(api=api, settings=settings, brain=httpx.Client(transport=httpx.MockTransport(refuse)))
    bot.handle(message("hello"))
    assert "can't reach my brain" in telegram.texts()[0]


# --- confirmations ----------------------------------------------------------------------


def test_a_confirm_tier_action_offers_buttons_and_waits() -> None:
    telegram = FakeTelegram()
    brain = FakeBrain(
        [
            {
                "reply": "macos.move_to_trash: report.pdf. Shall I go ahead?",
                "pending_confirmation": "macos.move_to_trash: report.pdf",
            }
        ]
    )
    bot = make_bot(telegram, brain)
    bot.handle(message("bin report.pdf"))

    sent = telegram.sent()[0]
    labels = [b["text"] for b in sent["reply_markup"]["inline_keyboard"][0]]
    assert labels == ["Yes, go ahead", "No"]
    assert "report.pdf" in sent["text"]
    assert len(brain.requests) == 1, "nothing may run before the answer"
    assert 1 in bot._pending


def test_tapping_yes_confirms() -> None:
    telegram = FakeTelegram()
    brain = FakeBrain(
        [
            {"reply": "Shall I go ahead?", "pending_confirmation": "bin report.pdf"},
            {"reply": "Moved it to the Trash."},
        ]
    )
    bot = make_bot(telegram, brain)
    bot.handle(message("bin report.pdf"))
    bot.handle(callback(CONFIRM_YES))

    assert brain.requests[1] == ("/confirm", {"text": "yes", "session_id": session_for(1)})
    assert "Moved it to the Trash." in telegram.texts()[-1]
    assert 1 not in bot._pending


def test_tapping_no_declines() -> None:
    telegram = FakeTelegram()
    brain = FakeBrain(
        [
            {"reply": "Shall I go ahead?", "pending_confirmation": "bin report.pdf"},
            {"reply": "Left alone."},
        ]
    )
    bot = make_bot(telegram, brain)
    bot.handle(message("bin report.pdf"))
    bot.handle(callback(CONFIRM_NO))
    assert brain.requests[1][1]["text"] == "no"


def test_typing_yes_works_as_well_as_tapping() -> None:
    telegram = FakeTelegram()
    brain = FakeBrain(
        [
            {"reply": "Shall I go ahead?", "pending_confirmation": "bin it"},
            {"reply": "Done."},
        ]
    )
    bot = make_bot(telegram, brain)
    bot.handle(message("bin it"))
    bot.handle(message("yes"))
    assert brain.requests[1] == ("/confirm", {"text": "yes", "session_id": session_for(1)})


def test_the_buttons_are_removed_once_answered() -> None:
    """The same action must not be confirmable twice."""
    telegram = FakeTelegram()
    brain = FakeBrain([{"reply": "Shall I?", "pending_confirmation": "bin it"}, {"reply": "Done."}])
    bot = make_bot(telegram, brain)
    bot.handle(message("bin it"))
    bot.handle(callback(CONFIRM_YES))

    edit = telegram.named("editMessageText")[0]
    assert edit["reply_markup"] == {"inline_keyboard": []}


def test_a_stale_button_is_shrugged_off() -> None:
    telegram, brain = FakeTelegram(), FakeBrain([])
    make_bot(telegram, brain).handle(callback(CONFIRM_YES))
    assert brain.requests == []
    assert "already been settled" in telegram.named("answerCallbackQuery")[0]["text"]


# --- commands -----------------------------------------------------------------------------


def test_whoami_gives_the_id_needed_for_the_allowlist() -> None:
    telegram, brain = FakeTelegram(), FakeBrain([])
    make_bot(telegram, brain).handle(message("/whoami"))
    assert str(OWNER) in telegram.texts()[0]
    assert brain.requests == []


def test_help_does_not_reach_the_brain() -> None:
    telegram, brain = FakeTelegram(), FakeBrain([])
    make_bot(telegram, brain).handle(message("/help"))
    assert "/status" in telegram.texts()[0]
    assert brain.requests == []


def test_status_reports_the_brain() -> None:
    telegram, brain = FakeTelegram(), FakeBrain([])
    make_bot(telegram, brain).handle(message("/status"))
    assert "claude-sonnet-5" in telegram.texts()[0]


def test_an_unknown_command_is_just_a_question() -> None:
    """`/dinner` is not a command; it is something to ask about."""
    telegram = FakeTelegram()
    brain = FakeBrain([{"reply": "I don't order dinner yet."}])
    make_bot(telegram, brain).handle(message("/dinner"))
    assert brain.requests[0][1]["text"] == "/dinner"


# --- the polling loop ---------------------------------------------------------------------


def test_run_processes_updates_then_stops() -> None:
    telegram = FakeTelegram(
        updates=[
            [
                {
                    "update_id": 7,
                    "message": {
                        "chat": {"id": 1},
                        "from": {"id": OWNER, "username": "arup"},
                        "text": "hello",
                    },
                }
            ]
        ]
    )
    brain = FakeBrain([{"reply": "Hello."}])
    bot = make_bot(telegram, brain)
    bot.run(max_updates=1)
    assert brain.requests[0][1]["text"] == "hello"


def test_a_failed_poll_does_not_kill_the_loop() -> None:
    """A laptop that sleeps drops the long-poll connection; that is not fatal."""
    state = {"polls": 0}
    sent: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getUpdates":
            state["polls"] += 1
            if state["polls"] == 1:
                raise httpx.ConnectError("laptop was asleep")
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 9,
                            "message": {
                                "chat": {"id": 1},
                                "from": {"id": OWNER},
                                "text": "still there?",
                            },
                        }
                    ],
                },
            )
        if method == "sendMessage":
            sent.append(json.loads(request.content)["text"])
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(200, json={"ok": True, "result": True})

    brain = FakeBrain([{"reply": "Still here."}])
    settings = Settings(token="t", allowed_user_ids=frozenset({OWNER}), brain_url="http://brain")
    api = TelegramAPI(token="t", client=httpx.Client(transport=httpx.MockTransport(handle)))
    bot = Bot(api=api, settings=settings, brain=brain.client())

    bot.run(max_updates=1)

    assert state["polls"] == 2, "it must poll again after the failure"
    assert sent == ["Still here."]


# --- parsing -------------------------------------------------------------------------------


def test_non_text_messages_are_ignored() -> None:
    """A sticker is not an instruction."""
    assert parse_update({"update_id": 1, "message": {"chat": {"id": 1}, "sticker": {}}}) is None
    assert parse_update({"update_id": 1, "edited_message": {}}) is None


def test_a_long_answer_is_clipped_to_telegrams_limit() -> None:
    telegram = FakeTelegram()
    brain = FakeBrain([{"reply": "x" * 9000}])
    make_bot(telegram, brain).handle(message("say a lot"))
    assert len(telegram.texts()[0]) <= 4096
    assert "truncated" in telegram.texts()[0]


@pytest.mark.parametrize("text", ["", "   "])
def test_an_empty_reply_still_says_something(text: str) -> None:
    telegram = FakeTelegram()
    brain = FakeBrain([{"reply": text}])
    make_bot(telegram, brain).handle(message("hm"))
    assert telegram.texts()[0].strip()
