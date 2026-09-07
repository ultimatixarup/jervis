"""A ten-second microphone check: hear something, print it, say it back.

Deliberately does not involve the brain. When voice is misbehaving this separates
"the microphone and transcription work" from "the agent is wrong".
"""

from __future__ import annotations

import sys
import time

from .audio import MicSource
from .settings import load, load_env
from .stt import WhisperTranscriber
from .tts import build as build_tts
from .vad import EnergyDetector, RecordingLimits, SpeechDetector, record_utterance
from .wake import WakeListener


def main(argv: list[str] | None = None) -> int:
    seconds = float(argv[0]) if argv else 10.0
    load_env()
    config, _ = load()

    print(f"Loading {config.stt_model} ...")
    transcriber = WhisperTranscriber(config.stt_model)
    transcriber.warm_up()

    print(f"Loading wake word {config.wake_model!r} ...")
    listener = WakeListener(
        model=config.wake_model,
        threshold=config.wake_threshold,
        cooldown_seconds=config.wake_cooldown_seconds,
    )

    phrase = "Hey Jarvis" if config.wake_model == "hey_jarvis" else config.wake_model
    source = MicSource()
    print(f'Listening for {seconds:.0f}s. Say "{phrase}, what time is it".')

    deadline = time.monotonic() + seconds
    frames = source.frames()
    try:
        for frame in frames:
            if time.monotonic() > deadline:
                print("Timed out without hearing the wake word.")
                print("  Try scripts/doctor.sh, or lower voice.wake_threshold.")
                return 1
            if listener.feed(frame):
                print(f"Wake word heard (score {listener.last_score:.2f}). Listening ...")
                break

        utterance = record_utterance(frames, _detector(), RecordingLimits())
        if utterance.is_empty:
            print("Heard the wake word but no speech after it.")
            return 1

        print(f"Captured {utterance.audio.size / 16000:.1f}s of speech. Transcribing ...")
        started = time.monotonic()
        transcript = transcriber.transcribe(utterance.audio)
        elapsed = time.monotonic() - started
        print(
            f'Heard: "{transcript.text}"  (confidence {transcript.confidence:.2f}, {elapsed:.1f}s)'
        )

        if not transcript.is_usable:
            print("  That was discarded as too uncertain to act on.")

        tts = build_tts(config.tts_backend, config.tts_voice)
        tts.speak(f"I heard: {transcript.text}")
        while tts.is_speaking():
            time.sleep(0.05)
    finally:
        source.close()
    return 0


def _detector() -> SpeechDetector | EnergyDetector:
    try:
        return SpeechDetector()
    except Exception:
        return EnergyDetector()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
