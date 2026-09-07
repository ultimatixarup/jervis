"""Runs the YAML conversation scenarios in `scenarios/`.

PLAN.md §5 sketches the format. Two deliberate additions:

- Each turn carries a `model:` block - the assistant turn the fake Anthropic client
  replays. Without it a scenario would depend on what a live model happens to choose,
  which is not something a test can assert on.
- Paths use a `{tmp}` placeholder rather than `~/Desktop`. An automated suite should
  not create and trash files in someone's real Desktop.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from fake_anthropic import FakeAnthropic, text, tool_use

from jervis_brain.agent import Agent
from jervis_brain.config import Config, ServerConfig
from jervis_brain.mcp_client import MCPClientPool, to_wire_name
from jervis_brain.memory import MemoryStore
from jervis_brain.permissions import AuditEntry, AuditLog, Guard

SCENARIO_DIR = Path(__file__).parent / "scenarios"


@dataclass(frozen=True)
class Scenario:
    path: Path
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.data.get("name", self.path.stem))

    @property
    def macos_only(self) -> bool:
        return bool(self.data.get("macos_only", False))


def load_scenarios() -> list[Scenario]:
    return [
        Scenario(path, yaml.safe_load(path.read_text()) or {})
        for path in sorted(SCENARIO_DIR.glob("*.yaml"))
    ]


def _expand(value: Any, tmp: Path) -> Any:
    if isinstance(value, str):
        return value.replace("{tmp}", str(tmp))
    if isinstance(value, dict):
        return {k: _expand(v, tmp) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, tmp) for v in value]
    return value


def _blocks_for(spec: dict[str, Any], tmp: Path, block_id: str) -> list[Any]:
    """One `model:` entry becomes one assistant turn.

    `text:` ends the turn; `tool_use:` is a single call; `tools:` is several calls in
    one message, which is how a real model asks for parallel work.
    """
    if "text" in spec:
        return [text(str(spec["text"]))]
    if "tools" in spec:
        return [
            tool_use(
                to_wire_name(str(call["name"])),
                _expand(call.get("input", {}), tmp),
                block_id=f"{block_id}_{i}",
            )
            for i, call in enumerate(spec["tools"])
        ]
    return [
        tool_use(to_wire_name(str(spec["tool_use"])), _expand(spec.get("input", {}), tmp), block_id)
    ]


def _script(turns: list[dict[str, Any]], tmp: Path) -> list[list[Any]]:
    """Every `model:` entry across every turn, in order, one per completion."""
    script: list[list[Any]] = []
    for turn_index, turn in enumerate(turns):
        for entry_index, spec in enumerate(turn.get("model", [])):
            script.append(_blocks_for(spec, tmp, f"tu_{turn_index}_{entry_index}"))
    return script


def apply_setup(setup: dict[str, Any], tmp: Path) -> None:
    for raw_path, content in (setup.get("files") or {}).items():
        target = Path(_expand(raw_path, tmp))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content))


def check_expectations(
    expect: dict[str, Any], turn: Any, entries: list[AuditEntry], tmp: Path
) -> None:
    expect = _expand(expect, tmp)

    if "asks_confirmation" in expect:
        assert turn.awaiting_confirmation is expect["asks_confirmation"], (
            f"expected asks_confirmation={expect['asks_confirmation']}, "
            f"got {turn.awaiting_confirmation} (reply: {turn.reply!r})"
        )
    for name in expect.get("tool_calls", []):
        assert name in turn.tool_calls, f"{name} was not called; called {turn.tool_calls}"
    for name in expect.get("no_tool_calls", []):
        assert name not in turn.tool_calls, f"{name} ran but must not have"
    if "reply_contains" in expect:
        assert expect["reply_contains"] in turn.reply, f"reply was {turn.reply!r}"
    if "file_absent" in expect:
        assert not Path(expect["file_absent"]).exists(), f"{expect['file_absent']} still exists"
    if "file_present" in expect:
        assert Path(expect["file_present"]).exists(), f"{expect['file_present']} is missing"

    audit = expect.get("audit")
    if audit:
        matching = [e for e in entries if e.tool == audit.get("tool", e.tool)]
        assert matching, (
            f"no audit entry for {audit.get('tool')}; log has {[e.tool for e in entries]}"
        )
        entry = matching[-1]
        for field, expected in audit.items():
            if field == "tool":
                continue
            actual = getattr(entry, field)
            assert actual == expected, f"audit {field}: expected {expected!r}, got {actual!r}"


async def run_scenario(scenario: Scenario, tmp: Path) -> None:
    data = scenario.data
    apply_setup(data.get("setup") or {}, tmp)
    turns = list(data.get("turns") or [])

    config = Config(servers=(ServerConfig(name="macos"),))
    audit = AuditLog(tmp / "audit.jsonl")

    async with MCPClientPool((ServerConfig(name="macos"),), python=sys.executable) as pool:
        assert not pool.failures, pool.failures
        with MemoryStore(tmp / "memory.db") as memory:
            agent = Agent(
                FakeAnthropic(_script(turns, tmp)),
                pool,
                Guard(audit, confirm_window_seconds=60),
                memory,
                config,
                include_frontmost=False,
            )
            for turn_spec in turns:
                turn = await agent.ask(str(turn_spec["user"]), session_id="e2e")
                check_expectations(turn_spec.get("expect") or {}, turn, audit.entries(), tmp)
