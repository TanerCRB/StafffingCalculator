"""Request and response schemas for a scenario's commercial rule and revenue (F-06.1, F-06.2,
F-06.3, F-06.4; SC-4-01, SC-4-02, SC-4-03, SC-4-04).

Two boundary decisions are visible in the shapes below — both proven again, not merely assumed, by
the second model (Story Points, SC-4-04): a request carrying `price_per_point`/`accepted_points`/
`currency` still fits one discriminated union (D-6/A), and its revenue still fits the one response
shape below with no cost field.

**No field carries a cost** — not `default_cost_rate`, not a cost, a profit or a margin (ADR-0005,
addendum 2026-09-23 SC-4-01, point 3). Not "removed for callers without the permission": absent from
the schema, which is what lets the revenue go out without the SC-1-08 conjunction. Criterion K-11
asserts the whole field set by *equality*, so a cost field added here later fails a test on the day
it is added rather than leaking quietly. The first task adding profit or margin to this payload
reinstates the conjunction (same point). The Fixed Price fields of SC-4-02 — the agreed price and
its currency — are what the client pays, not what a person costs, and are asserted the same way.

**Money crosses the boundary as a fixed-point string** (`DecimalString`), never a JSON float
(ADR-0002). A revenue that cannot be stated is `"n/a"` with a named `state`, never `0` and never
`null` (ADR-0003, point 9; criterion K-10).

**Fixed Price is a second shape, not extra fields on the first** (SC-4-02, gate 1 K-07: "the T&M
response byte for byte unchanged"). The rule and the assumptions of a Fixed Price scenario are their
own classes (`FixedPriceCommercialTermsRead`, `FixedPriceRevenueAssumptionsRead`), chosen by
`model_type` — the discriminator, never the shape of the data (ADR-0003, point 9) — so a T&M, Story
Points or Outcome-based payload keeps exactly the fields it has without Fixed Price, in their order.
The revenue fields every model shares (`RevenueRead`, including the Outcome-based additions of
SC-4-03) are shared by Fixed Price too.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    model_validator,
)

from app.api.schemas.common import DecimalString, Iso4217Code
from app.api.schemas.project import ScenarioStatusLabel
from app.core.money import NOT_APPLICABLE
from app.models.commercial_terms import (
    AGREED_PRICE_PRECISION,
    AGREED_PRICE_SCALE,
    MODEL_TYPE_FIXED_PRICE,
    OUTCOME_AMOUNT_PRECISION,
    OUTCOME_AMOUNT_SCALE,
    OUTCOME_UNITS_PRECISION,
    OUTCOME_UNITS_SCALE,
    PROBABILITY_PRECISION,
    PROBABILITY_SCALE,
)

OutcomeCategory = Literal["not_achieved", "partial", "achieved", "exceeded"]
"""Four fixed outcome categories (ADR-0003, addendum 2026-09-25 SC-4-03, point 3) —
`app.models.commercial_terms.OUTCOME_CATEGORIES` in the API's spelling."""

ExpectedRevenueState = Literal["calculated", "no_probabilities", "not_applicable"]
"""The state of the expected revenue (points 5b-c): `no_probabilities` is the named state of a rule
with no probabilities; `not_applicable` — a model with no expected revenue, or revenue not stated
at all (then `state` says why)."""

SourceNotApplicable = Literal["not_applicable"]
"""A source the calculation does not read — a model with no rate catalogue (points 8, 10a)."""

ModelType = Literal["time_and_material", "story_points", "outcome_based", "fixed_price"]
"""The models a rule may name — the API spelling of `app.models.commercial_terms.MODEL_TYPES`. The
database CHECK is the rule; the request `Literal`s below only turn a client's typo into a `422`
naming the field."""

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
"""A model as a **response** carries it: whatever the stored row says, not a closed `Literal`.

The request side stays closed — `CommercialTermsCreateRequest` below is a union discriminated by
`model_type`, each variant with its own one-value `Literal`, so a model this version cannot price is
a `422` naming the field; the database CHECK is the rule behind it. This version creates only what
it can price. The
response side is open on purpose (R-02, SC-4-01 gate 2): in the mixed-version window of ADR-0001 a
row of a later model can exist while this code runs, and a closed `Literal` here would turn the
named `unsupported_model_type` state into a response-validation `500`."""

AgreedPriceInput = Annotated[
    DecimalString,
    Field(ge=0, max_digits=AGREED_PRICE_PRECISION, decimal_places=AGREED_PRICE_SCALE),
]
"""The agreed price on the way in: not below zero (D-5, `ck_fixed_price_terms_agreed_price_
non_negative`) and exactly as precise as `NUMERIC(14,4)` — `150000.12345` is a `422`, never
`150000.1235` stored in its place (ADR-0002, addendum 2026-09-21, point 2)."""


class TimeAndMaterialTermsCreateRequest(BaseModel):
    """`POST …/commercial-terms` for T&M — the one thing a T&M rule says: which model prices it.

    `extra="forbid"`: there is no rate, override, cap or day length to send (ADR-0003, points 4 and
    7, "Deferred"), and a field the server silently ignored would be a promise it does not keep.
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


OutcomeAmount = Annotated[
    DecimalString,
    Field(ge=0, max_digits=OUTCOME_AMOUNT_PRECISION, decimal_places=OUTCOME_AMOUNT_SCALE),
]
"""The rule's amount on the way in: non-negative and exactly as precise as `NUMERIC(14,4)` — a fifth
decimal place is a `422`, not a rounding at write time (ADR-0002)."""

OutcomeUnits = Annotated[
    DecimalString,
    Field(ge=0, max_digits=OUTCOME_UNITS_PRECISION, decimal_places=OUTCOME_UNITS_SCALE),
]

Probability = Annotated[
    DecimalString,
    Field(ge=0, le=100, max_digits=PROBABILITY_PRECISION, decimal_places=PROBABILITY_SCALE),
]
"""A percentage with at most two decimal places. `33.333` is a `422` — never a silent
rounding that would change a sum the user already checked (ADR-0003, addendum SC-4-03, point 4)."""


class OutcomeCategoryRequest(BaseModel):
    """One outcome category: the number of units achieved (a manual entry) and an optional
    probability.

    `units` is optional — mandatory only when the rule has a per-unit rate (the
    `OutcomeBasedTermsCreateRequest` validator, the same condition as
    `ck_outcome_terms_units_given_with_unit_rate`). Omitted stays `null`, never `0`."""

    model_config = ConfigDict(extra="forbid")

    units: OutcomeUnits | None = None
    probability: Probability | None = None


class OutcomeCategoriesRequest(BaseModel):
    """Exactly four fixed categories — every one mandatory, none extra (point 3)."""

    model_config = ConfigDict(extra="forbid")

    not_achieved: OutcomeCategoryRequest
    partial: OutcomeCategoryRequest
    achieved: OutcomeCategoryRequest
    exceeded: OutcomeCategoryRequest


class OutcomeBasedTermsCreateRequest(BaseModel):
    """`POST …/commercial-terms` for Outcome-based (F-06.3; ADR-0003, addendum 2026-09-25 SC-4-03).

    **Every rule here is also a rule of the database** (`app.models.commercial_terms.OutcomeTerms`):
    the schema turns a client's mistake into a `422` naming the field; the guarantee is the `CHECK`,
    because neither a fixture nor an import passes through this module. An optional component
    omitted or `null` means the component is absent — never `0` (point 2).
    """

    model_config = ConfigDict(extra="forbid")

    model_type: Literal["outcome_based"]
    currency: Iso4217Code
    """The rule's currency, with no conversion (point 7): different from the scenario's
    currency → `currency_mismatch`."""
    fixed_fee: OutcomeAmount
    success_bonus: OutcomeAmount | None = None
    """A binary bonus for the "achieved" and "exceeded" categories — not for "partial" (D-1)."""
    unit_rate: OutcomeAmount | None = None
    revenue_min: OutcomeAmount | None = None
    revenue_max: OutcomeAmount | None = None
    categories: OutcomeCategoriesRequest

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        """The same rules as `ck_outcome_terms_revenue_bounds_ordered`,
        `ck_outcome_terms_probabilities_sum_to_100` and
        `ck_outcome_terms_units_given_with_unit_rate`, as a `422` before any write."""
        if (
            self.revenue_min is not None
            and self.revenue_max is not None
            and self.revenue_min > self.revenue_max
        ):
            raise ValueError("revenue_min must not be greater than revenue_max")
        categories = (
            self.categories.not_achieved,
            self.categories.partial,
            self.categories.achieved,
            self.categories.exceeded,
        )
        if self.unit_rate is not None and any(category.units is None for category in categories):
            raise ValueError("units must be given for all four categories when unit_rate is given")
        probabilities = [category.probability for category in categories]
        given = [probability for probability in probabilities if probability is not None]
        if given and len(given) != len(probabilities):
            raise ValueError(
                "probabilities must be given for all four categories or for none of them"
            )
        if given and sum(given) != 100:
            raise ValueError("probabilities must sum to exactly 100.00")
        return self


class FixedPriceTermsCreateRequest(BaseModel):
    """`POST …/commercial-terms` for Fixed Price — the model and the agreed price of the whole
    project (F-06.2; D-1 = A: no milestones).

    Both price fields are required: a Fixed Price rule without a price is a `422`, never created as
    an incomplete rule (ADR-0003, addendum 2026-09-25 SC-4-02, point 2). The currency is stated,
    never inferred from `scenarios.currency` (which may be `null`). `extra="forbid"`: no adjustment,
    milestone or hours field exists to send (D-3 = C, D-1 = A).
    """

    model_config = ConfigDict(extra="forbid")

    model_type: Literal["fixed_price"]
    agreed_price: AgreedPriceInput
    currency: Iso4217Code


CommercialTermsCreateRequest = Annotated[
    TimeAndMaterialTermsCreateRequest
    | StoryPointsTermsCreateRequest
    | OutcomeBasedTermsCreateRequest
    | FixedPriceTermsCreateRequest,
    Field(discriminator="model_type"),
]
"""The request shape as a discriminated union on `model_type` (ADR-0003 addendum 2026-09-25, D-6/A;
ADR-0003, point 9 — chosen by `model_type`, never by the shape of the data): each model's own
request carries only its own fields (Fixed Price, SC-4-02, the same way), and Pydantic itself
refuses a `model_type` its `Literal` does not name, before any of this reaches
`app.data.commercial_terms`. A missing or unknown `model_type` is a `422` naming the field."""


class OutcomeCategoryRead(BaseModel):
    """One category of an Outcome-based rule as it was stored — `null` for absent, never `0`."""

    units: DecimalString | None
    probability: DecimalString | None


class OutcomeCategoriesRead(BaseModel):
    """Four fixed categories — the same shape as `OutcomeCategoriesRequest`."""

    not_achieved: OutcomeCategoryRead
    partial: OutcomeCategoryRead
    achieved: OutcomeCategoryRead
    exceeded: OutcomeCategoryRead


class OutcomeTermsRead(BaseModel):
    """Outcome-based rule parameters for reading (verification round 2 of SC-4-03, R-04) — the same
    shape as `OutcomeBasedTermsCreateRequest` without `model_type`, so a client can display
    exactly what it stored. Amounts as fixed-point strings copied from the row, with no
    rounding; an absent component is `null`. No cost at all — these are revenue parameters."""

    currency: str
    fixed_fee: DecimalString
    success_bonus: DecimalString | None
    unit_rate: DecimalString | None
    revenue_min: DecimalString | None
    revenue_max: DecimalString | None
    categories: OutcomeCategoriesRead


class FixedPriceEditRequest(BaseModel):
    """`PATCH …/commercial-terms` — change the agreed price of a draft (D-6 = A), plus the marker.

    Partial: a field absent from the body is left alone; at least one of the two must be present and
    neither may be `null`. `model_type` is not a field — it is immutable (ADR-0003, point 2), and
    `extra="forbid"` turns an attempt to send it into a `422`.
    """

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime
    """**The rule's** marker, as returned by the read this edit is based on (ADR-0007; ADR-0003,
    "Konsekwencje": it covers the details row). Required, and required with an offset."""

    agreed_price: AgreedPriceInput | None = None
    currency: Iso4217Code | None = None

    @model_validator(mode="after")
    def _at_least_one_field_and_none_of_them_null(self) -> Self:
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        if not changed:
            raise ValueError("An edit must name at least one field to change.")
        nulled = sorted(field for field in changed if getattr(self, field) is None)
        if nulled:
            raise ValueError(f"These fields cannot be set to null: {', '.join(nulled)}")
        return self

    def changes(self) -> dict[str, Any]:
        """The requested changes, read off `model_fields_set`, in declaration order."""
        changed = self.__pydantic_fields_set__ - {"updated_at"}
        return {
            field: getattr(self, field) for field in type(self).model_fields if field in changed
        }


class StoryPointsTermsEditRequest(StoryPointsTermsCreateRequest):
    updated_at: AwareDatetime


class OutcomeBasedTermsEditRequest(OutcomeBasedTermsCreateRequest):
    updated_at: AwareDatetime


class FixedPriceTermsReplaceRequest(FixedPriceTermsCreateRequest):
    updated_at: AwareDatetime


class TimeAndMaterialTermsEditRequest(TimeAndMaterialTermsCreateRequest):
    updated_at: AwareDatetime


CommercialTermsEditRequest = Annotated[
    TimeAndMaterialTermsEditRequest | StoryPointsTermsEditRequest |
    OutcomeBasedTermsEditRequest | FixedPriceTermsReplaceRequest,
    Field(discriminator="model_type"),
]


class CommercialTermsDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    updated_at: AwareDatetime


def _by_model_type(value: Any) -> str:
    """The discriminator of the two response shapes: `fixed_price` or everything else.

    Read off `model_type` — the stored discriminator — whether the value is a model instance or the
    dict FastAPI re-validates. Everything that is not Fixed Price (T&M, no rule, a model this
    version does not know) keeps the SC-4-01 shape.
    """
    model_type = value.get("model_type") if isinstance(value, dict) else getattr(
        value, "model_type", None
    )
    return "fixed_price" if model_type == MODEL_TYPE_FIXED_PRICE else "other"


def _commercial_terms_by_model_type(value: Any) -> str:
    model_type = value.get("model_type") if isinstance(value, dict) else getattr(
        value, "model_type", None
    )
    if model_type == MODEL_TYPE_FIXED_PRICE:
        return "fixed_price"
    return "story_points" if model_type == "story_points" else "other"


class CommercialTermsRead(BaseModel):
    """The rule itself: its id, its model and ADR-0007's marker (covering its details row too)."""

    id: uuid.UUID
    model_type: StoredModelType
    updated_at: datetime
    outcome_terms: OutcomeTermsRead | None
    """Outcome-based rule parameters (R-04). `null` for every other model — T&M has no
    parameters (ADR-0003, point 4) — and for an Outcome-based rule with no details row (then
    `revenue.state` is `incomplete_commercial_terms`). One additive field, the only change to the
    shape of a T&M response."""


class FixedPriceCommercialTermsRead(BaseModel):
    """A Fixed Price rule: `id`, `model_type`, `updated_at` and `outcome_terms` as on
    `CommercialTermsRead`, then its agreed price (SC-4-02). The shape is its own, chosen by
    `model_type`; `outcome_terms` is always `null` here and is carried only so that every model's
    rule has the same keys (SC-4-07, point 11; R-05 of the SC-4-02 review, 2026-09-28) — a client
    reading `commercial_terms.outcome_terms` never meets a missing key.

    `agreed_price` is the stored input at full precision (`NUMERIC(14,4)`) — the value an edit form
    loads, not the rounded revenue (ADR-0002, addendum 2026-09-21, point 2). Both are `null` only
    for a rule whose details row is missing, and then `revenue.state` is
    `incomplete_commercial_terms`.
    """

    id: uuid.UUID
    model_type: Literal["fixed_price"]
    updated_at: datetime
    outcome_terms: None
    """Always `null` — a Fixed Price rule has no Outcome-based parameters (R-05)."""
    agreed_price: DecimalString | None
    currency: str | None


class StoryPointsCommercialTermsRead(CommercialTermsRead):
    model_type: Literal["story_points"]
    price_per_point: DecimalString | None
    accepted_points: int | None
    currency: str | None


CommercialTermsReadAny = Annotated[
    Annotated[CommercialTermsRead, Tag("other")]
    | Annotated[StoryPointsCommercialTermsRead, Tag("story_points")]
    | Annotated[FixedPriceCommercialTermsRead, Tag("fixed_price")],
    Discriminator(_commercial_terms_by_model_type),
]


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
    hours_source: Literal["billable_hours"] | SourceNotApplicable
    vendor_axis: Literal["internal"] | SourceNotApplicable
    rate_source: (
        Literal["live_catalog", "approved_snapshot", "story_points_terms"] | SourceNotApplicable
    )
    """`not_applicable` on all three source fields for a model with no rate catalogue (Outcome-
    based; ADR-0003, addendum 2026-09-25 SC-4-03, point 8) — the assumptions name only what the
    calculation reads. Story Points: `hours_source`/`vendor_axis` `not_applicable`, `rate_source`
    `story_points_terms` (SC-4-04)."""
    rate_windows: list[RateWindowRead]
    unresolved_months: list[UnresolvedMonthRead]
    currencies: list[str]


class FixedPriceRevenueAssumptionsRead(BaseModel):
    """What a Fixed Price revenue depends on (SC-4-02; ADR-0003, addendum 2026-09-25, points 6, 8).

    The fields of `RevenueAssumptionsRead` in the same order, narrowed to Fixed Price's own values,
    and two additions (the SC-4-04 pattern of `story_points_terms`; decision of 2026-09-25 on Issue
    #66):

    - `rate_source` is `fixed_price_terms` — the price is the rule's own row, never the catalogue or
      its snapshot, for a draft and an approved scenario alike; `rate_windows` is always empty;
    - `hours_source` and `vendor_axis` are `not_applicable` — the price reads no hours and no rate
      row;
    - `price_basis` says what the figure is — the agreed price;
    - `price_adjustments` says what it leaves out — `not_included` (D-3 = C, Issue #112).
    """

    model_type: Literal["fixed_price"]
    hours_source: Literal["not_applicable"]
    vendor_axis: Literal["not_applicable"]
    rate_source: Literal["fixed_price_terms"]
    rate_windows: list[RateWindowRead]
    unresolved_months: list[UnresolvedMonthRead]
    currencies: list[str]
    price_basis: Literal["agreed_price"]
    price_adjustments: Literal["not_included"]


RevenueAssumptionsReadAny = Annotated[
    Annotated[RevenueAssumptionsRead, Tag("other")]
    | Annotated[FixedPriceRevenueAssumptionsRead, Tag("fixed_price")],
    Discriminator(_by_model_type),
]


class CategoryRevenueRead(BaseModel):
    """The revenue of one Outcome-based category, after the min/max clamp (points 5b, 6)."""

    category: OutcomeCategory
    units: DecimalString | None
    """`null` when the rule has no per-unit rate and no units were given — never `0`."""
    probability: DecimalString | None
    """`null` when the rule has no probabilities — never `0`."""
    amount: DecimalString


class RevenueRead(BaseModel):
    """The revenue of one scenario, or the named state that withholds it."""

    state: RevenueState
    amount: DecimalString | Literal[NOT_APPLICABLE]
    """A fixed-point string when `state` is `"calculated"`, `"n/a"` otherwise — never `0`. For
    Outcome-based: the **guaranteed** revenue (ADR-0003, addendum 2026-09-25 SC-4-03, point 5a)."""
    currency: str | None
    assumptions_used: RevenueAssumptionsReadAny
    expected_state: ExpectedRevenueState
    """An additive field of SC-4-03 (points 5b-d; gate 1, D-6)."""
    expected_amount: DecimalString | Literal[NOT_APPLICABLE]
    """The expected revenue — an amount only when `expected_state == "calculated"`, otherwise
    `"n/a"`."""
    category_revenues: list[CategoryRevenueRead]
    """Per-category revenues — empty for a model with no categories and for a named revenue
    state."""


class ScenarioCommercialTerms(BaseModel):
    """`GET`/`POST`/`PATCH …/scenarios/{id}/commercial-terms` — the rule and the revenue derived
    from it."""

    scenario_id: uuid.UUID
    scenario_status: ScenarioStatusLabel
    commercial_terms: CommercialTermsReadAny | None
    """`null` when the scenario has no rule — and then `revenue.state` is
    `"no_commercial_terms"`."""
    revenue: RevenueRead
