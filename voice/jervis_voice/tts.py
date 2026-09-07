"""Text to speech.

`say` is the default because it is always present, needs no key and no network, and
starts speaking immediately. The others are opt-in via voice.tts_backend.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

log = logging.getLogger("jervis.voice.tts")


class TTSBackend(Protocol):
    def speak(self, text: str) -> None: ...

    def stop(self) -> None: ...

    def is_speaking(self) -> bool: ...


def strip_for_speech(text: str) -> str:
    """Remove things that sound absurd read aloud.

    The brain is told not to emit markdown, but a stray backtick or bullet still gets
    through, and `say` reads punctuation runs as noise.
    """
    cleaned = text.replace("`", "").replace("*", "").replace("#", "")
    lines = [line.strip().lstrip("-").strip() for line in cleaned.splitlines()]
    return " ".join(line for line in lines if line).strip()


class SayBackend:
    """macOS `say`. Speaks asynchronously so barge-in can interrupt it."""

    def __init__(self, voice: str = "Daniel", rate: int | None = None) -> None:
        self.voice = voice
        self.rate = rate
        self._process: subprocess.Popen[bytes] | None = None

    def speak(self, text: str) -> None:
        spoken = strip_for_speech(text)
        if not spoken:
            return
        self.stop()
        argv = ["say"]
        if self.voice:
            argv += ["-v", self.voice]
        if self.rate:
            argv += ["-r", str(self.rate)]
        argv += ["--", spoken]
        self._process = subprocess.Popen(argv)

    def wait(self, timeout: float | None = None) -> None:
        if self._process is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._process.wait(timeout=timeout)

    def is_speaking(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()


class ElevenLabsBackend:
    """Better voice, at the cost of a key, a network round trip and latency."""

    API = "https://api.elevenlabs.io/v1/text-to-speech"

    def __init__(
        self,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",
        model: str = "eleven_turbo_v2_5",
        api_key: str | None = None,
    ) -> None:
        self.voice_id = voice_id
        self.model = model
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self._process: subprocess.Popen[bytes] | None = None

    def synthesise(self, text: str) -> bytes:
        import httpx

        if not self.api_key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY is not set; put it in ~/.jervis/.env or set "
                "voice.tts_backend to say."
            )
        response = httpx.post(
            f"{self.API}/{self.voice_id}",
            headers={"xi-api-key": self.api_key, "accept": "audio/mpeg"},
            json={
                "text": text,
                "model_id": self.model,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.content

    def speak(self, text: str) -> None:
        spoken = strip_for_speech(text)
        if not spoken:
            return
        self.stop()
        audio = self.synthesise(spoken)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            handle.write(audio)
            path = handle.name
        self._process = subprocess.Popen(["afplay", path])

    def is_speaking(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()


class RecordingBackend:
    """Records what would have been said. Used by the tests and the smoke script.

    `speaking_frames` makes it pretend to still be talking for that many polls, so
    barge-in can be tested without a real subprocess.
    """

    def __init__(self, speaking_frames: int = 0) -> None:
        self.spoken: list[str] = []
        self.stops = 0
        self._remaining = 0
        self.speaking_frames = speaking_frames

    def speak(self, text: str) -> None:
        self.spoken.append(text)
        self._remaining = self.speaking_frames

    def is_speaking(self) -> bool:
        if self._remaining > 0:
            self._remaining -= 1
            return True
        return False

    def stop(self) -> None:
        self.stops += 1
        self._remaining = 0


def build(backend: str, voice: str = "Daniel") -> TTSBackend:
    match backend:
        case "say":
            return SayBackend(voice=voice)
        case "elevenlabs":
            return ElevenLabsBackend()
        case "kokoro":
            raise NotImplementedError(
                "The Kokoro backend is not wired up. Use voice.tts_backend: say (the "
                "default) or elevenlabs."
            )
        case "none":
            return RecordingBackend()
        case _:
            raise ValueError(f"Unknown tts_backend {backend!r}; use say or elevenlabs.")


def play_chime() -> None:
    """A short sound so you know Jervis is listening, before you start talking."""
    for candidate in (
        "/System/Library/Sounds/Tink.aiff",
        "/System/Library/Sounds/Pop.aiff",
    ):
        if Path(candidate).exists() and shutil.which("afplay"):
            subprocess.Popen(["afplay", candidate])
            return
    log.debug("no chime sound available")
