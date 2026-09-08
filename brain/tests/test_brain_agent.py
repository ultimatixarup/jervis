"""The agent loop, driven against the real macOS MCP server.

PLAN.md §4 Phase 2 lists six scenarios; each has a test named after it below.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fake_anthropic import FakeAnthropic, text, tool_use

from jervis_brain.agent import Agent
from jervis_brain.config import Config, ServerConfig
from jervis_brain.events import (
    ConfirmationRequested,
    TextDelta,
    ToolFinished,
    ToolStarted,
)
from jervis_brain.mcp_client import MCPClientPool
from jervis_brain.memory import Kind, MemoryStore
from jervis_brain.permissions import AuditLog, Guard

pytestmark = pytest.mark.asyncio


@pytest.fixture
def config() -> Config:
    return Config(
        model="claude-sonnet-5",
        max_tool_rounds=12,
        servers=(ServerConfig(name="macos"),),
    )


@pytest.fixture
async def harness(tmp_path: Path, config: Config) -> AsyncIterator[Harness]:
    async with MCPClientPool((ServerConfig(name="macos"),), python=sys.executable) as pool:
        assert not pool.failures, pool.failures
        audit = AuditLog(tmp_path / "audit.jsonl")
        with MemoryStore(tmp_path / "memory.db") as memory:
            yield Harness(pool=pool, audit=audit, memory=memory, config=config)


class Harness:
    def __init__(
        self, pool: MCPClientPool, audit: AuditLog, memory: MemoryStore, config: Config
    ) -> None:
        self.pool = pool
        self.audit = audit
        self.memory = memory
        self.config = config

    def agent(self, script: list[Any], *, config: Config | None = None) -> Agent:
        return Agent(
            FakeAnthropic(script),
            self.pool,
            Guard(
                self.audit,
                confirm_window_seconds=(config or self.config).permissions.confirm_window_seconds,
            ),
            self.memory,
            config or self.config,
            include_frontmost=False,
        )


# --- wiring -------------------------------------------------------------------------


async def test_tools_reach_the_model_with_api_safe_names(harness: Harness) -> None:
    client = FakeAnthropic([[text("hello")]])
    agent = Agent(
        client,
        harness.pool,
        Guard(harness.audit),
        harness.memory,
        harness.config,
        include_frontmost=False,
    )
    await agent.ask("hello", session_id="s")

    names = client.tools_offered
    assert "macos__list_dir" in names
    assert all("." not in name for name in names), "a dot in a tool name is a 400 from the API"
    from jervis_brain.mcp_client import ANTHROPIC_TOOL_NAME

    assert all(ANTHROPIC_TOOL_NAME.match(name) for name in names)


async def test_the_system_prompt_is_split_for_caching(harness: Harness) -> None:
    client = FakeAnthropic([[text("hi")]])
    agent = Agent(
        client,
        harness.pool,
        Guard(harness.audit),
        harness.memory,
        harness.config,
        include_frontmost=False,
    )
    await agent.ask("hi", session_id="s")
    system = client.last_request["system"]
    assert len(system) == 2
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in system[1], "the volatile block must not be cached"
    assert "Right now it is" in system[1]["text"]


# --- scenario 1 ---------------------------------------------------------------------


async def test_scenario_1_read_only_request_runs_silently(harness: Harness, tmp_path: Path) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "invoice.pdf").write_text("x")
    (downloads / "photo.jpg").write_text("y")

    agent = harness.agent(
        [
            [tool_use("macos__list_dir", {"path": str(downloads)})],
            [text("Two things: invoice.pdf and photo.jpg.")],
        ]
    )
    turn = await agent.ask("what's in my Downloads", session_id="s")

    assert turn.tool_calls == ["macos.list_dir"]
    assert "invoice.pdf" in turn.reply
    assert not turn.awaiting_confirmation
    entry = harness.audit.entries()[0]
    assert entry.tier == "read" and entry.ok and entry.confirmed is None


# --- scenario 2 ---------------------------------------------------------------------


@pytest.mark.macos
async def test_scenario_2_delete_then_yes_deletes_and_audits_confirmed(
    harness: Harness, tmp_path: Path
) -> None:
    doomed = tmp_path / "report.pdf"
    doomed.write_text("quarterly numbers")

    agent = harness.agent(
        [
            [tool_use("macos__move_to_trash", {"path": str(doomed)})],
            [text("Moved report.pdf to the Trash.")],
        ]
    )
    asked = await agent.ask("delete report.pdf", session_id="s")

    assert asked.awaiting_confirmation
    assert "report.pdf" in asked.reply
    assert "Shall I go ahead?" in asked.reply
    assert doomed.exists(), "nothing may happen before the yes"
    assert harness.audit.entries() == []

    done = await agent.confirm("yes", session_id="s")
    assert not done.awaiting_confirmation
    assert not doomed.exists()
    assert done.tool_calls == ["macos.move_to_trash"]
    entry = harness.audit.entries()[0]
    assert entry.tier == "confirm" and entry.confirmed is True and entry.ok is True


# --- scenario 3 ---------------------------------------------------------------------


async def test_scenario_3_delete_then_no_does_nothing_and_audits_declined(
    harness: Harness, tmp_path: Path
) -> None:
    spared = tmp_path / "report.pdf"
    spared.write_text("quarterly numbers")

    agent = harness.agent(
        [
            [tool_use("macos__move_to_trash", {"path": str(spared)})],
            [text("Left it where it was.")],
        ]
    )
    await agent.ask("delete report.pdf", session_id="s")
    turn = await agent.confirm("no", session_id="s")

    assert spared.exists()
    assert turn.tool_calls == []
    entry = harness.audit.entries()[0]
    assert entry.confirmed is False and entry.ok is False and entry.error == "user declined"


@pytest.mark.parametrize("answer", ["no", "cancel", "wait", "actually never mind", ""])
async def test_anything_but_a_yes_aborts(harness: Harness, tmp_path: Path, answer: str) -> None:
    spared = tmp_path / "keep.txt"
    spared.write_text("x")
    agent = harness.agent(
        [[tool_use("macos__move_to_trash", {"path": str(spared)})], [text("Fine.")]]
    )
    await agent.ask("bin it", session_id="s")
    await agent.confirm(answer, session_id="s")
    assert spared.exists()
    assert harness.audit.entries()[0].confirmed is False


# --- scenario 4 ---------------------------------------------------------------------


async def test_scenario_4_a_late_yes_is_a_fresh_utterance(harness: Harness, tmp_path: Path) -> None:
    doomed = tmp_path / "report.pdf"
    doomed.write_text("x")

    agent = harness.agent(
        [
            [tool_use("macos__move_to_trash", {"path": str(doomed)})],
            [text("Yes to what, exactly?")],
        ]
    )
    await agent.ask("delete report.pdf", session_id="s")

    # 61 seconds later.
    paused = agent._paused["s"]
    paused.pending.created_at -= 61

    turn = await agent.ask("yes", session_id="s")
    assert doomed.exists(), "an expired confirmation must never run"
    assert turn.tool_calls == []
    assert "s" not in agent._paused
    assert harness.audit.entries() == []


async def test_confirm_after_expiry_says_so(harness: Harness, tmp_path: Path) -> None:
    doomed = tmp_path / "r.pdf"
    doomed.write_text("x")
    agent = harness.agent([[tool_use("macos__move_to_trash", {"path": str(doomed)})]])
    await agent.ask("bin it", session_id="s")
    agent._paused["s"].pending.created_at -= 61

    turn = await agent.confirm("yes", session_id="s")
    assert "timed out" in turn.reply
    assert doomed.exists()


async def test_confirm_with_nothing_pending(harness: Harness) -> None:
    agent = harness.agent([])
    turn = await agent.confirm("yes", session_id="s")
    assert "nothing waiting" in turn.reply


async def test_a_yes_inside_the_window_still_works(harness: Harness, tmp_path: Path) -> None:
    target = tmp_path / "over.txt"
    target.write_text("old")
    agent = harness.agent(
        [
            [
                tool_use(
                    "macos__write_file",
                    {"path": str(target), "content": "new", "mode": "overwrite"},
                )
            ],
            [text("Overwritten.")],
        ]
    )
    await agent.ask("overwrite it", session_id="s")
    agent._paused["s"].pending.created_at -= 59
    await agent.confirm("go ahead", session_id="s")
    assert target.read_text() == "new"


# --- scenario 5 ---------------------------------------------------------------------


async def test_scenario_5_blocked_path_never_reaches_the_shell(harness: Harness) -> None:
    agent = harness.agent(
        [
            [tool_use("macos__run_shell", {"cmd": "cat ~/.ssh/id_rsa"})],
            [text("That one's off limits, I'm afraid.")],
        ]
    )
    turn = await agent.ask("show me my ssh key", session_id="s")

    entry = harness.audit.entries()[0]
    assert entry.tier == "blocked" and entry.ok is False

    # The model was told, in the tool result, why it was refused.
    client_messages = turn
    assert "off limits" in client_messages.reply
    stored = harness.memory.history("s")
    results = [
        block
        for message in stored
        if isinstance(message["content"], list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert results and results[0]["is_error"] is True
    assert "blocked by policy" in str(results[0]["content"])


async def test_blocked_read_of_the_keychain(harness: Harness) -> None:
    agent = harness.agent(
        [
            [tool_use("macos__list_dir", {"path": "~/Library/Keychains"})],
            [text("Blocked.")],
        ]
    )
    await agent.ask("list my keychains", session_id="s")
    assert harness.audit.entries()[0].tier == "blocked"


# --- scenario 6 ---------------------------------------------------------------------


async def test_scenario_6_an_unfounded_claim_is_stripped(harness: Harness) -> None:
    """The model says it deleted something in a turn where nothing ran."""
    agent = harness.agent([[text("Done. I've deleted the file for you.")]])
    turn = await agent.ask("delete something", session_id="s")

    assert "I've deleted" not in turn.reply
    assert "did not actually do that" in turn.reply
    assert turn.tool_calls == []


async def test_a_claim_after_a_real_action_is_left_alone(harness: Harness, tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    agent = harness.agent(
        [
            [tool_use("macos__write_file", {"path": str(target), "content": "hi"})],
            [text("Done. I've saved that to new.txt.")],
        ]
    )
    turn = await agent.ask("write a file", session_id="s")
    assert turn.reply == "Done. I've saved that to new.txt."
    assert target.read_text() == "hi"


async def test_a_claim_after_a_failed_action_is_stripped(harness: Harness) -> None:
    agent = harness.agent(
        [
            [tool_use("macos__open_app", {"name": "NoSuchAppAtAll"})],
            [text("I've opened it for you.")],
        ]
    )
    turn = await agent.ask("open that app", session_id="s")
    assert "did not actually do that" in turn.reply


# --- loop mechanics -----------------------------------------------------------------


async def test_max_tool_rounds_stops_the_loop(harness: Harness, tmp_path: Path) -> None:
    config = replace(harness.config, max_tool_rounds=3)
    script = [[tool_use("macos__list_dir", {"path": str(tmp_path)}, f"tu_{i}")] for i in range(5)]
    agent = harness.agent(script, config=config)

    turn = await agent.ask("keep looking", session_id="s")
    assert turn.stopped_early
    assert len(turn.tool_calls) == 3
    assert "stopped after 3 steps" in turn.reply


async def test_parallel_tool_calls_all_run_and_return_together(
    harness: Harness, tmp_path: Path
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    agent = harness.agent(
        [
            [
                tool_use("macos__list_dir", {"path": str(tmp_path / "a")}, "tu_a"),
                tool_use("macos__list_dir", {"path": str(tmp_path / "b")}, "tu_b"),
            ],
            [text("Both empty.")],
        ]
    )
    turn = await agent.ask("compare a and b", session_id="s")
    assert turn.tool_calls == ["macos.list_dir", "macos.list_dir"]
    stored = harness.memory.history("s")
    result_message = next(
        m
        for m in stored
        if isinstance(m["content"], list)
        and all(b.get("type") == "tool_result" for b in m["content"])
    )
    assert len(result_message["content"]) == 2, "results must come back in one user message"


async def test_a_tool_error_is_handed_back_to_the_model(harness: Harness, tmp_path: Path) -> None:
    agent = harness.agent(
        [
            [tool_use("macos__read_file", {"path": str(tmp_path / "ghost.txt")})],
            [text("There's no such file.")],
        ]
    )
    turn = await agent.ask("read ghost.txt", session_id="s")
    assert "no such file" in turn.reply.lower()
    assert harness.audit.entries()[0].ok is False


async def test_an_unknown_tool_does_not_crash_the_turn(harness: Harness) -> None:
    agent = harness.agent(
        [
            [tool_use("macos__no_such_tool", {})],
            [text("I don't have that.")],
        ]
    )
    turn = await agent.ask("do the impossible", session_id="s")
    assert turn.reply
    assert harness.audit.entries()[0].tier == "blocked"


async def test_history_persists_across_turns(harness: Harness) -> None:
    agent = harness.agent([[text("Noted.")], [text("Yes, you said hello.")]])
    await agent.ask("hello", session_id="s")
    turn = await agent.ask("what did I just say?", session_id="s")

    assert turn.reply == "Yes, you said hello."
    history = harness.memory.history("s")
    assert history[0] == {"role": "user", "content": "hello"}
    assert len(history) == 4


async def test_sessions_are_isolated(harness: Harness) -> None:
    agent = harness.agent([[text("one")], [text("two")]])
    await agent.ask("first", session_id="a")
    await agent.ask("second", session_id="b")
    assert len(harness.memory.history("a")) == 2
    assert len(harness.memory.history("b")) == 2


async def test_memories_are_injected_into_the_prompt(harness: Harness) -> None:
    harness.memory.remember(Kind.PREFERENCE, "Arup takes oat milk in coffee")
    client = FakeAnthropic([[text("Noted.")]])
    agent = Agent(
        client,
        harness.pool,
        Guard(harness.audit),
        harness.memory,
        harness.config,
        include_frontmost=False,
    )
    await agent.ask("what milk do I take in coffee?", session_id="s")
    assert "oat milk" in client.last_request["system"][1]["text"]


async def test_ask_generates_a_session_id_when_not_given(harness: Harness) -> None:
    agent = harness.agent([[text("hi")]])
    turn = await agent.ask("hello")
    assert turn.session_id
    assert harness.memory.history(turn.session_id)


# --- events -------------------------------------------------------------------------


def kinds(events: list[Any]) -> list[str]:
    return [type(e).__name__ for e in events]


async def test_no_sink_behaves_exactly_as_before(harness: Harness, tmp_path: Path) -> None:
    """Guards the default path: the server and the voice loop pass no sink."""
    (tmp_path / "a.txt").write_text("x")
    agent = harness.agent(
        [
            [tool_use("macos__list_dir", {"path": str(tmp_path)})],
            [text("One file.")],
        ]
    )
    turn = await agent.ask("what's there", session_id="s")
    assert turn.reply == "One file."
    assert turn.tool_calls == ["macos.list_dir"]


async def test_a_read_turn_emits_start_then_finish(harness: Harness, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    events: list[Any] = []
    agent = harness.agent(
        [
            [tool_use("macos__list_dir", {"path": str(tmp_path)})],
            [text("One file.")],
        ]
    )
    await agent.ask("what's there", session_id="s", on_event=events.append)

    started = next(e for e in events if isinstance(e, ToolStarted))
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert started.tool == "macos.list_dir"
    # summarise() shortens a long path tail-first, so the identifying part survives.
    assert tmp_path.name in started.summary
    assert finished.call_id == started.call_id, "start and finish must pair by call_id"
    assert finished.ok is True
    assert finished.duration_ms >= 0
    assert kinds(events).index("ToolStarted") < kinds(events).index("ToolFinished")


async def test_parallel_calls_pair_by_call_id(harness: Harness, tmp_path: Path) -> None:
    """Two calls to the same tool in one round: pairing by name would be ambiguous."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    events: list[Any] = []
    agent = harness.agent(
        [
            [
                tool_use("macos__list_dir", {"path": str(tmp_path / "a")}, "tu_a"),
                tool_use("macos__list_dir", {"path": str(tmp_path / "b")}, "tu_b"),
            ],
            [text("Both empty.")],
        ]
    )
    await agent.ask("compare", session_id="s", on_event=events.append)

    starts = [e for e in events if isinstance(e, ToolStarted)]
    finishes = [e for e in events if isinstance(e, ToolFinished)]
    assert {e.call_id for e in starts} == {"tu_a", "tu_b"}
    assert {e.call_id for e in finishes} == {"tu_a", "tu_b"}


async def test_a_failing_tool_finishes_with_ok_false(harness: Harness, tmp_path: Path) -> None:
    events: list[Any] = []
    agent = harness.agent(
        [
            [tool_use("macos__read_file", {"path": str(tmp_path / "ghost.txt")})],
            [text("No such file.")],
        ]
    )
    await agent.ask("read it", session_id="s", on_event=events.append)
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.ok is False
    assert finished.detail


async def test_a_blocked_tool_is_not_silent(harness: Harness) -> None:
    """A refusal the user cannot see is a refusal they will not understand."""
    events: list[Any] = []
    agent = harness.agent(
        [
            [tool_use("macos__run_shell", {"cmd": "cat ~/.ssh/id_rsa"})],
            [text("Off limits.")],
        ]
    )
    await agent.ask("show my key", session_id="s", on_event=events.append)
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.ok is False
    assert "blocked by policy" in finished.detail


async def test_a_confirm_tier_call_suspends_without_finishing(
    harness: Harness, tmp_path: Path
) -> None:
    """ToolStarted then ConfirmationRequested; the ToolFinished lands on the resume."""
    doomed = tmp_path / "r.pdf"
    doomed.write_text("x")
    events: list[Any] = []
    agent = harness.agent(
        [
            [tool_use("macos__move_to_trash", {"path": str(doomed)})],
            [text("Left alone.")],
        ]
    )
    await agent.ask("bin it", session_id="s", on_event=events.append)

    assert kinds(events).count("ToolStarted") == 1
    assert kinds(events).count("ToolFinished") == 0
    requested = next(e for e in events if isinstance(e, ConfirmationRequested))
    started = next(e for e in events if isinstance(e, ToolStarted))
    assert requested.call_id == started.call_id

    resumed: list[Any] = []
    await agent.confirm("no", session_id="s", on_event=resumed.append)
    finished = next(e for e in resumed if isinstance(e, ToolFinished))
    assert finished.call_id == started.call_id
    assert doomed.exists()


async def test_text_deltas_concatenate_to_the_reply(harness: Harness) -> None:
    events: list[Any] = []
    agent = harness.agent([[text("Ten past four.")]])
    turn = await agent.ask("what time is it", session_id="s", on_event=events.append)

    deltas = [e for e in events if isinstance(e, TextDelta)]
    assert len(deltas) > 1, "the fixture must split text, or streaming is untested"
    assert "".join(d.text for d in deltas) == turn.reply


async def test_a_sink_that_raises_does_not_break_the_turn(harness: Harness) -> None:
    def angry(_event: Any) -> None:
        raise RuntimeError("renderer exploded")

    turn = await harness.agent([[text("Fine.")]]).ask("hi", session_id="s", on_event=angry)
    assert turn.reply == "Fine."
