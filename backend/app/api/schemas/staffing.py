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
from typing import Annotated, Any, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.api.schemas.common import DecimalString
from app.models.staffing import HOURS_COLUMNS, HOURS_PRECISION, HOURS_SCALE

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


class StaffingPositionRead(BaseModel):
    """One staffing position with its whole monthly grid.

    The same shape from `GET`, from `POST` and from `PATCH`: a screen showing a grid has no second
    schema for "the grid after a write", and the token a client needs for its next edit is in the
    same place every time.
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
    moves when any month of the grid is edited — a false collision between two months of one
    position, accepted by name in that addendum, because the position is the unit of editing."""

    allocations: list[StaffingAllocation]


class StaffingPositionList(BaseModel):
    """An object, not a bare array — room for filtering or pagination later without breaking the
    contract, as `ProjectListResponse` and `CatalogRateList`. NF-03 (200 positions × 36 months) is
    not measured by SC-3-01; that is named in the plan entry, and this shape is what leaves room to
    answer it."""

    positions: list[StaffingPositionRead]
