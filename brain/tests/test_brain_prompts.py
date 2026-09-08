"""System prompt construction."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jervis_brain import prompts
from jervis_brain.memory import Kind, Memory

MEMORIES = [
    Memory(1, Kind.PREFERENCE, "Takes oat milk in coffee", "2026-01-01T00:00:00Z"),
    Memory(2, Kind.FACT, "Dentist is Dr Chen", "2026-01-01T00:00:00Z"),
]
WHEN = datetime(2026, 3, 4, 15, 30, tzinfo=UTC)


def test_stable_block_names_the_persona_and_the_rules() -> None:
    block = prompts.stable_block("Jervis", "Arup")
    assert "You are Jervis, Arup's personal assistant" in block
    for rule in ("Never say an action is done", "blocked outright", "No markdown"):
        assert rule in block


def test_stable_block_does_not_change_between_turns() -> None:
    """It is the cached prefix; anything varying in it defeats the cache."""
    assert prompts.stable_block() == prompts.stable_block()


def test_volatile_block_carries_the_time() -> None:
    block = prompts.volatile_block(now=WHEN, frontmost=None)
    assert "Wednesday 4 March 2026" in block
    assert "3:30 PM" in block


def test_volatile_block_includes_memories_and_the_frontmost_app() -> None:
    block = prompts.volatile_block(MEMORIES, now=WHEN, frontmost="Safari")
    assert "The frontmost app is Safari." in block
    assert "- (preference) Takes oat milk in coffee" in block
    assert "- (fact) Dentist is Dr Chen" in block


def test_volatile_block_omits_absent_context() -> None:
    block = prompts.volatile_block(now=WHEN, include_frontmost=False)
    assert "frontmost" not in block
    assert "remember" not in block


def test_build_system_marks_only_the_stable_block_cacheable() -> None:
    system = prompts.build_system(MEMORIES, now=WHEN, frontmost="Mail")
    assert len(system) == 2
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in system[1]
    assert "Right now it is" in system[1]["text"]
    assert "oat milk" in system[1]["text"]


def test_build_system_can_skip_caching() -> None:
    assert "cache_control" not in prompts.build_system(now=WHEN, cache=False)[0]


def test_frontmost_app_never_raises() -> None:
    """A missing osascript or a denied permission must not break a turn."""
    assert prompts._frontmost_app() is None or isinstance(prompts._frontmost_app(), str)


# --- channels -----------------------------------------------------------------------


def test_the_voice_persona_is_unchanged() -> None:
    """The voice block is the prompt cache's prefix and the voice loop's contract.

    If this fails, the cache is cold for every voice turn and the spoken persona has
    drifted - both worth noticing loudly.
    """
    voice = prompts.stable_block(channel=prompts.Channel.VOICE)
    assert "You are speaking aloud" in voice
    assert "No markdown, no bullet points, no emoji." in voice
    assert prompts.stable_block() == voice, "VOICE must remain the default"


def test_the_text_persona_allows_structure() -> None:
    text_block = prompts.stable_block(channel=prompts.Channel.TEXT)
    assert "You are speaking aloud" not in text_block
    assert "read, not heard" in text_block
    assert "No headings, no bold, no emoji." in text_block


def test_the_channels_differ_only_in_manner() -> None:
    voice = prompts.stable_block(channel=prompts.Channel.VOICE)
    text_block = prompts.stable_block(channel=prompts.Channel.TEXT)
    assert voice != text_block
    # Every safety rule is identical on both channels.
    for rule in (
        "Never say an action is done unless a tool result says it is done",
        "blocked outright",
        "Read the summary back and wait",
    ):
        assert rule in voice and rule in text_block


@pytest.mark.parametrize("channel", list(prompts.Channel))
def test_each_channel_is_byte_stable(channel: prompts.Channel) -> None:
    """A block that varies between turns can never be cached."""
    assert prompts.stable_block(channel=channel) == prompts.stable_block(channel=channel)


@pytest.mark.parametrize("channel", list(prompts.Channel))
def test_build_system_keeps_the_cache_split(channel: prompts.Channel) -> None:
    system = prompts.build_system(now=WHEN, frontmost=None, channel=channel)
    assert len(system) == 2
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in system[1]
