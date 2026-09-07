"""The listening state machine. Table-driven, per PLAN.md §4 Phase 3."""

from __future__ import annotations

import pytest

from jervis_voice.states import Action, ActionKind, Event, Machine, State


def actions(machine: Machine, event: Event) -> list[ActionKind]:
    return [a.kind for a in machine.handle(event)]


def current(machine: Machine) -> State:
    """Read the state through a call, so mypy does not narrow it across handle()."""
    return machine.state


def pending(machine: Machine) -> str | None:
    return machine.pending_confirmation


# (starting state, event, expected state, expected actions)
TRANSITIONS = [
    (
        State.IDLE,
        Event.wake(),
        State.LISTENING,
        [ActionKind.PLAY_CHIME, ActionKind.RECORD_UTTERANCE],
    ),
    (State.IDLE, Event.utterance("stray words"), State.IDLE, []),
    (State.IDLE, Event.speech_finished(), State.IDLE, []),
    (State.LISTENING, Event.utterance("what time is it"), State.THINKING, [ActionKind.ASK_BRAIN]),
    (State.LISTENING, Event.nothing_heard(), State.IDLE, []),
    (State.LISTENING, Event.wake(), State.LISTENING, []),
    (State.THINKING, Event.reply("It's four."), State.SPEAKING, [ActionKind.SPEAK]),
    (State.THINKING, Event.wake(), State.THINKING, []),
    (State.SPEAKING, Event.speech_finished(), State.IDLE, []),
    (
        State.SPEAKING,
        Event.wake(),
        State.LISTENING,
        [ActionKind.STOP_SPEAKING, ActionKind.PLAY_CHIME, ActionKind.RECORD_UTTERANCE],
    ),
]


@pytest.mark.parametrize(
    ("start", "event", "expected_state", "expected_actions"),
    TRANSITIONS,
    ids=[f"{t[0]}+{t[1].kind}" for t in TRANSITIONS],
)
def test_transitions(
    start: State, event: Event, expected_state: State, expected_actions: list[ActionKind]
) -> None:
    machine = Machine(state=start)
    assert actions(machine, event) == expected_actions
    assert current(machine) is expected_state


def test_a_full_turn() -> None:
    machine = Machine()
    machine.handle(Event.wake())
    machine.handle(Event.utterance("what time is it"))
    assert current(machine) is State.THINKING
    machine.handle(Event.reply("Ten past four."))
    assert current(machine) is State.SPEAKING
    machine.handle(Event.speech_finished())
    assert current(machine) is State.IDLE
    assert not machine.awaiting_confirmation


# --- the confirmation window --------------------------------------------------------


def reach_confirm(machine: Machine) -> None:
    machine.handle(Event.wake())
    machine.handle(Event.utterance("delete report.pdf"))
    machine.handle(Event.reply("Bin report.pdf?", pending_confirmation="move_to_trash"))
    machine.handle(Event.speech_finished())


def test_a_pending_confirmation_opens_the_window_and_listens_directly() -> None:
    machine = Machine()
    machine.handle(Event.wake())
    machine.handle(Event.utterance("delete report.pdf"))
    machine.handle(Event.reply("Bin it?", pending_confirmation="move_to_trash"))
    assert actions(machine, Event.speech_finished()) == [
        ActionKind.OPEN_CONFIRM_WINDOW,
        ActionKind.RECORD_UTTERANCE,
    ]
    assert current(machine) is State.AWAITING_CONFIRM
    assert machine.awaiting_confirmation


def test_answering_goes_to_the_brain_as_a_confirmation_not_a_new_question() -> None:
    machine = Machine()
    reach_confirm(machine)
    assert actions(machine, Event.utterance("yes")) == [
        ActionKind.CLOSE_CONFIRM_WINDOW,
        ActionKind.CONFIRM_BRAIN,
    ]
    assert current(machine) is State.THINKING


def test_the_window_expiring_abandons_the_action() -> None:
    machine = Machine()
    reach_confirm(machine)
    assert actions(machine, Event.confirm_window_expired()) == [ActionKind.CLOSE_CONFIRM_WINDOW]
    assert current(machine) is State.IDLE
    assert pending(machine) is None


def test_silence_is_not_consent() -> None:
    machine = Machine()
    reach_confirm(machine)
    assert actions(machine, Event.nothing_heard()) == [ActionKind.CLOSE_CONFIRM_WINDOW]
    assert current(machine) is State.IDLE


def test_the_wake_word_during_a_confirmation_abandons_it() -> None:
    """Saying "Jervis" again is starting over, not agreeing."""
    machine = Machine()
    reach_confirm(machine)
    assert actions(machine, Event.wake()) == [
        ActionKind.CLOSE_CONFIRM_WINDOW,
        ActionKind.PLAY_CHIME,
        ActionKind.RECORD_UTTERANCE,
    ]
    assert current(machine) is State.LISTENING
    assert pending(machine) is None


# --- barge-in -----------------------------------------------------------------------


def test_barge_in_stops_the_speech_first() -> None:
    machine = Machine(state=State.SPEAKING)
    performed = actions(machine, Event.wake())
    assert performed[0] is ActionKind.STOP_SPEAKING, "must stop talking before listening"
    assert current(machine) is State.LISTENING


def test_barge_in_drops_a_pending_confirmation() -> None:
    """Interrupting the read-back must not leave a live yes/no hanging."""
    machine = Machine()
    machine.handle(Event.wake())
    machine.handle(Event.utterance("delete it"))
    machine.handle(Event.reply("Bin it?", pending_confirmation="move_to_trash"))
    assert pending(machine) is not None
    machine.handle(Event.wake())
    assert pending(machine) is None
    assert current(machine) is State.LISTENING


# --- errors -------------------------------------------------------------------------


@pytest.mark.parametrize("start", list(State))
def test_an_error_is_always_spoken(start: State) -> None:
    machine = Machine(state=start, pending_confirmation="something")
    performed = actions(machine, Event.error("I couldn't reach my brain."))
    assert performed == [ActionKind.STOP_SPEAKING, ActionKind.SPEAK]
    assert current(machine) is State.SPEAKING
    assert pending(machine) is None


def test_history_records_every_transition() -> None:
    machine = Machine()
    machine.handle(Event.wake())
    machine.handle(Event.utterance("hello"))
    assert machine.history[0][0] is State.IDLE
    assert machine.history[-1][2] is State.THINKING


def test_action_equality_is_structural() -> None:
    assert Action(ActionKind.SPEAK, "hi") == Action(ActionKind.SPEAK, "hi")
