"""The HTTP client the voice loop talks to the brain through."""

from __future__ import annotations

import httpx
import pytest

from jervis_voice.brain import BrainClient, Reply


def client_with(handler: object) -> BrainClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return BrainClient(base_url="http://brain", client=httpx.Client(transport=transport))


def test_ask_parses_a_reply() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ask"
        return httpx.Response(
            200,
            json={"reply": "Two files.", "session_id": "voice", "tool_calls": ["macos.list_dir"]},
        )

    reply = client_with(handler).ask("what's in Downloads")
    assert reply.text == "Two files."
    assert reply.tool_calls == ("macos.list_dir",)
    assert not reply.awaiting_confirmation


def test_a_pending_confirmation_is_surfaced() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"reply": "Bin it?", "pending_confirmation": "trash"})

    reply = client_with(handler).ask("delete it")
    assert reply.awaiting_confirmation
    assert reply.pending_confirmation == "trash"


def test_confirm_hits_the_confirm_route() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"reply": "Done."})

    assert client_with(handler).confirm("yes").text == "Done."
    assert seen == ["/confirm"]


def test_the_session_id_is_sent() -> None:
    import json

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"reply": "ok"})

    BrainClient(
        base_url="http://brain",
        session_id="kitchen",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ).ask("hello")
    assert seen["session_id"] == "kitchen"


def test_an_http_error_is_raised_not_swallowed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    with pytest.raises(httpx.HTTPStatusError):
        client_with(handler).ask("hello")


def test_health() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"ok": True, "tools": []})

    assert client_with(handler).health()["ok"] is True


def test_wait_until_ready_gives_up() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert client_with(handler).wait_until_ready(attempts=2, delay=0.01) is False


def test_wait_until_ready_succeeds() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    assert client_with(handler).wait_until_ready(attempts=1, delay=0.01) is True


def test_reply_defaults() -> None:
    assert Reply("hi").tool_calls == ()
    assert not Reply("hi").awaiting_confirmation
