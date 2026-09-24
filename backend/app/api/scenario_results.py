"""A scenario's whole-life profit, margin and markup (F-10, SC-7-01; ADR-0004 "fits"; Issue #12).

`GET /projects/{project_id}/scenarios/{scenario_id}/results` — `RESULTS_READ`.

**`RESULTS_READ` on the endpoint, `PERSONNEL_COSTS_READ` on four of its fields** (ADR-0005, aneks
2026-09-24), the same split SC-5-01 introduced: a caller holding `RESULTS_READ` without
`PERSONNEL_COSTS_READ` (or without `project_access.can_view_personnel_costs` for this project) gets
a `200` whose `profit`, `margin`, `markup` and `included_cost` are `null` — a refusal of *those
fields*, never of the resource. `revenue` and `additional_cost` are never gated (they are not
personnel costs by nature — SC-4-01, SC-5-05) and `personnel_cost` carries the SC-5-01/SC-5-06 gate
it already had. The conjunction is applied in
`app.api.response_shaping.shape_scenario_results`/`_without_scenario_profitability`.

**A router of its own**, like every nested scenario path here: the address carries both identifiers
because the scope predicate lives on the project (ADR-0001, addendum 2026-09-19), and a verb on the
commercial, personnel-cost or additional-cost router would put a mixed figure under a module whose
name promises a single, independent calculation (F-06: independent calculation per model).

**Every "nothing here for you" answers with one body** (`SCENARIO_RESULTS_NOT_FOUND_DETAIL`): a
project outside the caller's scope, a project that does not exist and a scenario of another project
are one `404` — the same shape every nested scenario path already has (criterion K-03).

**A `409` this endpoint alone can answer** (Reviewer, SC-7-01, R-01): the revenue and the base
personnel cost are read by two separate statements (`app.data.scenario_results`), and an approval
committing *between* them would otherwise price one against the live catalogue and cost the other
against the frozen snapshot — one `profit` built from two different moments of the same scenario.
`app.data.scenario_results.ScenarioResultsRaceDetected` names that exact disagreement; this endpoint
answers it as a `409`, the same "the scenario changed since it was read, retry" vocabulary every
write path in this repository already uses, applied here to a read instead of a write. Never a
`200` with numbers mixed from two moments, and never a `500`.

**`str(race)` is generic, on purpose** (Reviewer, SC-7-01, R-02, Low): this `except` runs *before*
`shape_scenario_results` and its gates, so the `409` body is a second, ungated channel out of this
endpoint — `ScenarioResultsRaceDetected` never puts a `rate_source` (`"live_catalog"` /
`"approved_snapshot"`) in its own message for exactly that reason, and this handler must keep
passing `str(race)` through unchanged rather than building a more detailed message from the
exception's `revenue_source`/`cost_source` attributes.

**Read only.** There is nothing to write: the three components are written under their own
permissions (`COMMERCIAL_WRITE`, `STAFFING_WRITE`).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import shape_scenario_results
from app.api.schemas.scenario_results import ScenarioResults
from app.core.identity import CallerIdentity, Permission
from app.data.scenario_results import ScenarioResultsRaceDetected, scenario_results_for_caller
from app.db.session import get_session

router = APIRouter(
    prefix="/projects/{project_id}/scenarios/{scenario_id}/results", tags=["scenario-results"]
)

SCENARIO_RESULTS_NOT_FOUND_DETAIL = "Scenario not found."
"""The same wording as `app.api.scenarios.SCENARIO_NOT_FOUND_DETAIL`: the thing that may not exist
is the scenario. The indistinguishability is not maintained by this constant alone —
`app.data.scenario_results` returns the same `None` for every such case."""


@router.get(
    "",
    response_model=ScenarioResults,
    summary=(
        "Read a scenario's whole-life profit, margin and markup, next to the revenue, personnel "
        "cost and additional cost they are built from"
    ),
    responses={
        404: {"description": SCENARIO_RESULTS_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: the scenario's approval status changed while this endpoint "
            "was composing revenue, personnel cost and additional cost into one result. Retry."
        },
    },
)
def read_scenario_results(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.RESULTS_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> ScenarioResults:
    """The scenario-wide result — or a `404` that says nothing about whether the scenario exists.

    - **200** — for a **draft**, priced and costed against the live catalogue; for an **approved**
      scenario, against the windows and rates frozen at its approval (AC-04), exactly as the three
      components' own endpoints already answer. `profit`, `margin`, `markup` and `included_cost`
      are `"n/a"` when at least one of the four sources (`revenue`, the base personnel cost, the
      paid-absence cost, the additional cost) is not itself `calculated` — each source still names
      its own state on its own field, never folded into one shared sentinel. They are `null`
      instead of a number or `"n/a"` when the caller may not see personnel costs of this scenario's
      project (K-06) — `revenue` and `additional_cost` stay visible either way.
    - **404** — the scenario is not the caller's, does not exist, or belongs to another project.
    - **409** — the scenario's approval status changed *while this endpoint was reading it*
      (`ScenarioResultsRaceDetected`, R-01): never a `200` mixing a live-catalogue revenue with an
      approved-snapshot cost.
    - **403** — the permission dependency (`RESULTS_READ`), before the database.
    """
    try:
        view = scenario_results_for_caller(session, caller, project_id, scenario_id)
    except ScenarioResultsRaceDetected as race:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(race)) from None
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIO_RESULTS_NOT_FOUND_DETAIL
        )
    return shape_scenario_results(view, caller)
