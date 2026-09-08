"""Reading a total out of an order preview, and deciding whether it has moved.

The upstream server returns the preview as prose, so the total has to be found in it.
Everything here refuses rather than guesses: an unparsed total means no order is
placed, because the alternative is approving one figure and being charged another.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# "Total: $42.30", "Order total  42.30 USD", "total — $42".
_TOTAL_LINE = re.compile(
    r"\b(?:order\s+)?total\b[^0-9$]{0,20}\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
    re.IGNORECASE,
)
_ANY_AMOUNT = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")

# PLAN.md §4 Phase 6: the charge must land within 10% of what was approved.
DEFAULT_TOLERANCE = 0.10


class UnreadableTotal(ValueError):
    """No total could be found. Refuse rather than place an order blind."""


@dataclass(frozen=True)
class Drift:
    approved: Decimal
    current: Decimal

    @property
    def difference(self) -> Decimal:
        return self.current - self.approved

    @property
    def fraction(self) -> float:
        if self.approved == 0:
            return 0.0 if self.current == 0 else 1.0
        return float(abs(self.difference) / self.approved)

    def within(self, tolerance: float = DEFAULT_TOLERANCE) -> bool:
        return self.fraction <= tolerance

    def describe(self) -> str:
        direction = "more" if self.difference > 0 else "less"
        return (
            f"the total is now ${self.current} rather than ${self.approved} - "
            f"${abs(self.difference)} {direction}, {self.fraction:.0%} off"
        )


def parse_total(text: str) -> Decimal:
    """Find the order total in a preview.

    Prefers a line that says "total". Falls back to the largest amount mentioned,
    because a total is not smaller than the items it is made of - and being wrong in
    that direction means refusing a valid order rather than placing a wrong one.
    """
    labelled = _TOTAL_LINE.findall(text or "")
    candidates = labelled or _ANY_AMOUNT.findall(text or "")
    if not candidates:
        raise UnreadableTotal("I couldn't find a total in the order preview, so I won't place it.")
    amounts = []
    for raw in candidates:
        try:
            amounts.append(Decimal(raw.replace(",", "")))
        except InvalidOperation:
            continue
    if not amounts:
        raise UnreadableTotal("The total in the preview wasn't a number I could read.")
    return max(amounts)
