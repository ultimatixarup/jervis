"""Transcription."""

from __future__ import annotations

import numpy as np
import pytest
from speech import available, speech_wav, word_error_rate

from jervis_voice.audio import read_wav
from jervis_voice.stt import (
    HALLUCINATIONS,
    MIN_CONFIDENCE,
    Transcript,
    WhisperTranscriber,
    confidence_from,
)


def test_confidence_from_segments() -> None:
    assert confidence_from({"segments": []}) == 0.0
    assert confidence_from({}) == 0.0
    clear = confidence_from({"segments": [{"avg_logprob": -0.1, "no_speech_prob": 0.01}]})
    muddy = confidence_from({"segments": [{"avg_logprob": -2.5, "no_speech_prob": 0.6}]})
    assert clear > 0.8
    assert muddy < 0.2
    assert clear > muddy


def test_segments_without_a_logprob_are_skipped() -> None:
    assert confidence_from({"segments": [{"no_speech_prob": 0.1}]}) == 0.0


@pytest.mark.parametrize("text", sorted(HALLUCINATIONS))
def test_whisper_hallucinations_are_not_usable(text: str) -> None:
    assert not Transcript(text, 0.99).is_usable


def test_low_confidence_is_not_usable() -> None:
    """Acting on a misheard command is worse than asking again."""
    assert not Transcript("delete everything", MIN_CONFIDENCE - 0.01).is_usable
    assert Transcript("delete everything", MIN_CONFIDENCE + 0.01).is_usable


def test_whitespace_only_is_not_usable() -> None:
    assert not Transcript("   ", 0.99).is_usable


def test_empty_audio_short_circuits() -> None:
    """No model load, no cost, for a recording that captured nothing."""
    assert WhisperTranscriber().transcribe(np.array([], dtype=np.int16)) == Transcript("", 0.0)


@pytest.mark.slow
@pytest.mark.skipif(not available(), reason="needs macOS `say` and ffmpeg")
@pytest.mark.parametrize(
    "spoken",
    [
        "what time is it",
        "what is on my desktop",
        "delete the file called report",
    ],
)
def test_real_speech_transcribes_accurately(spoken: str) -> None:
    audio = read_wav(speech_wav(spoken))
    transcript = WhisperTranscriber().transcribe(audio)
    assert transcript.is_usable, f"{transcript!r} was discarded"
    wer = word_error_rate(spoken, transcript.text)
    assert wer < 0.2, f"WER {wer:.2f}: heard {transcript.text!r}, expected {spoken!r}"


@pytest.mark.slow
@pytest.mark.skipif(not available(), reason="needs macOS `say` and ffmpeg")
def test_yes_is_heard_as_yes() -> None:
    """The confirmation word has to survive transcription, or nothing gets approved."""
    from jervis_brain.permissions import is_confirmation

    transcript = WhisperTranscriber().transcribe(read_wav(speech_wav("yes")))
    assert is_confirmation(transcript.text), f"heard {transcript.text!r}"
