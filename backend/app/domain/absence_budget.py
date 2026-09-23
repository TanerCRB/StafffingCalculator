"""The leave budget as the capacity formula needs it: one rule, spelled once (F-05, SC-3-03).

Two decisions of ADR-0008's addendum of 2026-09-22 (SC-3-03) live here and nowhere else, and the
**order between them is part of the rule**, not an implementation detail:

```
entitlement_days     = budget_days × headcount                                  (R-01: per FTE)
top_up_days(window)  = max(0, entitlement_days − manual_statutory_days_in_window)        (point 9)
share_hours(month)   = the month's slice of round(top_up_days × hours_per_day),
                       remainder distributed so the window's slices sum exactly  (point 10 + S-01)
```

**The budget is a number of days per FTE.** A position of five people holds five times the
regulation's figure (Issue #54, variant A; reviewer R-01), and that is also what makes the `max`
below a comparison of like with like: booked absences are counted per instance and never
deduplicated (SC-3-02, K-06), so their sum is person-days for the whole position.

**`max` first, on the whole window; proration second, onto the months.** The reverse — a `max` per
month — gives a drastically different answer, and the addendum spells out the case: a budget of 24
days over a year against 20 days of leave planned in July is 24 days deducted over the year in the
mandated order and 42 in the reversed one (point 9a). That is a mutation a test kills, not a
preference.

**What the budget adds is the difference, never the total.** A manual absence of the flagged type
already removes capacity in the month it actually falls in — that is SC-3-02's mechanism and it is
untouched — so the budget contributes only what the manual rows have not already consumed. Two
consequences, both accepted deliberately:

- the **sum over the window** is the same whether the leave was booked or not (it is `budget_days`
  either way, until the manual rows exceed the budget), while the **distribution over the months**
  differs: the month holding the booked absence loses more, the others less. Criterion K-05 asserts
  both, because either assertion alone is satisfied by a wrong implementation;
- leave that fits inside the budget is **invisible** in the result — planning 5 days against a
  budget of 26 changes no figure at all (point 9c). That is correct and will be read as a fault, so
  it has to be said on screen rather than discovered.

**Only the absence type flagged `is_statutory_leave` counts against the budget** (point 8). An
absence of any other type is deducted independently, on top — a global `max` over all types would
let a training day absorb somebody's holiday entitlement (criterion K-05, reading 5).

**`Decimal` throughout, and no intermediate rounding of the entitlement.** The division is exact
`Decimal` at full context precision and the single rounding rule stays `app.core.money.round_money`
(NF-01, ADR-0002, invariant-guardian rule 2). Rounding a share to whole days first is what criterion
K-03's mutations (b) and (c) are about: at twelve months it silently gains or loses several days a
year. Where rounding *is* unavoidable — hours are money-shaped and have two decimal places — the
remainder is **distributed rather than dropped**, so the window's shares add back up to the
entitlement exactly (`_allocated_hours`).

**A missing budget row is a named state, never a zero** (point 7), and so is a budget nobody can
apply. `NO_BUDGET`, `NO_STATUTORY_LEAVE_TYPE` and `NO_CALENDAR` all travel with
`app.core.money.NOT_APPLICABLE`: a `0` would be a number every later sum adds up, and it would be
indistinguishable from a real budget of zero days — which is a legal row meaning "this engagement
type has no entitlement".

**No clock.** Nothing here reads `date.today()`: the month is the caller's and the window is the
database's.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.money import NOT_APPLICABLE, round_money

RESOLVED = "resolved"
NO_BUDGET = "no_budget"
NO_CALENDAR = "no_calendar"
"""The named states of a budget figure — never a silent number (criterion K-08).

`NO_BUDGET` is "no budget row covers this month", which covers both "none was ever entered" and
"the last window has expired" (ADR-0008, addendum SC-3-03, points 7 and 10b). `NO_CALENDAR` is the
state the capacity already has one module over, repeated here rather than invented: a location with
no calendar has no budget either, and the addendum (point 3a) requires that to be **one** named
state to show and not two independent ones.

Spelled as constants because the API schema declares them as a `Literal` and the two must not drift
apart."""

NO_STATUTORY_LEAVE_TYPE = "no_statutory_leave_type"
"""The named state for "no absence type carries the flag" (ADR-0008, addendum SC-3-03, point 8b).

The half of the rule an index cannot enforce. The answer is that the type was not named — never the
first type alphabetically, never one whose name contains "leave", never a silent `0` and never a
`500`. Guessing would be a second resolution mechanism next to the flag, which is the thing rule 13
of the Invariant Guardian exists to prevent.

**It is a state of the capacity as much as of the catalogue** (reviewer R-02). Until SC-3-03's
review it was emitted only by `GET /catalog/absence-budgets`, while the capacity grid quietly
applied the whole entitlement on top of the booked leave it could not identify — 46 days a year
where the regulation grants 26. A freshly migrated database is in exactly that state, because the
flag defaults to `false` on every row and nothing prompts an administrator to set it, so the silent
version of this answer was the *default* one."""


@dataclass(frozen=True)
class StatutoryLeaveType:
    """The one absence type budgets settle against, as the calculation needs it.

    A value object rather than the ORM row, for the reason `CalendarBasis` is one: the same shape
    has to be producible from the live dictionary and from an approval snapshot, and a consumer
    taking an ORM row would have to be rewritten the day it reads the snapshot instead.

    The two commercial flags are carried, **read from this row and never copied onto the budget**
    (criterion K-04): "what does a day of this budget cost, and is it billable" is a property of the
    type, and a literal on the budget row would stop following the type the day somebody edits it.
    """

    absence_type_id: uuid.UUID
    name: str
    generates_cost: bool
    generates_revenue: bool


@dataclass(frozen=True)
class AbsenceBudget:
    """One budget row as the formula needs it: days, a closed window and where the number came from.

    `effective_to` is **inclusive** and never `None` — the database refuses an open-ended window on
    this table (ADR-0008, addendum SC-3-03, point 10b), and this type states that in its own
    signature rather than carrying a case the data cannot produce.
    """

    budget_id: uuid.UUID
    calendar_id: uuid.UUID
    engagement_type_id: uuid.UUID
    budget_days: Decimal
    unit: str
    source: str
    effective_from: date
    effective_to: date

    @property
    def months_in_window(self) -> int:
        """How many whole months the window spans — the denominator of the proration.

        A count, not an interpretation: the database refuses a window that does not start on the
        first of a month and end on the last day of one
        (`app.models.catalog.BUDGET_WINDOW_MONTH_ALIGNED_EXPRESSION`), so there is no partial month
        for two readers to count differently. That is what makes the invariant of ADR-0008's
        addendum (point 10a) exact rather than approximate: the shares of every month of a window
        add back up to `budget_days`, with no day gained or lost on the way.

        Always at least 1 — the same CHECK plus `effective_to >= effective_from` make a window
        shorter than a month impossible, so this can never be a zero denominator.
        """
        return (
            (self.effective_to.year - self.effective_from.year) * 12
            + (self.effective_to.month - self.effective_from.month)
            + 1
        )

    def months(self) -> Iterable[date]:
        """Every month of the window, as its first day — the grid the proration divides across.

        Used by the tests that assert the sum invariant and by any reader that needs the window's
        own months rather than the ones a scenario happens to plan for.
        """
        year, month = self.effective_from.year, self.effective_from.month
        for _ in range(self.months_in_window):
            yield date(year, month, 1)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    def month_index(self, period_month: date) -> int:
        """Where that month sits in the window, counting from `0` — the proration's position.

        Load-bearing since the remainder distribution below (S-01/R-05): the share of a month is a
        *difference of two cumulative figures*, so it depends on where the month is, not only on how
        many months there are. Raises for a month outside the window, because reaching this function
        with one means the resolution that picked this budget disagrees with the window on the row —
        and a silent `0` would quietly give that month the first month's share.
        """
        index = (period_month.year - self.effective_from.year) * 12 + (
            period_month.month - self.effective_from.month
        )
        if not 0 <= index < self.months_in_window:
            raise ValueError(
                f"{period_month.isoformat()} is outside the budget window "
                f"{self.effective_from.isoformat()}..{self.effective_to.isoformat()}"
            )
        return index


@dataclass(frozen=True)
class BudgetShare:
    """What one budget takes out of one month, and where that figure came from.

    `hours` is a `Decimal` when `state == RESOLVED` and the string `NOT_APPLICABLE` in every other
    state. Two fields rather than one nullable number, because "there is no budget here" and "the
    budget came out as zero" are two different answers and a single field would make them one
    (ADR-0008, addendum SC-3-03, point 7).

    **Hours, not days, and that is the figure the capacity actually subtracts.** Since the remainder
    distribution (S-01/R-05) the months of one window are not all equal to the cent, so "the share
    in
    days" is no longer a single number that multiplies out to what each month loses — reporting one
    would be reporting a figure nothing uses. The entitlement in *days* is still carried, as
    `budget_days`, where it belongs: on the description of the row.

    The source fields are not decoration: with only a monthly figure, a reader cannot tell a large
    deduction caused by a short window from one caused by a large entitlement — and the length of
    the window is precisely what criterion K-03's first contrast varies.
    """

    state: str
    hours: Decimal | str
    budget_id: uuid.UUID | None = None
    budget_days: Decimal | None = None
    unit: str | None = None
    source: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    months_in_window: int | None = None
    statutory_days_absorbed: int | None = None
    """How many manually booked days of the flagged type the window already holds — the `max`'s
    other operand, exposed so the "why did this figure move?" question is answerable without
    re-deriving it. It is a count over the **whole window**, not over this month, and it is in
    person-days for the whole position (see `month_budget_share`)."""

    entitlement_days: Decimal | None = None
    """`budget_days × headcount` — what the position is entitled to over the window (R-01).

    Carried beside `budget_days` rather than instead of it, because they answer two questions a
    reader needs separately: what the *regulation* grants one person, and what *this position*
    therefore holds. They differ by exactly the headcount, and a payload carrying only the first
    would make a five-person position look like it lost a fifth of what it did."""


def _allocated_hours(
    total_hours: Decimal, month_index: int, months_in_window: int
) -> Decimal:
    """This month's slice of `total_hours`, as a difference of two rounded cumulative figures.

    **The remainder distribution** (invariant-guardian S-01, reviewer R-05, decision of a human
    2026-09-22). The rule ADR-0008's addendum states is "budget ÷ months of the window", and the
    property it requires is stronger than the formula: *the shares of all the months of a window add
    back up to the budget* (point 10a). An even share rounded per month does not have that property
    the moment the division does not terminate — 26 days over 7 months at 7.50 hours is
    `27.857142… → 27.86` seven times over, i.e. `195.02` where the entitlement is `195.00`. Two
    cents
    of capacity conjured out of rounding, in a figure that multiplies by a rate.

    So each month gets `round(total × (i+1)/n) − round(total × i/n)`: a running total, rounded once
    per boundary. The sum telescopes to `round(total × n/n) − round(0) = total`, **exactly**, for
    any
    entitlement and any window length. No month is more than a cent from the even share, which is
    the property the simpler "the last month absorbs the remainder" does not have — there the whole
    accumulated error lands on one month, and at long windows that month is visibly different from
    its neighbours for a reason nobody can explain from the data.

    `total_hours` is expected to be already rounded (it is the window's whole deduction and a
    quantity in its own right); the arithmetic in between is exact `Decimal` and the only rounding
    is `round_money`, the project's single rule.
    """
    def cumulative(months: int) -> Decimal:
        return round_money(total_hours * Decimal(months) / Decimal(months_in_window))

    return cumulative(month_index + 1) - cumulative(month_index)


def month_budget_share(
    budget: AbsenceBudget | None,
    *,
    period_month: date | None = None,
    headcount: int = 1,
    standard_hours_per_day: Decimal | None = None,
    statutory_leave_named: bool = True,
    manual_statutory_days_in_window: int = 0,
    state: str = NO_BUDGET,
) -> BudgetShare:
    """The budget's share of one month — or a named state, with no number at all.

    Three ways this answers a state rather than a figure, and none of them is a zero:

    - **`budget is None`** — no row covers the month: none was entered for this pair, or every
      window has expired, or the position's location names no calendar (in which case the caller
      passes `state=NO_CALENDAR`, so the one state the answer shows is the calendar's — ADR-0008,
      addendum SC-3-03, point 3a);
    - **no absence type carries `is_statutory_leave`** (`statutory_leave_named=False`) — the budget
      exists and cannot be applied, because the `max` of point 9 has no second operand: nothing says
      *which* booked absences the entitlement already covers. The answer is
      `NO_STATUTORY_LEAVE_TYPE` and `"n/a"`, and the budget deducts **nothing** (reviewer R-02).
      Deducting it anyway is the double count that state exists to make visible — booked leave would
      be removed once as itself and once as the untouched entitlement, which is 46 days a year where
      the regulation grants 26. That is also the state a freshly migrated database is in: the flag
      defaults to `false` on every row and nothing asks an administrator to set it;
    - **the position has no calendar** — `state=NO_CALENDAR`, passed in.

    The arithmetic, in the mandated order (ADR-0008, addendum SC-3-03, points 9 and 10):

    1. `entitlement = budget_days × headcount` — the budget is a number of days **per FTE** (Issue
       #54, variant A), and a position of five people holds five times it (reviewer R-01). This is
       also what makes the comparison below dimensionally honest: booked absences are counted per
       *instance* and never deduplicated (SC-3-02, K-06), so their sum is already person-days for
       the
       whole position, and comparing it against a per-FTE entitlement would have been a comparison
       of
       two different units;
    2. `top_up = max(0, entitlement − manual_statutory_days_in_window)` — resolved over the **whole
       window**, because the manual rows and the budget are two sources of one quantity and the
       period of the comparison is the window the budget already has. `max(0, …)` is not decoration:
       without it an over-booked window produces a *negative* top-up, handing capacity back and
       pushing months with no absence in them above their gross figure;
    3. the top-up becomes hours (`× standard_hours_per_day`, rounded once) and is spread over the
       months of the window by `_allocated_hours`, which distributes the remainder so the window's
       shares sum to the entitlement exactly.

    Which month it is enters the formula only through that distribution — the share is even to
    within a cent, and the *reason* months differ is the booked absences, which SC-3-02 already
    deducts in the month they fall in. So the month holding booked leave loses that leave plus this
    share, and every other month loses this share alone.
    """
    if budget is None:
        return BudgetShare(state=state, hours=NOT_APPLICABLE)
    if not statutory_leave_named:
        return BudgetShare(state=NO_STATUTORY_LEAVE_TYPE, hours=NOT_APPLICABLE)
    if period_month is None or standard_hours_per_day is None:
        raise ValueError(
            "a resolved budget share needs the month it is for and the calendar's standard day: "
            "the share depends on where the month sits in the window (remainder distribution) and "
            "is reported in hours"
        )

    entitlement = budget.budget_days * Decimal(headcount)
    top_up = entitlement - Decimal(manual_statutory_days_in_window)
    total_hours = round_money(max(Decimal(0), top_up) * standard_hours_per_day)
    return BudgetShare(
        state=RESOLVED,
        hours=_allocated_hours(
            total_hours,
            budget.month_index(period_month),
            budget.months_in_window,
        ),
        budget_id=budget.budget_id,
        budget_days=budget.budget_days,
        entitlement_days=entitlement,
        unit=budget.unit,
        source=budget.source,
        effective_from=budget.effective_from,
        effective_to=budget.effective_to,
        months_in_window=budget.months_in_window,
        statutory_days_absorbed=manual_statutory_days_in_window,
    )


#
# **There is deliberately no `budget_for(month)` function here.** Which window covers a month is
# resolved by the database, with `valid_period @> :month` against the generated column
# (`app.data.absence_budget.budgets_for_months`) — the same expression the `EXCLUDE` constraint
# reads, so the two cannot disagree about where a window ends by a day (ADR-0008, point 3). A
# second, Python-side comparison here would be that second place, and it is also where criterion
# K-06's mutation lives: "the most recent window that has started" agrees with containment for every
# pair of adjacent windows and answers with an expired budget for a month in a gap between two.
