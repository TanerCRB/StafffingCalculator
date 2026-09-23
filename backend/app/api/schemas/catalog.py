"""Request and response schemas for the catalogue endpoints (F-03, SC-2-01).

Two boundary decisions are visible in the shapes below.

**`default_cost_rate` is `DecimalString | None`, on the same model as everything else.** There is no
`CatalogRateWithoutCost` variant and no second endpoint: denying the cost rate is the removal of a
*field* from a row that is otherwise fully present, answered with `200` (ADR-0005, addendum
2026-09-19, point 5). A separate restricted model would make "you may not see this cost" and "this
tuple has no rate" the same payload. The removal itself happens in
`app.api.response_shaping.shape_catalog_rate`, before serialisation — never in the frontend.

**`effective_to` is inclusive here.** "Valid until 31 December" means the 31st is covered; the
conversion to PostgreSQL's half-open form happens only inside the generated `valid_period` column
(ADR-0008, point 3). Nothing in this module adds or subtracts a day.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.api.schemas.common import DecimalString, Iso4217Code, NonEmptyName
from app.core.money import NOT_APPLICABLE
from app.models.catalog import (
    BUDGET_PRECISION,
    BUDGET_SCALE,
    BUDGET_UNIT_DAY,
    RATE_PRECISION,
    RATE_SCALE,
    RATE_UNIT_HOUR,
)

NotApplicableValue = Literal[NOT_APPLICABLE]
"""`"n/a"` as a type — the project's one sentinel for a figure that cannot be computed.

Taken from `app.core.money` rather than spelled again, exactly as `app.api.schemas.staffing` takes
it: this repository already answers a margin with a zero denominator that way (invariant-guardian
rule 3, F-10, AC-05), and a second string meaning the same thing would make a client need two
branches for one idea."""

RateAmount = Annotated[
    DecimalString, Field(ge=0, max_digits=RATE_PRECISION, decimal_places=RATE_SCALE)
]
"""A rate amount on the way in: non-negative, and **exactly** as precise as `NUMERIC(14,4)` is.

`max_digits`/`decimal_places` are taken from the column's own constants, so the boundary cannot
drift away from the storage it is protecting. The point is not tidiness (R-05, reviewer
2026-09-19): `123.45678` used to be accepted and stored as `123.4568`, i.e. **silently rounded at
write time** — the one thing ADR-0008 point 6 and `create_rate`'s own docstring promise does not
happen. A rate is input; a request whose precision cannot be kept is a request to be refused, not
to be adjusted.

The pair also closes half of R-01: an amount wider than the column (`22003
numeric_field_overflow`, which used to surface as a `409` claiming an overlap) is now a `422` naming
the field. Trailing zeros are not precision — Pydantic accepts `100.00000`, because that value *is*
representable at scale 4."""

LATEST_PLANNING_DATE = date(2999, 12, 31)
"""The far end of a date a rate window may name — see `_dates_are_inside_the_planning_horizon`.

Not `date.max`: `valid_period` adds a day to `effective_to`, so `9999-12-31` asks for a date outside
the range the driver can carry (R-01). Not "today + N years" either — that would make the same
request valid or invalid depending on when it is sent, which is the class of thing an injected time
source exists to prevent."""

RateUnit = str
"""The unit as it crosses the boundary: a plain string, validated against `RATE_UNIT_HOUR` below.

Deliberately not `Literal["hour"]`: F-07 adds daily and monthly units, and the request schema is not
where that list belongs (the database CHECK constraint is — see `app.models.catalog`). A `Literal`
here would also make it tempting to treat the schema as the guarantee, which criterion K-07 exists
to refute."""


class DimensionEntry(BaseModel):
    """One row of one dimension dictionary. No `kind` field: the kind is the endpoint's path."""

    id: uuid.UUID
    name: str

    updated_at: datetime
    """ADR-0007's concurrency marker, carried on **every** read of this row and required back on an
    edit (SC-2-04).

    On the list representation as well as on the single-entry one, unlike `ProjectDetail`, which
    carries it while `ProjectListItem` does not: a dictionary has no detail endpoint to read one
    entry from, so a marker absent here would be a marker no client could ever obtain — and the
    only way left to edit would be to send one the client made up, which the database would refuse
    every time."""


class DimensionEntryList(BaseModel):
    """An object, not a bare array — room for filtering/pagination metadata later without breaking
    the contract, exactly as `ProjectListResponse`."""

    entries: list[DimensionEntry]


class DimensionEntryCreateRequest(BaseModel):
    """The body of `POST /catalog/dimensions/{dimension}`.

    `extra="forbid"`, as everywhere on the way in: an unknown key is a 422 rather than a silently
    dropped field, so a body trying to smuggle an `id`, a rate or a permission fails loudly. The
    dimension itself is a path segment and cannot be named in the body.
    """

    model_config = ConfigDict(extra="forbid")

    name: NonEmptyName


class DimensionEntryEditRequest(BaseModel):
    """The body of `PATCH /catalog/dimensions/{dimension}/{entry_id}` (SC-2-04).

    `extra="forbid"`, as on every request model here: an unknown key is a `422` rather than a
    silently dropped field, so a body trying to smuggle an `id`, a `created_at` or a different
    dimension fails loudly instead of looking like it worked.
    """

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime
    """The marker returned by the read this edit is based on (ADR-0007). **Required** — an edit
    without one is not "an edit that skips the check", it is a malformed request, and accepting it
    would make the protection opt-in for whoever forgets. Must carry an offset: a naive timestamp
    compared against a `timestamptz` column is a guess about which clock the client meant."""

    name: NonEmptyName
    """The only editable field a dictionary entry has — it *is* its name.

    Required here rather than optional-and-partial like the rate fields below: with one editable
    field, "partial" and "complete" are the same request, and an optional field would only add the
    case of a body that carries nothing but the marker — an edit that changes nothing while moving
    the marker and invalidating every other client's copy of it."""


class CatalogRate(BaseModel):
    """One default rate row for one dimension tuple over one effective window."""

    id: uuid.UUID

    role_id: uuid.UUID
    seniority_id: uuid.UUID
    location_id: uuid.UUID
    engagement_type_id: uuid.UUID

    vendor_id: uuid.UUID | None = None
    """The subcontractor this price belongs to, or `null` for the organisation's own rate
    (SC-2-03).

    `null` is a **named state**, not a missing value, and the two are spelled differently on
    purpose: `default_cost_rate: null` means "removed for this caller", while `vendor_id: null`
    means "internal" and is the same answer for every caller. A client renders it as a named state
    ("Internal"), never as an empty cell shared with other absences (criterion K-09).

    Not gated by anything: `CATALOG_READ` covers every vendor's price list, a business decision
    taken explicitly at gate 1 and recorded in ADR-0005's addendum of 2026-09-21, point 2."""

    default_cost_rate: DecimalString | None = None
    """`None` means "removed for this caller", and it is the only reason it can be `None`: the
    column is `NOT NULL`. A caller without `PERSONNEL_COSTS_READ` receives the row with this field
    emptied and every other field intact — including `default_selling_rate` (ADR-0005, addendum
    2026-09-19, points 3 and 5)."""

    default_selling_rate: DecimalString
    currency: str
    unit: str

    effective_from: date
    effective_to: date | None = None
    """Inclusive, and `None` for an open-ended window (ADR-0008, point 2) — not a `9999-12-31`
    sentinel a client would have to recognise."""

    updated_at: datetime
    """ADR-0007's concurrency marker (SC-2-04): carried on every read, required on every edit.

    Never gated: it is a timestamp of the row, not a fact about a person or a cost, and a caller
    without `PERSONNEL_COSTS_READ` needs it for exactly the same reason as anybody else — to edit
    the fields they *can* see without overwriting somebody else's change."""


class CatalogRateList(BaseModel):
    rates: list[CatalogRate]
    total: int
    """The count of every row matching the request's filter (`on_date`), without the page limit
    applied — never `len(rates)`, which is only true while the catalogue fits in one page (K-11).
    A client compares the two to know whether it is holding everything or page one of more."""


class CatalogRateCreateRequest(BaseModel):
    """The body of `POST /catalog/rates`.

    `extra="forbid"` for the same reason as on every other request model here, and one specific to
    this one: `valid_period` is a generated column, so a body naming it must fail rather than look
    like it set the window.
    """

    model_config = ConfigDict(extra="forbid")

    role_id: uuid.UUID
    seniority_id: uuid.UUID
    location_id: uuid.UUID
    engagement_type_id: uuid.UUID

    vendor_id: uuid.UUID | None = None
    """Optional on the way in, and its absence means "internal" — the same reading as everywhere
    else (SC-2-03). The reference itself is guaranteed by the database
    (`fk_catalog_default_rates_vendor_id`), not by this schema: a vendor that does not exist is a
    `409`, because a rate naming a vendor nobody created is a price belonging to nobody (K-07)."""

    default_cost_rate: RateAmount
    default_selling_rate: RateAmount
    """`Decimal`, never `float` (ADR-0002) — the request side too, not only the response: a
    `float` field would round the value before it ever reached the `NUMERIC(14,4)` column.
    `ge=0`: a negative rate is not a discount, it is a sign error, and the calculation layer has
    no meaning for it. Nothing here rounds — the scale of the column is larger than the
    currency's minor unit on purpose (ADR-0008, point 6), and rounding belongs to the consumer
    (`round_money`). See `RateAmount` for why the precision is bounded here rather than left to
    the column."""

    currency: Iso4217Code
    unit: RateUnit = RATE_UNIT_HOUR

    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def _unit_is_accepted(self) -> Self:
        """Reject an unsupported unit at the boundary so the caller gets a 422 instead of the 500 a
        violated `unit_is_hour` CHECK constraint would produce.

        The constraint in the database stays the actual guarantee (K-07 asks about the path that
        never sees this schema) — this is the status code, not the rule.
        """
        if self.unit != RATE_UNIT_HOUR:
            raise ValueError(f"unit must be {RATE_UNIT_HOUR!r}")
        return self

    @model_validator(mode="after")
    def _effective_period_is_ordered(self) -> Self:
        """Same division of labour for the window: 422 here, `effective_period_ordered` in the
        database. An inverted window is not merely invalid — it generates an *empty* `daterange`,
        which `&&` never matches, so it would be a row the overlap constraint cannot see."""
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be earlier than effective_from")
        return self

    @model_validator(mode="after")
    def _dates_are_inside_the_planning_horizon(self) -> Self:
        """Refuse a date past `LATEST_PLANNING_DATE`, and say what to use instead.

        Two reasons, and the second one is a measured defect (R-01, reviewer 2026-09-19):

        1. A rate "valid until 31 December 9999" is not a plan, it is a sentinel — and this schema
           already has a way to say open-ended: `effective_to: null` (ADR-0008, point 2). Accepting
           both spellings would mean two representations of one fact, one of which no query treats
           as unbounded.
        2. `valid_period` is generated as `daterange(effective_from, effective_to + 1, '[)')`, so
           `effective_to = 9999-12-31` asks PostgreSQL for `10000-01-01` — a date the driver cannot
           carry back, which surfaced as a `DataError` and, before the SQLSTATE classification, as a
           `409` blaming an overlap that had not happened.

        A horizon rather than an exact exclusion of `9999-12-31`: the nearest defensible boundary is
        "a date a human could be planning for", and it makes the refusal a statement about the input
        instead of a workaround for one value the encoding cannot express.
        """
        too_far = sorted(
            name
            for name, value in (
                ("effective_from", self.effective_from),
                ("effective_to", self.effective_to),
            )
            if value is not None and value > LATEST_PLANNING_DATE
        )
        if too_far:
            raise ValueError(
                f"{', '.join(too_far)} must not be later than {LATEST_PLANNING_DATE.isoformat()}; "
                "an open-ended window is expressed by effective_to = null, not by a far-future date"
            )
        return self


# --- working calendars and absence types (F-05, SC-3-02) -----------------------------------------
#
# Two read-only payloads, and they are **not** `DimensionEntry` (ADR-0005, addendum 2026-09-22,
# point 3). The permission pair is the same one — `CATALOG_READ`/`CATALOG_WRITE`, no new permission,
# "the sixth and seventh dictionary, not a sixth mechanism". What is not the same is the *shape*: a
# calendar carries a standard working day, a week pattern and a set of days, and an absence type
# carries two flags. SC-2-03's gate-1 decision about vendors named exactly this condition — "the
# first attribute beyond `name` takes a dictionary out of the shared endpoints" — so these two have
# their own payloads and their own routes rather than being squeezed through
# `app.data.catalog.DIMENSION_MODELS`, where every column past `name` would be silently dropped on
# read and silently unset on write.


class WorkingCalendarDayEntry(BaseModel):
    """One exceptional day of one calendar: the date and what the calendar says about it."""

    day: date
    kind: str
    """`"non_working"` (a holiday the week pattern would have made a working day) or `"working"`
    (an extra working day the pattern would not have). A plain string validated by the database's
    enum type, not a `Literal` here, for the reason `RateUnit` gives: the request schema is not
    where the list of values belongs, and a `Literal` invites treating the schema as the
    guarantee."""


class WorkingCalendarEntry(BaseModel):
    """One working calendar with its basis and its exceptional days (F-05, SC-3-02).

    `standard_hours_per_day` crosses the boundary as a fixed-point string like every other decimal
    in this API (ADR-0002): hours are one multiplication away from money, and a JSON float would
    lose precisely the precision the `NUMERIC(4,2)` column keeps.

    **No effective-date window on the basis**, and the payload has no place for one on purpose
    (ADR-0008, addendum 2026-09-22): the unit of versioning is the calendar. A client showing "valid
    from" next to this figure would be showing a field nothing decides.
    """

    id: uuid.UUID
    name: str
    standard_hours_per_day: DecimalString
    week_pattern: str
    """Seven characters, Monday first, `'1'` for a working day — `'1111100'` is a Monday-to-Friday
    week, `'1111110'` includes Saturday. A string rather than a list of booleans because it is one
    value of the row, and because it is legible in a log line."""

    days: list[WorkingCalendarDayEntry]
    updated_at: datetime
    """ADR-0007's marker, carried although SC-3-02 ships no edit form for a calendar (addendum
    2026-09-22, point 3): the marker is on the table from its creation, and a read that dropped it
    would be the read from which a later form could not be built."""


class WorkingCalendarList(BaseModel):
    calendars: list[WorkingCalendarEntry]


BudgetDays = Annotated[
    DecimalString, Field(ge=0, max_digits=BUDGET_PRECISION, decimal_places=BUDGET_SCALE)
]
"""A number of budgeted days on the way in: non-negative, and exactly as precise as `NUMERIC(6,2)`.

`ge=0` and not `gt=0`: zero days is a legal, meaningful row — an engagement type with no leave
entitlement — and it stays distinguishable from *no row*, which is the named "no budget" state
(ADR-0008, addendum 2026-09-22 SC-3-03, point 7). A negative entitlement is a sign error with no
meaning in any calculation.

`max_digits`/`decimal_places` come from the column's own constants, so the boundary cannot drift
away from the storage it protects — the R-05 lesson applied one table over: without
`decimal_places`, `26.125` would be accepted and stored as `26.13`, i.e. silently rounded at write
time, which is the one thing an *input* value must never be."""

BudgetSourceText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
"""Where the number comes from — mandatory, non-blank, and bounded in length.

**Not `NonEmptyName`**, although the rules look alike: a source is a sentence ("Staff regulations
§12, 2026 edition"), not a name, and it is stored in a `varchar(500)` rather than a `varchar(200)`.
Sharing the alias would tie the two to one bound and make the next change to either move both.

The **content** of this field is otherwise unconstrained, and that is a decision this task could not
complete on its own — see `app.api.catalog.create_absence_budget`, which names what was decided here
and what is still open (ADR-0005, addendum 2026-09-22 SC-3-03, point 6)."""


class AbsenceTypeEntry(BaseModel):
    """One absence type: a name and its two independent flags (F-05, SC-3-02).

    **Neither flag is gated and neither is a cost.** They say whether time booked against this type
    still costs the organisation money and whether it is still billable — configuration, not an
    amount — so the personnel-cost gate of SC-1-08 is not activated by this payload and none is
    applied (ADR-0005, addendum 2026-09-22, point 7). The first response that carries the *cost of
    an absence* (F-07) has to reinstate the conjunction and prove it with a criterion of its own.
    """

    id: uuid.UUID
    name: str
    generates_cost: bool
    generates_revenue: bool

    is_statutory_leave: bool
    """Whether this is the type the leave **budget** settles against (F-05, SC-3-03).

    At most one type in the whole dictionary carries it, and the database is what says so
    (`app.models.catalog.STATUTORY_LEAVE_UNIQUE_INDEX`). A `bool` on every row rather than a single
    "which type is it" field somewhere else: the flag *is* on the row, and a client showing the
    dictionary has to be able to show which entry carries it."""

    updated_at: datetime


class AbsenceTypeList(BaseModel):
    absence_types: list[AbsenceTypeEntry]


# --- the absence budget (F-05, SC-3-03) ----------------------------------------------------------
#
# The eighth catalogue table, gated by `CATALOG_READ`/`CATALOG_WRITE` and by nothing else (ADR-0005,
# addendum 2026-09-22 SC-3-03, points 2-3). **No field here is a personnel cost**: a budget is a
# number of days, not an amount, and the one multiplier that would make it money — a cost rate — is
# gated separately and is not in this payload. `CATALOG_PERSONNEL_COST_FIELDS` therefore stays
# one-element, and adding a field of this payload to it is the mutation point 3 of that addendum
# rejects by name (it would give "personnel costs" a third meaning and, in today's system, make the
# whole feature unreachable — `PERSONNEL_COSTS_READ` is not in the placeholder's set).


class StatutoryLeaveRegime(BaseModel):
    """What a day of this budget does commercially — **read from the flagged absence type**.

    Never a copy of the flags onto the budget row (criterion K-04): "does leave of this kind still
    cost the organisation money, and is it still billable" is a property of the *type*, and a
    literal on the budget would stop following the type the moment somebody edits it. There is one
    such type at most, named by `absence_type.is_statutory_leave` and by no other rule — not "the
    first one alphabetically", not "the one whose name contains leave" (ADR-0008, addendum
    2026-09-22 SC-3-03, point 8).
    """

    absence_type_id: uuid.UUID
    name: str
    generates_cost: bool
    generates_revenue: bool


class AbsenceBudgetEntry(BaseModel):
    """One leave budget: a key, a number of days, a closed window and where the number came from."""

    id: uuid.UUID
    calendar_id: uuid.UUID
    engagement_type_id: uuid.UUID
    """The whole key, and only these two (ADR-0008, addendum 2026-09-22 SC-3-03, point 3). No
    `location_id`: locations sharing a calendar share its budget."""

    budget_days: DecimalString
    unit: str
    """Named rather than inferred from the size of the number (point 5): `"20"` read as days and
    `"20"` read as FTE-days are two different answers from one row."""

    source: str
    """Mandatory and non-blank — a number nobody can trace to a rule is a number nobody can check.
    **Never the author of the entry**: there is no column for one, and introducing it would expire
    the exemption that keeps the catalogue outside the `project_access` filter (ADR-0005, addendum
    2026-09-21 SC-2-04, point 6; addendum 2026-09-22 SC-3-03, point 6)."""

    effective_from: date
    effective_to: date
    """Inclusive at both ends and never `null` — this is the one table of ADR-0008's pattern whose
    window may not be open-ended (addendum SC-3-03, point 10b), so a client has no "unbounded" case
    to render here and a budget visibly *expires*."""

    statutory_leave_state: str
    """`"resolved"` or `"no_statutory_leave_type"` (`app.domain.absence_budget`).

    The second value is the named state of point 8b — the half of "exactly one flagged type" that no
    index can enforce. The answer is that no type was named; it is never a guess, never a silent
    `0`, and never a `500`."""

    statutory_leave: StatutoryLeaveRegime | None = None
    generates_cost: bool | NotApplicableValue
    generates_revenue: bool | NotApplicableValue
    """The regime, spelled twice on purpose: nested (with the type that produced it) and flat beside
    the budget's own fields, because the flat pair is what a grid renders and the nested object is
    what says *where it came from*. Both are `"n/a"`/`null` in the `no_statutory_leave_type` state —
    the project's one sentinel for a figure that cannot be computed (`app.core.money`), never
    `false`, which would read as a decided answer nobody decided."""

    updated_at: datetime
    """ADR-0007's marker, carried on every read although SC-3-03 ships no edit form: the marker is
    on the table from its creation, and a read that dropped it would be the read from which a later
    form could not be built (the argument `WorkingCalendarEntry.updated_at` already makes)."""


class AbsenceBudgetList(BaseModel):
    """An object, not a bare array — the same contract room every other list payload keeps."""

    budgets: list[AbsenceBudgetEntry]


class AbsenceBudgetCreateRequest(BaseModel):
    """The body of `POST /catalog/absence-budgets`.

    `extra="forbid"`, and here it does real work rather than tidying up (criterion K-02's second
    contrast): a body carrying `author`, `entered_by`, `user` or `approved_by` is refused with a
    `422` naming the field instead of being silently dropped. Naming *who* entered a number is not
    naming *where the number comes from*, and there is no column for the former (ADR-0008, addendum
    2026-09-22 SC-3-03, point 6). `valid_period` is likewise refused: it is a generated column, and
    a body naming it must fail rather than look like it set the window.
    """

    model_config = ConfigDict(extra="forbid")

    calendar_id: uuid.UUID
    engagement_type_id: uuid.UUID
    """Validated as identifiers only: that they name existing catalogue rows is decided by the
    foreign keys in the database, which is the path a client cannot go around."""

    budget_days: BudgetDays
    unit: str = BUDGET_UNIT_DAY
    source: BudgetSourceText
    """**Required, with no default.** A default — even `""` — would make the source of a number an
    optional field, which is precisely what criterion K-02 refuses; the database refuses it a second
    time, for every path that never sees this schema."""

    effective_from: date
    effective_to: date
    """Required and inclusive. Typed as `date` and not `date | None`, so "open-ended" cannot even be
    expressed here — the narrowing ADR-0008's addendum (SC-3-03, point 10b) gives this table, stated
    at the boundary as a `422` and in the database as a CHECK."""

    @model_validator(mode="after")
    def _unit_is_accepted(self) -> Self:
        """A `422` naming the field instead of the `500` a violated `unit_is_day` CHECK would be.

        The constraint in the database stays the guarantee — criterion K-02 asks about the path that
        never sees this schema — and this is the status code, not the rule.
        """
        if self.unit != BUDGET_UNIT_DAY:
            raise ValueError(f"unit must be {BUDGET_UNIT_DAY!r}")
        return self

    @model_validator(mode="after")
    def _window_is_ordered_and_aligned_to_whole_months(self) -> Self:
        """The window starts on the first of a month and ends on the last day of one.

        Same division of labour as everywhere else: a `422` naming the field here,
        `ck_absence_budget_window_aligned_to_whole_months` in the database as the guarantee. The
        rule is not cosmetic — the monthly proration divides by the number of months of the window
        (ADR-0008, addendum SC-3-03, point 10), and a window starting mid-month would make "how many
        months is that" a question with several defensible answers and a different monthly figure
        for each.
        """
        if self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be earlier than effective_from")
        if self.effective_from.day != 1:
            raise ValueError(
                "effective_from must be the first day of a month; a budget window is a whole "
                f"number of months, so {self.effective_from.replace(day=1).isoformat()}, not "
                f"{self.effective_from.isoformat()}"
            )
        if (self.effective_to + timedelta(days=1)).day != 1:
            raise ValueError(
                "effective_to must be the last day of a month; a budget window is a whole number "
                "of months"
            )
        return self

    @model_validator(mode="after")
    def _dates_are_inside_the_planning_horizon(self) -> Self:
        """`LATEST_PLANNING_DATE`, applied to both dates — the same rule as on a rate window.

        `valid_period` is generated as `daterange(effective_from, effective_to + 1, '[)')`, so an
        `effective_to` of `9999-12-31` asks PostgreSQL for `10000-01-01`, a date the driver cannot
        carry back: a `500` where a `422` naming the field is the honest answer (R-01, one table
        over). Unlike a rate window, this one has no "open-ended" spelling to offer instead — a far
        future date is simply refused.
        """
        too_far = sorted(
            name
            for name, value in (
                ("effective_from", self.effective_from),
                ("effective_to", self.effective_to),
            )
            if value > LATEST_PLANNING_DATE
        )
        if too_far:
            raise ValueError(
                f"{', '.join(too_far)} must not be later than {LATEST_PLANNING_DATE.isoformat()}"
            )
        return self


NULLABLE_RATE_EDIT_FIELDS: frozenset[str] = frozenset({"effective_to"})
"""The one edit field whose explicit `null` is a value rather than a mistake.

`effective_to: null` means "open-ended" — a named state of the window, the same one the response
carries (ADR-0008, point 2) — so "close this window's end date" has to be expressible. Every other
editable field is `NOT NULL` in the database, so a `null` there would reach the `UPDATE` as `NULL`
and end as a `500` for what is a client mistake (the argument `ProjectEditRequest` makes for
refusing all of them).

That is also why **absent and `null` are two different requests here**, and why the partial
semantics of this schema are read off `model_fields_set` rather than off "which fields are not
null" (Issue #49, gate-1 decision Q-2)."""


class CatalogRateEditRequest(BaseModel):
    """The body of `PATCH /catalog/rates/{rate_id}` — the fields to change, plus the marker.

    **Partial, and that is the load-bearing property** (Issue #49, gate-1 decision Q-2). A field
    absent from the body is left alone; the fields that are present are the edit. The case it exists
    for is `default_cost_rate`: a caller without `PERSONNEL_COSTS_READ` never receives that field on
    any read (`app.api.response_shaping`), so their edit of a selling rate or a window must be able
    to omit it entirely and leave the stored cost untouched. The alternatives were measured against
    that case and rejected at gate 1 — whole-row `PUT` semantics would force such a caller either to
    send a number they invented or to give up editing the row at all.

    `extra="forbid"`, as on the create request, and for one reason specific to editing: `id`,
    `vendor_id`, the four dimension ids and `valid_period` are not editable
    (`app.data.catalog.EDITABLE_RATE_FIELDS`), and an ignored unknown key would make
    `{"role_id": …}` look like a re-keying that quietly did nothing.
    """

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime
    """The marker returned by the read this edit is based on (ADR-0007) — required, and required
    with an offset, for the reasons `DimensionEntryEditRequest.updated_at` gives."""

    default_cost_rate: RateAmount | None = None
    """Omitted = unchanged. Present = written, at the same precision the create path accepts
    (`RateAmount`): more than four decimal places is a `422`, never a silent rounding of somebody's
    cost rate (R-05).

    The permission is **not** consulted here and must not be: a caller without
    `PERSONNEL_COSTS_READ` may still write this field, which is the consequence of P-2 accepted
    deliberately at gate 1 (ADR-0005 addendum — "you write a cost you cannot read back"). What is
    gated is the response, in one place, by the shaping layer."""

    default_selling_rate: RateAmount | None = None
    currency: Iso4217Code | None = None

    effective_from: date | None = None
    effective_to: date | None = None
    """Inclusive, as everywhere else, and the one field whose explicit `null` is meaningful: it
    makes the window open-ended (`NULLABLE_RATE_EDIT_FIELDS`). Omitting it leaves the window's end
    exactly as it was — the two are different requests."""

    @model_validator(mode="after")
    def _at_least_one_field_and_none_of_them_wrongly_null(self) -> Self:
        """The two refusals of `ProjectEditRequest`, with one documented exception.

        A body carrying only the marker asks for no change at all; accepted, it would move
        `updated_at` and invalidate every other client's marker for nothing. A `null` on any field
        but `effective_to` would reach the `UPDATE` as `NULL` against a `NOT NULL` column — a `500`
        for a client mistake.
        """
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        if not changed:
            raise ValueError("An edit must name at least one field to change.")
        nulled = sorted(
            field
            for field in changed - NULLABLE_RATE_EDIT_FIELDS
            if getattr(self, field) is None
        )
        if nulled:
            raise ValueError(f"These fields cannot be set to null: {', '.join(nulled)}")
        return self

    @model_validator(mode="after")
    def _effective_period_is_ordered_when_both_are_given(self) -> Self:
        """Same division of labour as on create — and the same answer either way.

        Both dates in one body are checked here, so the caller gets a `422` naming the field. One of
        them alone cannot be checked here at all (the other end of the window lives in the row this
        request never read), and it is not checked in Python anywhere else either: the database's
        `ck_catalog_default_rates_effective_period_ordered` refuses it inside the `UPDATE` and the
        endpoint answers `409`. A `SELECT` here to fetch the missing end and compare would be
        check-then-act on a row another connection may be editing — the shape ADR-0008 rejects by
        name.
        """
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to must not be earlier than effective_from")
        return self

    @model_validator(mode="after")
    def _dates_are_inside_the_planning_horizon(self) -> Self:
        """`LATEST_PLANNING_DATE`, applied to whichever of the two dates the body carries — the same
        rule as on create, for the same two reasons (a sentinel date is not a plan, and
        `effective_to + 1` past the driver's range is a `500` rather than an answer)."""
        too_far = sorted(
            name
            for name, value in (
                ("effective_from", self.effective_from),
                ("effective_to", self.effective_to),
            )
            if value is not None and value > LATEST_PLANNING_DATE
        )
        if too_far:
            raise ValueError(
                f"{', '.join(too_far)} must not be later than {LATEST_PLANNING_DATE.isoformat()}; "
                "an open-ended window is expressed by effective_to = null, not by a far-future date"
            )
        return self

    def changes(self) -> dict[str, Any]:
        """The requested changes as column names → values, in declaration order.

        Read off `model_fields_set`, which is the whole of the partial semantics: a field the caller
        did not send is absent from this mapping, so it is absent from the `UPDATE`'s `SET` clause
        and the stored value is not touched. `default_cost_rate` is the field that makes this matter
        (see the class docstring).

        Declaration order rather than set iteration order, as in `ProjectEditRequest.changes`, so
        the generated `UPDATE` is the same statement for the same request every time.
        """
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        return {
            field: getattr(self, field)
            for field in type(self).model_fields
            if field in changed
        }
