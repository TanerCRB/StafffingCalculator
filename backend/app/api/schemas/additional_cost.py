"""Request and response schemas for a scenario's additional costs (F-08, SC-5-05; ADR-0014).

Three boundary decisions are visible in the shapes below.

**No field is gated.** Additional costs are read under `STAFFING_READ` with no
`PERSONNEL_COSTS_READ` conjunction (ADR-0014, point 11, Q-7 = B; ADR-0005, addendum 2026-09-23
SC-5-05, point 1), so nothing here is `null` "for this caller" — a field is `null` only where its
own meaning says so (`position_id` of a scenario-level cost, `end_month` of a one-off cost,
`currency` of a named state).

**Money crosses the boundary as a fixed-point string** (`DecimalString`), never a JSON float
(ADR-0002). On the way in an amount is bounded by the column's own precision: five decimal places
are a `422`, never a silent rounding at write time (ADR-0002, addendum SC-2-04, point 2; criterion
K-04).

**Every rule stated here is also stated in the database** (`app.models.additional_cost`). The schema
turns a client's mistake into a `422` naming the field; the guarantee is the constraint, because a
fixture, a seed script or a future import never passes through this module (criterion K-03).
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.api.schemas.catalog import LATEST_PLANNING_DATE
from app.api.schemas.common import DecimalString, Iso4217Code
from app.api.schemas.project import ScenarioStatusLabel
from app.api.schemas.staffing import MAX_ALLOCATION_MONTHS
from app.core.money import NOT_APPLICABLE
from app.models.additional_cost import AMOUNT_PRECISION, AMOUNT_SCALE

CostType = Literal["one_off", "recurring"]
"""Closed by ADR-0014 (point 5, Q-1 = A: a fixed amount, one-off or recurring) and by the CHECK
`ck_additional_cost_cost_type_known`."""

FundingSource = Literal["internal", "rebilled_to_client"]
"""Who carries the cost (ADR-0014, point 9). Deliberately not "vendor": in this repository a vendor
is a subcontractor (`catalog_vendors`)."""

AdditionalCostState = Literal["calculated", "currency_mismatch", "no_cost_currency"]

CostAmount = Annotated[
    DecimalString,
    Field(gt=0, max_digits=AMOUNT_PRECISION, decimal_places=AMOUNT_SCALE),
]
"""An amount on the way in: strictly positive (G-1 = A, `ck_additional_cost_amount_positive`) and
exactly as precise as `NUMERIC(14,4)`. `12.34567` is a `422`, not `12.3457` stored in its place."""

NULLABLE_EDIT_FIELDS: frozenset[str] = frozenset({"position_id", "end_month"})
"""The two fields whose explicit `null` means something on an edit: `position_id: null` detaches the
cost from its position (a scenario-level cost), `end_month: null` is the shape of a one-off cost."""


MAX_RECURRING_MONTHS = MAX_ALLOCATION_MONTHS
"""The most months one recurring cost may span, both ends included — 60, the bound of a staffing
grid per request (ADR-0014, point 4, gate 2 reviewer R-02).

Imported from `app.api.schemas.staffing` rather than spelled again: it is the same plausibility rule
("five years of monthly rows from one request"), and two copies could drift apart. Without it one
`POST` could create a cost of tens of thousands of months, and every later read and copy of the
scenario would spread it month by month. Like `MAX_ALLOCATION_MONTHS`, a rule about API input and
deliberately **not** a CHECK in the database: a fixture or an import testing the column's range must
not have to argue with it."""


def _months_spanned(start_month: date, end_month: date) -> int:
    """How many calendar months `[start_month, end_month]` covers, both ends included."""
    return (end_month.year - start_month.year) * 12 + end_month.month - start_month.month + 1


def _within_the_bound(start_month: date | None, end_month: date | None) -> None:
    if (
        start_month is not None
        and end_month is not None
        and _months_spanned(start_month, end_month) > MAX_RECURRING_MONTHS
    ):
        raise ValueError(
            f"a recurring cost may span at most {MAX_RECURRING_MONTHS} months, both ends included"
        )


def _first_of_month(name: str, value: date | None) -> None:
    if value is not None and value.day != 1:
        raise ValueError(f"{name} must be the first day of a month (YYYY-MM-01)")
    if value is not None and value > LATEST_PLANNING_DATE:
        raise ValueError(f"{name} must not be later than {LATEST_PLANNING_DATE.isoformat()}")


class AdditionalCostCreateRequest(BaseModel):
    """The body of `POST …/scenarios/{scenario_id}/additional-costs`.

    `extra="forbid"`: an unknown key is a `422` rather than a silently dropped field — a body
    carrying a `scenario_id`, a `description` or a person's name fails loudly (there is no column
    for any of them).
    """

    model_config = ConfigDict(extra="forbid")

    category_id: uuid.UUID
    """From the dictionary; that it exists is decided by the foreign key (`409` otherwise)."""

    position_id: uuid.UUID | None = None
    """Absent or `null` — a cost of the scenario as a whole (the "project" cost of F-08, Q-2 = A).
    Otherwise a position **of this scenario**; any other id is the same `404` as a missing one."""

    amount: CostAmount
    """For a recurring cost, the amount of **each** month (Q-3 = A), never a total to divide."""

    currency: Iso4217Code
    cost_type: CostType
    start_month: date
    """The first day of the month of a one-off cost, or of the first month of a recurring one."""

    end_month: date | None = None
    """The last month of a recurring cost, inclusive — required for one. Must be absent/`null` for a
    one-off cost."""

    funding_source: FundingSource

    @model_validator(mode="after")
    def _period_matches_the_type(self) -> Self:
        """The same rules as the five CHECKs on the period, as a `422` naming the field."""
        _first_of_month("start_month", self.start_month)
        _first_of_month("end_month", self.end_month)
        if self.cost_type == "one_off" and self.end_month is not None:
            raise ValueError("a one_off cost is one month: end_month must be null")
        if self.cost_type == "recurring":
            if self.end_month is None:
                raise ValueError("a recurring cost needs an end_month; it is never open-ended")
            if self.end_month < self.start_month:
                raise ValueError("end_month must not be earlier than start_month")
            _within_the_bound(self.start_month, self.end_month)
        return self


class AdditionalCostEditRequest(BaseModel):
    """The body of `PATCH …/additional-costs/{cost_id}` — the fields to change, plus the marker.

    Partial, like `CatalogRateEditRequest`: a field absent from the body is left alone. A rule that
    spans two fields (type and period) and receives only one of them here is checked by the
    database inside the `UPDATE`, never by a Python read of the other half (check-then-act).
    """

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime
    """**This cost's** marker, as returned by the read this edit is based on (ADR-0007, addendum
    SC-5-05). Required, and required with an offset."""

    category_id: uuid.UUID | None = None
    position_id: uuid.UUID | None = None
    amount: CostAmount | None = None
    currency: Iso4217Code | None = None
    cost_type: CostType | None = None
    start_month: date | None = None
    end_month: date | None = None
    funding_source: FundingSource | None = None

    @model_validator(mode="after")
    def _at_least_one_field_and_none_of_them_wrongly_null(self) -> Self:
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        if not changed:
            raise ValueError("An edit must name at least one field to change.")
        nulled = sorted(
            field for field in changed - NULLABLE_EDIT_FIELDS if getattr(self, field) is None
        )
        if nulled:
            raise ValueError(f"These fields cannot be set to null: {', '.join(nulled)}")
        _first_of_month("start_month", self.start_month)
        _first_of_month("end_month", self.end_month)
        if (
            self.start_month is not None
            and self.end_month is not None
            and self.end_month < self.start_month
        ):
            raise ValueError("end_month must not be earlier than start_month")
        # Checked when the body carries both ends; an edit sending only one of them is not bounded
        # here (the other end lives in the row this request never read) — a named gap, not a
        # Python read of the row (check-then-act).
        _within_the_bound(self.start_month, self.end_month)
        return self

    def changes(self) -> dict[str, Any]:
        """The requested changes, read off `model_fields_set`, in declaration order."""
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        return {
            field: getattr(self, field) for field in type(self).model_fields if field in changed
        }


class AdditionalCostDeleteRequest(BaseModel):
    """The body of `DELETE …/additional-costs/{cost_id}`: this cost's marker — a `DELETE` with a
    body, for the reason `StaffingAbsenceDeleteRequest` gives (one place for the token)."""

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime


class AdditionalCostRead(BaseModel):
    """One cost row, as stored."""

    id: uuid.UUID
    category_id: uuid.UUID
    category_name: str
    """The category's **current** name — a label read live, not frozen at approval (ADR-0014,
    point 2, Q-4 = A)."""
    position_id: uuid.UUID | None
    amount: DecimalString
    currency: str
    cost_type: CostType
    start_month: date
    end_month: date | None
    funding_source: FundingSource
    updated_at: datetime
    """ADR-0007's marker for this cost row — required back on its edit and its delete."""


class AdditionalCostSpreadRead(BaseModel):
    """One cost as the sum used it: the months it belongs to, with its category and funding."""

    cost_id: uuid.UUID
    position_id: uuid.UUID | None
    category_id: uuid.UUID
    category_name: str
    funding_source: FundingSource
    cost_type: CostType
    amount: DecimalString
    """The stored amount **per month** of the cost — the input, not a rounded share of anything."""
    currency: str
    months: list[date]


class AdditionalCostPeriodRead(BaseModel):
    """One month and the costs that belong to it — each cost once (AC-03)."""

    period_month: date
    cost_ids: list[uuid.UUID]


class AdditionalCostAssumptionsRead(BaseModel):
    """What the sum — or its absence — depends on. Present on both shapes of the answer."""

    costs: list[AdditionalCostSpreadRead]
    periods: list[AdditionalCostPeriodRead]
    currencies: list[str]


class AdditionalCostTotalRead(BaseModel):
    """The sum of the scenario's additional costs, or the named state that withholds it."""

    state: AdditionalCostState
    amount: DecimalString | Literal[NOT_APPLICABLE]
    """A fixed-point string when `state` is `"calculated"`, `"n/a"` for a named state — never `0`
    and never a partial sum."""
    currency: str | None
    assumptions_used: AdditionalCostAssumptionsRead


class ScenarioAdditionalCosts(BaseModel):
    """`GET …/scenarios/{id}/additional-costs` — the cost rows and their sum."""

    scenario_id: uuid.UUID
    scenario_status: ScenarioStatusLabel
    costs: list[AdditionalCostRead]
    additional_cost: AdditionalCostTotalRead
