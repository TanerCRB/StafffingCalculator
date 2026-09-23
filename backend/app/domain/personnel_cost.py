"""Base personnel cost of a scenario from worked time (F-07, SC-5-01; ADR-0013).

    base cost = Σ (`planned_allocation_hours` × the cost rate resolved for that position and month)

A pure function of what the data layer read — no `Session`, no clock, no catalogue lookup. Whether a
month *has* a cost rate, and which one, was decided in SQL by `app.data.personnel_cost`
(`month_has_cost_rate`, ADR-0013 point 1), identically for the live catalogue and for the approval
snapshot; this module only multiplies, adds and names what could not be costed.

**Independent of every revenue calculation** (F-06; rule 10 of the Invariant Guardian; ADR-0013
point 1). This module imports nothing of `app.domain.revenue` or of any commercial model, and none
of them imports it. The vocabulary below — states, windows, "unresolved month" — is therefore its
own, although it rhymes with the revenue's: sharing the revenue's types would make the cost path
depend on a module whose changes are decided for another calculation.

**Two shapes, never a third** (ADR-0013, point 2): `PersonnelCostResult` (an amount, a currency and
what it depends on) or `PersonnelCostUnavailable` (a named reason and what caused it). There is no
amount on the second, so "no cost rate" can never be read as a cost of `0`.

**Base, not fully loaded** (ADR-0013, point 5): `default_cost_rate` is the base rate, before
overheads, bonuses and benefits. Nothing here adds any of them, and nothing in either shape is
called or could be read as a loaded cost, an overhead, a profit or a margin. `COST_BASIS_BASE` is
the explicit label.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from app.core.money import round_money

# --- the named states (ADR-0013, point 2) --------------------------------------------------------

NO_COST_RATE: Final = "no_cost_rate"
"""At least one (position, month) is not resolved by **one cost rate over the whole month** — a gap
in the catalogue, a change of the cost rate or of its currency inside the month, or only a
subcontractor's window (ADR-0013, point 1). The whole cost is withheld, never the sum of the months
that did resolve (point 2: a partial cost is a silently understated one)."""

CURRENCY_MISMATCH: Final = "currency_mismatch"
"""The resolved cost rates carry more than one currency, or one other than the scenario's. No
conversion: `exchange_rates` (ADR-0006) does not exist and `1:1` would be invented."""

NO_COST_CURRENCY: Final = "no_cost_currency"
"""A plan with **no allocation rows at all** in a scenario with **no currency** — the mirror of the
revenue's `no_revenue_currency` (SC-4-01, R-04).

With no month there is no rate to take a currency from and the scenario declares none, so a cost of
`0.00` would be `0.00` of nothing, which a result may not be (it must name its currency). When the
scenario *does* declare a currency, the same empty plan is the true sum `0.00` in it (`calculated`).
An implementer's decision of SC-5-01, named in its report: ADR-0013 point 2 lists `no_cost_rate`
and `currency_mismatch` and does not reach the empty plan."""

CALCULATED: Final = "calculated"
"""Not a named state — the label the API gives a `PersonnelCostResult`, so a client reads one
`state` field whichever of the two shapes it got."""

# --- what the figure is and where it came from ---------------------------------------------------

COST_BASIS_BASE: Final = "base"
"""The cost is the **base** personnel cost: `default_cost_rate` before overheads (ADR-0013, point
5). The fully loaded cost is SC-5-02's, and it will be a different label, not this one widened."""

LIVE_CATALOG: Final = "live_catalog"
APPROVED_SNAPSHOT: Final = "approved_snapshot"

HOURS_SOURCE_PLANNED: Final = "planned_allocation_hours"
"""The one source of hours of the worked-time basis (ADR-0013, point 4): the plan, non-billable
effort included — never `billable_hours` (the revenue's basis) and never the availability."""

VENDOR_AXIS_INTERNAL: Final = "internal"
"""The vendor axis of every cost rate read: `vendor_id IS NULL`, the organisation's own rate — a
subcontractor's window never stands in for a missing internal one (ADR-0013, point 1)."""


@dataclass(frozen=True)
class CostRateWindow:
    """One catalogue window a cost used — its id, its dates, its cost rate and currency.

    The cost rate only: nothing on this type carries `default_selling_rate`, so the cost path
    cannot read the revenue's column by accident (ADR-0013, point 1).
    """

    source_rate_id: uuid.UUID
    effective_from: date
    effective_to: date | None
    cost_rate: Decimal
    currency: str


@dataclass(frozen=True)
class MonthCostRate:
    """The cost rate one (position, month) is costed at, and every window it came from.

    More than one window when catalogue boundaries fall inside the month but none of them changes
    the cost rate or its currency — e.g. a mid-month change of the *selling* rate alone. The windows
    then share `cost_rate` and `currency` by construction: that equality is part of the SQL
    predicate that produced this value, not something checked here.
    """

    cost_rate: Decimal
    currency: str
    windows: tuple[CostRateWindow, ...]


@dataclass(frozen=True)
class WorkedMonth:
    """One allocation row as the cost formula sees it: whose, which month, how many planned hours,
    and the cost rate of the month — or `None` when no single cost rate covers the whole month.

    `planned_allocation_hours` is the planner's figure for the **whole position**, headcount
    included, so it is never multiplied by the headcount again (ADR-0013, point 3). Neither the
    billable hours, nor the availability, nor the headcount travel on this type: a formula that
    cannot see them cannot use them (criterion K-01).
    """

    position_id: uuid.UUID
    period_month: date
    planned_allocation_hours: Decimal
    rate: MonthCostRate | None


@dataclass(frozen=True)
class UnresolvedCostMonth:
    """A (position, month) the named state is about — what `assumptions_used` points at."""

    position_id: uuid.UUID
    period_month: date


@dataclass(frozen=True)
class CostAssumptionsUsed:
    """What a cost figure — or its absence — depends on (F-06.5 applied to the cost).

    Present on both shapes of the answer. It names the cost rates used, so it **is** a personnel
    cost itself and is gated with the amount (ADR-0005, aneks 2026-09-23 SC-5-01, point 2).
    """

    rate_source: str
    hours_source: str = HOURS_SOURCE_PLANNED
    vendor_axis: str = VENDOR_AXIS_INTERNAL
    rate_windows: tuple[CostRateWindow, ...] = ()
    unresolved_months: tuple[UnresolvedCostMonth, ...] = ()
    currencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class PersonnelCostResult:
    """A stated base cost: rounded once, at the end, through `app.core.money.round_money`."""

    cost: Decimal
    currency: str
    assumptions_used: CostAssumptionsUsed
    basis: str = COST_BASIS_BASE


@dataclass(frozen=True)
class PersonnelCostUnavailable:
    """A named state: no cost can be stated, and `reason` says why. There is no amount on it."""

    reason: str
    assumptions_used: CostAssumptionsUsed
    basis: str = COST_BASIS_BASE


PersonnelCostAnswer = PersonnelCostResult | PersonnelCostUnavailable


def base_personnel_cost(
    months: Sequence[WorkedMonth],
    *,
    rate_source: str,
    scenario_currency: str | None,
) -> PersonnelCostAnswer:
    """The base personnel cost of one scenario, or the named state that withholds it.

    The order of the checks is the order of what a reader can act on:

    1. **Any month without one cost rate over the whole month → `no_cost_rate`**, for the whole
       cost, naming every (position, month) that caused it. Never the sum of the months that did
       resolve and never a `0` product for the missing month (ADR-0013, point 2). A month with zero
       planned hours still needs its rate: "is this month costed" is a question about the
       catalogue, not about the hours (point 4, the mirror of the revenue's R-04).
    2. **More than one currency among the costed months, or one other than the scenario's →
       `currency_mismatch`**. Nothing is converted.
    3. **No allocation row at all** — the sum is `0.00` in the scenario's currency when it declares
       one, otherwise the named state `no_cost_currency`.
    4. Otherwise the sum, in `Decimal` with no intermediate rounding, rounded **once** through
       `app.core.money.round_money` (ADR-0002; ADR-0013, point 3) — never per month, never per
       position.
    """
    windows = _distinct_windows(months)
    currencies = tuple(sorted({month.rate.currency for month in months if month.rate}))
    unresolved = tuple(
        UnresolvedCostMonth(position_id=month.position_id, period_month=month.period_month)
        for month in months
        if month.rate is None
    )
    assumptions = CostAssumptionsUsed(
        rate_source=rate_source,
        rate_windows=windows,
        unresolved_months=unresolved,
        currencies=currencies,
    )
    if unresolved:
        return PersonnelCostUnavailable(reason=NO_COST_RATE, assumptions_used=assumptions)
    if len(currencies) > 1 or (
        scenario_currency is not None and currencies and currencies != (scenario_currency,)
    ):
        return PersonnelCostUnavailable(reason=CURRENCY_MISMATCH, assumptions_used=assumptions)
    currency = currencies[0] if currencies else scenario_currency
    if currency is None:
        return PersonnelCostUnavailable(reason=NO_COST_CURRENCY, assumptions_used=assumptions)

    total = sum(
        (
            month.planned_allocation_hours * month.rate.cost_rate
            for month in months
            if month.rate is not None  # always true here; narrows the type
        ),
        Decimal("0"),
    )
    return PersonnelCostResult(
        cost=round_money(total), currency=currency, assumptions_used=assumptions
    )


def _distinct_windows(months: Sequence[WorkedMonth]) -> tuple[CostRateWindow, ...]:
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
