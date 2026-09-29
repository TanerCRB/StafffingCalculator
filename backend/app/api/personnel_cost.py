"""A scenario's base personnel cost (F-07, worked-time basis; SC-5-01; ADR-0013).

`GET /projects/{project_id}/scenarios/{scenario_id}/personnel-cost` — `STAFFING_READ`.

**`STAFFING_READ` on the endpoint, `PERSONNEL_COSTS_READ` on the field** (ADR-0005, addendum
2026-09-23 SC-5-01, point 1). The cost is a calculation over the scenario's staffing positions, so
it is read under the permission that reads them. `PERSONNEL_COSTS_READ` is never a requirement of
this endpoint: a caller holding `STAFFING_READ` without it gets a `200` whose amount and rates are
`null` — a refusal of the *field*, not of the resource (the precedent of SC-2-01, point 5). The
conjunction is applied in `app.api.response_shaping.shape_scenario_personnel_cost`.

**A router of its own**, like every nested scenario path here: the address carries both identifiers
because the scope predicate lives on the project (ADR-0001, addendum 2026-09-19), and a verb on the
staffing router would put a cost figure under a prefix whose module promises none.

**Every "nothing here for you" answers with one body** (`PERSONNEL_COST_NOT_FOUND_DETAIL`): a
project outside the caller's scope, a project that does not exist and a scenario of another
project are one `404` (criterion K-05). A scenario whose cost cannot be stated is not one of them —
it is a `200` with a named state.

**Two components, never a total** (SC-5-06; ADR-0013, addendum 2026-09-23 SC-5-06): the base cost
(`amount`) and, beside it, the cost of paid absences (`paid_absence_*`), each with its own state.
The paid-absence amount, its budget part and its assumptions go through the same conjunction as the
base amount; neither component is ever added to the other here (the total is plan block 7's).

**Read only.** There is nothing to write: the inputs are the staffing grid (written under
`STAFFING_WRITE`) and the catalogue (under `CATALOG_WRITE`).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import shape_scenario_personnel_cost
from app.api.schemas.personnel_cost import ScenarioPersonnelCost
from app.core.identity import CallerIdentity, Permission
from app.data.personnel_cost import scenario_cost_for_caller
from app.db.session import get_session

router = APIRouter(
    prefix="/projects/{project_id}/scenarios/{scenario_id}/personnel-cost",
    tags=["personnel-cost"],
)

PERSONNEL_COST_NOT_FOUND_DETAIL = "Scenario not found."
"""The same wording as `app.api.scenarios.SCENARIO_NOT_FOUND_DETAIL`: the thing that may not exist
is the scenario. The indistinguishability is not maintained by this constant alone —
`app.data.personnel_cost` returns the same `None` for every such case."""


@router.get(
    "",
    response_model=ScenarioPersonnelCost,
    summary="Read a scenario's base personnel cost (worked time × base cost rate)",
    responses={404: {"description": PERSONNEL_COST_NOT_FOUND_DETAIL}},
)
def read_personnel_cost(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> ScenarioPersonnelCost:
    """The base personnel cost — or a `404` that says nothing about whether the scenario exists.

    - **200** — for a **draft**, costed against the live catalogue; for an **approved** scenario,
      against the windows frozen at its approval (AC-04; criterion K-07). A cost that cannot be
      stated is `"n/a"` with a named `state` (K-03). `amount` and `assumptions_used` are `null`
      unless the caller holds `PERSONNEL_COSTS_READ` **and** its `project_access` row for this
      scenario's project allows personnel costs (K-04).
    - **404** — the scenario is not the caller's, does not exist, or belongs to another project.
    - **403** — the permission dependency (`STAFFING_READ`), before the database.
    """
    view = scenario_cost_for_caller(session, caller, project_id, scenario_id)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=PERSONNEL_COST_NOT_FOUND_DETAIL
        )
    return shape_scenario_personnel_cost(view, caller)
