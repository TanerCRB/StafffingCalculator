"""Request and response schemas for a scenario's commercial rule and revenue (F-06.1, SC-4-01).

Two boundary decisions are visible in the shapes below.

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
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.api.schemas.common import DecimalString
from app.api.schemas.project import ScenarioStatusLabel
from app.core.money import NOT_APPLICABLE

ModelType = Literal["time_and_material"]
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


class CommercialTermsCreateRequest(BaseModel):
    """`POST …/commercial-terms` — the one thing a rule says today: which model prices the scenario.

    `extra="forbid"`: there is no rate, override, cap or day length to send (ADR-0003, points 4 and
    7, "Odłożone"), and a field the server silently ignored would be a promise it does not keep.
    """

    model_config = ConfigDict(extra="forbid")

    model_type: ModelType


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
    """What the revenue depends on — present on a calculated revenue and on a named state alike."""

    model_type: StoredModelType | None
    hours_source: Literal["billable_hours"]
    vendor_axis: Literal["internal"]
    rate_source: Literal["live_catalog", "approved_snapshot"]
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
