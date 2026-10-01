"""Response schema for a scenario's whole-life profit, margin and markup (F-10; SC-7-01, Issue #12;
ADR-0002 addendum 2026-09-24).

**Three already-proven read shapes, reused rather than re-declared** (ADR-0004 "fits", confirmed at
gate 1): `revenue` is `app.api.schemas.commercial_terms.RevenueRead` (SC-4-01), `personnel_cost` is
`app.api.schemas.personnel_cost.PersonnelCostRead` (SC-5-01/SC-5-06), `additional_cost` is
`app.api.schemas.additional_cost.AdditionalCostTotalRead` (SC-5-05). Each keeps naming its own
`state` independently — a scenario with, say, a `no_rate` revenue and a `calculated` cost reports
both, never one sentinel standing in for whichever of the three failed (ADR-0002, addendum SC-7-01,
"composite state").

**Four new fields, gated as a group and never split** (ADR-0005, addendum 2026-09-24 SC-7-01):
`profit`, `margin`, `markup` and `included_cost`. `null` when the caller may not see personnel
costs of this scenario's project (the conjunction `PERSONNEL_COSTS_READ` ∧ `project_access.
can_view_personnel_costs`, applied by `app.api.response_shaping._without_scenario_profitability`)
— never `403` of the whole resource. `NOT_APPLICABLE` ("n/a") when the conjunction is open but at
least one of the four components (`revenue`, the base cost, the paid-absence cost, the additional
cost) is not itself `calculated` — the two withholding reasons are deliberately different values
so a client, and a test, can tell "you may not see this" from "this cannot be computed" apart.

**`profitability_state` says why the four are `"n/a"`** (SC-4-03, R-01 of the verification,
2026-09-25): `not_applicable` — a component is not `calculated`; `currency_mismatch` — all four are,
but in more than one currency (possible when the scenario declares no currency: each component then
checks its own currency against nothing). An additive field, not a new value inside the four: those
keep their existing vocabulary (a decimal, `"n/a"` or `null`), so a client that reads only them
still sees a withheld figure, never a number summed across currencies. **Not gated**: it states no
figure, and every currency it is derived from is already visible on the three component payloads
(`SCENARIO_COST_FIELDS` leaves `currency` and `state` outside the gate for the same reason).

**Money crosses the boundary as a fixed-point string** (`DecimalString`), never a JSON float
(ADR-0002).
"""

import uuid
from typing import Literal

from pydantic import BaseModel

from app.api.schemas.additional_cost import AdditionalCostTotalRead
from app.api.schemas.commercial_terms import RevenueRead
from app.api.schemas.common import DecimalString
from app.api.schemas.personnel_cost import PersonnelCostRead
from app.api.schemas.project import ScenarioStatusLabel
from app.core.money import NOT_APPLICABLE

ProfitabilityState = Literal["calculated", "not_applicable", "currency_mismatch"]
"""The API spelling of `app.domain.scenario_results.ProfitabilityState` (the way `RevenueState`
spells the revenue's named states) — a schema module does not import the domain."""


class ScenarioResultsBase(BaseModel):
    """Existing scenario-wide result fields shared with compute-without-persist endpoints."""

    scenario_id: uuid.UUID
    scenario_status: ScenarioStatusLabel

    revenue: RevenueRead
    personnel_cost: PersonnelCostRead
    """Never gated further than SC-5-01/SC-5-06 already gate it — this endpoint reuses that gate
    unchanged rather than extending it (ADR-0005, addendum 2026-09-24 SC-7-01)."""
    additional_cost: AdditionalCostTotalRead
    """Never gated (ADR-0014, point 11; ADR-0005, addendum 2026-09-23 SC-5-05, point 1) — visible
    here exactly as it is on its own endpoint, even when
    `profit`/`margin`/`markup`/`included_cost` are withheld below."""

    included_cost: DecimalString | Literal[NOT_APPLICABLE] | None
    """`base` personnel cost + paid-absence cost + additional cost, all three already stated. `null`
    under the personnel-cost gate; `"n/a"` when any of the three (or the revenue below) is not
    itself `calculated` — never `0` and never a partial sum."""
    profit: DecimalString | Literal[NOT_APPLICABLE] | None
    """`revenue − included_cost`. Numeric even when `revenue` is exactly `0.00` (AC-05) — only a
    zero *included_cost* would make this `"n/a"`, and it never does: a stated `included_cost` is
    always a real number once every component is `calculated`."""
    margin: DecimalString | Literal[NOT_APPLICABLE] | None
    """`profit / revenue × 100`, via `app.core.money.ratio_percent` — `"n/a"` when `revenue` is
    exactly `0.00` (AC-05), whatever `profit` and `markup` are."""
    markup: DecimalString | Literal[NOT_APPLICABLE] | None
    """`profit / included_cost × 100`, via `app.core.money.ratio_percent` — `"n/a"` only when
    `included_cost` is exactly `0.00`, independently of `margin`."""
    profitability_state: ProfitabilityState
    """`calculated` when the four fields above are numbers (or `null` under the gate); otherwise
    why they are `"n/a"` — `not_applicable` or `currency_mismatch` (SC-4-03, R-01). Never gated."""


class ScenarioResults(ScenarioResultsBase):
    """`GET …/scenarios/{id}/results` — the scenario-wide profitability result."""

    expected_profit: DecimalString | Literal[NOT_APPLICABLE] | None
    """Expected revenue minus included cost; `null` under the same personnel-cost gate as profit."""
    expected_margin: DecimalString | Literal[NOT_APPLICABLE] | None
    """Expected profit divided by expected revenue via `ratio_percent`; `n/a` at zero revenue."""


class ScenarioResultsComparison(BaseModel):
    """`GET …/scenarios/compare` — the same ten result fields `ScenarioResults` carries once per
    named `scenario_id`, in request order (SC-6-02, F-09 pt 2; ADR-0001/ADR-0005, addendum
    2026-09-24).

    **A set of independent rows, never an aggregate.** No field here sums, nets or averages
    `revenue`/`profit`/etc. across the compared scenarios (ADR-0005 addendum SC-7-01 pt.6,
    reconfirmed for this endpoint) — each `ScenarioResults` row is exactly what `GET
    …/scenarios/{scenario_id}/results` would answer for that one scenario, unchanged. Perturbing one
    scenario's input changes only its own row; the others are byte-identical to before (K-01).

    **All-or-nothing, never partial.** This type is only ever constructed once every named
    `scenario_id` has resolved for the caller — a `scenario_id` outside scope, or belonging to
    another project, refuses the whole request before this type is built (`404`,
    `SCENARIO_RESULTS_NOT_FOUND_DETAIL`), and a race on any one of them refuses it with `409`. There
    is no row-shaped "not found" or "conflict" marker here on purpose (gate-1 decision, SC-6-02)."""

    results: list[ScenarioResults]
