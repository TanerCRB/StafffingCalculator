"""Request and response schemas for the scenario-staffing endpoints (F-04, SC-3-01).

Three boundary decisions are visible in the shapes below.

**No field carries a rate, a cost or a currency.** Not "removed for callers without the permission"
— absent from the schema, so there is nothing to gate (ADR-0005, addendum 2026-09-19, point 5). A
position is a dimension tuple, a headcount, a period and hours. The first task that does return a
resolved rate on a position (F-07, plan block 5) has to reinstate the SC-1-08 conjunction gate and
prove it with its own criterion; nothing here may be read as that gate already being in place.

**Hours cross the boundary as fixed-point strings** (`DecimalString`), never as JSON floats — the
same rule as for money, because hours are one multiplication away from it (NF-01, ADR-0002). The
bounds come from the column's own constants, so the boundary cannot drift away from the storage it
protects.

**Every rule stated here is also stated in the database.** This schema exists to turn a client's
mistake into a `422` naming the field instead of a `500` from a violated constraint; the guarantee
is the constraint (`app.models.staffing`), because a fixture, a seed script or a future import never
passes through this module. That division is the whole point of criterion K-04 — the schema is the
status code, not the rule.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.api.schemas.common import DecimalString, Iso4217Code
from app.core.money import NOT_APPLICABLE
from app.models.staffing import (
    COST_BASIS_FIXED_AMOUNT,
    COST_BASIS_WORKED_TIME,
    FIXED_AMOUNT_PRECISION,
    FIXED_AMOUNT_SCALE,
    HOURS_COLUMNS,
    HOURS_PRECISION,
    HOURS_SCALE,
)

HoursAmount = Annotated[
    DecimalString, Field(ge=0, max_digits=HOURS_PRECISION, decimal_places=HOURS_SCALE)
]
"""An hour figure on the way in: non-negative, and exactly as precise as `NUMERIC(10,2)` is.

`ge=0` and not `gt=0`: a month with zero hours is data — a gap in the middle of a position's period
is how a plan says "nobody works here that month" — while a negative figure is a sign error with no
meaning in any calculation. The precision bound is the lesson of R-05 (reviewer 2026-09-19) applied
one table over: without `decimal_places`, `7.125` would be accepted and stored as `7.13`, i.e.
silently rounded at write time, which is the one thing a *input* value must never be.

`max_digits` does the second half of that job: a figure with more integer digits than the column
can hold would otherwise reach PostgreSQL and return SQLSTATE `22003`, which is not one of the
refusal codes (`app.data.write_errors.REFUSAL_BY_SQLSTATE`) and therefore a `500`. Both halves are
asserted by `tests/test_staffing_hours_precision.py`; before that file they were a comment citing a
test in another table's suite (S-01, invariant-guardian 2026-09-19)."""

MAX_HEADCOUNT = 10_000
"""The largest `headcount` a request may name (R-04, reviewer 2026-09-19).

Not `2**31 - 1`, the column's own ceiling: the point of a bound at the boundary is to answer an
out-of-range request with a `422` naming the field instead of letting the database answer `22003`
and the API turn that into a `500`. Ten thousand people on **one** staffing position of one scenario
is already far past anything F-04 describes, and a number chosen at the limit of plausibility is a
number nobody has to revisit when a real plan grows — unlike `2**31 - 1`, which only moves the
defect instead of removing it.

Deliberately **not** mirrored as a CHECK constraint in the database. `headcount > 0` is in the
schema because zero people is a meaningless row whoever writes it; an upper limit is a plausibility
rule about API input, and a fixture or an import legitimately testing the column's range must not
have to argue with it."""

CostBasis = Literal["worked_time", "fixed_amount"]
"""The personnel-cost basis of a position (F-07, SC-5-03; ADR-0013, aneks 2026-09-25 SC-5-03) —
spelled here as a closed literal rather than imported as `COST_BASIS_VALUES`, the same convention
`CostType`/`FundingSource` (`app.api.schemas.additional_cost`) already use for a CHECK-backed
string column: the schema names the same two values the database's `cost_basis_known` CHECK does,
independently, so the two can be compared for drift rather than one silently defining the other."""

FixedAmount = Annotated[
    DecimalString, Field(gt=0, max_digits=FIXED_AMOUNT_PRECISION, decimal_places=FIXED_AMOUNT_SCALE)
]
"""A fixed personnel-cost amount on the way in: strictly positive (mirrors
`ck_staffing_position_fixed_amount_positive`) and exactly as precise as `NUMERIC(14,4)` is — the
same construction as `additional_cost.CostAmount`, so a fifth decimal place is a `422` naming the
field rather than a silent rounding at write time."""

MAX_ALLOCATION_MONTHS = 60
"""The most month rows one create request may carry — five years, per position (R-04).

A bound on work a client chooses the size of, not a statement about how long a position may run: the
position's own period (`start_date`/`end_date`) is unbounded, and a longer grid is written as more
than one request. NF-03 sizes a scenario at 36 months, so this leaves headroom over the requirement
while keeping the validator below, the insert and the response bounded per request."""


def _is_first_day_of_month(day: date) -> bool:
    """The API-side spelling of the database's `period_month = date_trunc('month', period_month)`.

    Two copies of one rule, and the division of labour between them is deliberate: this one produces
    a `422` that names the field, the constraint in the database is what actually holds (criterion
    K-04b). The day is *refused*, never truncated to the 1st — normalising input is how two
    spellings of one month end up indistinguishable in a column nobody can audit afterwards (the
    argument `Iso4217Code` makes for refusing `"eur"`).
    """
    return day.day == 1


class StaffingAllocationEntry(BaseModel):
    """One month of a position's grid, on the way **in** (nested in the create request).

    `extra="forbid"` here and not only on the enclosing model: Pydantic applies that setting per
    model and never down a tree, so a nested model without it re-opens the hole one level down — the
    reasoning `DeliveryPeriod` carries.
    """

    model_config = ConfigDict(extra="forbid")

    period_month: date
    availability_hours: HoursAmount
    planned_allocation_hours: HoursAmount
    billable_hours: HoursAmount
    """All three required, none defaulted. A default would be a fourth, invisible way for the three
    independent figures to end up equal to one another — which is exactly what criterion K-05 is
    about."""

    @model_validator(mode="after")
    def _period_month_is_the_first_of_a_month(self) -> Self:
        if not _is_first_day_of_month(self.period_month):
            raise ValueError(
                "period_month must be the first day of a month; monthly granularity means "
                f"{self.period_month.replace(day=1).isoformat()}, not "
                f"{self.period_month.isoformat()}"
            )
        return self


class StaffingPositionCreateRequest(BaseModel):
    """The body of `POST …/staffing-positions`: one position, with the months it plans for.

    The monthly grid is part of the create request rather than a second endpoint, and that is a
    decision with a guard behind it (see `app.data.staffing.create_position`): the month rows are
    inserted with the `position_id` of a position whose `INSERT` carried the `approved` guard, so
    they inherit that refusal structurally. A separate "add a month" endpoint would be a third write
    path and would need a third run of that guard — which SC-3-01 does not have, so the endpoint
    does not exist either. `allocations` may be empty: a position with no months yet is a legal
    draft state.
    """

    model_config = ConfigDict(extra="forbid")

    role_id: uuid.UUID
    seniority_id: uuid.UUID
    location_id: uuid.UUID
    engagement_type_id: uuid.UUID
    """The four business dimensions of the catalogue tuple, all required. Validated as *identifiers*
    only — that they name existing catalogue rows is decided by the foreign keys in the database,
    which is the path a client cannot go around (criterion K-01's contrast).

    **Not, since SC-2-03, the full key of a rate's `EXCLUDE` constraint** — that key has a fifth
    element, `vendor_id` (`app.models.catalog.RATE_EXCLUDE_KEY`), absent from this schema because
    nothing here resolves a rate. A future consumer that reads a rate for this position's tuple
    (Issue #9) must decide `vendor_id` itself; `app.data.catalog.resolve_rate` takes it as a
    required keyword argument with no default for exactly that reason (R-03)."""

    headcount: Annotated[int, Field(gt=0, le=MAX_HEADCOUNT)]
    """How many people the position plans for. The hours below are the total for the position, not
    per head (gate-1 decision 1) — a decision nothing in this task can falsify, because no consumer
    reads the two together yet.

    Bounded **above** as well as below (R-04, reviewer 2026-09-19): `headcount` lands in an
    `INTEGER` column, so a value past `2**31 - 1` used to reach PostgreSQL and come back as `22003`
    `numeric_field_overflow` — a code deliberately absent from
    `app.data.write_errors.REFUSAL_BY_SQLSTATE`, hence served as a `500`. "Something broke" was the
    wrong answer to a request that is simply out of range; see `MAX_HEADCOUNT`."""

    start_date: date
    end_date: date | None = None

    allocations: Annotated[
        list[StaffingAllocationEntry], Field(max_length=MAX_ALLOCATION_MONTHS)
    ] = Field(default_factory=list)
    """The months this request writes, bounded in length (R-04). Unbounded, a body naming tens of
    thousands of months occupied a worker for the whole of `_months_are_distinct` before any of it
    reached the database — the request was never going to be accepted, and the cost of refusing it
    was paid in latency for everybody else. Pydantic refuses the length before the validator below
    ever runs, so the bound is what makes the work bounded; the validator's complexity is a second,
    independent fix (see there)."""

    cost_basis: CostBasis = COST_BASIS_WORKED_TIME
    """The personnel-cost basis this position plans against (F-07, SC-5-03). Defaults to
    `worked_time`, so a body written before this task exists still creates exactly the position it
    always did (K-02)."""

    fixed_amount: FixedAmount | None = None
    fixed_amount_currency: Iso4217Code | None = None
    """The two fields `cost_basis = 'fixed_amount'` requires (ADR-0013, aneks 2026-09-25
    SC-5-03, Q1/Q2) — validated together below so a client's mistake is a `422` naming the
    field, not the database's `fixed_amount_required_for_its_basis` CHECK reached as a `409`.
    The guarantee stays the CHECK (criterion K-06); the schema only clarifies the error."""

    @model_validator(mode="after")
    def _fixed_amount_matches_its_basis(self) -> Self:
        if self.cost_basis == COST_BASIS_FIXED_AMOUNT:
            missing = [
                name
                for name in ("fixed_amount", "fixed_amount_currency")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(
                    "cost_basis 'fixed_amount' needs both fixed_amount and fixed_amount_currency: "
                    "missing " + ", ".join(missing)
                )
        elif self.fixed_amount is not None or self.fixed_amount_currency is not None:
            raise ValueError(
                "fixed_amount and fixed_amount_currency may only be set together with "
                "cost_basis 'fixed_amount'"
            )
        return self

    @model_validator(mode="after")
    def _period_is_ordered(self) -> Self:
        """Same boundary check as on a project and on a rate window; the database's
        `position_period_ordered` stays the guarantee, this is the error message."""
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must not be earlier than start_date")
        return self

    @model_validator(mode="after")
    def _months_are_distinct(self) -> Self:
        """One row per month in one request — `UNIQUE (position_id, period_month)` would otherwise
        refuse the whole insert with a `409` about a row the client cannot see, for what is plainly
        a malformed body.

        One pass over the list, with two sets (R-04): the first version asked `months.count(month)`
        *inside* a comprehension over the same list, i.e. O(n²) work on input a client chooses the
        size of. `max_length` on the field now bounds `n`, so the quadratic form would no longer be
        exploitable — but a bound and an algorithm are two independent statements, and this one
        holds whatever the bound later becomes."""
        seen: set[date] = set()
        repeated: set[date] = set()
        for entry in self.allocations:
            if entry.period_month in seen:
                repeated.add(entry.period_month)
            seen.add(entry.period_month)
        duplicated = sorted(month.isoformat() for month in repeated)
        if duplicated:
            raise ValueError(
                "allocations must name each month at most once; repeated: "
                + ", ".join(duplicated)
            )
        return self


class StaffingAllocationEditRequest(BaseModel):
    """The body of `PATCH …/allocations/{period_month}`: the figures to change, plus the token.

    Partial by design, exactly like `ProjectEditRequest`: "which fields did the caller send" is
    answered by `model_fields_set`, not by "which are not null". `period_month` is **not** a field
    here — it is the address of the row, and an editable one would be an undocumented way around
    `UNIQUE (position_id, period_month)`.
    """

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime
    """The **position's** `updated_at`, as returned by the read this edit is based on (ADR-0007,
    addendum 2026-09-19 — the token is the position's, not the month row's and not the scenario's).
    Required: an edit without a token is not an edit that skips the check, it is a malformed
    request. Must carry an offset — a naive timestamp compared against a `timestamptz` column is a
    guess about which clock the client meant."""

    availability_hours: HoursAmount | None = None
    planned_allocation_hours: HoursAmount | None = None
    billable_hours: HoursAmount | None = None

    @model_validator(mode="after")
    def _at_least_one_field_and_none_of_them_null(self) -> Self:
        """The two refusals that would otherwise be a 500 or a silent no-op (as on a project edit):
        an explicit `null` would reach the `UPDATE` as `NULL` against a `NOT NULL` column, and a
        body carrying only the token asks for no change while still rotating everyone's token."""
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        if not changed:
            raise ValueError("An edit must name at least one hours field to change.")
        nulled = sorted(field for field in changed if getattr(self, field) is None)
        if nulled:
            raise ValueError(f"These fields cannot be set to null: {', '.join(nulled)}")
        return self

    def changes(self) -> dict[str, Any]:
        """The requested changes as column names → values, in declaration order.

        Declaration order rather than set iteration order, so the generated `UPDATE` is the same for
        the same request every time.
        """
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        return {field: getattr(self, field) for field in HOURS_COLUMNS if field in changed}


POSITION_COST_BASIS_FIELDS: tuple[str, ...] = (
    "cost_basis",
    "fixed_amount",
    "fixed_amount_currency",
)


class StaffingPositionCostBasisEditRequest(BaseModel):
    """The body of `PATCH …/staffing-positions/{position_id}`: the personnel-cost basis to change,
    plus the position's token (F-07, SC-5-03; ADR-0013, aneks 2026-09-25 SC-5-03).

    Partial, like `StaffingAllocationEditRequest`, and edits the same row that edit does — through a
    **different** endpoint, because the allocation edit addresses a month (a different row) while
    this one addresses the position itself. `role_id`/`headcount`/the dates have no edit path yet
    (out of scope of SC-5-03) and are not fields here.
    """

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime
    """The **position's** `updated_at` (ADR-0007, addendum 2026-09-19 — the token is the position's,
    the same one the allocation and absence edits already share)."""

    cost_basis: CostBasis | None = None
    fixed_amount: FixedAmount | None = None
    fixed_amount_currency: Iso4217Code | None = None
    """`fixed_amount`/`fixed_amount_currency` accept an explicit `null` **only** to clear them when
    switching `cost_basis` back to `worked_time` in the same request — every other field on this
    schema forbids `null` for the reason `StaffingAllocationEditRequest` gives (an explicit `null`
    reaching the `UPDATE` as `NULL` against a column the caller did not mean to blank)."""

    @model_validator(mode="after")
    def _at_least_one_field_and_consistent_with_its_basis(self) -> Self:
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        if not changed:
            raise ValueError("An edit must name at least one field to change.")
        new_basis = self.cost_basis
        amount_given = "fixed_amount" in changed
        currency_given = "fixed_amount_currency" in changed
        if new_basis == COST_BASIS_FIXED_AMOUNT:
            missing = [
                name
                for name, given, value in (
                    ("fixed_amount", amount_given, self.fixed_amount),
                    ("fixed_amount_currency", currency_given, self.fixed_amount_currency),
                )
                if not given or value is None
            ]
            if missing:
                raise ValueError(
                    "switching cost_basis to 'fixed_amount' needs both fixed_amount and "
                    "fixed_amount_currency in the same request, both non-null: missing "
                    + ", ".join(missing)
                )
        elif new_basis == COST_BASIS_WORKED_TIME:
            if (amount_given and self.fixed_amount is not None) or (
                currency_given and self.fixed_amount_currency is not None
            ):
                raise ValueError(
                    "switching cost_basis to 'worked_time' cannot also set a non-null "
                    "fixed_amount/fixed_amount_currency in the same request; omit them or set "
                    "them to null"
                )
        elif amount_given != currency_given:
            raise ValueError(
                "fixed_amount and fixed_amount_currency must be changed together when "
                "cost_basis is not also changing"
            )
        # What this validator cannot refuse, having no view of the row: both fields named,
        # together, with cost_basis not also changing — legal when the position is already
        # 'fixed_amount' (an amount correction), illegal when it is still 'worked_time' (an amount
        # would land on the default basis, breaking the model's own invariant). That half of bramka
        # 1's Guardian finding is closed one layer down, in the same statement that writes the row
        # (`app.data.staffing.update_position_cost_basis`, `CostBasisMismatch`) — not here, because
        # a Pydantic validator has no session to ask.
        return self

    def changes(self) -> dict[str, Any]:
        """The requested changes, in declaration order — `None` included, for the two fields whose
        explicit `null` is meaningful (clearing them alongside a switch to `worked_time`)."""
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        return {
            field: getattr(self, field) for field in POSITION_COST_BASIS_FIELDS if field in changed
        }


NotApplicable = Literal[NOT_APPLICABLE]
"""`"n/a"` as a type — the project's one sentinel for a figure that cannot be computed.

Taken from `app.core.money`, not spelled again here: this repository already answers a margin with a
zero denominator that way (invariant-guardian rule 3, F-10, AC-05), and a second string meaning the
same thing would make a client need two branches for one idea. What is new in SC-3-02 is only the
*reason* — no calendar rather than no denominator."""

AbsenceBudgetState = Literal[
    "resolved", "no_budget", "no_statutory_leave_type", "no_calendar"
]
"""The named states of the leave budget behind a month's capacity (F-05, SC-3-03).

Four, and none of them is decoration:

- `"no_budget"` covers both "no budget has been entered for this pair" and "every window has
  expired" (ADR-0008, addendum SC-3-03, points 7 and 10b) — deliberately one answer, because both
  mean the same thing to a planner: this figure rests on nothing, go and enter a budget;
- `"no_statutory_leave_type"` means the budget exists and **was not applied**, because no absence
  type carries `is_statutory_leave` and so nothing says which booked leave the entitlement already
  covers (point 8b; reviewer R-02). It has to be visible *here*, on the capacity, and not only on
  the catalogue endpoint: the alternative is deducting the entitlement on top of leave that was
  already deducted — 46 days a year where the regulation grants 26 — with nothing in the payload
  saying so. It is also the state a freshly migrated database is in, because the flag defaults to
  `false` on every row and nothing prompts anyone to set it;
- `"no_calendar"` appears here as well as in `CapacityState` because a location with no calendar has
  no budget either, and the addendum (point 3a) requires that to be **one** named state a client
  shows rather than two independent ones.

The values come from `app.domain.absence_budget` and are not spelled again here."""

CapacityState = Literal["resolved", "no_calendar"]
"""The named states of a derived capacity, spelled as a closed set at the boundary.

The values come from `app.domain.capacity`, and the pair is what stops `derived_capacity_hours:
"n/a"` from being an ambiguous answer: `"no_calendar"` says *why*. Criterion K-23's two mutations
are exactly the two ways this field could be avoided — a silent `0.00` (no state at all) and a
`500` (no answer at all)."""


class DerivedCapacitySource(BaseModel):
    """Which calendar a derived capacity came from, and on what basis it was computed.

    Required by ADR-0008's addendum of 2026-09-22 (point 4) rather than added for convenience: the
    calendar carries no effective-date window, so a scenario pointing at a *stale* calendar and one
    pointing at the right calendar produce the same shape of answer and nothing would tell them
    apart. F-02 ("identify the source of each inherited or overridden value") is the requirement;
    this object is the answer.

    `working_days` and `absence_day_equivalents` are the two counts the figure is made of, so a
    reader can see *why* a capacity is what it is without re-deriving it — and so a test can tell a
    capacity that is low because of absences from one that is low because of a holiday.
    """

    calendar_id: uuid.UUID
    calendar_name: str
    standard_hours_per_day: DecimalString
    working_days: int
    absence_day_equivalents: int


class AbsenceBudgetSource(BaseModel):
    """Which budget row a month's deduction came from, and how it was spread (F-05, SC-3-03).

    The counterpart of `DerivedCapacitySource`, and required for the same reason: with only a
    monthly figure, a reader cannot tell a large deduction caused by a **short window** from one
    caused by a large entitlement — and the length of the window is exactly what criterion K-03's
    first contrast varies (26 days over twelve months and 26 over six are different answers from
    the same entitlement). F-02 ("identify the source of each inherited or overridden value") is the
    requirement; this object is the answer.

    `source` is the free text the budget row carries — where the number comes from, never who
    entered it (ADR-0008, addendum 2026-09-22 SC-3-03, point 6). It is **not** gated: a budget is
    organisational data, not a personnel cost (ADR-0005, addendum 2026-09-22 SC-3-03, point 3).
    """

    budget_id: uuid.UUID
    budget_days: DecimalString
    """What the regulation grants **one person** over the whole window, as stored — not the monthly
    share and not this position's total. `Decimal` across the boundary as a fixed-point string, like
    every other decimal in this API: these days multiply a standard working day and then a rate
    (NF-01, ADR-0002)."""

    entitlement_days: DecimalString
    """What **this position** holds over the window: `budget_days × headcount` (reviewer R-01).

    Carried beside `budget_days` rather than instead of it, because a reader needs both: a
    five-person position entitled to 130 days from a regulation granting 26 is two facts, and a
    payload showing only the first makes the deduction look four fifths too large — which is exactly
    the misreading that would have hidden the bug this field was added with."""

    unit: str
    source: str
    effective_from: date
    effective_to: date
    """Inclusive, as stored, and never `null`: this table refuses an open-ended window (ADR-0008,
    addendum SC-3-03, point 10b), so the field has no "open-ended" case for a client to render."""

    months_in_window: int
    statutory_days_absorbed: int
    """The two numbers the share is made of: the denominator of the proration, and how many
    person-days of booked statutory leave the window already holds. The second one is what makes the
    `max` rule visible — leave that fits inside the budget changes no capacity figure at all (point
    9c), and this field is where a screen can say so instead of leaving it to be discovered. It is
    counted per booked absence and never deduplicated (SC-3-02, K-06), i.e. in the same person-days
    as `entitlement_days` above."""


class StaffingAllocation(BaseModel):
    """One month of a position's grid, on the way **out**.

    No `updated_at`: the month row has no token of its own, and inventing one in the payload would
    advertise a concurrency granularity ADR-0007's addendum decided against.
    """

    id: uuid.UUID
    period_month: date
    availability_hours: DecimalString
    planned_allocation_hours: DecimalString
    billable_hours: DecimalString

    derived_capacity_hours: DecimalString | NotApplicable
    """What the calendar and the absences say this month's capacity is (F-05, SC-3-02).

    **Beside `availability_hours`, never instead of it** (criterion K-08). The typed figure above is
    an input a planner asserted; this one is derived, and the two are allowed to disagree — that
    disagreement is the information F-05 exists to surface. Nothing on the write path ever copies
    this value into the `availability_hours` column, and no `UPDATE` against
    `staffing_position_allocation` is issued by a read.

    `"n/a"` when the position's location names no calendar, together with
    `derived_capacity_state = "no_calendar"` below. Never `0.00` for that case: a zero is a number
    every later sum would add up, and this one would be wrong."""

    derived_capacity_state: CapacityState
    derived_capacity_source: DerivedCapacitySource | None = None
    """`None` exactly when the state is `"no_calendar"`, and never otherwise. Two fields rather than
    one nullable number, because "could not be computed" and "came out as zero" are two different
    answers and one field would make them one."""

    absence_budget_hours: DecimalString | NotApplicable
    """How many hours of this month's capacity the **leave budget** removed (F-05, SC-3-03).

    Already inside `derived_capacity_hours` above — this field says how much of the reduction was
    the budget rather than the absences actually booked, and it is the same figure the calculation
    subtracted, not a second computation of it.

    `"n/a"` whenever `absence_budget_state` is not `"resolved"`. Never `0.00` for a missing budget:
    a zero is a number every later sum adds up, and it would be indistinguishable from a budget of
    zero days — which is a legal row meaning "this engagement type has no entitlement" (ADR-0008,
    addendum 2026-09-22 SC-3-03, point 7)."""

    absence_budget_state: AbsenceBudgetState
    absence_budget_source: AbsenceBudgetSource | None = None
    """`None` exactly when the state is not `"resolved"`. The same two-field shape as the derived
    capacity above, for the same reason: "there is no budget here" and "the budget came out as
    zero" must not be one answer."""


class StaffingAbsence(BaseModel):
    """One planned absence of one position, on the way **out** (F-05, SC-3-02).

    **Four fields, and the ones that are missing are the decision.** There is no person, no note, no
    justification and no comment — not "removed for callers without a permission", but absent from
    the schema because absent from the table (ADR-0005, addendum 2026-09-22, point 6). An absence
    hangs on an anonymous position and names its kind only through the dictionary.

    No `updated_at`: the token is the position's (ADR-0007, addendum 2026-09-22, point 1), and it is
    carried once, on `StaffingPositionRead` below. A token here would advertise a concurrency
    granularity that does not exist.
    """

    id: uuid.UUID
    absence_type_id: uuid.UUID
    start_date: date
    end_date: date
    """Inclusive at both ends, as stored. Nothing on this path adds or subtracts a day — the
    intersection with the calendar's working days happens in `app.domain.capacity`, once."""


class StaffingAbsenceList(BaseModel):
    """An object, not a bare array — the same contract room `StaffingPositionList` keeps."""

    absences: list[StaffingAbsence]


class StaffingAbsenceCreateRequest(BaseModel):
    """The body of `POST …/staffing-positions/{position_id}/absences`.

    `extra="forbid"`, and here it is doing real work rather than tidying up: a body carrying
    `person`, `employee`, `note` or `justification` is refused with a `422` naming the field instead
    of being silently dropped. The personal-data boundary of ADR-0005's addendum (point 6) is
    structural in the table; this is the boundary saying so out loud to whoever tries.
    """

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime
    """The **position's** token, as returned by the read this write is based on (ADR-0007, addendum
    2026-09-22, point 1 — the aggregate's token, not the absence's). Required, and required with an
    offset, for the reasons `StaffingAllocationEditRequest.updated_at` gives: a write without a
    token is a malformed request, not a write that skips the check."""

    absence_type_id: uuid.UUID
    """From the dictionary, and validated as an *identifier* only: that it names an existing type is
    decided by the foreign key in the database, which is the path a client cannot go around."""

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _period_is_ordered(self) -> Self:
        """Same division of labour as everywhere else: a `422` naming the field here, and
        `ck_staffing_position_absence_absence_period_ordered` in the database as the guarantee.

        An inverted range is not merely invalid — it intersects no calendar day at all, so without
        the refusal it would be an absence that silently consumes nothing.
        """
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be earlier than start_date")
        return self


class StaffingAbsenceDeleteRequest(BaseModel):
    """The body of `DELETE …/absences/{absence_id}`: the position's concurrency token.

    **A `DELETE` with a body**, deliberately. The alternative spellings were an `If-Match` header
    (a second place where this API would carry a concurrency token, when every other write carries
    it in the body) and no token at all (a delete that cannot lose a race it is in — ADR-0007
    applies to removing a row exactly as it applies to changing one). One vocabulary, in one place,
    for all three writes of this aggregate.
    """

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime


class StaffingPositionRead(BaseModel):
    """One staffing position with its whole monthly grid and its absences.

    The same shape from `GET`, from `POST`, from `PATCH` and from `DELETE`: a screen showing a grid
    has no second schema for "the grid after a write", and the token a client needs for its next
    edit is in the same place every time.
    """

    id: uuid.UUID

    role_id: uuid.UUID
    seniority_id: uuid.UUID
    location_id: uuid.UUID
    engagement_type_id: uuid.UUID

    headcount: int
    start_date: date
    end_date: date | None = None

    updated_at: datetime
    """ADR-0007's concurrency token for this position *and its months* (addendum 2026-09-19). It
    moves when any month of the grid is edited — and, since SC-3-02, when an absence is added or
    removed (ADR-0007, addendum 2026-09-22, point 2). Both are false collisions accepted by name in
    those addenda, because the position is the unit of editing."""

    allocations: list[StaffingAllocation]
    absences: list[StaffingAbsence]
    """The position's planned absences (F-05), on every representation of the position. On the list
    as well as on the single-position payload, for the reason `DimensionEntry.updated_at` gives: a
    field absent from the only read a client performs is a field no client can use."""


class StaffingPositionList(BaseModel):
    """An object, not a bare array — the room `ProjectListResponse` and `CatalogRateList` already
    keep. SC-3-01 named it "room for filtering or pagination later without breaking the contract";
    SC-3-05 (ADR-0017) is that later, and `total` is the field the room was for."""

    positions: list[StaffingPositionRead]
    total: int
    """The count of every position of this scenario, before `limit`/`offset` are applied — never
    `len(positions)`, which is only true while the whole grid fits in one page (K-02, K-04).
    Computed in the same read as the page (`app.data.staffing.list_positions`), never a second
    query that could disagree with it (ADR-0017 point 5). A client compares the two to know whether
    it is holding the whole grid or page one of more — and, without any `limit`/`offset` named at
    all, the two are always equal (K-01): the default answer is still the whole grid, exactly as
    before this task."""
