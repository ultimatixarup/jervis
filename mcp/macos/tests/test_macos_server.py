"""Tool declarations and an over-stdio integration pass.

The integration tests drive the server exactly as the brain will: a real subprocess,
a real MCP client session, real JSON-RPC.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp.client.stdio import stdio_client

from jervis_mcp_macos.server import mcp
from jervis_mcp_macos.tiers import ESCALATION_KEY, TIER_KEY, Escalation, Tier
from mcp import ClientSession, StdioServerParameters, types

EXPECTED_TIERS: dict[str, Tier] = {
    "run_shell": Tier.WRITE,
    "run_applescript": Tier.WRITE,
    "open_app": Tier.WRITE,
    "open_url": Tier.WRITE,
    "list_dir": Tier.READ,
    "read_file": Tier.READ,
    "search_files": Tier.READ,
    "write_file": Tier.WRITE,
    "move_to_trash": Tier.CONFIRM,
    "notify": Tier.WRITE,
    "clipboard_get": Tier.READ,
    "clipboard_set": Tier.WRITE,
    "screenshot": Tier.READ,
    "system_info": Tier.READ,
    "set_volume": Tier.WRITE,
    "toggle_do_not_disturb": Tier.WRITE,
}


async def _declared_tools() -> list[types.Tool]:
    return await mcp.list_tools()


def test_all_tools_have_tier() -> None:
    """A tool without a tier can never be classified, so it must not ship."""
    for tool in asyncio.run(_declared_tools()):
        meta = tool.meta or {}
        assert TIER_KEY in meta, f"{tool.name} declares no {TIER_KEY}"
        assert meta[TIER_KEY] in {t.value for t in Tier}, f"{tool.name}: bad tier"


def test_tiers_match_the_plan() -> None:
    actual = {t.name: t.meta[TIER_KEY] for t in asyncio.run(_declared_tools()) if t.meta}
    assert actual == {name: tier.value for name, tier in EXPECTED_TIERS.items()}


def test_escalation_hints_are_declared_where_a_static_tier_is_not_enough() -> None:
    by_name = {t.name: (t.meta or {}) for t in asyncio.run(_declared_tools())}
    assert by_name["run_shell"][ESCALATION_KEY] == Escalation.DESTRUCTIVE_COMMAND
    assert by_name["run_applescript"][ESCALATION_KEY] == Escalation.DESTRUCTIVE_COMMAND
    assert by_name["write_file"][ESCALATION_KEY] == Escalation.OVERWRITES_EXISTING_FILE
    assert ESCALATION_KEY not in by_name["list_dir"]


def test_read_tools_are_marked_read_only() -> None:
    for tool in asyncio.run(_declared_tools()):
        if (tool.meta or {}).get(TIER_KEY) == Tier.READ:
            assert tool.annotations and tool.annotations.read_only_hint, (
                f"{tool.name} is tier=read but not annotated read-only"
            )


def test_no_tool_moves_money_or_reads_secrets() -> None:
    """PLAN.md §0: blocked capabilities must not exist as tools at all."""
    names = {t.name for t in asyncio.run(_declared_tools())}
    forbidden = {"transfer", "pay", "keychain", "ssh_key", "set_security"}
    assert not {n for n in names if any(f in n for f in forbidden)}


def test_every_tool_has_a_description() -> None:
    for tool in asyncio.run(_declared_tools()):
        assert tool.description and tool.description.strip(), f"{tool.name} has no description"


# --- integration over stdio ---------------------------------------------------------


@asynccontextmanager
async def _session() -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(command=sys.executable, args=["-m", "jervis_mcp_macos"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def _text(result: Any) -> str:
    return "\n".join(b.text for b in result.content if isinstance(b, types.TextContent))


async def _call(name: str, args: dict[str, Any]) -> Any:
    async with _session() as session:
        return await session.call_tool(name, args)


@pytest.mark.asyncio
async def test_server_starts_and_lists_the_same_tools() -> None:
    async with _session() as session:
        listed = await session.list_tools()
    assert {t.name for t in listed.tools} == set(EXPECTED_TIERS)
    assert all((t.meta or {}).get(TIER_KEY) for t in listed.tools)


@pytest.mark.asyncio
async def test_list_dir_and_read_file_round_trip(tmp_path: Path) -> None:
    async with _session() as session:
        written = await session.call_tool(
            "write_file", {"path": str(tmp_path / "note.txt"), "content": "hello jervis"}
        )
        assert not written.is_error, _text(written)

        listing = await session.call_tool("list_dir", {"path": str(tmp_path)})
        assert "note.txt" in _text(listing)

        read_back = await session.call_tool("read_file", {"path": str(tmp_path / "note.txt")})
        assert _text(read_back) == "hello jervis"


@pytest.mark.asyncio
async def test_write_file_modes(tmp_path: Path) -> None:
    target = str(tmp_path / "modes.txt")
    async with _session() as session:
        await session.call_tool("write_file", {"path": target, "content": "one"})

        clash = await session.call_tool("write_file", {"path": target, "content": "two"})
        assert clash.is_error and "already exists" in _text(clash)

        await session.call_tool("write_file", {"path": target, "content": "+two", "mode": "append"})
        await session.call_tool(
            "write_file", {"path": target, "content": "three", "mode": "overwrite"}
        )
        final = await session.call_tool("read_file", {"path": target})
    assert _text(final) == "three"


@pytest.mark.asyncio
async def test_read_file_truncates(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    big.write_text("x" * 5000)
    result = await _call("read_file", {"path": str(big), "max_bytes": 100})
    text = _text(result)
    assert text.startswith("x" * 100)
    assert "truncated at 100 bytes" in text


@pytest.mark.asyncio
async def test_run_shell_returns_output_and_exit_code(tmp_path: Path) -> None:
    async with _session() as session:
        ok = await session.call_tool("run_shell", {"cmd": "echo hi", "cwd": str(tmp_path)})
        assert _text(ok).strip() == "hi"

        bad = await session.call_tool("run_shell", {"cmd": "exit 3"})
        assert "exit 3" in _text(bad)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("run_shell", {"cmd": "cat ~/.ssh/id_rsa"}),
        ("run_applescript", {"script": 'do shell script "cat ~/.ssh/id_rsa"'}),
        ("read_file", {"path": "~/.ssh/id_rsa"}),
        ("list_dir", {"path": "~/Library/Keychains"}),
        ("write_file", {"path": "~/.ssh/authorized_keys", "content": "nope"}),
        ("move_to_trash", {"path": "/System/Library"}),
    ],
)
async def test_blocked_paths_are_refused_by_the_server_itself(
    tool: str, args: dict[str, Any]
) -> None:
    """Defence-in-depth: the server refuses regardless of what the brain's guard did."""
    result = await _call(tool, args)
    assert result.is_error, f"{tool} did not refuse {args}"
    assert "blocked by policy" in _text(result)


@pytest.mark.asyncio
async def test_open_url_rejects_non_http_schemes() -> None:
    result = await _call("open_url", {"url": "file:///etc/passwd"})
    assert result.is_error
    assert "http" in _text(result)


@pytest.mark.asyncio
async def test_set_volume_rejects_out_of_range() -> None:
    result = await _call("set_volume", {"pct": 150})
    assert result.is_error
    assert "between 0 and 100" in _text(result)


@pytest.mark.asyncio
async def test_search_files_never_returns_blocked_paths(tmp_path: Path) -> None:
    result = await _call("search_files", {"query": "kMDItemFSName=*", "root": str(tmp_path)})
    assert "/.ssh/" not in _text(result)


@pytest.mark.asyncio
@pytest.mark.macos
async def test_system_info_reports_the_real_machine() -> None:
    import json

    info = json.loads(_text(await _call("system_info", {})))
    assert set(info) >= {"battery", "volume", "frontmost_app", "wifi", "disk_free", "hostname"}
    assert "GB" in info["disk_free"]


@pytest.mark.asyncio
@pytest.mark.macos
async def test_move_to_trash_removes_the_file(tmp_path: Path) -> None:
    doomed = tmp_path / "doomed.txt"
    doomed.write_text("bye")
    result = await _call("move_to_trash", {"path": str(doomed)})
    assert not result.is_error, _text(result)
    assert not doomed.exists()


@pytest.mark.asyncio
@pytest.mark.macos
async def test_screenshot_returns_a_png() -> None:
    result = await _call("screenshot", {})
    if result.is_error and "Screen Recording" in _text(result):
        pytest.skip("Screen Recording is not granted - run scripts/grant-permissions.sh")
    assert not result.is_error, _text(result)
    images = [b for b in result.content if isinstance(b, types.ImageContent)]
    assert images and images[0].mimeType == "image/png"


@pytest.mark.asyncio
@pytest.mark.macos
async def test_screenshot_rejects_a_malformed_region() -> None:
    result = await _call("screenshot", {"region": "10,20,oops"})
    assert result.is_error
    assert "x,y,width,height" in _text(result)
