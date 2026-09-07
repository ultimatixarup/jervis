"""Audio capture, and the seams that let the loop be tested without a microphone."""

from __future__ import annotations

import queue
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16_000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320 samples
# openwakeword wants 80ms at a time; webrtcvad only accepts 10/20/30ms. 20ms frames
# divide evenly into both, so one stream feeds both without resampling or buffering
# tricks.
WAKE_CHUNK_FRAMES = 4


class AudioSource(Protocol):
    """A stream of 20ms int16 mono frames at 16kHz."""

    def frames(self) -> Iterator[np.ndarray]: ...

    def close(self) -> None: ...


def frame_duration_ms(frame_count: int) -> int:
    return frame_count * FRAME_MS


def rms(frame: np.ndarray) -> float:
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))


def to_float32(audio: np.ndarray) -> np.ndarray:
    """int16 PCM to the [-1, 1] float32 whisper expects."""
    return (audio.astype(np.float32) / 32768.0).clip(-1.0, 1.0)


class MicSource:
    """The real microphone, via sounddevice."""

    def __init__(self, device: int | str | None = None, queue_size: int = 200) -> None:
        self.device = device
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=queue_size)
        self._stream: object | None = None
        self._dropped = 0

    def _callback(self, indata: np.ndarray, _frames: int, _time: object, status: object) -> None:
        if status:  # overflow/underflow; keep going rather than kill the agent
            pass
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            # Better to lose the oldest audio than to block CoreAudio's thread.
            self._dropped += 1

    def start(self) -> None:
        import sounddevice as sd

        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            blocksize=FRAME_SAMPLES,
            channels=1,
            dtype="int16",
            device=self.device,
            callback=self._callback,
        )
        stream.start()
        self._stream = stream

    def frames(self) -> Iterator[np.ndarray]:
        if self._stream is None:
            self.start()
        while True:
            yield self._queue.get()

    @property
    def dropped_frames(self) -> int:
        return self._dropped

    def close(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            stream.stop()  # type: ignore[attr-defined]
            stream.close()  # type: ignore[attr-defined]


class WavSource:
    """A wav file as an audio source, for tests and offline replay.

    After the file runs out it yields silence rather than stopping, so a consumer
    waiting for end-of-utterance behaves as it would against a live microphone in a
    quiet room instead of hanging.
    """

    def __init__(self, path: str | Path, *, pad_silence_ms: int = 2000) -> None:
        self.path = Path(path)
        self.pad_silence_ms = pad_silence_ms

    def frames(self) -> Iterator[np.ndarray]:
        yield from read_wav_frames(self.path)
        silent = np.zeros(FRAME_SAMPLES, dtype=np.int16)
        for _ in range(self.pad_silence_ms // FRAME_MS):
            yield silent

    def close(self) -> None:
        return None


def read_wav(path: str | Path) -> np.ndarray:
    """Read a mono 16kHz int16 wav into one array."""
    with wave.open(str(path), "rb") as handle:
        if handle.getframerate() != SAMPLE_RATE:
            raise ValueError(f"{path} is {handle.getframerate()}Hz; expected {SAMPLE_RATE}")
        if handle.getnchannels() != 1:
            raise ValueError(f"{path} is not mono")
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16)


def read_wav_frames(path: str | Path) -> Iterator[np.ndarray]:
    audio = read_wav(path)
    for start in range(0, len(audio) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        yield audio[start : start + FRAME_SAMPLES]


def write_wav(path: str | Path, audio: np.ndarray) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(audio.astype(np.int16).tobytes())
