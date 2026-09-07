"""Speech to text."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from .audio import to_float32

log = logging.getLogger("jervis.voice.stt")

DEFAULT_MODEL = "mlx-community/whisper-small.en-mlx"

# Below this, whisper is guessing at noise. Discard rather than act on it - acting on
# a misheard command is worse than asking again.
MIN_CONFIDENCE = 0.35

# Whisper emits these for silence or breath. Treated as nothing heard.
HALLUCINATIONS = frozenset(
    {
        "",
        "you",
        "thank you.",
        "thanks for watching!",
        "thank you for watching!",
        "bye.",
        "...",
        ".",
        "[blank_audio]",
        "(silence)",
    }
)


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float

    @property
    def is_usable(self) -> bool:
        cleaned = self.text.strip().lower()
        if cleaned in HALLUCINATIONS or not cleaned:
            return False
        return self.confidence >= MIN_CONFIDENCE


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray) -> Transcript: ...


def confidence_from(result: dict[str, Any]) -> float:
    """Turn whisper's per-segment log-probabilities into one 0-1 number."""
    segments = result.get("segments") or []
    if not segments:
        return 0.0
    scores: list[float] = []
    for segment in segments:
        logprob = segment.get("avg_logprob")
        no_speech = segment.get("no_speech_prob", 0.0)
        if logprob is None:
            continue
        scores.append(math.exp(logprob) * (1.0 - float(no_speech)))
    return sum(scores) / len(scores) if scores else 0.0


class WhisperTranscriber:
    """mlx-whisper, which runs on the Apple Silicon GPU."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model

    def transcribe(self, audio: np.ndarray) -> Transcript:
        if audio.size == 0:
            return Transcript("", 0.0)

        import mlx_whisper

        result = mlx_whisper.transcribe(
            to_float32(audio),
            path_or_hf_repo=self.model,
            language="en",
            fp16=True,
            condition_on_previous_text=False,
        )
        text = str(result.get("text", "")).strip()
        confidence = confidence_from(result)
        log.info("heard %r (confidence %.2f)", text, confidence)
        return Transcript(text, confidence)

    def warm_up(self) -> None:
        """Load the model before it is first needed, so turn one is not slow."""
        from .audio import SAMPLE_RATE

        self.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.int16))
