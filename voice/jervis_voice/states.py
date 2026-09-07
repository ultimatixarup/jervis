"""The listening loop's state machine, with no I/O in it.

PLAN.md §4 Phase 3: IDLE -> LISTENING -> THINKING -> SPEAKING -> (AWAITING_CONFIRM).
Keeping the transitions here, separate from microphones and subprocesses, is what
makes barge-in and the confirmation window testable without hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto


class State(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    AWAITING_CONFIRM = "awaiting_confirm"


class EventKind(StrEnum):
    WAKE_WORD = auto()
    UTTERANCE = auto()
    NOTHING_HEARD = auto()
    REPLY = auto()
    SPEECH_FINISHED = auto()
    CONFIRM_WINDOW_EXPIRED = auto()
    ERROR = auto()


class ActionKind(StrEnum):
    PLAY_CHIME = auto()
    RECORD_UTTERANCE = auto()
    ASK_BRAIN = auto()
    CONFIRM_BRAIN = auto()
    SPEAK = auto()
    STOP_SPEAKING = auto()
    OPEN_CONFIRM_WINDOW = auto()
    CLOSE_CONFIRM_WINDOW = auto()


@dataclass(frozen=True)
class Event:
    kind: EventKind
    text: str = ""
    pending_confirmation: str | None = None

    @classmethod
    def wake(cls) -> Event:
        return cls(EventKind.WAKE_WORD)

    @classmethod
    def utterance(cls, text: str) -> Event:
        return cls(EventKind.UTTERANCE, text=text)

    @classmethod
    def nothing_heard(cls) -> Event:
        return cls(EventKind.NOTHING_HEARD)

    @classmethod
    def reply(cls, text: str, pending_confirmation: str | None = None) -> Event:
        return cls(EventKind.REPLY, text=text, pending_confirmation=pending_confirmation)

    @classmethod
    def speech_finished(cls) -> Event:
        return cls(EventKind.SPEECH_FINISHED)

    @classmethod
    def confirm_window_expired(cls) -> Event:
        return cls(EventKind.CONFIRM_WINDOW_EXPIRED)

    @classmethod
    def error(cls, text: str) -> Event:
        return cls(EventKind.ERROR, text=text)


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    text: str = ""


@dataclass
class Machine:
    """Events in, actions out. The caller performs the actions."""

    state: State = State.IDLE
    # Set while a confirm-tier action is waiting on a spoken yes; the loop listens
    # directly instead of waiting for the wake word again.
    pending_confirmation: str | None = None
    history: list[tuple[State, EventKind, State]] = field(default_factory=list)

    @property
    def awaiting_confirmation(self) -> bool:
        return self.state is State.AWAITING_CONFIRM

    def handle(self, event: Event) -> list[Action]:
        before = self.state
        actions = self._handle(event)
        self.history.append((before, event.kind, self.state))
        return actions

    def _handle(self, event: Event) -> list[Action]:
        if event.kind is EventKind.ERROR:
            # Say what went wrong rather than dying quietly on someone's desk.
            self.pending_confirmation = None
            self.state = State.SPEAKING
            return [Action(ActionKind.STOP_SPEAKING), Action(ActionKind.SPEAK, event.text)]

        match self.state:
            case State.IDLE:
                if event.kind is EventKind.WAKE_WORD:
                    self.state = State.LISTENING
                    return [
                        Action(ActionKind.PLAY_CHIME),
                        Action(ActionKind.RECORD_UTTERANCE),
                    ]

            case State.LISTENING:
                if event.kind is EventKind.UTTERANCE:
                    self.state = State.THINKING
                    return [Action(ActionKind.ASK_BRAIN, event.text)]
                if event.kind is EventKind.NOTHING_HEARD:
                    self.state = State.IDLE
                    return []

            case State.THINKING:
                if event.kind is EventKind.REPLY:
                    self.pending_confirmation = event.pending_confirmation
                    self.state = State.SPEAKING
                    return [Action(ActionKind.SPEAK, event.text)]

            case State.SPEAKING:
                if event.kind is EventKind.WAKE_WORD:
                    # Barge-in: stop mid-sentence and listen.
                    self.pending_confirmation = None
                    self.state = State.LISTENING
                    return [
                        Action(ActionKind.STOP_SPEAKING),
                        Action(ActionKind.PLAY_CHIME),
                        Action(ActionKind.RECORD_UTTERANCE),
                    ]
                if event.kind is EventKind.SPEECH_FINISHED:
                    if self.pending_confirmation is not None:
                        self.state = State.AWAITING_CONFIRM
                        return [
                            Action(ActionKind.OPEN_CONFIRM_WINDOW),
                            Action(ActionKind.RECORD_UTTERANCE),
                        ]
                    self.state = State.IDLE
                    return []

            case State.AWAITING_CONFIRM:
                if event.kind is EventKind.UTTERANCE:
                    self.state = State.THINKING
                    return [
                        Action(ActionKind.CLOSE_CONFIRM_WINDOW),
                        Action(ActionKind.CONFIRM_BRAIN, event.text),
                    ]
                if event.kind in (
                    EventKind.CONFIRM_WINDOW_EXPIRED,
                    EventKind.NOTHING_HEARD,
                ):
                    # Silence is not consent. Drop it and go back to sleep.
                    self.pending_confirmation = None
                    self.state = State.IDLE
                    return [Action(ActionKind.CLOSE_CONFIRM_WINDOW)]
                if event.kind is EventKind.WAKE_WORD:
                    self.pending_confirmation = None
                    self.state = State.LISTENING
                    return [
                        Action(ActionKind.CLOSE_CONFIRM_WINDOW),
                        Action(ActionKind.PLAY_CHIME),
                        Action(ActionKind.RECORD_UTTERANCE),
                    ]

        return []  # anything else is ignored in this state
