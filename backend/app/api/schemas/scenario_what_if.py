"""Request and response schema for the salary-raise "what-if" (F-09 pt.3; SC-6-04, Issue #88;
ADR-0015).

**The response reuses `ScenarioResults` by inheritance, never a parallel type** (ADR-0015, point 4:
"keeps a response shape close to GET …/results"). The fields `GET …/results` answers
(since SC-4-03 including `profitability_state`) are exactly the fields here, so a client already
rendering that endpoint renders this one with one added field (`salary_raise_percent`) and one
different value inside `personnel_cost.assumptions_used.rate_source` — never a second response
shape to learn.

**The query parameter is a percentage, allowed to be negative but floored at `-100`** (ADR-0015,
point 6, tightened by Reviewer R-01, 2026-09-24): `0` is the neutral element K-01 proves, and a
negative value hypothesises a cut, not only a raise — nothing in ADR-0015 or the Issue restricts
this to positive numbers, and a bare `gt=0` would refuse a legitimate hypothesis the domain formula
otherwise handles the same way as a positive one (`dataclasses.replace` multiplies by `1 + p/100`
whatever the sign of `p`). `-100` is not that same "no range rule" as
`app.api.schemas.project.TargetMarginPercent`, though, and deliberately so: a personnel cost rate
is a `Decimal` this formula never checks the sign of (`app.domain.personnel_cost.
base_personnel_cost` sums `hours × rate` without a non-negativity assertion — nothing in ADR-0013
gives it one, because a real catalogue rate is never negative). Below `-100%` the multiplier
`1 + p/100` turns negative and `base_personnel_cost` would silently sum a **negative** hourly rate
across real hours: a `200` with a physically meaningless personnel cost (an employee cannot be paid
a negative wage), `profit` larger than `revenue`, and `margin`/`markup` above `100%` — presented
with the exact same shape and apparent legitimacy as a real result. `-100` is the floor because it
is the one boundary the formula already has a name for: the raised rate is exactly `0`, which is
`base_personnel_cost`'s own "no cost for this hour" — still a rate a real catalogue window could
hold, never a negative one it could not.
"""

from decimal import Decimal
from typing import Annotated

from fastapi import Query

from app.api.schemas.common import DecimalString
from app.api.schemas.scenario_results import ScenarioResults
from app.models.organization_defaults import PERCENT_PRECISION, PERCENT_SCALE

SALARY_RAISE_PERCENT_FLOOR = Decimal("-100")
"""The one bound `salary_raise_percent` carries beyond `NUMERIC(6, 3)`'s own shape (Reviewer R-01,
2026-09-24): below this, the raised rate `cost_rate * (1 + p/100)` turns negative, and
`base_personnel_cost`/`paid_absence_cost` sum a negative `Decimal` rate across real hours with no
rejection of their own — a confident `200` with a personnel cost, profit and margin that are
physically meaningless. Exactly `-100` is still accepted: the raised rate is exactly `0`, a
dimensionally sane "this hour costs nothing", not a negative one."""

SalaryRaisePercentQuery = Annotated[
    Decimal,
    Query(
        max_digits=PERCENT_PRECISION,
        decimal_places=PERCENT_SCALE,
        ge=SALARY_RAISE_PERCENT_FLOOR,
        description=(
            "A hypothetical percentage change applied to every resolved personnel cost rate of "
            "this scenario, computed and returned without persisting anything (ADR-0015). 0 is "
            "the neutral element: the cost/profit figures are byte-identical to the same "
            "scenario's GET .../results, up to personnel_cost.assumptions_used.rate_source. "
            "Negative values hypothesise a cut and are accepted down to -100 (the raised rate "
            "reaches exactly 0); below -100 the raised rate would go negative, which no real "
            "catalogue rate ever does, and is refused."
        ),
    ),
]


class ScenarioWhatIfSalaryRaiseResults(ScenarioResults):
    """`GET …/scenarios/{id}/what-if` — the same fields `ScenarioResults` carries (revenue,
    personnel_cost, additional_cost, included_cost, profit, margin, markup, profitability_state),
    next to the hypothetical raise that produced them.

    Never persisted and never approaching an `approved` scenario (ADR-0015, points 2 and 5):
    `personnel_cost.assumptions_used.rate_source` reads `"what_if_hypothetical"` whenever
    `personnel_cost.state` is `"calculated"` — the third value `app.api.schemas.personnel_cost.
    CostAssumptionsRead.rate_source` grew for exactly this endpoint — while `revenue`'s own
    `assumptions_used.rate_source` is exactly what `GET …/results` reports for it: one of the two
    real catalogue sources for a model priced from the catalogue (T&M), or the model's own
    non-catalogue source — `story_points_terms` (Story Points) or `not_applicable` (Outcome-based,
    ADR-0003 addendum 2026-09-25 SC-4-03, points 8 and 12) — never
    `what_if_hypothetical`: the raise never touches revenue (F-06, rule 10 of the Invariant
    Guardian).
    """

    salary_raise_percent: DecimalString
    """The raise this result was computed with, echoed from the request."""


BillableUtilizationDecreaseQuery = Annotated[
    Decimal,
    Query(
        max_digits=PERCENT_PRECISION,
        decimal_places=PERCENT_SCALE,
        description=(
            "A decrease in billable utilization, in percentage points, applied per position and "
            "month to planned allocation hours. Negative values and results with negative "
            "billable hours are refused with a generic 422."
        ),
    ),
]


class ScenarioWhatIfBillableUtilizationResults(ScenarioResults):
    """`GET .../what-if/billable-utilization` with a T&M revenue-input substitution."""

    billable_utilization_decrease_percentage_points: DecimalString
    """The requested decrease is echoed in the response."""


class ScenarioWhatIfDelayedStartResults(ScenarioResults):
    """`GET .../what-if/delayed-start` result for a whole-calendar-month shift."""

    delay_months: int
