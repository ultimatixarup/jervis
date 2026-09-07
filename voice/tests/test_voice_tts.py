"""Speech output."""

from __future__ import annotations

import time

import pytest

from jervis_voice.tts import (
    ElevenLabsBackend,
    RecordingBackend,
    SayBackend,
    build,
    strip_for_speech,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("`code`", "code"),
        ("**bold**", "bold"),
        ("# Heading", "Heading"),
        ("- one\n- two", "one two"),
        ("line one\nline two", "line one line two"),
        ("  spaced  ", "spaced"),
        ("", ""),
    ],
)
def test_strip_for_speech(raw: str, expected: str) -> None:
    assert strip_for_speech(raw) == expected


def test_recording_backend() -> None:
    backend = RecordingBackend()
    backend.speak("hello")
    assert backend.spoken == ["hello"]
    assert not backend.is_speaking()
    backend.stop()
    assert backend.stops == 1


def test_recording_backend_can_pretend_to_be_speaking() -> None:
    backend = RecordingBackend(speaking_frames=2)
    backend.speak("a long answer")
    assert backend.is_speaking()
    assert backend.is_speaking()
    assert not backend.is_speaking()


def test_build_selects_a_backend() -> None:
    assert isinstance(build("say"), SayBackend)
    assert isinstance(build("elevenlabs"), ElevenLabsBackend)
    assert isinstance(build("none"), RecordingBackend)


def test_build_rejects_the_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown tts_backend"):
        build("festival")


def test_kokoro_says_it_is_not_wired_up() -> None:
    """Better a clear message than a backend that silently says nothing."""
    with pytest.raises(NotImplementedError, match="not wired up"):
        build("kokoro")


def test_elevenlabs_without_a_key_explains_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
        ElevenLabsBackend().synthesise("hello")


def test_elevenlabs_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["json"] = kwargs.get("json")
        # raise_for_status needs the originating request attached.
        return httpx.Response(200, content=b"audio-bytes", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    backend = ElevenLabsBackend(voice_id="abc", api_key="key-123")
    assert backend.synthesise("hello") == b"audio-bytes"
    assert captured["url"] == f"{ElevenLabsBackend.API}/abc"
    assert captured["headers"]["xi-api-key"] == "key-123"  # type: ignore[index]
    assert captured["json"]["text"] == "hello"  # type: ignore[index]


@pytest.mark.macos
def test_say_backend_speaks_and_can_be_stopped() -> None:
    backend = SayBackend(voice="Daniel", rate=400)
    backend.speak("testing one two three four five six seven eight")
    assert backend.is_speaking()
    backend.stop()
    time.sleep(0.2)
    assert not backend.is_speaking()


@pytest.mark.macos
def test_say_backend_ignores_empty_text() -> None:
    backend = SayBackend()
    backend.speak("   ")
    assert not backend.is_speaking()


@pytest.mark.macos
def test_stopping_when_not_speaking_is_harmless() -> None:
    SayBackend().stop()
