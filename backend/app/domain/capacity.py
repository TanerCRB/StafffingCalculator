"""Derived monthly capacity: what a staffing position can actually work (F-05, SC-3-02).

One formula, spelled once:

```
capacity(month) = headcount × working_days(month) × standard_hours_per_day
                − absence_day_equivalents(month) × standard_hours_per_day
```

floored at zero. Five properties of it are acceptance criteria rather than implementation choices,
and each one is a line below that a mutation would change:

- **The hours per day come from the calendar of the position's location** (K-01). There is no
  constant `8` in this module, in `app.data.staffing`, or anywhere else on this path — a fallback
  constant is the mutation K-01 exists to kill, and no calendar in the test suite is allowed to
  carry `8.00` so that it could not hide behind one.
- **Which days are working days is data** (K-02): the calendar's week pattern plus its exceptional
  days, both rows in the database. `day.weekday() < 5` appears nowhere; a calendar working Monday
  to Saturday is `'1111110'`.
- **One absence costs one person-day, whatever the headcount is** (K-04). `headcount` multiplies
  the *capacity*, never the deduction: a team of three with one person on holiday loses one
  person's day, not three.
- **Overlapping absences are added up, never deduplicated** (K-06). Two absences over the same
  three days are two people away, so the deduction is per instance. A `set` of days or a
  `COUNT(DISTINCT day)` here would turn two people into one.
- **The result is floored at zero** (K-07), and the floor is `max`, not a subtraction that
  "cannot" go negative: a position whose absences exceed its month has zero capacity, not a
  negative one, and a negative capacity multiplied by a rate is a negative cost.

**`Decimal` throughout, and the one rounding point is the shared helper.** Hours are one
multiplication away from money (NF-01, ADR-0002), so the arithmetic is exact `Decimal` and the
single `quantize` goes through `app.core.money.round_money` — never an ad-hoc `round()`.

**No clock.** Nothing here reads `date.today()`: every boundary is a month the caller named and a
calendar the database holds (invariant-guardian rule on injected time sources).

**What this module does not do:** touch a rate, a cost or a currency (F-07/F-08 are plan block 5),
read the absence type's `generates_cost`/`generates_revenue` flags (nothing does yet — see
`app.models.catalog.AbsenceType`), or write anything. It is a pure function of rows it is handed.
"""

import calendar as _calendar
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.core.money import NOT_APPLICABLE, round_money
from app.models.catalog import WEEK_PATTERN_LENGTH, WorkingCalendarDayKind

RESOLVED = "resolved"
NO_CALENDAR = "no_calendar"
"""The two named states of a derived capacity figure — never a silent number (K-23).

`NO_CALENDAR` is what a position in a location with `calendar_id IS NULL` gets: the hours come back
as `app.core.money.NOT_APPLICABLE` (`"n/a"`), the same sentinel this project already uses for a
figure that cannot be computed, and **not** as `0` and **not** as an exception. A zero would be a
number every later sum would happily add up; an exception would make an incomplete catalogue a
server error. ADR-0008's addendum of 2026-09-22 (point 7) decides this explicitly: an omission is a
state, not a hole to fill with a guess.

Spelled as constants rather than written at each call site, because the API schema declares them as
a `Literal` and the two must not drift apart."""


@dataclass(frozen=True)
class CalendarBasis:
    """One working calendar, as the capacity formula needs it: a basis and a set of days.

    Assembled by `app.data.staffing` from `working_calendar` + `working_calendar_day`, or copied out
    of the approval snapshot by a future reader — the shape is the same either way, which is the
    point of it being a value object rather than an ORM row. Nothing here queries.
    """

    calendar_id: uuid.UUID
    name: str
    standard_hours_per_day: Decimal
    week_pattern: str
    exceptional_days: Mapping[date, WorkingCalendarDayKind]
    """Every day the calendar names explicitly, and what it says about it. A day present here
    overrides the week pattern in *both* directions (K-02): a `NON_WORKING` Tuesday is a holiday, a
    `WORKING` Saturday is an extra day. Keyed by day, which is what `UNIQUE (calendar_id, day)` in
    the database makes possible — two rows for one day would be two answers to one question."""

    def is_working_day(self, day: date) -> bool:
        """Is this calendar date a working day of this calendar?

        The exceptional day wins over the pattern, and the pattern is read from the string rather
        than from `weekday() < 5`. `week_pattern` is validated by a CHECK constraint in the
        database, so the index below cannot be out of range for a row that came from there.
        """
        exception = self.exceptional_days.get(day)
        if exception is not None:
            return exception is WorkingCalendarDayKind.WORKING
        return self.week_pattern[day.weekday() % WEEK_PATTERN_LENGTH] == "1"


@dataclass(frozen=True)
class AbsenceSpan:
    """One absence as the formula needs it: an inclusive range of calendar days.

    The absence *type* is deliberately absent from this object. Nothing in the capacity formula
    depends on it: every absence removes the same person-day whatever it is called, and whether it
    also costs money or earns any is F-07/F-08's question about the type's flags, not this one's.
    """

    start_date: date
    end_date: date


@dataclass(frozen=True)
class MonthCapacity:
    """The derived capacity of one position for one month, with the source of the figure.

    `hours` is a `Decimal` when `state == RESOLVED` and the string `NOT_APPLICABLE` when
    `state == NO_CALENDAR`; the other fields are `None` in that second case. Two fields rather than
    one nullable number, because "this could not be computed" and "this came out as zero" are two
    different answers and a single field would make them one.

    The source fields are not decoration: ADR-0008's addendum of 2026-09-22 (point 4) requires a
    resolved capacity to name *which* calendar and *what* basis it came from, because a scenario
    pointing at a stale calendar is otherwise indistinguishable from one pointing at the right
    calendar (F-02, "identify the source of each inherited or overridden value").
    """

    period_month: date
    state: str
    hours: Decimal | str
    calendar_id: uuid.UUID | None = None
    calendar_name: str | None = None
    standard_hours_per_day: Decimal | None = None
    working_days: int | None = None
    absence_day_equivalents: int | None = None


def days_of_month(period_month: date) -> Iterable[date]:
    """Every calendar day of the month `period_month` begins.

    Takes the first of the month (the shape `staffing_position_allocation.period_month` is
    constrained to by `FIRST_DAY_OF_MONTH_EXPRESSION`) and yields its days. `calendar.monthrange`
    rather than arithmetic on `timedelta(days=31)`, so February and leap years are the standard
    library's problem rather than this module's.
    """
    _, length = _calendar.monthrange(period_month.year, period_month.month)
    first = period_month.replace(day=1)
    return (first + timedelta(days=offset) for offset in range(length))


def working_days_in_month(basis: CalendarBasis, period_month: date) -> int:
    """How many working days this calendar has in that month — pattern plus exceptional days."""
    return sum(1 for day in days_of_month(period_month) if basis.is_working_day(day))


def absence_day_equivalents_in_month(
    basis: CalendarBasis, absences: Iterable[AbsenceSpan], period_month: date
) -> int:
    """Person-days removed from that month by those absences.

    **Summed per absence, deliberately.** Each span is intersected with the month and with the
    calendar's working days on its own, and the results are added: two absences over the same three
    days remove six person-days, not three (K-06). Deduplicating the union of days — a `set`, a
    `COUNT(DISTINCT day)` — would silently turn two absent people into one, and the resulting
    capacity would be too high on exactly the positions where over-allocation matters most.

    Days that are not working days of the calendar contribute nothing (K-05): an absence over a
    weekend or a public holiday removes no capacity, because there was none to remove. That is why
    this counts the *intersection* rather than `(end_date - start_date).days + 1`.
    """
    month_days = list(days_of_month(period_month))
    return sum(
        sum(
            1
            for day in month_days
            if absence.start_date <= day <= absence.end_date and basis.is_working_day(day)
        )
        for absence in absences
    )


def month_capacity(
    basis: CalendarBasis | None,
    *,
    headcount: int,
    period_month: date,
    absences: Iterable[AbsenceSpan] = (),
) -> MonthCapacity:
    """The derived capacity of one position for one month — or the named "no calendar" state.

    `basis is None` means the position's location names no calendar. The answer is the named state
    and `"n/a"`, never `0` and never an exception (K-23): a caller that showed `0` would be showing
    a plan in which nobody can work, and a caller that crashed would make an incomplete catalogue a
    server error rather than something to fill in.

    The arithmetic is exact `Decimal` (`headcount` and both day counts are integers, so the only
    inexact step would be a `float`, and there is none), and the single rounding point is
    `round_money` — the project's one rounding rule (invariant-guardian rule 2). The floor is
    applied *after* the subtraction and is a `max`, not an assumption: the case it exists for is a
    position whose absences exceed its month, which is ordinary data in a plan that has been edited
    down (K-07).
    """
    if basis is None:
        return MonthCapacity(
            period_month=period_month, state=NO_CALENDAR, hours=NOT_APPLICABLE
        )

    working_days = working_days_in_month(basis, period_month)
    consumed = absence_day_equivalents_in_month(basis, absences, period_month)
    gross = Decimal(headcount) * Decimal(working_days) * basis.standard_hours_per_day
    net = gross - Decimal(consumed) * basis.standard_hours_per_day
    return MonthCapacity(
        period_month=period_month,
        state=RESOLVED,
        hours=round_money(max(net, Decimal(0))),
        calendar_id=basis.calendar_id,
        calendar_name=basis.name,
        standard_hours_per_day=basis.standard_hours_per_day,
        working_days=working_days,
        absence_day_equivalents=consumed,
    )
