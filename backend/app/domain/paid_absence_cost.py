"""The cost of paid absences — a named component beside the base personnel cost (F-07, F-05;
SC-5-06; ADR-0013, aneks 2026-09-23 SC-5-06).

    paid absence cost = Σ over (position, allocation month) of
                        (manual paid-absence hours + leave-budget top-up hours) × cost rate

A pure function of what the data layer read — no `Session`, no clock, no catalogue lookup. The same
shape of input comes from the live catalogue (a draft) and from the approval snapshot (an approved
scenario); which one it was is decided in `app.data.paid_absence_cost`, never here.

**A component of its own, beside the base cost and never inside it** (aneks SC-5-06, points 1 and
4). The base cost's hours are `planned_allocation_hours` (ADR-0013, point 4, unchanged); this
component's hours come from a different, separately named source, and its amount is never added to
the base amount here or anywhere else in this task — a total personnel cost is plan block 7's.

**Where the hours come from** (aneks SC-5-06, point 2):

- **(a) manual absences whose type has `generates_cost = true`**, counted exactly as the capacity
  counts them (SC-3-02, `app.domain.capacity.absence_day_equivalents_in_month`): the working days of
  the position's calendar inside the absence, per instance, times the calendar's standard day.
  **Never `× headcount`**: one absence is one person away, whatever the position's headcount is (the
  capacity's K-04 applied to the cost). Which types count is read off the flag and only off it —
  `PaidAbsenceSpan` carries `generates_cost` and nothing else, so a formula that cannot see
  `generates_revenue` cannot filter by it;
- **(b) for the statutory-leave type, when it generates cost, the budget's top-up** — the share of
  `max(0, entitlement − manual statutory days in the window)` this month holds, exactly the figure
  the capacity subtracts (`app.domain.absence_budget.month_budget_share`, `BudgetShare.hours`). The
  manual statutory rows are already in (a); what (b) adds is the difference, **never the whole
  entitlement** on top of them — that is the "46 days where the regulation grants 26" of SC-3-03
  R-02, transposed onto a cost. Over the budget's window the statutory hours costed are therefore
  `max(budget, manual)`, not their sum.

**What the budget does when the statutory type does not generate cost**: nothing, and that is a
decided answer, not a missing one — the budget contributes `0` hours because the type's flag says a
day of that leave costs nothing extra. When the budget *cannot* be applied (no row covers the month,
or no type is flagged statutory, so nobody can say whether its days cost anything) the answer is a
named state, never a silent `0` (aneks SC-5-06, point 4; criterion K-03).

**The same cost rate as the base cost** (aneks SC-5-06, point 3): the rate resolved for the
(position, month) by ADR-0013's predicate (`app.data.personnel_cost.month_has_cost_rate`), handed
in as the same `MonthCostRate`. The component is costed only in months with an allocation row,
whatever `planned_allocation_hours` is — no proportion to the plan, no floor at zero planned hours.
That is a simplification accepted at gate 1 (Q-3, Q-4): absences not deducted from the plan are
costed twice, once as worked time and once here; named, not repaired.

**Two shapes, never a third**, like the base cost: `PaidAbsenceCostResult` or
`PaidAbsenceCostUnavailable`. Any (position, month) whose hours or rate cannot be established
withholds the **whole component** — never the sum of the months that did resolve, never `0`, and
never a reason to touch the base amount (criterion K-04).

**Independent of every revenue calculation** (F-06; rule 10 of the Invariant Guardian; aneks
SC-5-06, control N-4). Nothing here imports `app.domain.revenue*` or `app.data.commercial_terms`,
and none of them imports this module. `generates_revenue` is not read by anything in this component.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from app.core.money import round_money
from app.domain.absence_budget import NO_BUDGET, NO_STATUTORY_LEAVE_TYPE, BudgetShare
from app.domain.absence_budget import RESOLVED as BUDGET_RESOLVED
from app.domain.capacity import (
    NO_CALENDAR,
    AbsenceSpan,
    CalendarBasis,
    absence_day_equivalents_in_month,
)
from app.domain.personnel_cost import (
    COST_BASIS_BASE,
    COST_BASIS_FULLY_LOADED,
    CURRENCY_MISMATCH,
    NO_COST_CURRENCY,
    NO_COST_RATE,
    MonthCostRate,
    surcharge_fraction,
)

# --- the named states of the component (aneks SC-5-06, point 4) ---------------------------------

HOURS_STATES: Final = (NO_CALENDAR, NO_STATUTORY_LEAVE_TYPE, NO_BUDGET)
"""The states in which a month's paid-absence **hours** cannot be established, in the order the
component reports them when several months disagree.

- `no_calendar` — the position's location names no calendar: no working day can be counted, and
  the budget cannot be turned into hours (the capacity's K-23 state, the same constant);
- `no_statutory_leave_type` — a budget covers the month but no absence type is flagged statutory,
  so nothing says whether a day of it costs anything, nor which booked leave it already covers;
- `no_budget` — the statutory type generates cost (or none is named) and no budget window covers
  the month.

Each one is the budget's or the capacity's own constant — one vocabulary, so the grid and the
component cannot name one fact twice. `no_cost_rate`, `currency_mismatch` and `no_cost_currency`
follow, with the base cost's meaning (`app.domain.personnel_cost`)."""

STATE_ORDER: Final = (*HOURS_STATES, NO_COST_RATE)
"""Which unresolved reason names the whole component when months fail for different reasons: what
the hours need first (the catalogue's calendar and budget), then the rate."""

BUDGET_NOT_COST_GENERATING: Final = "statutory_leave_not_cost_generating"
"""A month's budget part is `0` **because the statutory type's `generates_cost` is `false`** — a
decided answer carried in `assumptions_used`, so a zero budget part never has to be guessed at."""

BUDGET_APPLIED: Final = "applied"
"""A month's budget part is the budget's top-up share for that month (`BudgetShare.hours`)."""

HOURS_SOURCE_PAID_ABSENCES: Final = "paid_absences_and_leave_budget_top_up"
"""The one source of hours of this component — named, because it is not the base cost's
(`planned_allocation_hours`)."""


# --- what the formula is handed -------------------------------------------------------------------


@dataclass(frozen=True)
class PaidAbsenceSpan:
    """One booked absence as this formula needs it: an inclusive range and whether its **type**
    generates cost.

    `generates_cost` is read from the type — from the live dictionary for a draft, from
    `approved_snapshot_absence_type` for an approved scenario — and nothing else about the type
    travels here. In particular `generates_revenue` does not: a formula that cannot see it cannot
    filter by it (criterion K-01; F-06).
    """

    start_date: date
    end_date: date
    generates_cost: bool


@dataclass(frozen=True)
class PaidAbsenceMonth:
    """One allocation row as this component sees it.

    - `basis` — the position's calendar (`None`: the location names none, `no_calendar`);
    - `absences` — every booked absence of the position, each carrying its type's cost flag;
    - `budget` — the budget's share of this month exactly as the capacity computed it
      (`app.data.staffing.position_view`), state included;
    - `statutory_generates_cost` — the statutory type's `generates_cost`, `None` when no type is
      flagged statutory;
    - `rate` — the cost rate of the (position, month), `None` when ADR-0013's predicate does not
      resolve it. The same object the base cost is computed with.
    """

    position_id: uuid.UUID
    period_month: date
    basis: CalendarBasis | None
    absences: tuple[PaidAbsenceSpan, ...]
    budget: BudgetShare
    statutory_generates_cost: bool | None
    rate: MonthCostRate | None


# --- what it answers ------------------------------------------------------------------------------


@dataclass(frozen=True)
class PaidAbsenceMonthHours:
    """The hours one (position, month) contributes — both parts, and why the budget part is what
    it is."""

    position_id: uuid.UUID
    period_month: date
    manual_hours: Decimal
    budget_hours: Decimal
    budget_part: str
    """`BUDGET_APPLIED` or `BUDGET_NOT_COST_GENERATING`."""


@dataclass(frozen=True)
class UnresolvedPaidAbsenceMonth:
    """A (position, month) the component could not cost, and the reason."""

    position_id: uuid.UUID
    period_month: date
    reason: str


@dataclass(frozen=True)
class PaidAbsenceAssumptionsUsed:
    """What the component depends on (F-06.5) — present on both shapes of the answer.

    It carries hours per month and no rate: the cost rates are the base cost's
    (`CostAssumptionsUsed.rate_windows`), and repeating them here would be a second list of one
    fact. Gated with the amount all the same (aneks SC-5-06, point 6): hours times the base cost's
    rates is the amount, one multiplication away.
    """

    hours_source: str = HOURS_SOURCE_PAID_ABSENCES
    months: tuple[PaidAbsenceMonthHours, ...] = ()
    unresolved_months: tuple[UnresolvedPaidAbsenceMonth, ...] = ()
    currencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class PaidAbsenceCostResult:
    """A stated component: the whole amount and the part of it that came from the budget, each
    rounded once, at the end, through `app.core.money.round_money`."""

    cost: Decimal
    budget_cost: Decimal
    currency: str
    assumptions_used: PaidAbsenceAssumptionsUsed
    basis: str = COST_BASIS_BASE
    """`base` — the base cost rate, before overheads (aneks SC-5-06, point 5)."""


@dataclass(frozen=True)
class PaidAbsenceCostUnavailable:
    """A named state: the component cannot be stated, and `reason` says why. No amount on it."""

    reason: str
    assumptions_used: PaidAbsenceAssumptionsUsed
    basis: str = COST_BASIS_BASE


PaidAbsenceCostAnswer = PaidAbsenceCostResult | PaidAbsenceCostUnavailable


# --- the formula ----------------------------------------------------------------------------------


def month_paid_absence_hours(month: PaidAbsenceMonth) -> PaidAbsenceMonthHours | str:
    """The two hour figures of one (position, month), or the named state that withholds them.

    1. **No calendar → `no_calendar`.** Nothing can be counted: not a working day of an absence,
       not a budget share in hours.
    2. **Manual part** — the working days of the calendar covered by absences whose type generates
       cost, **per instance** (two absences over one day are two person-days, SC-3-02 K-06), times
       the calendar's standard day. The count is the capacity's own function, not a second spelling
       of "working day inside an absence". No `headcount` anywhere on this line.
    3. **Budget part** — if the statutory type is named and does **not** generate cost, `0` by
       decision. Otherwise the budget must be applied (`BudgetShare.state == resolved`) and its
       month's top-up (`BudgetShare.hours`) is the part; any other budget state (`no_budget`,
       `no_statutory_leave_type`) names the month. The top-up already subtracts the booked
       statutory days over the window (`max` first, proration second — ADR-0008 aneks SC-3-03,
       points 9 and 10), which is why adding it to the manual statutory rows of (2) is never the
       whole entitlement twice.
    """
    basis = month.basis
    if basis is None:
        return NO_CALENDAR
    costed = [
        AbsenceSpan(start_date=span.start_date, end_date=span.end_date)
        for span in month.absences
        if span.generates_cost
    ]
    manual_days = absence_day_equivalents_in_month(basis, costed, month.period_month)
    manual_hours = Decimal(manual_days) * basis.standard_hours_per_day

    if month.statutory_generates_cost is False:
        budget_hours = Decimal("0.00")
        budget_part = BUDGET_NOT_COST_GENERATING
    elif month.budget.state == BUDGET_RESOLVED and isinstance(month.budget.hours, Decimal):
        budget_hours = month.budget.hours
        budget_part = BUDGET_APPLIED
    else:
        return month.budget.state

    return PaidAbsenceMonthHours(
        position_id=month.position_id,
        period_month=month.period_month,
        manual_hours=manual_hours,
        budget_hours=budget_hours,
        budget_part=budget_part,
    )


_ResolvedPaidAbsenceMonths = list[tuple[PaidAbsenceMonthHours, MonthCostRate]]


def _resolve_paid_absence_months(
    months: Sequence[PaidAbsenceMonth], *, scenario_currency: str | None
) -> tuple[PaidAbsenceAssumptionsUsed, _ResolvedPaidAbsenceMonths, str | None, str | None]:
    """The three checks `paid_absence_cost` and `fully_loaded_paid_absence_cost` share (SC-5-02),
    extracted once so the two cannot silently disagree about which months resolve.

    Returns `(assumptions, resolved, currency, reason)` — `resolved` is always the months whose
    hours and rate both resolved, whatever `reason` says (a caller checks `reason is not None`
    first, exactly as `paid_absence_cost` always has, and never reads `resolved` before that check).
    """
    resolved: list[tuple[PaidAbsenceMonthHours, MonthCostRate]] = []
    unresolved: list[UnresolvedPaidAbsenceMonth] = []
    for month in months:
        hours = month_paid_absence_hours(month)
        if isinstance(hours, str):
            unresolved.append(
                UnresolvedPaidAbsenceMonth(
                    position_id=month.position_id, period_month=month.period_month, reason=hours
                )
            )
        elif month.rate is None:
            unresolved.append(
                UnresolvedPaidAbsenceMonth(
                    position_id=month.position_id,
                    period_month=month.period_month,
                    reason=NO_COST_RATE,
                )
            )
        else:
            resolved.append((hours, month.rate))

    currencies = tuple(sorted({rate.currency for _, rate in resolved}))
    assumptions = PaidAbsenceAssumptionsUsed(
        months=tuple(hours for hours, _ in resolved),
        unresolved_months=tuple(unresolved),
        currencies=currencies,
    )
    if unresolved:
        reasons = {month.reason for month in unresolved}
        reason = next(state for state in STATE_ORDER if state in reasons)
        return assumptions, resolved, None, reason
    if len(currencies) > 1 or (
        scenario_currency is not None and currencies and currencies != (scenario_currency,)
    ):
        return assumptions, resolved, None, CURRENCY_MISMATCH
    currency = currencies[0] if currencies else scenario_currency
    if currency is None:
        return assumptions, resolved, None, NO_COST_CURRENCY
    return assumptions, resolved, currency, None


def paid_absence_cost(
    months: Sequence[PaidAbsenceMonth], *, scenario_currency: str | None
) -> PaidAbsenceCostAnswer:
    """The paid-absence component of one scenario, or the named state that withholds it.

    The order of the checks (`_resolve_paid_absence_months`):

    1. **Any month whose hours or rate cannot be established → a named state for the whole
       component**, naming every (position, month) and its reason. The component's state is the
       first reason of `STATE_ORDER` present. Never the sum of the months that did resolve, never
       `0` for the missing ones (criterion K-04). A month with no paid absence still needs its
       rate — "is this month costed" is a question about the catalogue, as for the base cost.
    2. **More than one currency among the months, or one other than the scenario's →
       `currency_mismatch`**. Nothing is converted.
    3. **No allocation row at all** — `0.00` in the scenario's currency when it declares one,
       otherwise `no_cost_currency` (the base cost's rule, so the two cannot disagree about an
       empty plan).
    4. Otherwise the sums, exact `Decimal` all the way, rounded **once** each through
       `app.core.money.round_money` — never per month, never per position.
    """
    assumptions, resolved, currency, reason = _resolve_paid_absence_months(
        months, scenario_currency=scenario_currency
    )
    if reason is not None:
        return PaidAbsenceCostUnavailable(reason=reason, assumptions_used=assumptions)

    total = sum(
        ((hours.manual_hours + hours.budget_hours) * rate.cost_rate for hours, rate in resolved),
        Decimal("0"),
    )
    budget_total = sum(
        (hours.budget_hours * rate.cost_rate for hours, rate in resolved), Decimal("0")
    )
    return PaidAbsenceCostResult(
        cost=round_money(total),
        budget_cost=round_money(budget_total),
        currency=currency,
        assumptions_used=assumptions,
    )


# --- the fully loaded paid-absence cost (SC-5-02, F-07; ADR-0013, aneks 2026-09-25 pt 5) ---------


@dataclass(frozen=True)
class FullyLoadedPaidAbsenceCostResult:
    """The component's fully loaded cost: the same hours as `PaidAbsenceCostResult`, at the same
    rate plus its surcharge — never a different rate and never a different hours source."""

    cost: Decimal
    surcharge_amount: Decimal
    currency: str
    assumptions_used: PaidAbsenceAssumptionsUsed
    basis: str = COST_BASIS_FULLY_LOADED


@dataclass(frozen=True)
class FullyLoadedPaidAbsenceCostUnavailable:
    """A named state, for the identical reason the base component cannot be stated
    (`_resolve_paid_absence_months` is shared) — never a reason of its own."""

    reason: str
    assumptions_used: PaidAbsenceAssumptionsUsed
    basis: str = COST_BASIS_FULLY_LOADED


FullyLoadedPaidAbsenceCostAnswer = (
    FullyLoadedPaidAbsenceCostResult | FullyLoadedPaidAbsenceCostUnavailable
)


def fully_loaded_paid_absence_cost(
    months: Sequence[PaidAbsenceMonth], *, scenario_currency: str | None
) -> FullyLoadedPaidAbsenceCostAnswer:
    """The paid-absence component's fully loaded cost (ADR-0013, aneks 2026-09-23 SC-5-06 pt 5,
    applied by SC-5-02's aneks of 2026-09-25): "the paid-absence component gets the surcharge the
    same way the base cost does" — the same `surcharge_fraction` of the same `MonthCostRate`, the
    same (manual + budget) hours `paid_absence_cost` already sums, never a fraction of a different
    figure.

    Exactly `paid_absence_cost`'s three checks (shared via `_resolve_paid_absence_months`); the sum
    is one unrounded pass over hours × rate × (1 + surcharge fraction), for the reason
    `fully_loaded_personnel_cost` gives its own single pass.
    """
    assumptions, resolved, currency, reason = _resolve_paid_absence_months(
        months, scenario_currency=scenario_currency
    )
    if reason is not None:
        return FullyLoadedPaidAbsenceCostUnavailable(reason=reason, assumptions_used=assumptions)

    base_total = Decimal("0")
    surcharge_total = Decimal("0")
    for hours, rate in resolved:
        base_amount = (hours.manual_hours + hours.budget_hours) * rate.cost_rate
        base_total += base_amount
        surcharge_total += base_amount * surcharge_fraction(rate)

    return FullyLoadedPaidAbsenceCostResult(
        cost=round_money(base_total + surcharge_total),
        surcharge_amount=round_money(surcharge_total),
        currency=currency,
        assumptions_used=assumptions,
    )
