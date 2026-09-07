"""Permission tiers. See PLAN.md §0.

The tier travels on each tool's MCP ``meta`` under ``x-jervis-tier``.

PLAN.md §4 Phase 1 says to put it in ``annotations``, but ``ToolAnnotations`` in the
MCP SDK is a closed pydantic model: an unknown key is silently *dropped*, so every
tool would arrive at the brain untiered with no error anywhere. ``meta`` is the
spec's extension point and round-trips intact.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

TIER_KEY = "x-jervis-tier"
ESCALATION_KEY = "x-jervis-escalates-on"


class Tier(StrEnum):
    """Ordered least to most restrictive."""

    READ = "read"
    WRITE = "write"
    CONFIRM = "confirm"


class Escalation(StrEnum):
    """Why a tool's effective tier can be higher than its declared one.

    A static tier cannot express "write, unless the arguments are dangerous". These
    values tell the brain's permission guard (PLAN.md §4 Phase 2) which argument
    inspection to run before executing the call.
    """

    DESTRUCTIVE_COMMAND = "destructive-command"
    OVERWRITES_EXISTING_FILE = "overwrites-existing-file"


def tool_meta(tier: Tier, escalates_on: Escalation | None = None) -> dict[str, Any]:
    """Build the ``meta`` payload for a tool declaration."""
    meta: dict[str, Any] = {TIER_KEY: tier.value}
    if escalates_on is not None:
        meta[ESCALATION_KEY] = escalates_on.value
    return meta
