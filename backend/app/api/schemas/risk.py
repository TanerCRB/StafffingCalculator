"""Request and response schemas for a scenario's risks and risk reserves (F-09 pt 4-5, SC-6-08).

Boundary decisions visible in the shapes below (ADR-0021):

**No field is gated, and no field is personnel cost** (point 9). Risks and reserves are read under
`STAFFING_READ` with no `PERSONNEL_COSTS_READ` conjunction and carry no `position_id`, so nothing
here is `null` "for this caller" - a field is `null` only where its own meaning says so.

**The risk read carries kinds and counts only, never an amount** (gate 1 G-1): `RiskRead` has no
money field at all, so "no amount appears" is a property of the type, not of a serialiser. The
representation (`none` / `cost_event` / `reserve` / `both`) is `both` - the double-representation
signal - exactly when at least one cost event and at least one reserve carry the declared link.

**Money crosses the boundary as a fixed-point string** (`DecimalString`), never a JSON float. On the
way in a reserve amount is bounded by the column's own precision: five decimal places are a `422`,
never a silent rounding at write time (ADR-0002, addendum SC-2-04, point 2).

Every rule stated here is also stated in the database (`app.models.risk`): the schema turns a
client's mistake into a `422` naming the field; the guarantee is the constraint.
"""

import uuid
from datetime import date, datetime
from typing import Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from app.api.schemas.additional_cost import (
    MAX_RECURRING_MONTHS,
    CostAmount,
    _first_of_month,
    _months_spanned,
)
from app.api.schemas.common import DecimalString, Iso4217Code, NonEmptyName
from app.api.schemas.project import ScenarioStatusLabel
from app.core.money import NOT_APPLICABLE

RiskRepresentation = Literal["none", "cost_event", "reserve", "both"]
ReserveType = Literal["one_off", "recurring"]
ReserveTotalState = Literal["calculated", "currency_mismatch", "no_cost_currency"]

RiskName = NonEmptyName
"""A risk's name: trimmed, 1-200 characters. The database refuses a blank one as well
(`ck_scenario_risk_name_not_blank`), and a second risk of the same name in one scenario
(`uq_scenario_risk_scenario_id_name`)."""

PAGE_QUERY_NOTE = (
    "Omitted together: the whole list (ADR-0017, point 6). Named: refused (422) outside the "
    "bounds, never silently clamped. Answered only once the scenario is confirmed in the caller's "
    "scope - a scenario outside it is always the 404."
)

NULLABLE_RESERVE_EDIT_FIELDS: frozenset[str] = frozenset({"risk_id", "end_month"})
"""The two fields whose explicit `null` means something on an edit: `risk_id: null` unlinks the
reserve from its risk, `end_month: null` is the shape of a one-off reserve."""


# --- risks ---------------------------------------------------------------------------------------


class RiskCreateRequest(BaseModel):
    """The body of `POST .../risks`. `extra="forbid"`: an unknown key (a description, a person, an
    amount) is a `422` - there is no column for any of them."""

    model_config = ConfigDict(extra="forbid")

    name: RiskName


class RiskEditRequest(BaseModel):
    """The body of `PATCH .../risks/{risk_id}` - the new name, plus this risk's marker."""

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime
    """**This risk's** marker (ADR-0007, addendum SC-6-08). Required, with an offset."""

    name: RiskName

    def changes(self) -> dict[str, Any]:
        return {"name": self.name}


class RiskDeleteRequest(BaseModel):
    """The body of `DELETE .../risks/{risk_id}`: this risk's marker."""

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime


class RiskRead(BaseModel):
    """One declared risk, the representations it has, and the counts they come from.

    **No amount field** (gate 1 G-1). A count is a number of rows carrying the declared link,
    position-level cost events included.
    """

    id: uuid.UUID
    name: str
    representation: RiskRepresentation
    double_represented: bool
    """`true` exactly when `representation` is `"both"` - the F-09 pt 5 signal, spelled once more as
    a boolean so a client does not have to compare strings. Additive: it never changes a total."""
    cost_event_count: int
    reserve_count: int
    updated_at: datetime
    """ADR-0007's marker for this risk row - required back on its edit and its delete."""


class ScenarioRisks(BaseModel):
    """`GET .../scenarios/{id}/risks` - one page of the scenario's declared risks (ADR-0017)."""

    scenario_id: uuid.UUID
    scenario_status: ScenarioStatusLabel
    total: int
    risks: list[RiskRead]


# --- reserves ------------------------------------------------------------------------------------

MAX_RESERVE_MONTHS = MAX_RECURRING_MONTHS
"""The most months one recurring reserve may span, both ends included - the same plausibility bound
as a recurring cost (ADR-0014, point 4; imported, not spelled again, so the two cannot drift). A
rule about API input, deliberately not a CHECK in the database."""


def _within_the_bound(start_month: date | None, end_month: date | None) -> None:
    if (
        start_month is not None
        and end_month is not None
        and _months_spanned(start_month, end_month) > MAX_RESERVE_MONTHS
    ):
        raise ValueError(
            f"a recurring reserve may span at most {MAX_RESERVE_MONTHS} months, both ends included"
        )


class ReserveCreateRequest(BaseModel):
    """The body of `POST .../risk-reserves`."""

    model_config = ConfigDict(extra="forbid")

    risk_id: uuid.UUID | None = None
    """Absent or `null` - a reserve with no declared risk (legal, never signalled). Otherwise a risk
    **of this scenario**; any other id is the same `404` as a missing one."""

    amount: CostAmount
    """For a recurring reserve, the amount of **each** month, never a total to divide."""

    currency: Iso4217Code
    reserve_type: ReserveType
    start_month: date
    end_month: date | None = None

    @model_validator(mode="after")
    def _period_matches_the_type(self) -> Self:
        _first_of_month("start_month", self.start_month)
        _first_of_month("end_month", self.end_month)
        if self.reserve_type == "one_off" and self.end_month is not None:
            raise ValueError("a one_off reserve is one month: end_month must be null")
        if self.reserve_type == "recurring":
            if self.end_month is None:
                raise ValueError("a recurring reserve needs an end_month; it is never open-ended")
            if self.end_month < self.start_month:
                raise ValueError("end_month must not be earlier than start_month")
            _within_the_bound(self.start_month, self.end_month)
        return self


class ReserveEditRequest(BaseModel):
    """The body of `PATCH .../risk-reserves/{reserve_id}` - the fields to change, plus the marker.

    Partial: a field absent from the body is left alone. A rule spanning two fields that receives
    only one of them here is checked by the database inside the `UPDATE`, never by a Python read of
    the other half (check-then-act)."""

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime
    risk_id: uuid.UUID | None = None
    amount: CostAmount | None = None
    currency: Iso4217Code | None = None
    reserve_type: ReserveType | None = None
    start_month: date | None = None
    end_month: date | None = None

    @model_validator(mode="after")
    def _at_least_one_field_and_none_of_them_wrongly_null(self) -> Self:
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        if not changed:
            raise ValueError("An edit must name at least one field to change.")
        nulled = sorted(
            field
            for field in changed - NULLABLE_RESERVE_EDIT_FIELDS
            if getattr(self, field) is None
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
        _within_the_bound(self.start_month, self.end_month)
        return self

    def changes(self) -> dict[str, Any]:
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        return {
            field: getattr(self, field) for field in type(self).model_fields if field in changed
        }


class ReserveDeleteRequest(BaseModel):
    """The body of `DELETE .../risk-reserves/{reserve_id}`: this reserve's marker."""

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime


class ReserveRead(BaseModel):
    """One reserve row, as stored."""

    id: uuid.UUID
    risk_id: uuid.UUID | None
    amount: DecimalString
    currency: str
    reserve_type: ReserveType
    start_month: date
    end_month: date | None
    updated_at: datetime


class ReserveTotalRead(BaseModel):
    """The sum of the scenario's reserves, or the named state that withholds it.

    **Its own named result, reported beside `additional_cost` and never inside it** (ADR-0021,
    point 3, Q-3 = A): nothing in `/results` or `/compare` reads it."""

    state: ReserveTotalState
    amount: DecimalString | Literal[NOT_APPLICABLE]
    """A fixed-point string when `state` is `"calculated"`, `"n/a"` for a named state - never `0`
    and never a partial sum."""
    currency: str | None
    currencies: list[str]
    """The currencies the reserves carry - what a `currency_mismatch` is about."""


class ScenarioReserves(BaseModel):
    """`GET .../scenarios/{id}/risk-reserves` - one page of reserves and the sum of all of them."""

    scenario_id: uuid.UUID
    scenario_status: ScenarioStatusLabel
    total: int
    """Every reserve of the scenario, before paging (ADR-0017, point 5)."""
    reserves: list[ReserveRead]
    reserve_total: ReserveTotalRead
