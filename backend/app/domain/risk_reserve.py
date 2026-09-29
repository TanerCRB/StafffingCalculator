"""Risk reserves and the representation state of a risk (F-09 pt 4-5, SC-6-08; ADR-0021).

Two pure functions, no `Session`, no clock, no catalogue lookup:

**The reserve total** (ADR-0021, point 4)

    reserve total = sum over every reserve, sum over every month of that reserve, its amount

- a **one-off** reserve belongs to exactly one month, its own; a **recurring** one to every month of
  its closed range `[start_month, end_month]`, both ends included, with its **full** amount in each
  (the ADR-0014 point 3 semantics, Q-4 = A) - never divided by the number of months;
- the sum is exact `Decimal` and is rounded **once**, at the end, through
  `app.core.money.round_money` - never per reserve, never per month;
- **two shapes, never a third** (ADR-0014, point 7, applied by ADR-0021, point 4): a
  `ReserveTotalResult` (an amount and a currency) or a `ReserveTotalUnavailable` (a named reason).
  There is no amount on the second, so `currency_mismatch` can never be read as a partial sum or
  as `0`;
- **no reserve at all** is `0.00` in the scenario's declared currency, or the named state
  `no_cost_currency` when the scenario declares none.

**The representation state of a risk** (ADR-0021, points 1 and 5): a function of two counts and of
nothing else - `none`, `cost_event`, `reserve`, or `both` (the double-representation signal). The
counts come from the declared link (`app.data.risk`), never from a category, a month or an amount.
Detection carries **no amount**, so it has no way to alter a total (control R-02).

**Independent of the revenue path and of the personnel cost** (rule 10 of the Invariant Guardian;
control R-08, a structural test at any import depth). This module imports nothing of
`app.domain.revenue*`, `app.domain.personnel_cost`, `app.domain.additional_cost` or the data layer,
and none of the revenue/personnel-cost modules imports it. Its state names rhyme with those of the
additional cost, and are spelled again here rather than imported: two vocabularies, so a change to
one cannot silently reach into the other's answer.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from app.core.money import round_money
from app.models.risk import RESERVE_TYPE_ONE_OFF, RESERVE_TYPE_RECURRING

CURRENCY_MISMATCH: Final = "currency_mismatch"
"""The reserves carry more than one currency, or one other than the scenario's declared currency.
No conversion (ADR-0006): nothing is converted and nothing is summed."""

NO_COST_CURRENCY: Final = "no_cost_currency"
"""No reserve at all in a scenario with no declared currency: a `0.00` of nothing, which a result
may not be (it must name its currency)."""

CALCULATED: Final = "calculated"
"""Not a named state - the label the API gives a `ReserveTotalResult`."""

REPRESENTATION_NONE: Final = "none"
REPRESENTATION_COST_EVENT: Final = "cost_event"
REPRESENTATION_RESERVE: Final = "reserve"
REPRESENTATION_BOTH: Final = "both"
"""`both` is the double-representation signal (F-09 pt 5): at least one cost event **and** at least
one reserve point at the same declared risk."""


def risk_representation(*, cost_event_count: int, reserve_count: int) -> str:
    """Which representations a risk has - from the two counts of its declared links only."""
    if cost_event_count < 0 or reserve_count < 0:
        raise ValueError("A representation count cannot be negative.")
    if cost_event_count > 0 and reserve_count > 0:
        return REPRESENTATION_BOTH
    if cost_event_count > 0:
        return REPRESENTATION_COST_EVENT
    if reserve_count > 0:
        return REPRESENTATION_RESERVE
    return REPRESENTATION_NONE


@dataclass(frozen=True)
class ReserveLine:
    """One reserve as the formula sees it."""

    reserve_id: uuid.UUID
    risk_id: uuid.UUID | None
    reserve_type: str
    amount: Decimal
    currency: str
    start_month: date
    end_month: date | None


@dataclass(frozen=True)
class ReserveTotalResult:
    """A stated sum: rounded once, at the end, through `app.core.money.round_money`."""

    amount: Decimal
    currency: str
    currencies: tuple[str, ...]


@dataclass(frozen=True)
class ReserveTotalUnavailable:
    """A named state: no sum can be stated, and `reason` says why. There is no amount on it."""

    reason: str
    currencies: tuple[str, ...]


ReserveTotalAnswer = ReserveTotalResult | ReserveTotalUnavailable


def reserve_months(line: ReserveLine) -> tuple[date, ...]:
    """The months one reserve belongs to: its own for a one-off, every month of the closed range
    for a recurring one. A type or shape the database refuses is refused here too, loudly."""
    if line.reserve_type == RESERVE_TYPE_ONE_OFF:
        return (line.start_month,)
    if line.reserve_type == RESERVE_TYPE_RECURRING:
        if line.end_month is None:
            raise ValueError(f"The recurring reserve {line.reserve_id} has no end month.")
        months: list[date] = []
        month = line.start_month
        while month <= line.end_month:
            months.append(month)
            month = (
                date(month.year + 1, 1, 1)
                if month.month == 12
                else date(month.year, month.month + 1, 1)
            )
        return tuple(months)
    raise ValueError(f"Unknown reserve type {line.reserve_type!r} on {line.reserve_id}.")


def reserve_total(
    lines: Sequence[ReserveLine], *, scenario_currency: str | None
) -> ReserveTotalAnswer:
    """The sum of a scenario's reserves, or the named state that withholds it.

    1. **More than one currency, or one other than the scenario's declared currency ->
       `currency_mismatch`.** Nothing is converted and nothing is summed - not even the reserves
       that do agree with the scenario's currency (no partial sum).
    2. **No reserve at all** - `0.00` in the scenario's currency when it declares one, otherwise
       `no_cost_currency`.
    3. Otherwise the exact sum of every (reserve, month) entry, rounded **once**.
    """
    currencies = tuple(sorted({line.currency for line in lines}))
    if len(currencies) > 1 or (
        scenario_currency is not None and currencies and currencies != (scenario_currency,)
    ):
        return ReserveTotalUnavailable(reason=CURRENCY_MISMATCH, currencies=currencies)
    currency = currencies[0] if currencies else scenario_currency
    if currency is None:
        return ReserveTotalUnavailable(reason=NO_COST_CURRENCY, currencies=currencies)
    total = sum((line.amount for line in lines for _month in reserve_months(line)), Decimal("0"))
    return ReserveTotalResult(amount=round_money(total), currency=currency, currencies=currencies)
