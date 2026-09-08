"""MCP server: Uber Eats, with the price check the plan asked for.

A proxy around `@striderlabs/mcp-ubereats`. Browsing and cart tools pass straight
through, tiered properly. Ordering does not: the upstream `ubereats_checkout` is never
exposed, so the only way to place an order is `place_order`, which re-previews first
and refuses if the total has moved (PLAN.md §4 Phase 6).

Without that, what you approve is the *instruction* - "order my usual" - and what you
are charged is whatever the total happens to be when the click lands. The upstream
server's own README says dynamic pricing may differ.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, signin
from .child import Child, ChildUnavailable
from .pricing import DEFAULT_TOLERANCE, Drift, UnreadableTotal, parse_total

TIER_KEY = "x-jervis-tier"
READ = {TIER_KEY: "read"}
WRITE = {TIER_KEY: "write"}
CONFIRM = {TIER_KEY: "confirm"}
READ_ONLY = ToolAnnotations(read_only_hint=True)

# A preview older than this is not a basis for spending money: baskets change, prices
# change, and the point of the check is that the two are close together in time.
PREVIEW_TTL_SECONDS = 300

child = Child()

mcp = MCPServer(
    "jervis-ubereats",
    version=__version__,
    instructions=(
        "Orders food on Uber Eats. Always preview_order first, read the total aloud, "
        "then place_order with the id it gave you. There is no other way to order."
    ),
)


@dataclass(frozen=True)
class Preview:
    id: str
    total: Decimal
    summary: str
    created_at: float

    def expired(self, now: float | None = None) -> bool:
        return (now or time.monotonic()) - self.created_at > PREVIEW_TTL_SECONDS


_previews: dict[str, Preview] = {}


async def _call(name: str, **args: Any) -> str:
    try:
        return await child.call(name, {k: v for k, v in args.items() if v is not None})
    except ChildUnavailable as exc:
        raise ToolError(str(exc)) from exc


# --- browsing: straight through --------------------------------------------------------


# When the session on disk is newer than the running child, the child is still using
# the old one. Tracked so `status` can reload rather than reporting a stale answer.
_child_started_at: float = 0.0


@mcp.tool(meta=READ, annotations=READ_ONLY, description="Whether you are signed in to Uber Eats.")
async def status() -> str:
    global _child_started_at
    session = signin.read_session()
    if session.looks_signed_in and session.modified_at > _child_started_at:
        # Signed in since the child started: it is holding the previous session.
        try:
            await child.restart()
            _child_started_at = session.modified_at
        except ChildUnavailable as exc:
            raise ToolError(str(exc)) from exc
    return await _call("ubereats_status")


@mcp.tool(
    meta=WRITE,
    description=(
        "Open a browser window to sign in to Uber Eats. Returns straight away - tell "
        "Arup to sign in in the window, then check status. Do not ask for a password."
    ),
)
async def sign_in(timeout_seconds: int = signin.DEFAULT_TIMEOUT_SECONDS) -> str:
    try:
        signin.start(timeout_seconds)
    except signin.SignInUnavailable as exc:
        raise ToolError(str(exc)) from exc
    minutes = max(1, timeout_seconds // 60)
    return (
        "A browser window is opening at the Uber Eats sign-in page. Sign in there "
        f"yourself - I never see your password. It waits about {minutes} minute(s), then "
        "closes and keeps the session. Say when you're done and I'll check."
    )


@mcp.tool(meta=WRITE, description="Sign out of Uber Eats and forget the session.")
async def logout() -> str:
    _previews.clear()
    return await _call("ubereats_logout")


@mcp.tool(meta=WRITE, description="Set the delivery address.")
async def set_address(address: str) -> str:
    return await _call("ubereats_set_address", address=address)


@mcp.tool(
    meta=READ, annotations=READ_ONLY, description="Search restaurants near the delivery address."
)
async def search(query: str, cuisine: str | None = None) -> str:
    return await _call("ubereats_search", query=query, cuisine=cuisine)


@mcp.tool(meta=READ, annotations=READ_ONLY, description="Get a restaurant's menu.")
async def get_restaurant(restaurant_id: str) -> str:
    return await _call("ubereats_get_restaurant", restaurantId=restaurant_id)


@mcp.tool(meta=WRITE, description="Add an item to the cart.")
async def add_to_cart(
    restaurant_id: str,
    item_name: str,
    quantity: int = 1,
    special_instructions: str | None = None,
) -> str:
    return await _call(
        "ubereats_add_to_cart",
        restaurantId=restaurant_id,
        itemName=item_name,
        quantity=quantity,
        specialInstructions=special_instructions,
    )


@mcp.tool(meta=READ, annotations=READ_ONLY, description="What is in the cart.")
async def view_cart() -> str:
    return await _call("ubereats_view_cart")


@mcp.tool(meta=WRITE, description="Empty the cart.")
async def clear_cart() -> str:
    _previews.clear()  # the basket changed, so no previous total means anything
    return await _call("ubereats_clear_cart")


@mcp.tool(meta=READ, annotations=READ_ONLY, description="Track the most recent order.")
async def track_order(order_id: str | None = None) -> str:
    return await _call("ubereats_track_order", orderId=order_id)


# --- ordering: not straight through -----------------------------------------------------


@mcp.tool(
    meta=READ,
    annotations=READ_ONLY,
    description=(
        "Price up the current cart without ordering. Returns the total and a preview id. "
        "Read the total out before asking to place it."
    ),
)
async def preview_order() -> str:
    summary = await _call("ubereats_checkout", confirm=False)
    try:
        total = parse_total(summary)
    except UnreadableTotal as exc:
        raise ToolError(f"{exc}\n\n{summary}") from exc

    preview = Preview(
        id=secrets.token_hex(4), total=total, summary=summary, created_at=time.monotonic()
    )
    _previews[preview.id] = preview
    return f"{summary}\n\nTotal ${total}. Preview id {preview.id}."


@mcp.tool(
    meta=CONFIRM,
    description=(
        "Place the order that preview_order priced up. Needs that preview's id. Refuses "
        "if the total has moved since. This spends money."
    ),
)
async def place_order(preview_id: str, tolerance: float = DEFAULT_TOLERANCE) -> str:
    preview = _previews.get(preview_id)
    if preview is None:
        raise ToolError(
            "No such preview. Run preview_order first and place the order with the id "
            "it returns - an order is never placed against a price nobody has seen."
        )
    if preview.expired():
        del _previews[preview_id]
        raise ToolError(
            f"That preview is more than {PREVIEW_TTL_SECONDS // 60} minutes old. "
            "Run preview_order again."
        )

    # Re-price immediately before ordering: this is the whole point of the tool.
    current_summary = await _call("ubereats_checkout", confirm=False)
    try:
        current_total = parse_total(current_summary)
    except UnreadableTotal as exc:
        raise ToolError(f"{exc} The order was not placed.") from exc

    drift = Drift(approved=preview.total, current=current_total)
    if not drift.within(tolerance):
        del _previews[preview_id]
        raise ToolError(
            f"I did not place the order: {drift.describe()}. "
            "Run preview_order again and confirm the new total."
        )

    placed = await _call("ubereats_checkout", confirm=True)
    del _previews[preview_id]
    moved = "" if drift.difference == 0 else f" (the total moved to ${current_total})"
    return f"Ordered — ${current_total}{moved}.\n\n{placed}"


def previews() -> dict[str, Preview]:
    """For tests."""
    return _previews
