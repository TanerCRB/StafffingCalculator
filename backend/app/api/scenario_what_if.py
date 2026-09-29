"""Salary-raise "what-if": a scenario's whole-life result recomputed against a hypothetical
percentage change to its personnel cost rates — never persisted (F-09 pt.3; SC-6-04, Issue #88;
ADR-0015).

`GET /projects/{project_id}/scenarios/{scenario_id}/what-if?salary_raise_percent=<decimal>` —
`RESULTS_READ`, the same permission `GET …/results` already declares (ADR-0015, point 8): this is
a read that computes a hypothesis instead of persisting one, so it needs no permission the real
result does not already require, and it gates `profit`/`margin`/`markup`/`included_cost`/
`personnel_cost` on the identical `PERSONNEL_COSTS_READ` ∧ `project_access.can_view_personnel_costs`
conjunction `GET …/results` applies to the same fields (`app.api.response_shaping.
shape_scenario_what_if_salary_raise`, reusing `_without_scenario_personnel_costs`/
`_without_scenario_profitability` unchanged).

**A `GET`, not a body-less `POST`** (ADR-0015, point 7): unlike `copy`/`archive`/`approve`/
`duplicate`, this endpoint writes nothing — a read with a parameter, in the same family as
`GET …/results` and `GET …/compare`.

**Scope: `draft` only** (ADR-0015, point 5). A scenario that is, or becomes mid-request, `approved`
answers the same `404` as a scenario outside the caller's scope or one that does not exist — never
a distinct error shape, and never a **served** compute against a substituted, hypothetical rate: the
real revenue and cost are read in-process before this refusal (the same figures that caller's own
`GET …/results` already shows them for this scenario), but the raise is never applied and no
response is ever built from them (`app.data.scenario_what_if`, "what 'never' means here"). A status
flip landing *between* any two of the three real reads this endpoint composes (revenue, cost,
additional cost) is caught first by the inherited race guard
(`app.data.scenario_results.ScenarioResultsRaceDetected`, the same `409` `GET …/results` already
answers with); a scenario that was `approved` throughout falls through to this endpoint's own
status refusal instead, because the race guard's three reads agree and have nothing to catch.

**Read only.** There is nothing to write: `app.data.scenario_what_if` never calls
`Session.add`/`flush`/`merge`/`commit` (ADR-0015, point 2) — a mutation to this module that added
one would be a mutation the structural test in `tests/test_scenario_what_if.py` kills.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import (
    shape_scenario_what_if_billable_utilization,
    shape_scenario_what_if_salary_raise,
)
from app.api.schemas.scenario_what_if import (
    BillableUtilizationDecreaseQuery,
    SalaryRaisePercentQuery,
    ScenarioWhatIfBillableUtilizationResults,
    ScenarioWhatIfSalaryRaiseResults,
)
from app.core.identity import CallerIdentity, Permission
from app.data.scenario_results import ScenarioResultsRaceDetected
from app.data.scenario_what_if import (
    scenario_what_if_billable_utilization_for_caller,
    scenario_what_if_salary_raise_for_caller,
)
from app.db.session import get_session
from app.domain.revenue_time_and_material import InvalidBillableUtilizationDecrease

router = APIRouter(
    prefix="/projects/{project_id}/scenarios/{scenario_id}/what-if", tags=["scenario-what-if"]
)

SCENARIO_WHAT_IF_NOT_FOUND_DETAIL = "Scenario not found."
"""The same wording — and the same body — as `app.api.scenario_results.
SCENARIO_RESULTS_NOT_FOUND_DETAIL`: a project outside scope, a nonexistent scenario, a scenario of
another project, and (ADR-0015, point 5) a scenario that is or becomes `approved` are one `404`,
never a distinguishable shape for the last one (K-03/K-06)."""


@router.get(
    "",
    response_model=ScenarioWhatIfSalaryRaiseResults,
    summary=(
        "Recompute a draft scenario's whole-life profit, margin and markup under a hypothetical "
        "salary raise, without persisting anything"
    ),
    responses={
        404: {"description": SCENARIO_WHAT_IF_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: the scenario's approval status changed while this endpoint "
            "was composing revenue and personnel cost. Retry."
        },
    },
)
def read_scenario_what_if_salary_raise(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    salary_raise_percent: SalaryRaisePercentQuery,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.RESULTS_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> ScenarioWhatIfSalaryRaiseResults:
    """The scenario's hypothetical result — or a `404` that says nothing about whether the
    scenario exists, is another caller's, or is approved (ADR-0015, point 5).

    - **200** — a **draft** scenario, priced against the live catalogue exactly as `GET …/results`
      would price it, and costed against the live catalogue **substituted** by
      `salary_raise_percent`. `0` reproduces `GET …/results`'s own cost/profit figures to the digit
      (K-01); `profit`, `margin`, `markup`, `included_cost` and `personnel_cost` are `null` under
      the same personnel-cost gate `GET …/results` already applies (K-04); `revenue` and
      `additional_cost` stay visible either way, and are byte-identical across every raise
      magnitude — the raise never touches them (K-05).
    - **404** — the scenario is not the caller's, does not exist, belongs to another project, or is
      (or becomes, mid-request) `approved` (K-03, K-06).
    - **409** — the scenario's approval status changed *while this endpoint was reading it*
      (`ScenarioResultsRaceDetected`): never a `200` mixing a live-catalogue revenue with an
      approved-snapshot cost, hypothetical or not.
    - **403** — the permission dependency (`RESULTS_READ`), before the database.
    - **422** — `salary_raise_percent` missing, or shaped outside `NUMERIC(6, 3)` (the same
      precision `app.api.schemas.project.TargetMarginPercent` validates against).
    """
    try:
        view = scenario_what_if_salary_raise_for_caller(
            session, caller, project_id, scenario_id, salary_raise_percent=salary_raise_percent
        )
    except ScenarioResultsRaceDetected as race:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(race)) from None
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIO_WHAT_IF_NOT_FOUND_DETAIL
        )
    return shape_scenario_what_if_salary_raise(view, caller)


@router.get(
    "/billable-utilization",
    response_model=ScenarioWhatIfBillableUtilizationResults,
    summary=(
        "Recompute a draft T&M scenario's revenue and profit under reduced billable utilization, "
        "without persisting anything"
    ),
    responses={
        404: {"description": SCENARIO_WHAT_IF_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: the scenario's approval status changed while this endpoint "
            "was composing revenue and personnel cost. Retry."
        },
        422: {"description": "Invalid utilization decrease."},
    },
)
def read_scenario_what_if_billable_utilization(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    billable_utilization_decrease_percentage_points: BillableUtilizationDecreaseQuery,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.RESULTS_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> ScenarioWhatIfBillableUtilizationResults:
    """Return a T&M revenue substitution on a draft; non-T&M and approved scenarios share 404."""
    try:
        view = scenario_what_if_billable_utilization_for_caller(
            session,
            caller,
            project_id,
            scenario_id,
            decrease_percentage_points=billable_utilization_decrease_percentage_points,
        )
    except ScenarioResultsRaceDetected as race:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(race)) from None
    except InvalidBillableUtilizationDecrease:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid utilization decrease.",
        ) from None
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIO_WHAT_IF_NOT_FOUND_DETAIL
        )
    return shape_scenario_what_if_billable_utilization(view, caller)
