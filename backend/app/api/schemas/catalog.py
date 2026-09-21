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
from datetime import date
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.schemas.common import DecimalString, Iso4217Code, NonEmptyName
from app.models.catalog import RATE_PRECISION, RATE_SCALE, RATE_UNIT_HOUR

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
