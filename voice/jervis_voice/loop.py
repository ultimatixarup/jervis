"""The listening loop: wake -> record -> transcribe -> ask the brain -> speak.

One thread reads the microphone; the state machine in states.py decides what happens
next. Everything that touches hardware or the network is behind a seam so the whole
loop can be run against a wav file and a recording TTS backend.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .audio import AudioSource, MicSource
from .brain import BrainClient
from .states import Action, ActionKind, Event, Machine, State
from .stt import Transcriber, WhisperTranscriber
from .tts import SayBackend, TTSBackend, play_chime
from .vad import EnergyDetector, RecordingLimits, SpeechDetector, record_utterance

log = logging.getLogger("jervis.voice.loop")


@dataclass
class VoiceConfig:
    wake_model: str = "hey_jarvis"
    wake_threshold: float = 0.6
    wake_cooldown_seconds: float = 2.0
    stt_model: str = "mlx-community/whisper-small.en-mlx"
    tts_backend: str = "say"
    tts_voice: str = "Daniel"
    confirm_window_seconds: int = 60
    limits: RecordingLimits = field(default_factory=RecordingLimits)


@dataclass
class Wake:
    """Anything that can say 'the wake word just happened'."""

    listener: Any

    def feed(self, frame: np.ndarray) -> bool:
        return bool(self.listener.feed(frame))


class VoiceLoop:
    def __init__(
        self,
        source: AudioSource,
        wake: Any,
        transcriber: Transcriber,
        tts: TTSBackend,
        brain: BrainClient,
        config: VoiceConfig | None = None,
        detector: SpeechDetector | EnergyDetector | None = None,
        *,
        chime: bool = True,
    ) -> None:
        self.source = source
        self.wake = wake
        self.transcriber = transcriber
        self.tts = tts
        self.brain = brain
        self.config = config or VoiceConfig()
        self.detector = detector or _default_detector()
        self.chime = chime

        self.machine = Machine()
        self._frames: Iterator[np.ndarray] | None = None
        self._confirm_deadline: float | None = None
        self._stop = threading.Event()
        self.turns = 0

    # --- the loop --------------------------------------------------------------------

    def run(self, max_turns: int | None = None) -> None:
        """Listen until stopped. `max_turns` bounds it for tests and the smoke script."""
        self._frames = iter(self.source.frames())
        log.info("listening for %r", self.config.wake_model)
        try:
            while not self._stop.is_set():
                if max_turns is not None and self.turns >= max_turns:
                    return
                if not self._tick():
                    return
        finally:
            self.tts.stop()
            self.source.close()

    def stop(self) -> None:
        self._stop.set()

    def _tick(self) -> bool:
        """One pass: wait for something to happen, then act on it. False when audio ends."""
        if self.machine.state is State.AWAITING_CONFIRM:
            if self._confirm_expired():
                self._dispatch(Event.confirm_window_expired())
                return True
            # In the confirmation window we listen directly - making someone say the
            # wake word again just to say "yes" would be absurd.
            return self._listen_and_dispatch()

        frame = self._next_frame()
        if frame is None:
            return False
        if self.wake.feed(frame):
            self._dispatch(Event.wake())
        return True

    def _listen_and_dispatch(self) -> bool:
        utterance = record_utterance(self._remaining_frames(), self.detector, self.config.limits)
        if utterance.is_empty:
            self._dispatch(Event.nothing_heard())
            return True
        transcript = self.transcriber.transcribe(utterance.audio)
        if not transcript.is_usable:
            log.info("discarded %r (confidence %.2f)", transcript.text, transcript.confidence)
            self._dispatch(Event.nothing_heard())
            return True
        self._dispatch(Event.utterance(transcript.text))
        return True

    # --- actions ---------------------------------------------------------------------

    def _dispatch(self, event: Event) -> None:
        """Hand an event to the machine and carry out whatever it asks for.

        A "turn" is a completed exchange - back to IDLE - not a brain call, so a
        request that needs confirming counts once, not twice.
        """
        before = self.machine.state
        for action in self.machine.handle(event):
            self._perform(action)
        if before is not State.IDLE and self.machine.state is State.IDLE:
            self.turns += 1

    def _perform(self, action: Action) -> None:
        match action.kind:
            case ActionKind.PLAY_CHIME:
                if self.chime:
                    play_chime()
            case ActionKind.RECORD_UTTERANCE:
                if self.machine.state is State.LISTENING:
                    self._listen_and_dispatch()
            case ActionKind.ASK_BRAIN:
                self._dispatch(self._call(self.brain.ask, action.text))
            case ActionKind.CONFIRM_BRAIN:
                self._dispatch(self._call(self.brain.confirm, action.text))
            case ActionKind.SPEAK:
                self._speak_and_listen(action.text)
            case ActionKind.STOP_SPEAKING:
                self.tts.stop()
            case ActionKind.OPEN_CONFIRM_WINDOW:
                self._confirm_deadline = time.monotonic() + self.config.confirm_window_seconds
            case ActionKind.CLOSE_CONFIRM_WINDOW:
                self._confirm_deadline = None

    def _call(self, fn: Any, text: str) -> Event:
        try:
            reply = fn(text)
        except Exception as exc:
            log.exception("brain call failed")
            return Event.error(f"I couldn't reach my brain just then. {type(exc).__name__}.")
        return Event.reply(reply.text, reply.pending_confirmation)

    def _speak_and_listen(self, text: str) -> None:
        """Say something, while still listening for an interruption.

        Barge-in only works if the microphone is read *during* speech, so this keeps
        pulling frames and feeding the wake listener until the speech ends. Waiting on
        the TTS process instead would make "Jervis" during a long answer do nothing.
        """
        self.tts.speak(text)
        while self.tts.is_speaking() and not self._stop.is_set():
            frame = self._next_frame()
            if frame is None:
                break
            if self.wake.feed(frame):
                self._dispatch(Event.wake())
                return
        self._dispatch(Event.speech_finished())

    # --- audio ------------------------------------------------------------------------

    def _next_frame(self) -> np.ndarray | None:
        if self._frames is None:
            self._frames = iter(self.source.frames())
        return next(self._frames, None)

    def _remaining_frames(self) -> Iterator[np.ndarray]:
        while True:
            frame = self._next_frame()
            if frame is None:
                return
            yield frame

    def _confirm_expired(self) -> bool:
        return self._confirm_deadline is not None and time.monotonic() >= self._confirm_deadline


def _default_detector() -> SpeechDetector | EnergyDetector:
    try:
        return SpeechDetector()
    except Exception:
        log.warning("webrtcvad unavailable; falling back to energy-based silence detection")
        return EnergyDetector()


def build(config: VoiceConfig, brain: BrainClient) -> VoiceLoop:
    from .wake import WakeListener

    return VoiceLoop(
        source=MicSource(),
        wake=WakeListener(
            model=config.wake_model,
            threshold=config.wake_threshold,
            cooldown_seconds=config.wake_cooldown_seconds,
        ),
        transcriber=WhisperTranscriber(config.stt_model),
        tts=SayBackend(voice=config.tts_voice) if config.tts_backend == "say" else _tts(config),
        brain=brain,
        config=config,
    )


def _tts(config: VoiceConfig) -> TTSBackend:
    from .tts import build as build_tts

    return build_tts(config.tts_backend, config.tts_voice)
