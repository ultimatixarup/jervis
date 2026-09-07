"""End-of-utterance detection, on real speech."""

from __future__ import annotations

import numpy as np
import pytest
from speech import available, frames_of, silence, speech_wav, with_silence

from jervis_voice.audio import FRAME_MS, FRAME_SAMPLES, read_wav
from jervis_voice.vad import (
    EnergyDetector,
    RecordingLimits,
    SpeechDetector,
    record_utterance,
)

pytestmark = pytest.mark.skipif(not available(), reason="needs macOS `say` and ffmpeg")


@pytest.fixture(params=["webrtc", "energy"])
def detector(request: pytest.FixtureRequest) -> SpeechDetector | EnergyDetector:
    return SpeechDetector(aggressiveness=2) if request.param == "webrtc" else EnergyDetector()


def test_it_stops_when_the_speaker_stops(detector: object) -> None:
    audio = with_silence("what time is it", trail_ms=1500)
    utterance = record_utterance(frames_of(audio), detector)  # type: ignore[arg-type]
    assert utterance.stopped_because == "silence"
    assert not utterance.is_empty
    assert utterance.speech_ms > 300


def test_the_leading_silence_is_not_recorded(detector: object) -> None:
    """Whisper does better without a second of nothing at the front."""
    spoken = read_wav(speech_wav("what time is it"))
    audio = np.concatenate([silence(1500), spoken, silence(1200)])
    utterance = record_utterance(frames_of(audio), detector)  # type: ignore[arg-type]
    assert utterance.audio.size < len(audio) - len(silence(1000))


def test_a_pause_mid_sentence_does_not_end_the_utterance(detector: object) -> None:
    """People pause. Cutting them off at the first gap is the classic failure."""
    audio = np.concatenate(
        [
            read_wav(speech_wav("delete the file")),
            silence(400),  # shorter than the 700ms end-of-utterance window
            read_wav(speech_wav("called report dot pdf")),
            silence(1200),
        ]
    )
    utterance = record_utterance(frames_of(audio), detector)  # type: ignore[arg-type]
    assert utterance.stopped_because == "silence"
    # Both halves survive: the recording is longer than either piece alone.
    assert utterance.audio.size > len(read_wav(speech_wav("delete the file"))) * 1.5


def test_silence_alone_is_nothing_heard(detector: object) -> None:
    utterance = record_utterance(frames_of(silence(4000)), detector)  # type: ignore[arg-type]
    assert utterance.is_empty
    assert utterance.stopped_because == "no_speech"


def test_a_long_utterance_is_capped(detector: object) -> None:
    audio = np.concatenate([read_wav(speech_wav("one two three four five"))] * 12)
    limits = RecordingLimits(max_ms=2000)
    utterance = record_utterance(frames_of(audio), detector, limits)  # type: ignore[arg-type]
    assert utterance.stopped_because == "max_length"
    assert utterance.audio.size <= 2000 * 16  # 2000ms at 16 samples/ms


def test_a_cough_is_not_an_utterance(detector: object) -> None:
    """Too little speech to be a command."""
    blip = np.concatenate(
        [
            silence(100),
            (np.random.default_rng(0).normal(0, 3000, FRAME_SAMPLES * 3).astype(np.int16)),
            silence(1500),
        ]
    )
    utterance = record_utterance(frames_of(blip), detector, RecordingLimits(min_speech_ms=400))  # type: ignore[arg-type]
    assert utterance.is_empty


def test_it_gives_up_if_nobody_speaks(detector: object) -> None:
    limits = RecordingLimits(lead_in_ms=200)
    frames = [np.zeros(FRAME_SAMPLES, dtype=np.int16)] * 100
    utterance = record_utterance(frames, detector, limits)  # type: ignore[arg-type]
    assert utterance.stopped_because == "no_speech"


def test_silence_window_is_honoured(detector: object) -> None:
    audio = with_silence("hello", trail_ms=2000)
    short = record_utterance(frames_of(audio), detector, RecordingLimits(silence_ms=200))  # type: ignore[arg-type]
    long = record_utterance(frames_of(audio), detector, RecordingLimits(silence_ms=1200))  # type: ignore[arg-type]
    assert long.audio.size > short.audio.size
    assert long.audio.size - short.audio.size >= (1000 // FRAME_MS) * FRAME_SAMPLES * 0.5
