"""FTE <-> hours from the working calendar of a position (F-04, F-05, SC-3-07).

One basis, spelled once:

```
hours_of_1_fte(month) = working_days(month) x standard_hours_per_day
```

It is the **gross** term of the capacity formula in `app.domain.capacity`, for one person, before
any absence or leave-budget subtraction (gate-1 decision Q3 = A, ADR-0008 addendum 2026-09-29,
point 4). Two conversions use it, and they use **different units**, which each result names in its
`unit` field:

- hours -> FTE (`hours_to_fte_percent`, `unit == "percent"`): `ratio_percent(hours, basis_hours)`,
  a share of a full-time calendar month in percent, `100.00` being 1.00 FTE (ADR-0002 addendum
  2026-09-29). The result also carries `fte_exact`, the unrounded quotient `hours / basis_hours`
  as a fraction (1 = 1 FTE), so a consumer never has to feed the rounded percent back in;
- FTE -> hours (`fte_to_hours`, `unit == "hours"`): `round_money(fte_fraction x basis_hours)`, one
  exact `Decimal` multiplication and one rounding. The argument is a **fraction** (`1` = 1 FTE,
  `0.5` = half), not a percent: `100` would mean a hundred FTE. The parameter is named
  `fte_fraction` for that reason.

**Hours stay authoritative; FTE is never round-tripped.** The percent is a display projection.
Whoever holds hours keeps the hours; whoever needs the FTE back as an input takes `fte_exact`, not
`value / 100`. The unit lives in a `unit` field rather than in two differently named value fields:
it is the smallest change that keeps `value` one column for a consumer while making the unit
explicit and checkable (`__post_init__` pairs it with the direction).

Properties that are acceptance criteria rather than implementation choices:

- **The hours per day come from the calendar** (A-K01): no constant `8`, no `40 / 5`, and nothing
  read from `scenarios.full_time_hours_per_week`. The basis is independent of that column by
  construction: the functions take only a `CalendarBasis`, so no scenario is in reach (gate-1
  decision Q4 = A).
- **Which days are working days is data** (A-K02): `capacity.working_days_in_month` is used, so the
  week pattern and the exceptional days are read exactly as the capacity reads them. `weekday() < 5`
  appears nowhere.
- **Gross of absences** (A-K03): this module has no absence parameter at all.
- **Three named states, never a silent zero** (A-K04, A-K04b): `no_calendar` (the constant of
  `app.domain.capacity`, not a second spelling), `no_working_days`, and `resolved`. Both
  unresolved states carry `app.core.money.NOT_APPLICABLE` as the value. The state is decided
  before any division, and `FteConversion.__post_init__` makes a state/value mismatch
  unconstructible. A calendar with zero hours per day cannot occur (the source table has
  `CHECK standard_hours_per_day > 0`; the approval snapshot copies the values and does not repeat
  the CHECK), but if one arrived the hours of 1 FTE would be zero, and the result is
  `no_working_days`, never a resolved `"n/a"`.
- **Every figure names its basis** (A-K05): calendar id, calendar name, hours per day, working
  days.
- **Position-level, no headcount factor**: the hours of a position are its total (ADR-0003 point 6),
  so the FTE derived from them is the FTE of the position and exceeds 100 % when `headcount > 1`;
  an FTE handed in is already a position total and is not multiplied by a headcount.
- **The one rounding rule** (A-K06): the only rounding calls are `ratio_percent` and `round_money`
  from `app.core.money`; `round()`/`quantize` do not appear here. `fte_exact` is a plain `Decimal`
  quotient at the ambient decimal context precision (28 digits, inherited from `app.core.money`,
  which sets no context of its own); it is not money and is never displayed or stored as such.
- **Input is validated, not trusted** (R-03): `hours` and `fte_fraction` must be a finite,
  non-negative `Decimal` no larger than `MAX_INPUT`; anything else is a `ValueError` (a
  non-`Decimal` is a `TypeError`), raised before any state is decided. Zero is legitimate. The
  bound exists because `Decimal.quantize` raises `decimal.InvalidOperation` once a result
  outgrows the context
  precision; a named `ValueError` is the contract instead.
- **The month is normalised**: `period_month` may be any date in the month and the result echoes
  the first of that month.

**Not an input of any revenue or cost calculation** (ADR-0008 addendum 2026-09-29, point 7, and
ADR-0003 points 6-7): nothing here feeds `billable_hours`, `availability_hours`,
`planned_allocation_hours` or `derived_capacity_hours`, and nothing imports this module yet. An
import tripwire in `tests/test_fte_hours.py` pins that until a first consumer exists.

**Live or frozen is the caller's decision.** The functions take an already-resolved
`CalendarBasis` (or `None`), so a draft passes `basis_by_location` output and an approved scenario
passes `frozen_basis_by_location` output (ADR-0008 addendum 2026-09-29, point 6). No consumer exists
in this Story, so nothing here queries anything, and there is no clock: the month is an argument.

**Deferred, deliberately (FTE-6):** "an approved scenario reads the snapshot, not the live
calendar" is *not* proven by this Story: with no consumer there is no path on which to prove it.
The first consumer (#79 SC-5-04 or #80) is the first place a scenario and a basis meet, and proves
it there.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final, Literal, get_args

from app.core.money import NOT_APPLICABLE, ratio_percent, round_money
from app.domain.capacity import NO_CALENDAR, RESOLVED, CalendarBasis, working_days_in_month

NO_WORKING_DAYS = "no_working_days"
"""A month in which the calendar has no working day: the hours of 1 FTE would be zero, so neither
direction can be computed. A state of its own, distinct from `NO_CALENDAR` (ADR-0008 addendum
2026-09-29, gap (b)); its value is `NOT_APPLICABLE`, never `0`."""

FteState = Literal["resolved", "no_calendar", "no_working_days"]
FteUnit = Literal["percent", "hours"]
UNIT_PERCENT: Final = "percent"
UNIT_HOURS: Final = "hours"

# A `Literal` cannot be spelled from variables, so the three strings appear once above and are
# checked against the constants of `capacity` at import: the two spellings cannot drift apart.
assert get_args(FteState) == (RESOLVED, NO_CALENDAR, NO_WORKING_DAYS)

MAX_INPUT: Final = Decimal("1000000000")
"""Upper bound of `hours` and `fte_fraction` (a billion). Far beyond any real position, and small
enough that `x basis_hours` (at most 31 x 24 h) stays inside the 28-digit decimal context, so
`quantize` cannot raise `decimal.InvalidOperation`."""


@dataclass(frozen=True)
class FteConversion:
    """One conversion result, with the basis it came from.

    `value` is in the unit named by `unit`: the FTE share **in percent** for `hours_to_fte_percent`
    and **hours** for `fte_to_hours` (both `Decimal`, two places). It is `NOT_APPLICABLE` in both
    unresolved states, which a consumer tells apart by `state`, not by the value. The basis fields
    are `None` when there is no calendar; `basis_hours` is `None` unless resolved. `fte_exact` is
    the unrounded FTE fraction (1 = 1 FTE) of the hours direction and `None` otherwise.

    The pairing is a rule of the type: `state == RESOLVED` if and only if `value` is a `Decimal`
    and `basis_hours` is set; the unresolved states carry `NOT_APPLICABLE` and no `fte_exact`; and
    `fte_exact` exists only for a resolved percent result.
    """

    period_month: date
    state: FteState
    value: Decimal | str
    unit: FteUnit
    calendar_id: uuid.UUID | None = None
    calendar_name: str | None = None
    standard_hours_per_day: Decimal | None = None
    working_days: int | None = None
    basis_hours: Decimal | None = None
    """Hours of 1 FTE in the month: `working_days x standard_hours_per_day`, exact."""
    fte_exact: Decimal | None = None
    """`hours / basis_hours` unrounded, as a fraction (1 = 1 FTE); the hours -> FTE direction."""

    def __post_init__(self) -> None:
        if self.state not in get_args(FteState):
            raise ValueError(f"unknown state {self.state!r}")
        if self.unit not in get_args(FteUnit):
            raise ValueError(f"unknown unit {self.unit!r}")
        resolved = self.state == RESOLVED
        if resolved != isinstance(self.value, Decimal):
            raise ValueError("state 'resolved' if and only if value is a Decimal")
        if resolved != (self.basis_hours is not None):
            raise ValueError("state 'resolved' if and only if basis_hours is set")
        if not resolved and self.value != NOT_APPLICABLE:
            raise ValueError(f"an unresolved state carries {NOT_APPLICABLE!r} as its value")
        if self.fte_exact is not None and not (resolved and self.unit == UNIT_PERCENT):
            raise ValueError("fte_exact exists only for a resolved percent result")
        if resolved and self.unit == UNIT_PERCENT and self.fte_exact is None:
            raise ValueError("a resolved percent result carries fte_exact")


@dataclass(frozen=True)
class _Resolved:
    """The month's basis, resolved once for both directions."""

    state: FteState
    period_month: date
    basis: CalendarBasis | None
    working_days: int | None = None
    basis_hours: Decimal | None = None


def _resolve(basis: CalendarBasis | None, period_month: date) -> _Resolved:
    """The one resolver: normalise the month, then decide the state before any division."""
    month = period_month.replace(day=1)
    if basis is None:
        return _Resolved(NO_CALENDAR, month, None)
    working_days = working_days_in_month(basis, month)
    basis_hours = Decimal(working_days) * basis.standard_hours_per_day
    if basis_hours == 0:  # no working day, or (defensively) a zero-hour day
        return _Resolved(NO_WORKING_DAYS, month, basis, working_days=working_days)
    return _Resolved(RESOLVED, month, basis, working_days, basis_hours)


def _require_amount(name: str, amount: Decimal) -> None:
    if not isinstance(amount, Decimal):
        raise TypeError(f"{name} must be a Decimal, not {type(amount).__name__}")
    if not amount.is_finite():
        raise ValueError(f"{name} must be finite, got {amount}")
    if amount < 0:
        raise ValueError(f"{name} must not be negative, got {amount}")
    if amount > MAX_INPUT:
        raise ValueError(f"{name} must not exceed {MAX_INPUT}, got {amount}")


def _conversion(
    resolved: _Resolved, *, unit: FteUnit, value: Decimal | str, fte_exact: Decimal | None = None
) -> FteConversion:
    basis = resolved.basis
    if basis is None:
        return FteConversion(
            period_month=resolved.period_month,
            state=resolved.state,
            value=NOT_APPLICABLE,
            unit=unit,
        )
    return FteConversion(
        period_month=resolved.period_month,
        state=resolved.state,
        value=value if resolved.state == RESOLVED else NOT_APPLICABLE,
        unit=unit,
        calendar_id=basis.calendar_id,
        calendar_name=basis.name,
        standard_hours_per_day=basis.standard_hours_per_day,
        working_days=resolved.working_days,
        basis_hours=resolved.basis_hours,
        fte_exact=fte_exact if resolved.state == RESOLVED else None,
    )


def hours_to_fte_percent(
    basis: CalendarBasis | None, *, period_month: date, hours: Decimal
) -> FteConversion:
    """The FTE of a position for one month, as a percent of a full-time month.

    `hours` is the position's allocated hours (a total, not per head). `value` is the percent
    (`100.00` means 1.00 FTE; a position with `headcount = 3` at full time is `300.00`) and
    `fte_exact` the unrounded fraction. Absences and the leave budget are not parameters: the basis
    is gross.
    """
    _require_amount("hours", hours)
    resolved = _resolve(basis, period_month)
    if resolved.basis_hours is None:
        return _conversion(resolved, unit=UNIT_PERCENT, value=NOT_APPLICABLE)
    return _conversion(
        resolved,
        unit=UNIT_PERCENT,
        value=ratio_percent(hours, resolved.basis_hours),
        fte_exact=hours / resolved.basis_hours,
    )


def fte_to_hours(
    basis: CalendarBasis | None, *, period_month: date, fte_fraction: Decimal
) -> FteConversion:
    """The hours of a position for one month from its FTE **fraction** (1 = a full calendar month).

    `fte_fraction` is the exact `Decimal` FTE of the **position** — a total that already includes
    any headcount, so there is no headcount factor here. It is a fraction, never a percent: `1`
    is one FTE, `100` would be a hundred. Take it from `FteConversion.fte_exact`, not from a
    rounded percent.
    """
    _require_amount("fte_fraction", fte_fraction)
    resolved = _resolve(basis, period_month)
    if resolved.basis_hours is None:
        return _conversion(resolved, unit=UNIT_HOURS, value=NOT_APPLICABLE)
    return _conversion(
        resolved, unit=UNIT_HOURS, value=round_money(fte_fraction * resolved.basis_hours)
    )
