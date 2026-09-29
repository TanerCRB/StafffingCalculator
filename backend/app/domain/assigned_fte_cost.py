"""The assigned-FTE personnel cost basis of a scenario
(F-07, SC-5-04; ADR-0013, addendum 2026-09-29 SC-5-04).

A **third, independent formula** beside `app.domain.personnel_cost`'s worked-time one and
`app.domain.fixed_amount_cost`'s stated amount — not a branch inside either. A position on this
basis is costed from the FTE stored on it, not from its planned hours (ADR-0013, point 4,
unchanged):

```
hours(month)  = fte x working_days(month) x standard_hours_per_day        (exact, never rounded)
amount(month) = hours(month) priced at the month's cost rate by its unit  (`priced_amount`)
cost          = round_money( sum over the months of amount(month) )       (one rounding, at the end)
```

so `hour` costs `fte x D x S x rate`, `day` costs `fte x D x rate` and `month` costs `fte x rate`
(point 3). `D` and `S` come from the position's location calendar, live for a draft and frozen for
an approved scenario — the caller decides which and hands in the resolved `CalendarBasis`
(`app.data.personnel_cost`); nothing here queries or reads a clock.

**What is priced.** The months in which the position has an allocation row — only those, and the
hours of those rows are not an input (points 4 and 6: an FTE position is never also priced by the
worked-time formula, and the dispatch that guarantees it lives in `app.data.personnel_cost`, at the
formula input). A position with a stored FTE and no allocation row at all is the named state
`no_planned_months`, never `0.00`.

**Named states, never `0`** (point 5). In this order: `no_cost_rate`, `currency_mismatch`,
`no_calendar`, `no_working_days`, `no_planned_months`, and `no_cost_currency` under the same
condition as the other components. **An FTE position needs a resolved calendar whatever the unit of
its rate**: the hours of the month come from the calendar, so even an hourly rate cannot be applied
without one — which deliberately differs from the worked-time basis, where the hours are a figure
the planner typed (ADR-0013 SC-5-08, points 4 and 7). A state withholds only this component, never
the worked-time cost, the paid-absence cost or the fixed-amount cost, and never leaves a partial
sum.

**Two shapes, never a third**: an `AssignedFteCostResult` (an amount, its currency, what it
depends on) or an `AssignedFteCostUnavailable` (a named reason and what caused it). No amount lives
on the second shape.

**No surcharge and no fully loaded figure** (point 7): this component is the stated basis, not a
base an overhead marks up. **No headcount factor** (point 2): the stored FTE is already a position
total.

**Independence** (F-06, rule 10 of the Invariant Guardian; point 12). This module imports
- nothing of the revenue path (`app.data.commercial_terms`, `app.domain.revenue*`);
- neither of the other two formulas: not `base_personnel_cost`/`fully_loaded_personnel_cost`, not
  `app.domain.fixed_amount_cost`, not any `app.data.*` module;
- from `app.domain.personnel_cost` only the **shared pricing primitives** the ADR names as the one
  place the three unit formulas live (`priced_amount`, SC-5-08 point 6, and the resolved-rate value
  types it prices with) — the same reuse `app.domain.paid_absence_cost` makes. A structural test
  (`tests/test_assigned_fte_cost.py`) pins the exact set of names, so the primitives cannot quietly
  grow into the formula.
Its states are spelled again as this module's own constants, as `fixed_amount_cost` does with its
own: the vocabularies mean the same idea and are independent. The dispatcher, and only the
dispatcher, may import several formulas (`app.data.personnel_cost`).

**Nothing here decides who may see the figure** (ADR-0005 addendum 2026-09-29 SC-5-04): that is
`app.api.response_shaping`'s conjunction, applied to whatever `app.data.personnel_cost` builds from
this module's answer.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from app.core.money import round_money
from app.domain.capacity import NO_CALENDAR, CalendarBasis
from app.domain.fte_hours import NO_WORKING_DAYS, exact_fte_hours
from app.domain.personnel_cost import CostRateWindow, MonthCostRate, priced_amount

NO_COST_RATE: Final = "no_cost_rate"
"""A month of an FTE position without one cost rate over the whole month (same predicate as the
worked-time basis: `app.data.personnel_cost.month_has_cost_rate`, ADR-0013 point 1)."""

CURRENCY_MISMATCH: Final = "currency_mismatch"
"""The costed months carry more than one currency, or one other than `scenarios.currency` when it
is declared. Nothing is converted (ADR-0006)."""

NO_COST_CURRENCY: Final = "no_cost_currency"
"""No currency to state the figure in: no FTE position at all and no `scenarios.currency`, or no
month resolved a currency (`0.00` would be `0.00` of nothing)."""

NO_PLANNED_MONTHS: Final = "no_planned_months"
"""A position with a stored FTE and no allocation row: there is no month to price it in. A named
state, never `0.00` (point 4)."""

CALCULATED: Final = "calculated"
"""Not a named state — the label the API gives an `AssignedFteCostResult`."""

COST_BASIS_ASSIGNED_FTE: Final = "assigned_fte"
"""The value of `staffing_position.cost_basis` this formula answers for — spelled here, not
imported from `app.models.staffing`: this module is handed already-read values, never a `Session`
or a model class."""

HOURS_SOURCE_ASSIGNED_FTE: Final = "assigned_fte_x_calendar_basis_hours"
"""Where the hours of this component come from: the stored FTE times the calendar's basis hours of
the month — never `planned_allocation_hours` (ADR-0013, points 3 and 4)."""

VENDOR_AXIS_INTERNAL: Final = "internal"
"""The vendor axis of every cost rate read, as for the worked-time basis (ADR-0013, point 1)."""


@dataclass(frozen=True)
class AssignedFteLine:
    """One `assigned_fte` position, as this formula sees it: whose, and its stored FTE (a fraction,
    a position total). No headcount, no hours, no dimension tuple."""

    position_id: uuid.UUID
    assigned_fte: Decimal


@dataclass(frozen=True)
class AssignedFteMonth:
    """One allocation row of an `assigned_fte` position: whose, which month, the month's cost rate
    (or `None` when no single cost rate covers the whole month) and the calendar of the position's
    location — from the **same source as the rate** (live for a draft, the approval snapshot for an
    approved scenario), or `None` when the location has no calendar.

    No `planned_allocation_hours` field at all: a formula that cannot see the planner's hours
    cannot use them (points 4 and 6). The FTE is not here either — it is the position's, in
    `AssignedFteLine`, not repeated per month."""

    position_id: uuid.UUID
    period_month: date
    rate: MonthCostRate | None
    basis: CalendarBasis | None


@dataclass(frozen=True)
class UnresolvedFteMonth:
    """A (position, month) the `no_cost_rate` state is about."""

    position_id: uuid.UUID
    period_month: date


@dataclass(frozen=True)
class AssignedFteAssumptionsUsed:
    """What an assigned-FTE figure — or its absence — depends on, on both shapes of the answer
    (F-06.5 applied to this basis). It names stored FTE values and cost rates, so it is a personnel
    cost itself and is gated with the amount (ADR-0005 addendum 2026-09-29 SC-5-04)."""

    rate_source: str
    hours_source: str = HOURS_SOURCE_ASSIGNED_FTE
    vendor_axis: str = VENDOR_AXIS_INTERNAL
    lines: tuple[AssignedFteLine, ...] = ()
    rate_windows: tuple[CostRateWindow, ...] = ()
    unresolved_months: tuple[UnresolvedFteMonth, ...] = ()
    currencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssignedFteCostResult:
    """A stated assigned-FTE cost: rounded once, through `app.core.money.round_money`."""

    cost: Decimal
    currency: str
    assumptions_used: AssignedFteAssumptionsUsed
    basis: str = COST_BASIS_ASSIGNED_FTE


@dataclass(frozen=True)
class AssignedFteCostUnavailable:
    """A named state: no assigned-FTE cost can be stated, and `reason` says why. No amount here."""

    reason: str
    assumptions_used: AssignedFteAssumptionsUsed
    basis: str = COST_BASIS_ASSIGNED_FTE


AssignedFteCostAnswer = AssignedFteCostResult | AssignedFteCostUnavailable


def _distinct_windows(months: Sequence[AssignedFteMonth]) -> tuple[CostRateWindow, ...]:
    """Every window used, once, in a stable order (start of the window, then its source id)."""
    distinct = {
        window.source_rate_id: window
        for month in months
        if month.rate is not None
        for window in month.rate.windows
    }
    return tuple(
        sorted(distinct.values(), key=lambda window: (window.effective_from, window.source_rate_id))
    )


def _month_amounts(
    months: Sequence[AssignedFteMonth], fte_by_position: dict[uuid.UUID, Decimal]
) -> list[Decimal] | str:
    """Every month's unrounded amount — or the calendar state that withholds the whole component:
    `no_calendar` before `no_working_days` when both occur (the missing calendar is the one a person
    fixes first, as in ADR-0013 SC-5-08 Q-D). Only called once every month has a rate."""
    amounts: list[Decimal] = []
    states: set[str] = set()
    for month in months:
        if month.rate is None:  # never true where this is called; narrows the type
            continue
        hours = exact_fte_hours(
            month.basis,
            period_month=month.period_month,
            fte_fraction=fte_by_position[month.position_id],
        )
        if isinstance(hours, str):
            states.add(hours)
            continue
        # The unit formulas live in `priced_amount` and nowhere else: `hour` multiplies, `day`
        # divides by the calendar's hours per day, `month` by the whole month's basis hours.
        amount = priced_amount(hours, month.rate, month.basis, month.period_month)
        if isinstance(amount, str):
            states.add(amount)
        else:
            amounts.append(amount)
    if NO_CALENDAR in states:
        return NO_CALENDAR
    if NO_WORKING_DAYS in states:
        return NO_WORKING_DAYS
    return amounts


def assigned_fte_cost(
    lines: Sequence[AssignedFteLine],
    months: Sequence[AssignedFteMonth],
    *,
    rate_source: str,
    scenario_currency: str | None,
) -> AssignedFteCostAnswer:
    """The sum of a scenario's `assigned_fte` positions, or the named state that withholds it.

    `lines` is every position on this basis, `months` every allocation row of those positions. In
    the order of what a reader can act on:

    1. **Any month without one cost rate over the whole month → `no_cost_rate`**, naming every
       (position, month) that caused it. Never the sum of the months that did resolve.
    2. **More than one currency among the months, or one other than the scenario's →
       `currency_mismatch`**. Nothing is converted.
    3. **A position without a calendar → `no_calendar`; a month with no working day →
       `no_working_days`**, whatever the unit of the rate, for the whole component.
    4. **A position with no month at all → `no_planned_months`.**
    5. **No currency to state the figure in → `no_cost_currency`.** With no FTE position at all the
       component is `0.00` in the scenario's currency when it declares one.
    6. Otherwise the sum of the months' exact amounts, rounded **once** through
       `app.core.money.round_money` (ADR-0002) — never per month, never per position.
    """
    windows = _distinct_windows(months)
    currencies = tuple(sorted({month.rate.currency for month in months if month.rate}))
    unresolved = tuple(
        UnresolvedFteMonth(position_id=month.position_id, period_month=month.period_month)
        for month in months
        if month.rate is None
    )
    assumptions = AssignedFteAssumptionsUsed(
        rate_source=rate_source,
        lines=tuple(lines),
        rate_windows=windows,
        unresolved_months=unresolved,
        currencies=currencies,
    )

    def unavailable(reason: str) -> AssignedFteCostUnavailable:
        return AssignedFteCostUnavailable(reason=reason, assumptions_used=assumptions)

    if unresolved:
        return unavailable(NO_COST_RATE)
    if len(currencies) > 1 or (
        scenario_currency is not None and currencies and currencies != (scenario_currency,)
    ):
        return unavailable(CURRENCY_MISMATCH)

    fte_by_position = {line.position_id: line.assigned_fte for line in lines}
    amounts = _month_amounts(months, fte_by_position)
    if isinstance(amounts, str):
        return unavailable(amounts)

    positions_with_months = {month.position_id for month in months}
    if any(line.position_id not in positions_with_months for line in lines):
        return unavailable(NO_PLANNED_MONTHS)

    currency = currencies[0] if currencies else scenario_currency
    if currency is None:
        return unavailable(NO_COST_CURRENCY)

    total = sum(amounts, Decimal("0"))
    return AssignedFteCostResult(
        cost=round_money(total), currency=currency, assumptions_used=assumptions
    )
