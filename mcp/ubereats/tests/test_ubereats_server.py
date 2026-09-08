"""The ordering flow. Nothing here touches Uber Eats or spends anything.

The property under test throughout: money moves only when someone has been shown the
actual total and it has not changed since.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from jervis_mcp_ubereats import server as srv

PREVIEW = "Burrito $12.00\nDelivery $3.30\nTotal: $15.30"
DEARER = "Burrito $22.00\nDelivery $3.30\nTotal: $25.30"


class FakeChild:
    """Stands in for the upstream server. Records every call."""

    def __init__(self, replies: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.replies = replies or {}
        self.preview_text = PREVIEW

    async def call(self, name: str, args: dict[str, Any] | None = None) -> str:
        args = args or {}
        self.calls.append((name, args))
        if name == "ubereats_checkout":
            if args.get("confirm"):
                return "Order placed. Arriving 7:15pm."
            return self.preview_text
        return str(self.replies.get(name, f"{name} ok"))

    @property
    def placed(self) -> bool:
        return any(n == "ubereats_checkout" and a.get("confirm") for n, a in self.calls)


@pytest.fixture
def child(monkeypatch: pytest.MonkeyPatch) -> FakeChild:
    fake = FakeChild()
    monkeypatch.setattr(srv, "child", fake)
    srv.previews().clear()
    return fake


# --- the tool surface --------------------------------------------------------------------


def test_the_upstream_checkout_is_not_exposed() -> None:
    """The whole design: there is no way to order that skips the price check."""
    names = {t.name for t in asyncio.run(srv.mcp.list_tools())}
    assert "place_order" in names
    assert "preview_order" in names
    assert not any("checkout" in n for n in names)


def test_only_place_order_is_confirm_tier() -> None:
    tiers = {t.name: (t.meta or {}).get("x-jervis-tier") for t in asyncio.run(srv.mcp.list_tools())}
    assert tiers["place_order"] == "confirm"
    assert tiers["preview_order"] == "read", "pricing something up spends nothing"
    assert tiers["add_to_cart"] == "write"
    assert {n for n, t in tiers.items() if t == "confirm"} == {"place_order"}


# --- previewing ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_prices_the_cart_without_ordering(child: FakeChild) -> None:
    out = await srv.preview_order()
    assert "15.30" in out
    assert child.calls == [("ubereats_checkout", {"confirm": False})]
    assert not child.placed


@pytest.mark.asyncio
async def test_a_preview_with_no_readable_total_refuses(child: FakeChild) -> None:
    child.preview_text = "Something went wrong"
    with pytest.raises(ToolError, match="couldn't find a total"):
        await srv.preview_order()
    assert not child.placed


# --- placing --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_steady_price_is_ordered(child: FakeChild) -> None:
    preview_id = _id_from(await srv.preview_order())
    out = await srv.place_order(preview_id)

    assert child.placed
    assert "15.30" in out
    # Priced again immediately before ordering - that is the check.
    assert [n for n, _ in child.calls].count("ubereats_checkout") == 3


@pytest.mark.asyncio
async def test_a_moved_price_is_refused(child: FakeChild) -> None:
    """The bug this whole tool exists to prevent."""
    preview_id = _id_from(await srv.preview_order())
    child.preview_text = DEARER

    with pytest.raises(ToolError, match="did not place the order"):
        await srv.place_order(preview_id)
    assert not child.placed


@pytest.mark.asyncio
async def test_the_refusal_says_what_the_new_price_is(child: FakeChild) -> None:
    preview_id = _id_from(await srv.preview_order())
    child.preview_text = DEARER
    with pytest.raises(ToolError) as excinfo:
        await srv.place_order(preview_id)
    assert "25.30" in str(excinfo.value) and "15.30" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_small_rise_still_goes_through(child: FakeChild) -> None:
    preview_id = _id_from(await srv.preview_order())
    child.preview_text = "Total: $16.00"  # under 10%
    out = await srv.place_order(preview_id)
    assert child.placed
    assert "moved" in out


@pytest.mark.asyncio
async def test_ordering_without_a_preview_is_refused(child: FakeChild) -> None:
    """No order is ever placed against a price nobody has seen."""
    with pytest.raises(ToolError, match="No such preview"):
        await srv.place_order("made-up-id")
    assert not child.placed


@pytest.mark.asyncio
async def test_a_stale_preview_is_refused(child: FakeChild) -> None:
    preview_id = _id_from(await srv.preview_order())
    stale = srv.previews()[preview_id]
    srv.previews()[preview_id] = srv.Preview(
        id=stale.id,
        total=stale.total,
        summary=stale.summary,
        created_at=stale.created_at - srv.PREVIEW_TTL_SECONDS - 1,
    )
    with pytest.raises(ToolError, match="minutes old"):
        await srv.place_order(preview_id)
    assert not child.placed


@pytest.mark.asyncio
async def test_a_preview_cannot_be_used_twice(child: FakeChild) -> None:
    """Otherwise one yes could place the same order repeatedly."""
    preview_id = _id_from(await srv.preview_order())
    await srv.place_order(preview_id)
    with pytest.raises(ToolError, match="No such preview"):
        await srv.place_order(preview_id)


@pytest.mark.asyncio
async def test_a_refusal_burns_the_preview(child: FakeChild) -> None:
    """You must re-price and look again, not retry until it slips through."""
    preview_id = _id_from(await srv.preview_order())
    child.preview_text = DEARER
    with pytest.raises(ToolError):
        await srv.place_order(preview_id)
    with pytest.raises(ToolError, match="No such preview"):
        await srv.place_order(preview_id)


@pytest.mark.asyncio
async def test_an_unreadable_reprice_refuses(child: FakeChild) -> None:
    preview_id = _id_from(await srv.preview_order())
    child.preview_text = "the site is having a moment"
    with pytest.raises(ToolError, match="not placed"):
        await srv.place_order(preview_id)
    assert not child.placed


@pytest.mark.asyncio
async def test_clearing_the_cart_invalidates_previews(child: FakeChild) -> None:
    """The basket changed, so the total nobody re-checked means nothing."""
    preview_id = _id_from(await srv.preview_order())
    await srv.clear_cart()
    with pytest.raises(ToolError, match="No such preview"):
        await srv.place_order(preview_id)


# --- pass-through -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browsing_reaches_the_upstream_server(child: FakeChild) -> None:
    await srv.search("thai", cuisine="thai")
    await srv.add_to_cart("r1", "Pad See Ew", quantity=2)
    names = [n for n, _ in child.calls]
    assert names == ["ubereats_search", "ubereats_add_to_cart"]
    assert child.calls[1][1]["restaurantId"] == "r1"
    assert child.calls[1][1]["quantity"] == 2


@pytest.mark.asyncio
async def test_absent_optional_arguments_are_not_sent(child: FakeChild) -> None:
    await srv.search("thai")
    assert "cuisine" not in child.calls[0][1]


def test_no_source_file_calls_checkout_with_confirm_true_outside_place_order() -> None:
    """A grep, in the spirit of PLAN.md §4 Phase 5's money-movement test."""
    source = Path(srv.__file__).read_text()
    before, _, after = source.partition("async def place_order")
    assert "confirm=True" not in before
    assert after.count("confirm=True") == 1


def _id_from(preview_output: str) -> str:
    return preview_output.rsplit("Preview id ", 1)[1].rstrip(".")


def test_a_missing_browser_gives_the_command_that_fixes_it() -> None:
    """The upstream error is a boxed banner telling you to run `npx playwright
    install`, which is the wrong command - it uses patchright."""
    from jervis_mcp_ubereats.child import BROWSER_HINT, _looks_like_a_missing_browser

    banner = (
        '{"success":false,"error":"browserType.launch: Executable doesn\'t exist at '
        "/Users/x/Library/Caches/ms-playwright/chromium_headless_shell-1234/...\\n"
        'Please run: npx playwright install"}'
    )
    assert _looks_like_a_missing_browser(banner)
    assert "npx patchright install chromium" in BROWSER_HINT
    assert not _looks_like_a_missing_browser("restaurant not found")
