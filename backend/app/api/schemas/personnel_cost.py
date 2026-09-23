"""Response schema for a scenario's base personnel cost (F-07, SC-5-01; ADR-0013).

Three boundary decisions are visible in the shapes below.

**The figure is explicitly the base cost** (ADR-0013, point 5): `cost_basis` is the literal
`"base"`, and no field anywhere in this payload is, or could be read as, a fully loaded cost, an
overhead, a bonus, a profit or a margin. Criterion K-03 asserts the whole field set by *equality*,
so such a field added later fails a test on the day it is added.

**Two fields are personnel costs and are removed for a caller the gate refuses** — `amount` and
`assumptions_used` (which names every cost rate used). They are `null` then, never `"0.00"`, never
`"n/a"`: `"n/a"` is the named-state sentinel, and reusing it for "withheld" would make "the
catalogue has no cost rate" and "you may not see the cost rate" one answer. The removal is done by
`app.api.response_shaping`, before serialisation (ADR-0005, aneks 2026-09-23 SC-5-01, points 2–3).

**Money crosses the boundary as a fixed-point string** (`DecimalString`), never a JSON float
(ADR-0002).
"""

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.api.schemas.common import DecimalString
from app.api.schemas.project import ScenarioStatusLabel
from app.core.money import NOT_APPLICABLE

PersonnelCostState = Literal[
    "calculated",
    "no_cost_rate",
    "currency_mismatch",
    "no_cost_currency",
]


class CostRateWindowRead(BaseModel):
    """One catalogue window the cost used — the cost rate only, never the selling rate."""

    source_rate_id: uuid.UUID
    effective_from: date
    effective_to: date | None
    """Inclusive, `null` when open-ended — passed through as stored (ADR-0008, point 3)."""
    default_cost_rate: DecimalString
    currency: str


class UnresolvedCostMonthRead(BaseModel):
    """One (position, month) without a single cost rate over the whole month."""

    position_id: uuid.UUID
    period_month: date


class CostAssumptionsRead(BaseModel):
    """What the cost depends on — present on a calculated cost and on a named state alike, and
    gated with the amount because it names the cost rates."""

    hours_source: Literal["planned_allocation_hours"]
    vendor_axis: Literal["internal"]
    rate_source: Literal["live_catalog", "approved_snapshot"]
    rate_windows: list[CostRateWindowRead]
    unresolved_months: list[UnresolvedCostMonthRead]
    currencies: list[str]


class PersonnelCostRead(BaseModel):
    """The base personnel cost of one scenario, or the named state that withholds it."""

    state: PersonnelCostState
    cost_basis: Literal["base"]
    """Always `"base"`: `default_cost_rate` before overheads (ADR-0013, point 5)."""
    amount: DecimalString | Literal[NOT_APPLICABLE] | None
    """A fixed-point string when `state` is `"calculated"`, `"n/a"` for a named state — and `null`
    when the caller may not see personnel costs of this scenario's project."""
    currency: str | None
    assumptions_used: CostAssumptionsRead | None
    """`null` when the caller may not see personnel costs of this scenario's project."""


class ScenarioPersonnelCost(BaseModel):
    """`GET …/scenarios/{id}/personnel-cost` — the scenario's base personnel cost."""

    scenario_id: uuid.UUID
    scenario_status: ScenarioStatusLabel
    personnel_cost: PersonnelCostRead
