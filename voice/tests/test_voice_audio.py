"""Audio plumbing."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from jervis_voice.audio import (
    FRAME_MS,
    FRAME_SAMPLES,
    SAMPLE_RATE,
    WAKE_CHUNK_FRAMES,
    WavSource,
    frame_duration_ms,
    read_wav,
    read_wav_frames,
    rms,
    to_float32,
    write_wav,
)


def test_frame_size_divides_into_both_consumers() -> None:
    """20ms suits webrtcvad (10/20/30ms) and openwakeword (80ms) without buffering."""
    assert FRAME_SAMPLES == 320
    assert FRAME_MS in (10, 20, 30), "webrtcvad accepts only these"
    assert FRAME_SAMPLES * WAKE_CHUNK_FRAMES == 1280, "openwakeword wants 80ms"


def test_frame_duration() -> None:
    assert frame_duration_ms(50) == 1000


def test_rms() -> None:
    assert rms(np.array([], dtype=np.int16)) == 0.0
    assert rms(np.zeros(320, dtype=np.int16)) == 0.0
    assert rms(np.full(320, 1000, dtype=np.int16)) == pytest.approx(1000.0)


def test_to_float32_is_in_range() -> None:
    extremes = np.array([-32768, 0, 32767], dtype=np.int16)
    converted = to_float32(extremes)
    assert converted.dtype == np.float32
    assert converted.min() >= -1.0
    assert converted.max() <= 1.0


def test_wav_round_trip(tmp_path: Path) -> None:
    audio = (np.sin(np.linspace(0, 40, SAMPLE_RATE)) * 8000).astype(np.int16)
    path = tmp_path / "tone.wav"
    write_wav(path, audio)
    assert np.array_equal(read_wav(path), audio)


def test_read_wav_frames_are_uniform(tmp_path: Path) -> None:
    path = tmp_path / "tone.wav"
    write_wav(path, np.zeros(SAMPLE_RATE, dtype=np.int16))
    frames = list(read_wav_frames(path))
    assert len(frames) == SAMPLE_RATE // FRAME_SAMPLES
    assert all(f.size == FRAME_SAMPLES for f in frames)


def test_read_wav_rejects_the_wrong_rate(tmp_path: Path) -> None:
    import wave

    path = tmp_path / "wrong.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"\x00\x00" * 100)
    with pytest.raises(ValueError, match="44100Hz"):
        read_wav(path)


def test_read_wav_rejects_stereo(tmp_path: Path) -> None:
    import wave

    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(b"\x00\x00" * 200)
    with pytest.raises(ValueError, match="not mono"):
        read_wav(path)


def test_wav_source_pads_with_silence(tmp_path: Path) -> None:
    """A recording consumer waiting for silence must not hang at end of file."""
    path = tmp_path / "short.wav"
    write_wav(path, np.full(FRAME_SAMPLES * 2, 5000, dtype=np.int16))
    frames = list(WavSource(path, pad_silence_ms=200).frames())
    assert len(frames) == 2 + 200 // FRAME_MS
    assert rms(frames[-1]) == 0.0
    WavSource(path).close()


@pytest.mark.macos
@pytest.mark.slow
def test_the_real_microphone_produces_frames() -> None:
    """The mic path is the one thing no fixture can stand in for."""
    from jervis_voice.audio import MicSource

    source = MicSource()
    try:
        frames = []
        for frame in source.frames():
            frames.append(frame)
            if len(frames) >= 25:  # half a second
                break
    finally:
        source.close()

    assert len(frames) == 25
    assert all(f.size == FRAME_SAMPLES for f in frames)
    assert all(f.dtype == np.int16 for f in frames)
    assert source.dropped_frames == 0
