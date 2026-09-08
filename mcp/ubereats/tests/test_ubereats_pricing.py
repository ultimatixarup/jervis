"""Reading a total, and deciding whether it has moved."""

from __future__ import annotations

from decimal import Decimal

import pytest

from jervis_mcp_ubereats.pricing import (
    DEFAULT_TOLERANCE,
    Drift,
    UnreadableTotal,
    parse_total,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Subtotal: $18.00\nFees: $4.30\nTotal: $22.30", "22.30"),
        ("Order total  42.30 USD", "42.30"),
        ("TOTAL — $9", "9"),
        ("Total: $1,204.50", "1204.50"),
        ("total $22.30", "22.30"),
    ],
)
def test_a_labelled_total_wins(text: str, expected: str) -> None:
    assert parse_total(text) == Decimal(expected)


def test_without_a_label_the_largest_amount_is_taken() -> None:
    """A total is never smaller than the items in it, so erring high means refusing a
    good order rather than placing a wrong one."""
    assert parse_total("Burrito $12.00\nDrink $3.50\nDelivery $2.99\n$18.49") == Decimal("18.49")


@pytest.mark.parametrize("text", ["", "no prices here", "Total: unavailable", None])
def test_an_unreadable_total_refuses(text: str) -> None:
    """The alternative is approving one figure and being charged another."""
    with pytest.raises(UnreadableTotal):
        parse_total(text)  # type: ignore[arg-type]


# --- drift ------------------------------------------------------------------------------


def test_an_unchanged_total_is_within_tolerance() -> None:
    drift = Drift(Decimal("22.30"), Decimal("22.30"))
    assert drift.within()
    assert drift.difference == 0
    assert drift.fraction == 0.0


def test_a_small_rise_is_allowed() -> None:
    assert Drift(Decimal("20.00"), Decimal("21.00")).within()  # 5%


def test_a_big_rise_is_not() -> None:
    drift = Drift(Decimal("20.00"), Decimal("30.00"))  # 50%
    assert not drift.within()
    assert "more" in drift.describe()
    assert "50%" in drift.describe()


def test_a_big_drop_is_also_refused() -> None:
    """A total that collapsed means the basket changed, not that you got lucky."""
    drift = Drift(Decimal("40.00"), Decimal("4.00"))
    assert not drift.within()
    assert "less" in drift.describe()


def test_the_boundary_is_inclusive() -> None:
    assert Drift(Decimal("100"), Decimal("110")).within(DEFAULT_TOLERANCE)
    assert not Drift(Decimal("100"), Decimal("110.01")).within(DEFAULT_TOLERANCE)


def test_a_zero_approved_total_is_not_a_free_pass() -> None:
    """Guards the division: 0 -> anything must not read as 0% drift."""
    assert not Drift(Decimal("0"), Decimal("25.00")).within()
    assert Drift(Decimal("0"), Decimal("0")).within()
