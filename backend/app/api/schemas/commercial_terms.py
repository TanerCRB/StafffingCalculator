"""Request and response schemas for a scenario's commercial rule and revenue (F-06.1, F-06.4;
SC-4-01, SC-4-04).

Two boundary decisions are visible in the shapes below — both proven again, not merely assumed, by
the second model (Story Points, SC-4-04): a request carrying `price_per_point`/`accepted_points`/
`currency` still fits one discriminated union (D-6/A), and its revenue still fits the one response
shape below with no cost field.

**No field carries a cost** — not `default_cost_rate`, not a cost, a profit or a margin (ADR-0005,
addendum 2026-09-23 SC-4-01, point 3). Not "removed for callers without the permission": absent from
the schema, which is what lets the revenue go out without the SC-1-08 conjunction. Criterion K-11
asserts the whole field set by *equality*, so a cost field added here later fails a test on the day
it is added rather than leaking quietly. The first task adding profit or margin to this payload
reinstates the conjunction (same point).

**Money crosses the boundary as a fixed-point string** (`DecimalString`), never a JSON float
(ADR-0002). A revenue that cannot be stated is `"n/a"` with a named `state`, never `0` and never
`null` (ADR-0003, point 9; criterion K-10).
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.common import DecimalString, Iso4217Code
from app.api.schemas.project import ScenarioStatusLabel
from app.core.money import NOT_APPLICABLE

ModelType = Literal["time_and_material", "story_points"]
"""The models a rule may name — the API spelling of `app.models.commercial_terms.MODEL_TYPES`. The
database CHECK is the rule; this `Literal` only turns a client's typo into a `422` naming the
field."""

RevenueState = Literal[
    "calculated",
    "no_commercial_terms",
    "incomplete_commercial_terms",
    "unsupported_model_type",
    "no_rate",
    "currency_mismatch",
    "no_revenue_currency",
]

StoredModelType = str
"""A model as a **response** carries it: whatever the stored row says, not the `Literal` above.

The request side stays closed (`ModelType`) — this version creates only what it can price. The
response side is open on purpose (R-02, SC-4-01 gate 2): in the mixed-version window of ADR-0001 a
row of a later model can exist while this code runs, and a closed `Literal` here would turn the
named `unsupported_model_type` state into a response-validation `500`."""


class TimeAndMaterialTermsCreateRequest(BaseModel):
    """`POST …/commercial-terms` — the one thing a Time & Material rule says: which model it is.

    `extra="forbid"`: there is no rate, override, cap or day length to send (ADR-0003, points 4 and
    7, "Odłożone"), and a field the server silently ignored would be a promise it does not keep.
    """

    model_config = ConfigDict(extra="forbid")

    model_type: Literal["time_and_material"]


PricePerPoint = Annotated[DecimalString, Field(gt=0, max_digits=14, decimal_places=4)]
"""Strictly positive (`ck_story_points_terms_price_per_point_positive`) and exactly as precise as
the column's own `NUMERIC(14,4)` — the same boundary shape `CostAmount`
(`app.api.schemas.additional_cost`) already gives a money field with a database CHECK behind it."""


class StoryPointsTermsCreateRequest(BaseModel):
    """`POST …/commercial-terms` — a Story Points rule's three domain values (SC-4-04, F-06.4).

    `extra="forbid"`, for the same reason as the T&M variant. `accepted_points` is written once,
    here, with no edit path (ADR-0003 addendum 2026-09-25, D-5/A): the request is the only place
    this value is ever set. No budget cap, no "sprint fee" field (D-1/A, D-4/A — out of scope).
    """

    model_config = ConfigDict(extra="forbid")

    model_type: Literal["story_points"]
    price_per_point: PricePerPoint
    accepted_points: int = Field(ge=0)
    currency: Iso4217Code


CommercialTermsCreateRequest = Annotated[
    TimeAndMaterialTermsCreateRequest | StoryPointsTermsCreateRequest,
    Field(discriminator="model_type"),
]
"""The request shape as a discriminated union on `model_type` (ADR-0003 addendum 2026-09-25, D-6/A):
the first model with domain fields (Story Points) does not grow one wide, optional-fielded shape —
each model's own request carries only its own fields, and Pydantic itself refuses a `model_type` its
`Literal` does not name, before any of this reaches `app.data.commercial_terms`."""


class CommercialTermsRead(BaseModel):
    """The rule itself: its id, its model and ADR-0007's marker (covering its details row too)."""

    id: uuid.UUID
    model_type: StoredModelType
    updated_at: datetime


class RateWindowRead(BaseModel):
    """One catalogue window the revenue used (F-06.5, `assumptions_used`). The selling rate only."""

    source_rate_id: uuid.UUID
    effective_from: date
    effective_to: date | None
    """Inclusive, `null` when open-ended — passed through as stored (ADR-0008, point 3)."""
    default_selling_rate: DecimalString
    currency: str


class UnresolvedMonthRead(BaseModel):
    """One (position, month) that caused a named state."""

    position_id: uuid.UUID
    period_month: date


class RevenueAssumptionsRead(BaseModel):
    """What the revenue depends on — present on a calculated revenue and on a named state alike.

    One shape for every model (SC-4-04): a Story Points revenue reads no hour, no vendor axis and
    no rate window at all, and names that honestly (`"not_applicable"`, `"story_points_terms"`)
    rather than reusing a Time & Material value that would misdescribe it — `rate_windows` and
    `unresolved_months` are simply empty for this model, the same way `RateWindowRead` is unused by
    a named state today.
    """

    model_type: StoredModelType | None
    hours_source: Literal["billable_hours", "not_applicable"]
    vendor_axis: Literal["internal", "not_applicable"]
    rate_source: Literal["live_catalog", "approved_snapshot", "story_points_terms"]
    rate_windows: list[RateWindowRead]
    unresolved_months: list[UnresolvedMonthRead]
    currencies: list[str]


class RevenueRead(BaseModel):
    """The revenue of one scenario, or the named state that withholds it."""

    state: RevenueState
    amount: DecimalString | Literal[NOT_APPLICABLE]
    """A fixed-point string when `state` is `"calculated"`, `"n/a"` otherwise — never `0`."""
    currency: str | None
    assumptions_used: RevenueAssumptionsRead


class ScenarioCommercialTerms(BaseModel):
    """`GET`/`POST …/scenarios/{id}/commercial-terms` — the rule and the revenue derived from it."""

    scenario_id: uuid.UUID
    scenario_status: ScenarioStatusLabel
    commercial_terms: CommercialTermsRead | None
    """`null` when the scenario has no rule — and then `revenue.state` is
    `"no_commercial_terms"`."""
    revenue: RevenueRead
