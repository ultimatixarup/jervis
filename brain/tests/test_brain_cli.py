"""The `jervis` CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from jervis_brain import cli as cli_module
from jervis_brain.agent import Turn
from jervis_brain.config import Config, ServerConfig
from jervis_brain.permissions import AuditEntry, AuditLog


@dataclass
class StubAgent:
    turns: list[Turn]
    asked: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)

    async def ask(self, text: str, session_id: str = "s") -> Turn:
        self.asked.append(text)
        return self.turns.pop(0)

    async def confirm(self, text: str, session_id: str = "s") -> Turn:
        self.confirmed.append(text)
        return self.turns.pop(0)


@dataclass
class StubPool:
    failures: dict[str, str] = field(default_factory=dict)
    tools: list[Any] = field(default_factory=list)

    async def stop(self) -> None:
        return None


@dataclass
class StubMemory:
    def close(self) -> None:
        return None


def make_runtime(turns: list[Turn], config: Config, **kwargs: Any) -> Any:
    @dataclass
    class R:
        config: Config
        pool: StubPool
        memory: StubMemory
        agent: StubAgent

    return R(config, kwargs.get("pool", StubPool()), StubMemory(), StubAgent(turns))


@pytest.fixture
def config(tmp_path: Path) -> Config:
    from dataclasses import replace

    base = Config(servers=(ServerConfig(name="macos"),))
    return replace(base, paths=replace(base.paths, audit_log=tmp_path / "audit.jsonl"))


@pytest.fixture
def api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")


def install(monkeypatch: pytest.MonkeyPatch, runtime: Any) -> None:
    async def build(*_a: Any, **_k: Any) -> Any:
        return runtime

    monkeypatch.setattr(cli_module, "build_runtime", build)


@pytest.mark.usefixtures("api_key")
def test_ask_prints_the_reply(monkeypatch: pytest.MonkeyPatch, config: Config) -> None:
    runtime = make_runtime([Turn(reply="Two files.", session_id="cli")], config)
    install(monkeypatch, runtime)

    result = CliRunner().invoke(cli_module.cli, ["ask", "what's", "in", "Downloads"])
    assert result.exit_code == 0
    assert "Two files." in result.output
    assert runtime.agent.asked == ["what's in Downloads"]


@pytest.mark.usefixtures("api_key")
def test_ask_prompts_for_a_pending_confirmation(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    runtime = make_runtime(
        [
            Turn(reply="Bin report.pdf?", session_id="cli", pending_confirmation="bin it"),
            Turn(reply="Done.", session_id="cli"),
        ],
        config,
    )
    install(monkeypatch, runtime)

    result = CliRunner().invoke(cli_module.cli, ["ask", "delete it"], input="yes\n")
    assert result.exit_code == 0
    assert "Done." in result.output
    assert runtime.agent.confirmed == ["yes"]


@pytest.mark.usefixtures("api_key")
def test_repl_loops_until_eof(monkeypatch: pytest.MonkeyPatch, config: Config) -> None:
    runtime = make_runtime(
        [Turn(reply="one", session_id="repl"), Turn(reply="two", session_id="repl")], config
    )
    install(monkeypatch, runtime)

    result = CliRunner().invoke(cli_module.cli, ["repl"], input="first\n\nsecond\n")
    assert result.exit_code == 0
    assert "one" in result.output and "two" in result.output
    assert runtime.agent.asked == ["first", "second"], "a blank line is not a turn"


@pytest.mark.usefixtures("api_key")
def test_repl_keeps_asking_while_confirmation_is_pending(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    runtime = make_runtime(
        [
            Turn(reply="Bin it?", session_id="repl", pending_confirmation="bin"),
            Turn(reply="Left alone.", session_id="repl"),
        ],
        config,
    )
    install(monkeypatch, runtime)
    result = CliRunner().invoke(cli_module.cli, ["repl"], input="delete it\nno\n")
    assert "Left alone." in result.output
    assert runtime.agent.confirmed == ["no"]


def test_status_reports_servers_and_failures(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    AuditLog(config.paths.audit_log).write(
        AuditEntry(
            timestamp="2026-01-01T00:00:00Z",
            session_id="s",
            tool="macos.list_dir",
            args={},
            tier="read",
            ok=True,
        )
    )
    runtime = make_runtime([], config, pool=StubPool(failures={"mail": "boom"}))
    install(monkeypatch, runtime)

    result = CliRunner().invoke(cli_module.cli, ["status"])
    assert "claude-sonnet-5" in result.output
    assert "FAILED mail: boom" in result.output
    assert "macos.list_dir" in result.output


def test_audit_prints_the_tail(monkeypatch: pytest.MonkeyPatch, config: Config) -> None:
    monkeypatch.setattr(cli_module, "load_config", lambda: config)
    AuditLog(config.paths.audit_log).write(
        AuditEntry(timestamp="t", session_id="s", tool="macos.notify", args={}, tier="write")
    )
    result = CliRunner().invoke(cli_module.cli, ["audit"])
    assert "macos.notify" in result.output


def test_audit_says_so_when_empty(monkeypatch: pytest.MonkeyPatch, config: Config) -> None:
    monkeypatch.setattr(cli_module, "load_config", lambda: config)
    result = CliRunner().invoke(cli_module.cli, ["audit"])
    assert "nothing logged yet" in result.output


@pytest.mark.usefixtures("api_key")
def test_a_failed_server_is_warned_about(monkeypatch: pytest.MonkeyPatch, config: Config) -> None:
    runtime = make_runtime(
        [Turn(reply="ok", session_id="cli")], config, pool=StubPool(failures={"bank": "nope"})
    )
    install(monkeypatch, runtime)
    result = CliRunner().invoke(cli_module.cli, ["ask", "hi"])
    assert "bank" in result.output


@pytest.mark.parametrize("command", ["ask", "repl"])
def test_missing_credentials_are_explained_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, config: Config, command: str, tmp_path: Path
) -> None:
    from jervis_brain import credentials

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(credentials, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(credentials, "CREDENTIALS_DIR", tmp_path / "no-profiles")
    install(monkeypatch, make_runtime([], config))

    args = [command] if command == "repl" else [command, "hi"]
    result = CliRunner().invoke(cli_module.cli, args)
    assert result.exit_code != 0
    # Both routes to credentials are offered, not just the one.
    assert "ANTHROPIC_API_KEY" in result.output
    assert "ant auth login" in result.output
    assert "doctor.sh" in result.output


def test_status_works_without_an_api_key(monkeypatch: pytest.MonkeyPatch, config: Config) -> None:
    """Diagnostics must not need the key they are diagnosing."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    install(monkeypatch, make_runtime([], config))
    assert CliRunner().invoke(cli_module.cli, ["status"]).exit_code == 0
