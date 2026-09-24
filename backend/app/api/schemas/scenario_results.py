"""Response schema for a scenario's whole-life profit, margin and markup (F-10; SC-7-01, Issue #12;
ADR-0002 aneks 2026-09-24).

**Three already-proven read shapes, reused rather than re-declared** (ADR-0004 "fits", confirmed at
gate 1): `revenue` is `app.api.schemas.commercial_terms.RevenueRead` (SC-4-01), `personnel_cost` is
`app.api.schemas.personnel_cost.PersonnelCostRead` (SC-5-01/SC-5-06), `additional_cost` is
`app.api.schemas.additional_cost.AdditionalCostTotalRead` (SC-5-05). Each keeps naming its own
`state` independently — a scenario with, say, a `no_rate` revenue and a `calculated` cost reports
both, never one sentinel standing in for whichever of the three failed (ADR-0002, aneks SC-7-01,
"stan złożony").

**Four new fields, gated as a group and never split** (ADR-0005, aneks 2026-09-24): `profit`,
`margin`, `markup` and `included_cost`. `null` when the caller may not see personnel costs of this
scenario's project (the conjunction `PERSONNEL_COSTS_READ` ∧ `project_access.
can_view_personnel_costs`, applied by `app.api.response_shaping._without_scenario_profitability`) —
never `403` of the whole resource. `NOT_APPLICABLE` ("n/a") when the conjunction is open but at
least one of the four components (`revenue`, the base cost, the paid-absence cost, the additional
cost) is not itself `calculated` — the two withholding reasons are deliberately different values so
a client, and a test, can tell "you may not see this" from "this cannot be computed" apart.

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


class ScenarioResults(BaseModel):
    """`GET …/scenarios/{id}/results` — the scenario-wide profit, margin and markup, next to the
    three components they are built from."""

    scenario_id: uuid.UUID
    scenario_status: ScenarioStatusLabel

    revenue: RevenueRead
    personnel_cost: PersonnelCostRead
    """Never gated further than SC-5-01/SC-5-06 already gate it — this endpoint reuses that gate
    unchanged rather than extending it (ADR-0005, aneks 2026-09-24)."""
    additional_cost: AdditionalCostTotalRead
    """Never gated (ADR-0014, point 11; ADR-0005, aneks 2026-09-23 SC-5-05, point 1) — visible here
    exactly as it is on its own endpoint, even when `profit`/`margin`/`markup`/`included_cost` are
    withheld below."""

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
