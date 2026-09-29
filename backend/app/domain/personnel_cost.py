"""Base personnel cost of a scenario from worked time (F-07, SC-5-01; ADR-0013).

    base cost = Σ (`planned_allocation_hours` priced at the cost rate resolved for that position and
                   month, by the rate's unit — `hour`, `day` or `month`; see `priced_amount`)

A pure function of what the data layer read — no `Session`, no clock, no catalogue lookup. Whether a
month *has* a cost rate, and which one, was decided in SQL by `app.data.personnel_cost`
(`month_has_cost_rate`, ADR-0013 point 1), identically for the live catalogue and for the approval
snapshot; this module only multiplies, adds and names what could not be costed.

**Independent of every revenue calculation** (F-06; rule 10 of the Invariant Guardian; ADR-0013
point 1). This module imports nothing of `app.domain.revenue` or of any commercial model, and none
of them imports it. The vocabulary below — states, windows, "unresolved month" — is therefore its
own, although it rhymes with the revenue's: sharing the revenue's types would make the cost path
depend on a module whose changes are decided for another calculation.

**Since SC-5-08 (F-07), the cost rate carries a unit** (`cost_rate_unit`, ADR-0013 addendum
2026-09-29): with `H` the planned hours, `S` the position's calendar `standard_hours_per_day` and
`D` its `working_days_in_month`, `hour` = `H × rate`, `day` = `H / S × rate`, `month` =
`rate × H / (D × S)` — unrounded, summed, rounded **once** at the end. A `day`/`month` position
needs its location's calendar (`WorkedMonth.basis`); its absence is the named state `no_calendar`
for the whole cost, and a `month` position in a month with zero working days is `no_working_days`. A
scenario of `hour` positions only never reads a calendar. The precedence is `no_cost_rate`, then
`currency_mismatch`, then the calendar states.

**Two shapes, never a third** (ADR-0013, point 2): `PersonnelCostResult` (an amount, a currency and
what it depends on) or `PersonnelCostUnavailable` (a named reason and what caused it). There is no
amount on the second, so "no cost rate" can never be read as a cost of `0`.

**Base, not fully loaded** (ADR-0013, point 5): `default_cost_rate` is the base rate, before
overheads, bonuses and benefits. Nothing here adds any of them, and nothing in either shape is
called or could be read as a loaded cost, an overhead, a profit or a margin. `COST_BASIS_BASE` is
the explicit label.

**Since SC-5-02 (F-07), a second, named field beside it: the fully loaded cost**
(`fully_loaded_personnel_cost`, `COST_BASIS_FULLY_LOADED`) — base cost plus a per-tuple surcharge
percentage (`catalog_default_rates.surcharge_percent`), or `0` for a tuple whose row already
carries it (`includes_surcharge`, criterion K-02). The two figures share one resolution
(`_resolve_months`): a month uncosted for the base cost is uncosted for the fully loaded cost too,
in the same currency, for the same reason — never a second, independent judgement of the same
months.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from app.core.money import round_money
from app.domain.capacity import NO_CALENDAR, CalendarBasis, working_days_in_month

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

NO_WORKING_DAYS: Final = "no_working_days"
"""A **month-unit** cost rate in a month whose calendar has zero working days (`D = 0`): the
`month` formula would divide by zero (ADR-0013, addendum 2026-09-29 SC-5-08, Q-D). A `day`-unit
position never reaches it — it needs only `standard_hours_per_day`. Like every named state it
withholds the whole cost and is never `0`. (`no_calendar` is `app.domain.capacity.NO_CALENDAR`, the
one constant the capacity and the paid-absence component already share.)"""

COST_RATE_UNIT_HOUR: Final = "hour"
COST_RATE_UNIT_DAY: Final = "day"
COST_RATE_UNIT_MONTH: Final = "month"
"""The three units of `default_cost_rate`, spelled once for the calculation. The same strings the
catalogue's CHECK constraint admits (`app.models.catalog.COST_RATE_UNITS`); repeated here rather
than imported because a formula module does not reach into the ORM models."""

CALCULATED: Final = "calculated"
"""Not a named state — the label the API gives a `PersonnelCostResult`, so a client reads one
`state` field whichever of the two shapes it got."""

# --- what the figure is and where it came from ---------------------------------------------------

COST_BASIS_BASE: Final = "base"
"""The cost is the **base** personnel cost: `default_cost_rate` before overheads (ADR-0013, point
5). The fully loaded cost is SC-5-02's, and it will be a different label, not this one widened."""

LIVE_CATALOG: Final = "live_catalog"
APPROVED_SNAPSHOT: Final = "approved_snapshot"

WHAT_IF_HYPOTHETICAL: Final = "what_if_hypothetical"
"""The third value of `rate_source` (ADR-0015, SC-6-04, Issue #88): a cost computed on a rate
structure substituted by a hypothetical salary raise and never persisted. Closed at two values
(`LIVE_CATALOG`/`APPROVED_SNAPSHOT`) until this task; every place that compares `rate_source` by
equality has been audited so this third value is never mistaken for either real source — in
particular `app.data.scenario_results.ScenarioResultsRaceDetected`'s guard, which the what-if path
(`app.data.scenario_what_if`) never feeds a substituted view: it compares the two **real** sources
before applying any raise, exactly as `scenario_results_for_caller` does (ADR-0013, addendum
2026-09-24 "reuse boundary for a recalculation without persisting")."""

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

    **`surcharge_percent`/`includes_surcharge` since SC-5-02** (ADR-0013, addendum 2026-09-25,
    Q4/Q5): the two new columns of `catalog_default_rates`, on the same row and therefore in every
    window this type already carries — no second window type, no second predicate. The SQL
    predicate that produces `WorkedMonth.rate` (`app.data.personnel_cost.month_has_cost_rate`)
    requires these two to agree across the windows of one month exactly as it already required
    `cost_rate`/`currency` to (closing the gap Guardian/Reviewer named at gate 2 review,
    2026-09-25): a boundary that moves only the surcharge or its flag is exactly as disqualifying
    as one that moves the cost rate."""

    source_rate_id: uuid.UUID
    effective_from: date
    effective_to: date | None
    cost_rate: Decimal
    currency: str
    surcharge_percent: Decimal
    includes_surcharge: bool
    cost_rate_unit: str
    """`hour`/`day`/`month` (SC-5-08) — the unit `cost_rate` is stated in. Required, no default: a
    builder that forgot it would price every window hourly without a word."""


@dataclass(frozen=True)
class MonthCostRate:
    """The cost rate one (position, month) is costed at, and every window it came from.

    More than one window when catalogue boundaries fall inside the month but none of them changes
    the cost rate, its currency, its surcharge percentage or its "already includes it" flag — e.g. a
    mid-month change of the *selling* rate alone. The windows then share all four values by
    construction: that equality is part of the SQL predicate that produced this value, not something
    checked here (SC-5-02 extended the predicate to the surcharge pair — a window boundary that
    moves only one of them makes the whole month unresolved, and `MonthCostRate` is never built from
    it).
    """

    cost_rate: Decimal
    currency: str
    windows: tuple[CostRateWindow, ...]
    surcharge_percent: Decimal
    includes_surcharge: bool
    cost_rate_unit: str
    """The unit `cost_rate` is stated in (SC-5-08). One value across the month's windows, like the
    other four: the SQL predicate makes a unit change inside the month `no_cost_rate`. A what-if
    raise (`dataclasses.replace`) scales `cost_rate` and carries this unchanged (ADR-0015)."""


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
    basis: CalendarBasis | None
    """The calendar of the position's location, from the **same source as the rate** — live for a
    draft, the approval snapshot for an approved scenario (ADR-0013 addendum 2026-09-29, point 5).
    `None` when the location has no calendar — and also when the scenario needs no calendar at all
    (every rate hourly), where none is read."""


@dataclass(frozen=True)
class UnresolvedCostMonth:
    """A (position, month) the named state is about — what `assumptions_used` points at."""

    position_id: uuid.UUID
    period_month: date


@dataclass(frozen=True)
class CostAssumptionsUsed:
    """What a cost figure — or its absence — depends on (F-06.5 applied to the cost).

    Present on both shapes of the answer. It names the cost rates used, so it **is** a personnel
    cost itself and is gated with the amount (ADR-0005, addendum 2026-09-23 SC-5-01, point 2).
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


def priced_amount(
    hours: Decimal, rate: MonthCostRate, basis: CalendarBasis | None, period_month: date
) -> Decimal | str:
    """`hours` priced at `rate` by its unit — an **unrounded** `Decimal`, or the named state that
    says why it cannot be (ADR-0013, addendum 2026-09-29 SC-5-08, points 3, 4 and 7).

    The one place the three unit formulas live; the base cost, the fully loaded cost and the
    paid-absence component all call it, so no path can hard-code `hour`.

    - `hour`: `hours × rate`. The calendar is not read.
    - `day`: `hours / S × rate`, `S = basis.standard_hours_per_day`. No calendar → `no_calendar`,
      **whatever the hours** (Q-E: a zero-hour position without a calendar is not `0.00`).
    - `month`: `rate × hours / (D × S)`, `D = working_days_in_month(basis, period_month)`. No
      calendar → `no_calendar`; `D = 0` → `no_working_days`, never a division.

    The product is formed before the single division, so the amount carries one division's error at
    most and never a rounded per-day or per-month figure (no intermediate rounding). Hours above
    capacity are not capped: a month rate priced on 176 h of a 168 h month costs more than the rate.
    """
    unit = rate.cost_rate_unit
    if unit == COST_RATE_UNIT_HOUR:
        return hours * rate.cost_rate
    if basis is None:
        return NO_CALENDAR
    if unit == COST_RATE_UNIT_DAY:
        return hours * rate.cost_rate / basis.standard_hours_per_day
    if unit == COST_RATE_UNIT_MONTH:
        working_days = working_days_in_month(basis, period_month)
        if working_days == 0:
            return NO_WORKING_DAYS
        return rate.cost_rate * hours / (Decimal(working_days) * basis.standard_hours_per_day)
    # A unit the CHECK constraint does not admit: refuse loudly rather than price it as something.
    raise ValueError("Unknown cost rate unit; the cost cannot be stated.")


def _month_amounts(months: Sequence[WorkedMonth]) -> list[Decimal] | str:
    """Every month's unrounded amount, in the order of `months` — or the state that withholds the
    whole cost: `no_calendar` before `no_working_days` when both occur (both are about the calendar,
    and the first is the one a person fixes first). Only called once every month has a rate."""
    amounts: list[Decimal] = []
    states: set[str] = set()
    for month in months:
        if month.rate is None:  # never true where this is called; narrows the type
            continue
        amount = priced_amount(
            month.planned_allocation_hours, month.rate, month.basis, month.period_month
        )
        if isinstance(amount, str):
            states.add(amount)
        else:
            amounts.append(amount)
    if NO_CALENDAR in states:
        return NO_CALENDAR
    if NO_WORKING_DAYS in states:
        return NO_WORKING_DAYS
    return amounts


def _resolve_months(
    months: Sequence[WorkedMonth], *, rate_source: str, scenario_currency: str | None
) -> tuple[CostAssumptionsUsed, str | None, str | None, list[Decimal]]:
    """The checks every cost figure over `WorkedMonth` shares, extracted once (SC-5-02) so
    `base_personnel_cost` and `fully_loaded_personnel_cost` cannot silently disagree about which
    months are costed and in which currency.

    Returns `(assumptions, currency, reason, amounts)`, with exactly one of `currency`/`reason` not
    `None` — unless the plan is empty and the scenario names no currency either, in which case
    `reason` is `NO_COST_CURRENCY` and `currency` stays `None`. A caller checks `reason is not None`
    first, exactly as `base_personnel_cost` always has. `amounts` holds each month's unrounded
    amount by its unit (`priced_amount`), aligned with `months`, and is meaningful only when
    `reason is None`.

    The order of the named states is `no_cost_rate`, `currency_mismatch`, then `no_calendar` /
    `no_working_days` (ADR-0013, addendum 2026-09-29, Q-C).
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
        return assumptions, None, NO_COST_RATE, []
    if len(currencies) > 1 or (
        scenario_currency is not None and currencies and currencies != (scenario_currency,)
    ):
        return assumptions, None, CURRENCY_MISMATCH, []
    amounts = _month_amounts(months)
    if isinstance(amounts, str):
        return assumptions, None, amounts, []
    currency = currencies[0] if currencies else scenario_currency
    if currency is None:
        return assumptions, None, NO_COST_CURRENCY, []
    return assumptions, currency, None, amounts


def base_personnel_cost(
    months: Sequence[WorkedMonth],
    *,
    rate_source: str,
    scenario_currency: str | None,
) -> PersonnelCostAnswer:
    """The base personnel cost of one scenario, or the named state that withholds it.

    The order of the checks is the order of what a reader can act on (`_resolve_months`):

    1. **Any month without one cost rate over the whole month → `no_cost_rate`**, for the whole
       cost, naming every (position, month) that caused it. Never the sum of the months that did
       resolve and never a `0` product for the missing month (ADR-0013, point 2). A month with zero
       planned hours still needs its rate: "is this month costed" is a question about the
       catalogue, not about the hours (point 4, the mirror of the revenue's R-04).
    2. **More than one currency among the costed months, or one other than the scenario's →
       `currency_mismatch`**. Nothing is converted.
    3. **No allocation row at all** — the sum is `0.00` in the scenario's currency when it declares
       one, otherwise the named state `no_cost_currency`.
    3b. **A `day`/`month` position without a calendar → `no_calendar`; a `month` position in a
       month with zero working days → `no_working_days`** (SC-5-08), for the whole cost, after the
       two checks above and before the empty-plan rule.
    4. Otherwise the sum of `priced_amount` per month, in `Decimal` with no intermediate rounding,
       rounded **once** through
       `app.core.money.round_money` (ADR-0002; ADR-0013, point 3) — never per month, never per
       position.
    """
    assumptions, currency, reason, amounts = _resolve_months(
        months, rate_source=rate_source, scenario_currency=scenario_currency
    )
    if reason is not None:
        return PersonnelCostUnavailable(reason=reason, assumptions_used=assumptions)

    total = sum(amounts, Decimal("0"))
    return PersonnelCostResult(
        cost=round_money(total), currency=currency, assumptions_used=assumptions
    )


# --- the fully loaded personnel cost (SC-5-02, F-07; ADR-0013, addendum 2026-09-25) -------------

COST_BASIS_FULLY_LOADED: Final = "fully_loaded"
"""The fully loaded cost: `default_cost_rate` **plus** its surcharge (ADR-0013, addendum 2026-09-25
SC-5-02, Q4). A field of its own, never `COST_BASIS_BASE` widened — criterion K-01: the base cost
stays exactly the figure SC-5-01 proved, and the fully loaded cost is a second, named field beside
it, never a silent replacement."""


def surcharge_fraction(rate: MonthCostRate) -> Decimal:
    """The fraction of `rate.cost_rate` this month's surcharge adds — `0` when the row already
    carries it (criterion K-02).

    The one place `surcharge_percent` and `includes_surcharge` are read together: they share one
    row of `catalog_default_rates` (ADR-0013, addendum 2026-09-25 SC-5-02, Q5), so a formula that
    read one without the other could double the surcharge on a tuple whose base rate already
    includes it. A flag read here and a percent read elsewhere is exactly the shape the mutation
    "delete the `includes_surcharge` branch, always add the percent" would produce undetected.
    """
    if rate.includes_surcharge:
        return Decimal("0")
    return rate.surcharge_percent / Decimal("100")


@dataclass(frozen=True)
class FullyLoadedPersonnelCostResult:
    """A stated fully loaded cost: the base cost plus its surcharge, and the surcharge alone —
    each rounded once, at the end, through `app.core.money.round_money` (never a second rounding of
    an already-rounded figure)."""

    cost: Decimal
    """Base cost + surcharge, computed from one unrounded sum (ADR-0013, point 3) — never
    `base_personnel_cost(...).cost + surcharge_amount`, which would round twice."""
    surcharge_amount: Decimal
    """The surcharge alone — `0.00` for every month whose row already includes it (K-02)."""
    currency: str
    assumptions_used: CostAssumptionsUsed
    """The same rate windows `base_personnel_cost` names for the identical months — not a second,
    parallel list: the fully loaded cost is a second consumer of the same resolved rates, never a
    second resolution of them (ADR-0013, addendum 2026-09-24 "reuse boundary", applied one task
    over)."""
    basis: str = COST_BASIS_FULLY_LOADED


@dataclass(frozen=True)
class FullyLoadedPersonnelCostUnavailable:
    """A named state: no fully loaded cost can be stated, for the identical reason the base cost
    cannot (`_resolve_months` is shared) — never a reason of its own."""

    reason: str
    assumptions_used: CostAssumptionsUsed
    basis: str = COST_BASIS_FULLY_LOADED


FullyLoadedPersonnelCostAnswer = (
    FullyLoadedPersonnelCostResult | FullyLoadedPersonnelCostUnavailable
)


def fully_loaded_personnel_cost(
    months: Sequence[WorkedMonth],
    *,
    rate_source: str,
    scenario_currency: str | None,
) -> FullyLoadedPersonnelCostAnswer:
    """The fully loaded personnel cost of one scenario, or the named state that withholds it.

    Exactly `base_personnel_cost`'s three checks (`_resolve_months`, shared) — a month that cannot
    be costed cannot be fully loaded either, and the two functions must never disagree about which
    months those are or which currency the result is in.

    The sum itself is one unrounded pass over the same months, base and surcharge together
    (`total = Σ hours × rate × (1 + surcharge_fraction)`) — never `base_personnel_cost(...).cost +
    fully_loaded... .surcharge_amount`, which would compound two already-rounded figures and could
    disagree with the single-pass sum by a cent (ADR-0013, point 3: one rounding, at the end).
    """
    assumptions, currency, reason, amounts = _resolve_months(
        months, rate_source=rate_source, scenario_currency=scenario_currency
    )
    if reason is not None:
        return FullyLoadedPersonnelCostUnavailable(reason=reason, assumptions_used=assumptions)

    base_total = Decimal("0")
    surcharge_total = Decimal("0")
    # `amounts` is aligned with `months` (every month has a rate here); the surcharge percentage
    # applies to the base amount **after** unit conversion, still one final rounding (SC-5-08).
    for month, base_amount in zip(months, amounts, strict=True):
        if month.rate is None:  # always false here; narrows the type
            continue
        base_total += base_amount
        surcharge_total += base_amount * surcharge_fraction(month.rate)

    return FullyLoadedPersonnelCostResult(
        cost=round_money(base_total + surcharge_total),
        surcharge_amount=round_money(surcharge_total),
        currency=currency,
        assumptions_used=assumptions,
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
