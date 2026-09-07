"""End-of-utterance detection.

Someone speaking to an assistant pauses mid-sentence. Stopping at the first silent
frame cuts them off, so speech has to be *absent* for a sustained window before the
utterance is considered finished.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import numpy as np

from .audio import FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE


@dataclass(frozen=True)
class RecordingLimits:
    silence_ms: int = 700
    max_ms: int = 15_000
    # Below this, whatever was captured is noise rather than an utterance.
    min_speech_ms: int = 200
    # How long to wait for someone to start speaking at all before giving up.
    lead_in_ms: int = 3_000


@dataclass
class Utterance:
    audio: np.ndarray
    speech_ms: int
    stopped_because: str  # "silence" | "max_length" | "no_speech" | "stream_ended"

    @property
    def is_empty(self) -> bool:
        return self.stopped_because == "no_speech" or self.audio.size == 0


class SpeechDetector:
    """webrtcvad wrapped so it can be swapped out in tests."""

    def __init__(self, aggressiveness: int = 2) -> None:
        import webrtcvad

        self._vad = webrtcvad.Vad(aggressiveness)

    def is_speech(self, frame: np.ndarray) -> bool:
        if frame.size != FRAME_SAMPLES:
            return False
        return bool(self._vad.is_speech(frame.astype(np.int16).tobytes(), SAMPLE_RATE))


class EnergyDetector:
    """Deterministic fallback used by the tests, and when webrtcvad is unavailable."""

    def __init__(self, threshold: float = 500.0) -> None:
        self.threshold = threshold

    def is_speech(self, frame: np.ndarray) -> bool:
        from .audio import rms

        return rms(frame) >= self.threshold


def record_utterance(
    frames: Iterable[np.ndarray],
    detector: SpeechDetector | EnergyDetector,
    limits: RecordingLimits | None = None,
) -> Utterance:
    """Consume frames until the speaker stops, and return what they said.

    Pure with respect to I/O: it reads an iterable and returns audio, so the same code
    runs against a microphone, a wav file, or a hand-built list of frames.
    """
    limits = limits or RecordingLimits()
    max_frames = limits.max_ms // FRAME_MS
    silence_needed = limits.silence_ms // FRAME_MS
    lead_in_frames = limits.lead_in_ms // FRAME_MS

    collected: list[np.ndarray] = []
    speech_frames = 0
    trailing_silence = 0
    heard_speech = False
    reason = "stream_ended"

    for index, frame in enumerate(_iterate(frames)):
        speaking = detector.is_speech(frame)

        if not heard_speech:
            if speaking:
                heard_speech = True
            elif index >= lead_in_frames:
                return Utterance(np.array([], dtype=np.int16), 0, "no_speech")
            else:
                continue  # do not record the pause before they start

        collected.append(frame)
        if speaking:
            speech_frames += 1
            trailing_silence = 0
        else:
            trailing_silence += 1
            if trailing_silence >= silence_needed:
                reason = "silence"
                break

        if len(collected) >= max_frames:
            reason = "max_length"
            break

    if not heard_speech:
        return Utterance(np.array([], dtype=np.int16), 0, "no_speech")

    audio = np.concatenate(collected) if collected else np.array([], dtype=np.int16)
    speech_ms = speech_frames * FRAME_MS
    if speech_ms < limits.min_speech_ms:
        return Utterance(audio, speech_ms, "no_speech")
    return Utterance(audio, speech_ms, reason)


def _iterate(frames: Iterable[np.ndarray]) -> Iterator[np.ndarray]:
    yield from frames
