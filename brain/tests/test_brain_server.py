"""The localhost HTTP endpoint."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fake_anthropic import FakeAnthropic, text, tool_use
from fastapi.testclient import TestClient

from jervis_brain.agent import Agent
from jervis_brain.config import Config, ServerConfig
from jervis_brain.mcp_client import MCPClientPool
from jervis_brain.memory import MemoryStore
from jervis_brain.permissions import AuditLog, Guard
from jervis_brain.server import Runtime, create_app


@pytest.fixture
async def runtime(tmp_path: Path) -> AsyncIterator[Runtime]:
    config = Config(servers=(ServerConfig(name="macos"),))
    async with MCPClientPool((ServerConfig(name="macos"),), python=sys.executable) as pool:
        with MemoryStore(tmp_path / "m.db") as memory:
            agent = Agent(
                FakeAnthropic(
                    [
                        [text("Two files.")],
                        [tool_use("macos__move_to_trash", {"path": str(tmp_path / "x.txt")})],
                        [text("Left alone.")],
                    ]
                ),
                pool,
                Guard(AuditLog(tmp_path / "audit.jsonl")),
                memory,
                config,
                include_frontmost=False,
            )
            yield Runtime(config, pool, memory, agent)


@pytest.fixture
def client(runtime: Runtime) -> Iterator[TestClient]:
    with TestClient(create_app(runtime)) as client:
        yield client


def test_health_lists_the_tools(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["model"] == "claude-sonnet-5"
    assert "macos.list_dir" in body["tools"]
    assert body["server_failures"] == {}


def test_ask(client: TestClient) -> None:
    body = client.post("/ask", json={"text": "what's in Downloads", "session_id": "s"}).json()
    assert body["reply"] == "Two files."
    assert body["session_id"] == "s"
    assert body["pending_confirmation"] is None


def test_ask_rejects_empty_text(client: TestClient) -> None:
    assert client.post("/ask", json={"text": ""}).status_code == 422


def test_confirm_round_trip(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("x")
    client.post("/ask", json={"text": "hello", "session_id": "s"})

    asked = client.post("/ask", json={"text": "bin x.txt", "session_id": "s"}).json()
    assert asked["pending_confirmation"] is not None
    assert "x.txt" in asked["pending_confirmation"]

    answered = client.post("/confirm", json={"text": "no", "session_id": "s"}).json()
    assert answered["pending_confirmation"] is None
    assert (tmp_path / "x.txt").exists()


def test_confirm_without_anything_pending(client: TestClient) -> None:
    body = client.post("/confirm", json={"text": "yes", "session_id": "nothing"}).json()
    assert "nothing waiting" in body["reply"]
