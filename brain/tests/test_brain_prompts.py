"""System prompt construction."""

from __future__ import annotations

from datetime import UTC, datetime

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
