"""Shared money handling — the single rounding point invariant-guardian.md rule 2 requires.

Every calculation in this project reads and writes `Decimal`, never `float`/`double` (rule 1).
This module is the one place that rounds; nothing else in the codebase should call `round()` or
`quantize()` on a monetary value directly.
"""

from decimal import ROUND_HALF_UP, Decimal

TWO_PLACES = Decimal("0.01")

NOT_APPLICABLE = "n/a"
"""Sentinel returned by a ratio calculation (margin, markup) with a zero denominator — never a
numeric 0, null, or NaN. See agents/invariant-guardian.md rule 3, requirements F-10 and AC-05."""


def round_money(value: Decimal) -> Decimal:
    """Round a monetary amount to 2 decimal places, half-up. The only rounding rule in this
    project — every other module quantizes money by calling this, not `Decimal.quantize()`
    directly, so the rule stays in one place."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def ratio_percent(numerator: Decimal, denominator: Decimal) -> Decimal | str:
    """`numerator / denominator * 100`, rounded to 2 places — or NOT_APPLICABLE when the
    denominator is zero. Used for margin (profit/revenue) and markup (profit/cost)."""
    if denominator == 0:
        return NOT_APPLICABLE
    return round_money((numerator / denominator) * 100)
