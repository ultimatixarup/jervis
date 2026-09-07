"""Build speech fixtures with macOS `say`, so the audio tests use real speech.

Synthetic tones would exercise the code path without exercising the thing that
matters - whether a real utterance is segmented and transcribed correctly.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000
CACHE = Path(tempfile.gettempdir()) / "jervis-speech-fixtures"


def available() -> bool:
    return bool(shutil.which("say") and shutil.which("ffmpeg"))


def speech_wav(text: str, voice: str = "Daniel", rate: int = 180) -> Path:
    """Render *text* to a 16kHz mono wav, cached between runs."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{text}|{voice}|{rate}".encode()).hexdigest()[:16]
    target = CACHE / f"{key}.wav"
    if target.exists():
        return target

    with tempfile.TemporaryDirectory() as tmp:
        aiff = Path(tmp) / "speech.aiff"
        subprocess.run(
            ["say", "-v", voice, "-r", str(rate), "-o", str(aiff), "--", text], check=True
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(aiff),
                "-ar",
                str(SAMPLE_RATE),
                "-ac",
                "1",
                "-sample_fmt",
                "s16",
                str(target),
            ],
            check=True,
        )
    return target


def silence(duration_ms: int) -> np.ndarray:
    return np.zeros(SAMPLE_RATE * duration_ms // 1000, dtype=np.int16)


def with_silence(text: str, *, lead_ms: int = 200, trail_ms: int = 1200) -> np.ndarray:
    """Speech surrounded by silence, the way a real utterance arrives."""
    from jervis_voice.audio import read_wav

    return np.concatenate([silence(lead_ms), read_wav(speech_wav(text)), silence(trail_ms)])


def frames_of(audio: np.ndarray) -> list[np.ndarray]:
    from jervis_voice.audio import FRAME_SAMPLES

    return [
        audio[i : i + FRAME_SAMPLES]
        for i in range(0, len(audio) - FRAME_SAMPLES + 1, FRAME_SAMPLES)
    ]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Standard WER, for asserting a transcript is close enough."""
    ref = [w.strip(".,!?").lower() for w in reference.split()]
    hyp = [w.strip(".,!?").lower() for w in hypothesis.split()]
    if not ref:
        return 0.0 if not hyp else 1.0

    distances = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        previous, distances[0] = distances[0], i
        for j, hyp_word in enumerate(hyp, start=1):
            current = distances[j]
            distances[j] = min(
                distances[j] + 1,
                distances[j - 1] + 1,
                previous + (ref_word != hyp_word),
            )
            previous = current
    return distances[len(hyp)] / len(ref)
