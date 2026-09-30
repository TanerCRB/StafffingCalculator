"""The sum of a scenario's additional costs, spread over months (F-08, SC-5-05; ADR-0014).

    additional cost = Σ over every cost, Σ over every month of that cost, the cost's amount

A pure function of the rows the data layer read — no `Session`, no clock, no catalogue lookup.

**The spread** (ADR-0014, points 3 and 8; AC-03):

- a **one-off** cost belongs to exactly one period row, its own month — whether it is attached to
  the scenario or to a position, however many allocation months that position has;
- a **recurring** cost belongs to every month of its closed range `[start_month, end_month]`,
  both ends included, with its **full** amount in each (Q-3 = A: never divided by the number of
  months), and to no month outside it.

What the spread never reads: the project's delivery period and the position's allocation months. A
cost month outside either still counts (point 3, the mirror of ADR-0013 point 4) — nothing here
truncates, intersects or refuses it, and no such input travels on `CostLine`.

**Money** (ADR-0002; ADR-0014, point 6): `Decimal` from the column to the result, the sum exact,
and **one** rounding at the very end through `app.core.money.round_money` — never per cost, never
per month.

**Two shapes, never a third** (point 7): `AdditionalCostResult` (an amount, a currency, what it
depends on) or `AdditionalCostUnavailable` (a named reason and what caused it). There is no amount
on the second, so `currency_mismatch` can never be read as a cost of `0` or as a partial sum.

**Independent of the revenue path and of the personnel cost** (point 9, Q-5 = A; rule 10 of the
Invariant Guardian). This module imports nothing of `app.domain.revenue*` or
`app.domain.personnel_cost`, and none of them imports it (control D-8, a structural test). Its
vocabulary — states, "calculated" — therefore rhymes with theirs without sharing their types.
`funding_source` is carried and reported, and it decides nothing here: a `rebilled_to_client` cost
enters the sum exactly like an `internal` one (point 9, analyst G-3).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from app.core.money import round_money
from app.models.additional_cost import COST_TYPE_ONE_OFF, COST_TYPE_RECURRING

# --- the named states (ADR-0014, point 7) --------------------------------------------------------

CURRENCY_MISMATCH: Final = "currency_mismatch"
"""The costs carry more than one currency, or one other than the scenario's declared currency. No
conversion: `exchange_rates` (ADR-0006) does not exist and `1:1` would be invented."""

NO_COST_CURRENCY: Final = "no_cost_currency"
"""No cost at all in a scenario with no currency: a `0.00` of nothing, which a result may not be
(it must name its currency). With a declared currency the same empty scenario is `calculated`,
`0.00`."""

CALCULATED: Final = "calculated"
"""Not a named state — the label the API gives an `AdditionalCostResult`, so a client reads one
`state` field whichever of the two shapes it got."""


@dataclass(frozen=True)
class CostLine:
    """One additional cost as the formula sees it — the row, and the name of its category.

    Deliberately absent: the project's delivery period and the position's allocation months. A
    formula that cannot see them cannot truncate a cost to them (criterion K-02).
    """

    cost_id: uuid.UUID
    position_id: uuid.UUID | None
    category_id: uuid.UUID
    category_name: str
    funding_source: str
    cost_type: str
    amount: Decimal
    currency: str
    start_month: date
    end_month: date | None


@dataclass(frozen=True)
class SpreadCost:
    """One cost with the months it belongs to — the breakdown `assumptions_used` reports."""

    line: CostLine
    months: tuple[date, ...]


@dataclass(frozen=True)
class PeriodRow:
    """One month, and every cost that belongs to it — once per cost (AC-03, rule 16)."""

    period_month: date
    cost_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class AdditionalCostAssumptions:
    """What the sum — or its absence — depends on: every cost with its months, category and
    funding, the period rows, and the currencies seen. Present on both shapes of the answer."""

    costs: tuple[SpreadCost, ...] = ()
    periods: tuple[PeriodRow, ...] = ()
    currencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdditionalCostResult:
    """A stated sum: rounded once, at the end, through `app.core.money.round_money`."""

    amount: Decimal
    currency: str
    assumptions_used: AdditionalCostAssumptions
    period_amounts: tuple[tuple[date, Decimal, str], ...] = ()


@dataclass(frozen=True)
class AdditionalCostUnavailable:
    """A named state: no sum can be stated, and `reason` says why. There is no amount on it."""

    reason: str
    assumptions_used: AdditionalCostAssumptions


AdditionalCostAnswer = AdditionalCostResult | AdditionalCostUnavailable


def months_of(line: CostLine) -> tuple[date, ...]:
    """The months one cost belongs to: its own for a one-off, every month of the closed range for a
    recurring one — both ends included.

    A type or shape the database refuses (`ck_additional_cost_*`) is refused here too, loudly: a
    recurring cost with no end has no finite sum, and guessing one would be the silent answer
    ADR-0008's addendum SC-5-05 (point 2) forbids.
    """
    if line.cost_type == COST_TYPE_ONE_OFF:
        return (line.start_month,)
    if line.cost_type == COST_TYPE_RECURRING:
        if line.end_month is None:
            raise ValueError(f"The recurring additional cost {line.cost_id} has no end month.")
        months: list[date] = []
        month = line.start_month
        while month <= line.end_month:
            months.append(month)
            month = _next_month(month)
        return tuple(months)
    raise ValueError(f"Unknown additional cost type {line.cost_type!r} on {line.cost_id}.")


def _next_month(month: date) -> date:
    """The first day of the following month (the input is a first day already, by CHECK)."""
    if month.month == 12:
        return date(month.year + 1, 1, 1)
    return date(month.year, month.month + 1, 1)


def additional_cost_total(
    lines: Sequence[CostLine],
    *,
    scenario_currency: str | None,
    allow_currency_mismatch: bool = False,
) -> AdditionalCostAnswer:
    """The sum of a scenario's additional costs, or the named state that withholds it.

    1. **More than one currency among the costs, or one other than the scenario's declared currency
       → `currency_mismatch`** (point 7). Nothing is converted and nothing is summed.
    2. **No cost at all** — `0.00` in the scenario's currency when it declares one, otherwise the
       named state `no_cost_currency`.
    3. Otherwise the sum of every (cost, month) entry, in `Decimal` with no intermediate rounding,
       rounded **once** through `app.core.money.round_money`.

    The sum is taken over the period rows the answer reports, entry by entry, so the months a
    reader sees and the months that were added up are one list, not two computations that could
    disagree.
    """
    spread = tuple(SpreadCost(line=line, months=months_of(line)) for line in lines)
    by_month: dict[date, list[uuid.UUID]] = {}
    for cost in spread:
        for month in cost.months:
            by_month.setdefault(month, []).append(cost.line.cost_id)
    periods = tuple(
        PeriodRow(period_month=month, cost_ids=tuple(by_month[month])) for month in sorted(by_month)
    )
    currencies = tuple(sorted({line.currency for line in lines}))
    assumptions = AdditionalCostAssumptions(costs=spread, periods=periods, currencies=currencies)

    if not allow_currency_mismatch and (
        len(currencies) > 1
        or (scenario_currency is not None and currencies and currencies != (scenario_currency,))
    ):
        return AdditionalCostUnavailable(reason=CURRENCY_MISMATCH, assumptions_used=assumptions)
    currency = scenario_currency or (currencies[0] if currencies else None)
    if currency is None:
        return AdditionalCostUnavailable(reason=NO_COST_CURRENCY, assumptions_used=assumptions)

    amount_of = {cost.line.cost_id: cost.line.amount for cost in spread}
    currency_of = {cost.line.cost_id: cost.line.currency for cost in spread}
    total = sum(
        (amount_of[cost_id] for period in periods for cost_id in period.cost_ids),
        Decimal("0"),
    )
    return AdditionalCostResult(
        amount=round_money(total),
        currency=currency,
        assumptions_used=assumptions,
        period_amounts=tuple(
            (period.period_month, amount_of[cost_id], currency_of[cost_id])
            for period in periods
            for cost_id in period.cost_ids
        ),
    )
