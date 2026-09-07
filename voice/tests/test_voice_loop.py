"""The whole listening loop, wired to the real brain over its real HTTP routes.

The microphone is a wav file, the model is scripted, and the speaker is a list. The
MCP server, the permission guard, the audit log and the FastAPI routes are all real.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fake_anthropic import FakeAnthropic, tool_use
from fake_anthropic import text as say_text
from fastapi.testclient import TestClient
from speech import available, frames_of, silence, with_silence

from jervis_brain.agent import Agent
from jervis_brain.config import Config, ServerConfig
from jervis_brain.mcp_client import MCPClientPool
from jervis_brain.memory import MemoryStore
from jervis_brain.permissions import AuditLog, Guard
from jervis_brain.server import Runtime, create_app
from jervis_voice.brain import BrainClient
from jervis_voice.loop import VoiceConfig, VoiceLoop
from jervis_voice.states import State
from jervis_voice.stt import Transcript
from jervis_voice.tts import RecordingBackend
from jervis_voice.vad import EnergyDetector

pytestmark = pytest.mark.skipif(not available(), reason="needs macOS `say` and ffmpeg")


class ListSource:
    """A fixed list of frames as an audio source."""

    def __init__(self, frames: list[np.ndarray]) -> None:
        self._frames = frames
        self.closed = False

    def frames(self) -> Iterator[np.ndarray]:
        yield from self._frames

    def close(self) -> None:
        self.closed = True


@dataclass
class ScriptedWake:
    """Fires the wake word after a fixed number of frames."""

    fire_at: list[int] = field(default_factory=list)
    seen: int = 0

    def feed(self, _frame: np.ndarray) -> bool:
        self.seen += 1
        return self.seen in self.fire_at


@dataclass
class ScriptedTranscriber:
    """Returns the next scripted transcript for each captured utterance."""

    transcripts: list[Transcript]
    captured: list[int] = field(default_factory=list)

    def transcribe(self, audio: np.ndarray) -> Transcript:
        self.captured.append(int(audio.size))
        return self.transcripts.pop(0) if self.transcripts else Transcript("", 0.0)


@dataclass
class Brain:
    """The real brain over its real routes, with a scripted model behind it."""

    client: TestClient
    fake: FakeAnthropic
    audit: AuditLog

    def will_say(self, *turns: Any) -> None:
        """Queue assistant turns. Appends to the fake's own list, not a copy."""
        self.fake.script.extend(turns)


@pytest.fixture
def brain_app(tmp_path: Path) -> Iterator[Brain]:
    fake = FakeAnthropic([])
    audit = AuditLog(tmp_path / "audit.jsonl")
    config = Config(servers=(ServerConfig(name="macos"),))

    async def make_runtime() -> Runtime:
        # Built inside the app's lifespan so the MCP sessions live on the same event
        # loop that serves requests. Creating them on another loop deadlocks.
        pool = MCPClientPool((ServerConfig(name="macos"),), python=sys.executable)
        await pool.start()
        assert not pool.failures, pool.failures
        memory = MemoryStore(tmp_path / "m.db")
        agent = Agent(fake, pool, Guard(audit), memory, config, include_frontmost=False)
        return Runtime(config, pool, memory, agent)

    with TestClient(create_app(runtime_factory=make_runtime)) as client:
        yield Brain(client, fake, audit)


def make_loop(
    client: TestClient,
    frames: list[np.ndarray],
    wake_at: list[int],
    transcripts: list[Transcript],
    *,
    speaking_frames: int = 0,
) -> tuple[VoiceLoop, RecordingBackend, ScriptedTranscriber]:
    tts = RecordingBackend(speaking_frames=speaking_frames)
    transcriber = ScriptedTranscriber(list(transcripts))
    brain = BrainClient(base_url="http://testserver", client=client)
    loop = VoiceLoop(
        source=ListSource(frames),
        wake=ScriptedWake(fire_at=wake_at),
        transcriber=transcriber,
        tts=tts,
        brain=brain,
        config=VoiceConfig(),
        detector=EnergyDetector(),
        chime=False,
    )
    return loop, tts, transcriber


def utterance_frames(text: str) -> list[np.ndarray]:
    return frames_of(with_silence(text, lead_ms=100, trail_ms=1000))


# --- a plain turn --------------------------------------------------------------------


def test_wake_then_speak_then_hear_the_answer(brain_app: Brain) -> None:
    brain_app.will_say([say_text("Ten past four.")])

    frames = [silence(20)[:320]] * 3 + utterance_frames("what time is it")
    loop, tts, _ = make_loop(
        brain_app.client, frames, wake_at=[2], transcripts=[Transcript("what time is it", 0.95)]
    )
    loop.run(max_turns=1)

    assert tts.spoken == ["Ten past four."]
    assert loop.machine.state is State.IDLE


def test_nothing_is_said_without_the_wake_word(brain_app: Brain) -> None:
    loop, tts, transcriber = make_loop(
        brain_app.client, utterance_frames("what time is it"), wake_at=[], transcripts=[]
    )
    loop.run()
    assert tts.spoken == []
    assert transcriber.captured == []


def test_an_unusable_transcript_is_discarded(brain_app: Brain) -> None:
    """A low-confidence guess must not become a command."""
    frames = [silence(20)[:320]] * 3 + utterance_frames("mumble")
    loop, tts, _ = make_loop(
        brain_app.client, frames, wake_at=[2], transcripts=[Transcript("delete everything", 0.05)]
    )
    loop.run()
    assert tts.spoken == []
    assert loop.machine.state is State.IDLE


# --- the confirmation flow ------------------------------------------------------------


@pytest.mark.macos
def test_a_delete_is_read_back_and_only_runs_after_yes(brain_app: Brain, tmp_path: Path) -> None:
    doomed = tmp_path / "report.pdf"
    doomed.write_text("numbers")
    brain_app.will_say(
        [tool_use("macos__move_to_trash", {"path": str(doomed)})],
        [say_text("Moved it to the Trash.")],
    )

    frames = (
        [silence(20)[:320]] * 3
        + utterance_frames("delete report dot pdf")
        + utterance_frames("yes")
    )
    loop, tts, _ = make_loop(
        brain_app.client,
        frames,
        wake_at=[2],
        transcripts=[Transcript("delete report.pdf", 0.95), Transcript("yes", 0.98)],
    )
    loop.run(max_turns=1)

    assert len(tts.spoken) == 2
    assert "report.pdf" in tts.spoken[0]
    assert "Shall I go ahead?" in tts.spoken[0]
    assert tts.spoken[1] == "Moved it to the Trash."
    assert not doomed.exists()


def test_saying_no_leaves_the_file_alone(brain_app: Brain, tmp_path: Path) -> None:
    spared = tmp_path / "keep.txt"
    spared.write_text("still here")
    brain_app.will_say(
        [tool_use("macos__move_to_trash", {"path": str(spared)})],
        [say_text("Left it alone.")],
    )

    frames = [silence(20)[:320]] * 3 + utterance_frames("bin keep dot txt") + utterance_frames("no")
    loop, tts, _ = make_loop(
        brain_app.client,
        frames,
        wake_at=[2],
        transcripts=[Transcript("bin keep.txt", 0.95), Transcript("no", 0.98)],
    )
    loop.run(max_turns=1)

    assert spared.exists()
    assert tts.spoken[-1] == "Left it alone."


def test_silence_after_the_read_back_abandons_the_action(brain_app: Brain, tmp_path: Path) -> None:
    """Saying nothing must not be taken as yes."""
    spared = tmp_path / "keep.txt"
    spared.write_text("still here")
    brain_app.will_say([tool_use("macos__move_to_trash", {"path": str(spared)})])

    frames = (
        [silence(20)[:320]] * 3 + utterance_frames("bin keep dot txt") + frames_of(silence(4000))
    )
    loop, _tts, _ = make_loop(
        brain_app.client, frames, wake_at=[2], transcripts=[Transcript("bin keep.txt", 0.95)]
    )
    loop.run(max_turns=1)

    assert spared.exists()
    assert loop.machine.state is State.IDLE
    assert loop.machine.pending_confirmation is None


# --- barge-in --------------------------------------------------------------------------


def test_the_wake_word_interrupts_a_long_answer(brain_app: Brain) -> None:
    brain_app.will_say([say_text("A very long answer that goes on and on.")])

    frames = (
        [silence(20)[:320]] * 3 + utterance_frames("tell me everything") + [silence(20)[:320]] * 40
    )
    loop, tts, _ = make_loop(
        brain_app.client,
        frames,
        wake_at=[2, 200],
        transcripts=[Transcript("tell me everything", 0.95), Transcript("", 0.0)],
        speaking_frames=100,
    )
    loop.run()

    assert tts.stops >= 1, "speech must be stopped when interrupted"


# --- failures ---------------------------------------------------------------------------


def test_a_brain_that_is_down_is_spoken_about_not_swallowed(
    brain_app: Brain,
) -> None:
    frames = [silence(20)[:320]] * 3 + utterance_frames("what time is it")
    loop, tts, _ = make_loop(
        brain_app.client, frames, wake_at=[2], transcripts=[Transcript("what time is it", 0.95)]
    )
    # Point the client at a port nothing is listening on.
    loop.brain = BrainClient(base_url="http://127.0.0.1:1", timeout=1)
    loop.run()

    assert tts.spoken, "a failure must still be spoken aloud"
    assert "couldn't reach my brain" in tts.spoken[0]


def test_the_source_is_closed_when_the_loop_ends(brain_app: Brain) -> None:
    loop, _, _ = make_loop(brain_app.client, [silence(20)[:320]] * 5, wake_at=[], transcripts=[])
    loop.run()
    assert loop.source.closed  # type: ignore[attr-defined]
