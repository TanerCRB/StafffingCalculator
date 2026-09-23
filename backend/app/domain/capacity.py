"""Derived monthly capacity: what a staffing position can actually work (F-05, SC-3-02, SC-3-03).

One formula, spelled once:

```
capacity(month) = headcount × working_days(month) × standard_hours_per_day
                − absence_day_equivalents(month) × standard_hours_per_day
                − budget_share_days(month)       × standard_hours_per_day
```

floored at zero. The third line is SC-3-03: the leave **budget** of the position's (calendar,
engagement type) pair, prorated over the months of its window, on top of the absences actually
booked. What it contributes is the *difference* between the entitlement and the booked days over
the whole window, and the rule producing it — `max` first on the window, proration second — lives
in `app.domain.absence_budget` and is not repeated here (ADR-0008, addendum 2026-09-22 SC-3-03,
points 9 and 10).

Five properties of it are acceptance criteria rather than implementation choices, and each one is a
line below that a mutation would change:

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
read the absence type's `generates_cost`/`generates_revenue` flags (nothing reads them into a
calculation yet — see `app.models.catalog.AbsenceType`), decide which budget window applies to a
month (the database does, through `valid_period @> :month`), or write anything. It is a pure
function of rows it is handed.

The one flag it *does* consult is `AbsenceSpan.is_statutory_leave`, and only to know which booked
absences the budget has already counted — never to decide what an absence costs.
"""

import calendar as _calendar
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.core.money import NOT_APPLICABLE, round_money
from app.domain.absence_budget import NO_CALENDAR as BUDGET_NO_CALENDAR
from app.domain.absence_budget import RESOLVED as BUDGET_RESOLVED
from app.domain.absence_budget import BudgetShare, month_budget_share
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
    """One absence as the formula needs it: an inclusive range of calendar days, and one flag.

    The absence *type* is still absent from this object, and the flag is not a way back in: every
    absence removes the same person-day whatever it is called (SC-3-02), and whether it also costs
    money or earns any is still F-07/F-08's question about the type's flags, not this one's.

    What `is_statutory_leave` answers is a different question — whether this absence is one of the
    days the **budget** already counts (ADR-0008, addendum 2026-09-22 SC-3-03, points 8 and 9). It
    is a `bool` rather than the type's id because that is the whole of what the formula needs, and
    because the mapping from id to flag is a catalogue lookup that belongs one layer up. Its default
    is `False`, so an absence about which nothing was said is deducted on its own and absorbs
    nobody's entitlement — the direction in which being wrong is visible rather than silent.
    """

    start_date: date
    end_date: date
    is_statutory_leave: bool = False


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

    budget: BudgetShare | None = None
    """What the leave budget took out of this month, and where that came from (SC-3-03).

    `None` only where this object is built without one at all; the ordinary "there is no budget"
    answer is a `BudgetShare` carrying the named state and `"n/a"`, never a missing field and never
    a zero (criterion K-08).

    **`budget.hours` is the figure subtracted below, and this object holds no second copy of it.**
    It used to hold one (`budget_hours`, computed here from a share in days), and the remainder
    distribution of S-01/R-05 removed the reason: the share is now allocated per month by
    `app.domain.absence_budget`, so recomputing it anywhere else would be a second answer to a
    question already answered — and the two could differ by a cent on exactly the windows the
    distribution exists for."""


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


def absence_day_equivalents_between(
    basis: CalendarBasis,
    absences: Iterable[AbsenceSpan],
    start_date: date,
    end_date: date,
) -> int:
    """Person-days those absences remove from the inclusive range `start_date`..`end_date`.

    The general form of `absence_day_equivalents_in_month` below, extracted in SC-3-03 because the
    budget's `max` is resolved over the **window** rather than over a month (ADR-0008, addendum
    2026-09-22 SC-3-03, point 9): "how many days of statutory leave does this window already hold"
    is the same intersection question asked over a different range.

    One function rather than two similar ones, so "an absence counts only on the calendar's working
    days, and overlapping absences count once each" cannot end up meaning two different things in
    the two places that ask it.
    """
    days = _days_between(start_date, end_date)
    return sum(
        sum(
            1
            for day in days
            if absence.start_date <= day <= absence.end_date and basis.is_working_day(day)
        )
        for absence in absences
    )


def _days_between(start_date: date, end_date: date) -> list[date]:
    """Every calendar day of an inclusive range, oldest first."""
    if end_date < start_date:
        return []
    return [
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    ]


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
    return absence_day_equivalents_between(basis, absences, month_days[0], month_days[-1])


def month_capacity(
    basis: CalendarBasis | None,
    *,
    headcount: int,
    period_month: date,
    absences: Iterable[AbsenceSpan] = (),
    budget_share: BudgetShare | None = None,
) -> MonthCapacity:
    """The derived capacity of one position for one month — or the named "no calendar" state.

    `basis is None` means the position's location names no calendar. The answer is the named state
    and `"n/a"`, never `0` and never an exception (K-23): a caller that showed `0` would be showing
    a plan in which nobody can work, and a caller that crashed would make an incomplete catalogue a
    server error rather than something to fill in. A location with no calendar has no budget either,
    and the budget field carries the *same* named state rather than a second one of its own
    (ADR-0008, addendum 2026-09-22 SC-3-03, point 3a).

    `budget_share` is the leave budget's share of this month, already resolved by
    `app.domain.absence_budget.month_budget_share` — this function does not decide which window
    applies, how the `max` against booked leave came out, or how many months the window has. That
    separation is the point: the rule is stated once, in the module that owns it, and repeating any
    part of it here would be the second place it could change.

    The arithmetic is exact `Decimal` (`headcount` and both day counts are integers, so the only
    inexact step would be a `float`, and there is none), and the single rounding rule is
    `round_money` (invariant-guardian rule 2). The budget's share arrives in hours, already rounded
    by the module that allocated it, and it is that figure which is both subtracted here and
    reported by the payload — so the two cannot disagree by a cent. What is never rounded is the
    entitlement on the way there: `26 ÷ 12` stays exact until it has been multiplied by the standard
    day, which is criterion K-03's mutation (c) (`2.17 × 7.50 = 16.275 → 16.28`, an answer that is
    wrong by construction).

    **`headcount` multiplies the gross capacity here and the entitlement there, and neither
    multiplies the booked absences** (K-04): a team of three with one person on holiday loses one
    person's day, while a team of three is entitled to three people's leave (reviewer R-01).

    The floor is applied *after* both subtractions and is a `max`, not an assumption: a position
    whose absences and entitlement exceed its month has zero capacity, not a negative one, and a
    negative capacity multiplied by a rate is a negative cost (K-07).
    """
    if basis is None:
        return MonthCapacity(
            period_month=period_month,
            state=NO_CALENDAR,
            hours=NOT_APPLICABLE,
            budget=month_budget_share(None, state=BUDGET_NO_CALENDAR),
        )

    share = budget_share if budget_share is not None else month_budget_share(None)
    working_days = working_days_in_month(basis, period_month)
    consumed = absence_day_equivalents_in_month(basis, absences, period_month)
    gross = Decimal(headcount) * Decimal(working_days) * basis.standard_hours_per_day
    net = gross - Decimal(consumed) * basis.standard_hours_per_day
    # The share arrives already in hours and already rounded (`app.domain.absence_budget`), because
    # the month's slice depends on where the month sits in its window — the remainder distribution
    # of S-01/R-05. Converting days to hours here again is what this line used to do, and it is the
    # second place that rule could have changed.
    if share.state == BUDGET_RESOLVED and isinstance(share.hours, Decimal):
        net -= share.hours
    return MonthCapacity(
        period_month=period_month,
        state=RESOLVED,
        hours=round_money(max(net, Decimal(0))),
        calendar_id=basis.calendar_id,
        calendar_name=basis.name,
        standard_hours_per_day=basis.standard_hours_per_day,
        working_days=working_days,
        absence_day_equivalents=consumed,
        budget=share,
    )
