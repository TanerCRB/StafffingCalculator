"""Response schema for a scenario's personnel cost (F-07, SC-5-01/SC-5-02; ADR-0013).

Three boundary decisions are visible in the shapes below.

**The base figure is still explicitly the base cost** (ADR-0013, point 5): `cost_basis` is the
literal `"base"`, and `amount`/`currency`/`assumptions_used` are untouched by SC-5-02 — no
overhead, bonus, profit or margin was ever mixed into them, and none is now. **Since SC-5-02, a
second, named figure sits beside it**: `fully_loaded_amount` and `surcharge_amount` (criteria
K-01/K-02), the field set change ADR-0013's addendum of 2026-09-25 SC-5-02 (point 8) pre-announced
for exactly this task — the equality assertion `test_personnel_cost.py::_assert_field_sets` (K-03)
grows with it, deliberately, rather than a fully loaded field hiding inside the base one.

**Two fields are personnel costs and are removed for a caller the gate refuses** — `amount` and
`assumptions_used` (which names every cost rate used, and — since SC-5-02 — every window's surcharge
percentage and flag alongside it). They are `null` then, never `"0.00"`, never `"n/a"`: `"n/a"` is
the named-state sentinel, and reusing it for "withheld" would make "the catalogue has no cost rate"
and "you may not see the cost rate" one answer. The removal is done by `app.api.response_shaping`,
before serialisation (ADR-0005, addendum 2026-09-23 SC-5-01, points 2–3). `fully_loaded_amount` and
`surcharge_amount` are removed alongside `amount`, through the same `SCENARIO_COST_FIELDS` set.

**Money crosses the boundary as a fixed-point string** (`DecimalString`), never a JSON float
(ADR-0002).

**Since SC-5-06 the payload carries a second, named component** — the cost of paid absences
(`paid_absence_*`, ADR-0013 addendum 2026-09-23 SC-5-06) — beside the base amount and never summed
with it. Its amount, its budget part and its assumptions are personnel costs and join
`SCENARIO_COST_FIELDS`; its state and currency do not. **Since SC-5-02 (criterion K-05) the
component has its own fully loaded pair too** — `paid_absence_fully_loaded_amount`/
`paid_absence_surcharge_amount` — gated the same way.

**Since SC-5-03 a fourth, named component** — the fixed-amount basis's own cost (`fixed_amount_*`,
ADR-0013 addendum 2026-09-25 SC-5-03), beside the base amount and the paid-absence one and never
summed with either. The same split: `fixed_amount_amount` and `fixed_amount_assumptions_used` are
personnel costs and join `SCENARIO_COST_FIELDS`; `fixed_amount_state` and `fixed_amount_currency`
do not (ADR-0005, addendum 2026-09-25 SC-5-03, point 1 — a caller without the conjunction may
learn that this scenario's fixed-amount cost is, say, `currency_mismatch`, never the amount or the
currency of any line that produced it). **Carries no fully loaded/surcharge pair of its own**
(SC-5-02 crossed with SC-5-03): a fixed amount is not a rate a surcharge multiplies, so there is
no second figure to gate beside `fixed_amount_amount` the way `paid_absence` and the base cost
each gained one.
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
    surcharge_percent: DecimalString
    """The window's own surcharge percentage (SC-5-02) — present here, ungated like the catalogue's
    own field (`app.api.schemas.catalog.CatalogRate.surcharge_percent`, K-04), even though this
    payload as a whole sits behind the SC-1-08 conjunction: the percentage was never the gated part,
    the amount it multiplies is."""
    includes_surcharge: bool


class UnresolvedCostMonthRead(BaseModel):
    """One (position, month) without a single cost rate over the whole month."""

    position_id: uuid.UUID
    period_month: date


class CostAssumptionsRead(BaseModel):
    """What the cost depends on — present on a calculated cost and on a named state alike, and
    gated with the amount because it names the cost rates."""

    hours_source: Literal["planned_allocation_hours"]
    vendor_axis: Literal["internal"]
    rate_source: Literal["live_catalog", "approved_snapshot", "what_if_hypothetical"]
    """Closed at three values since SC-6-04 (ADR-0015): `what_if_hypothetical` appears only on
    `GET …/scenarios/{id}/what-if` (`app.api.schemas.scenario_what_if`), never on this scenario's
    own `GET …/personnel-cost` or `GET …/results`, which still only ever see the two real
    sources."""
    rate_windows: list[CostRateWindowRead]
    unresolved_months: list[UnresolvedCostMonthRead]
    currencies: list[str]


PaidAbsenceCostState = Literal[
    "calculated",
    "no_calendar",
    "no_statutory_leave_type",
    "no_budget",
    "no_cost_rate",
    "currency_mismatch",
    "no_cost_currency",
]
"""The paid-absence component's states (ADR-0013, addendum 2026-09-23 SC-5-06, point 4) — the
calendar's and the budget's own names first, then the base cost's."""


class PaidAbsenceMonthHoursRead(BaseModel):
    """The hours one (position, month) contributes to the paid-absence component."""

    position_id: uuid.UUID
    period_month: date
    manual_hours: DecimalString
    """Working-day hours of booked absences whose type generates cost — never `× headcount`."""
    budget_hours: DecimalString
    """The leave budget's top-up share of the month (`0.00` when the statutory type does not
    generate cost — `budget_part` says which)."""
    budget_part: Literal["applied", "statutory_leave_not_cost_generating"]


class UnresolvedPaidAbsenceMonthRead(BaseModel):
    """One (position, month) the component could not cost, and why."""

    position_id: uuid.UUID
    period_month: date
    reason: Literal["no_calendar", "no_statutory_leave_type", "no_budget", "no_cost_rate"]


FixedAmountCostState = Literal["calculated", "currency_mismatch", "no_cost_currency"]
"""The fixed-amount basis's own states (ADR-0013, addendum 2026-09-25 SC-5-03, point 1) — its own,
independent vocabulary, not the worked-time basis's `PersonnelCostState` reused: the two formulas
are two independent predicates (K-01), and the values happen to read the same because both mirror
`ADR-0014` point 7, not because one is derived from the other."""


class FixedAmountLineRead(BaseModel):
    """One `fixed_amount` position's contribution to the fixed-amount component."""

    position_id: uuid.UUID
    amount: DecimalString
    currency: str


class FixedAmountAssumptionsRead(BaseModel):
    """What the fixed-amount component — or its absence — depends on, gated with its amount
    exactly as `CostAssumptionsRead` is (it names a stated cost, one `headcount = 1` position away
    from naming what one person costs — ADR-0005, addendum 2026-09-25 SC-5-03, point 2)."""

    lines: list[FixedAmountLineRead]
    currencies: list[str]


class PaidAbsenceAssumptionsRead(BaseModel):
    """What the paid-absence component depends on — gated with its amount. No cost rate here: the
    rates are the base cost's, in `assumptions_used.rate_windows`."""

    hours_source: Literal["paid_absences_and_leave_budget_top_up"]
    months: list[PaidAbsenceMonthHoursRead]
    unresolved_months: list[UnresolvedPaidAbsenceMonthRead]
    currencies: list[str]


class PersonnelCostRead(BaseModel):
    """The base personnel cost of one scenario, or the named state that withholds it — and, beside
    it, the paid-absence component (SC-5-06), never added to it."""

    state: PersonnelCostState
    cost_basis: Literal["base"]
    """Always `"base"`: `default_cost_rate` before overheads (ADR-0013, point 5) — for the base
    amount and for the paid-absence component alike (addendum 2026-09-23 SC-5-06, point 5)."""
    amount: DecimalString | Literal[NOT_APPLICABLE] | None
    """A fixed-point string when `state` is `"calculated"`, `"n/a"` for a named state — and `null`
    when the caller may not see personnel costs of this scenario's project. **The base cost only**:
    the paid-absence component is never included in it (no total in SC-5-06)."""
    currency: str | None
    assumptions_used: CostAssumptionsRead | None
    """`null` when the caller may not see personnel costs of this scenario's project."""

    fully_loaded_amount: DecimalString | Literal[NOT_APPLICABLE] | None
    """The fully loaded cost — `amount` plus its surcharge (SC-5-02, criterion K-01). A field of its
    own, in the identical `currency` as `amount`, under the identical `state`: a month `amount`
    cannot state is a month this figure cannot state either (`app.domain.personnel_cost.
    fully_loaded_personnel_cost` shares its three checks with `base_personnel_cost`). Gated exactly
    like `amount` (K-03); `null` for the same reason `amount` is."""
    surcharge_amount: DecimalString | Literal[NOT_APPLICABLE] | None
    """The surcharge alone — `fully_loaded_amount` minus `amount`, from one unrounded sum, never
    computed by subtracting the two rounded fields back out. `"0.00"` for a scenario whose rates all
    carry `includes_surcharge = true` (K-02), never `null` unless the whole figure is withheld."""

    paid_absence_state: PaidAbsenceCostState
    """The component's own state — independent of `state`. Shown to every caller, like `state`."""
    paid_absence_amount: DecimalString | Literal[NOT_APPLICABLE] | None
    """The whole component (manual paid absences + budget top-up) × the month's cost rate; `"n/a"`
    for a named state; `null` when the caller may not see personnel costs."""
    paid_absence_budget_amount: DecimalString | Literal[NOT_APPLICABLE] | None
    """The part of `paid_absence_amount` that came from the leave budget's top-up — gated like the
    amount (point 6)."""
    paid_absence_currency: str | None
    paid_absence_assumptions_used: PaidAbsenceAssumptionsRead | None
    """`null` when the caller may not see personnel costs of this scenario's project."""

    paid_absence_fully_loaded_amount: DecimalString | Literal[NOT_APPLICABLE] | None
    """The paid-absence component's fully loaded cost (SC-5-02, criterion K-05) — the same
    treatment `fully_loaded_amount` gets for the base cost, applied to `paid_absence_amount`."""
    paid_absence_surcharge_amount: DecimalString | Literal[NOT_APPLICABLE] | None
    """The surcharge alone, on the paid-absence component — the mirror of `surcharge_amount`."""

    fixed_amount_state: FixedAmountCostState
    """The fixed-amount basis's own state (SC-5-03) — independent of `state` above and shown to
    every caller, like it: naming *why* a fixed-amount figure cannot be stated carries no amount
    and no currency by itself (ADR-0005, addendum 2026-09-25 SC-5-03, point 1 — the property this
    field's own gate test proves, not assumes)."""
    fixed_amount_amount: DecimalString | Literal[NOT_APPLICABLE] | None
    """The scenario's `fixed_amount` positions, summed — a **fourth**, independent figure beside
    `amount` (worked time), `fully_loaded_amount`/`surcharge_amount` and `paid_absence_amount`,
    never added to any of them (SC-5-03 out of scope: "a scenario cost total combining bases").
    `"n/a"` for a named state; `null` when the caller may not see personnel costs of this
    scenario's project."""
    fixed_amount_currency: str | None
    fixed_amount_assumptions_used: FixedAmountAssumptionsRead | None
    """`null` when the caller may not see personnel costs of this scenario's project."""


class ScenarioPersonnelCost(BaseModel):
    """`GET …/scenarios/{id}/personnel-cost` — the scenario's base personnel cost."""

    scenario_id: uuid.UUID
    scenario_status: ScenarioStatusLabel
    personnel_cost: PersonnelCostRead
