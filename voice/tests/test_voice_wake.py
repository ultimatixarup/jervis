"""Wake-word detection."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from speech import available, frames_of, silence, with_silence

from jervis_voice.audio import FRAME_SAMPLES
from jervis_voice.wake import (
    BUILTIN_MODELS,
    WakeListener,
    ensure_models,
    resolve_model_path,
)


def test_models_are_stored_outside_the_venv(tmp_path: Path) -> None:
    """site-packages is wiped by `uv sync --reinstall`; ~/.jervis is not."""
    from jervis_voice.wake import MODEL_DIR

    assert ".venv" not in str(MODEL_DIR)
    assert str(MODEL_DIR).startswith(str(Path.home()))


def test_ensure_models_is_idempotent(tmp_path: Path) -> None:
    first = ensure_models("hey_jarvis", tmp_path)
    names = sorted(p.name for p in first.glob("*.onnx"))
    ensure_models("hey_jarvis", tmp_path)
    assert sorted(p.name for p in first.glob("*.onnx")) == names
    assert "melspectrogram.onnx" in names
    assert "embedding_model.onnx" in names


def test_a_builtin_model_resolves_by_name(tmp_path: Path) -> None:
    ensure_models("hey_jarvis", tmp_path)
    resolved = resolve_model_path("hey_jarvis", tmp_path)
    assert resolved.endswith(".onnx") or resolved in BUILTIN_MODELS


def test_a_custom_model_resolves_to_its_file(tmp_path: Path) -> None:
    (tmp_path / "jervis.onnx").write_bytes(b"not really a model")
    assert resolve_model_path("jervis", tmp_path) == str(tmp_path / "jervis.onnx")


def test_an_unknown_model_says_how_to_fix_it(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Train one"):
        resolve_model_path("nonsense", tmp_path)


@pytest.mark.slow
def test_silence_never_triggers() -> None:
    listener = WakeListener(threshold=0.6)
    fired = [listener.feed(f) for f in frames_of(silence(3000))]
    assert not any(fired)
    assert listener.last_score < 0.1


@pytest.mark.slow
def test_unrelated_speech_does_not_trigger() -> None:
    if not available():
        pytest.skip("needs macOS `say` and ffmpeg")
    listener = WakeListener(threshold=0.6)
    audio = with_silence("what is the weather like tomorrow afternoon")
    assert not any(listener.feed(f) for f in frames_of(audio))


@pytest.mark.slow
def test_the_wake_phrase_triggers() -> None:
    if not available():
        pytest.skip("needs macOS `say` and ffmpeg")
    listener = WakeListener(threshold=0.4)
    triggered = False
    for voice in ("Daniel", "Samantha", "Alex"):
        listener.reset()
        audio = with_silence("hey jarvis", lead_ms=400, trail_ms=800)
        from speech import speech_wav

        from jervis_voice.audio import read_wav

        spoken = np.concatenate(
            [silence(400), read_wav(speech_wav("hey jarvis", voice=voice)), silence(800)]
        )
        del audio
        if any(listener.feed(f) for f in frames_of(spoken)):
            triggered = True
            break
    assert triggered, (
        "the wake word never fired on synthesised speech; check the model files and "
        f"threshold (best score seen {listener.last_score:.2f})"
    )


@pytest.mark.slow
def test_the_cooldown_suppresses_a_repeat_burst() -> None:
    """One spoken wake word produces many high frames; it must fire once."""
    listener = WakeListener(threshold=0.0, cooldown_seconds=5.0)
    frame = np.zeros(FRAME_SAMPLES, dtype=np.int16)
    fires = [listener.feed(frame, now=t / 50) for t in range(200)]
    assert sum(fires) <= 2, f"fired {sum(fires)} times inside the cooldown"
