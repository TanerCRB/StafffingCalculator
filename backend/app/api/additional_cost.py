"""A scenario's additional costs (F-08, SC-5-05; ADR-0014).

Under `/projects/{project_id}/scenarios/{scenario_id}/additional-costs`:

- `GET    ""` — the cost rows and their sum, spread per month (`STAFFING_READ`);
- `POST   ""` — add one cost (`STAFFING_WRITE`);
- `PATCH  "/{cost_id}"` — edit one cost, with its marker (`STAFFING_WRITE`);
- `DELETE "/{cost_id}"` — remove one cost, with its marker (`STAFFING_WRITE`).

**`STAFFING_READ`/`STAFFING_WRITE`, no new permission and no cost conjunction** (ADR-0014, point 11,
Q-7 = B; ADR-0005, addendum 2026-09-23 SC-5-05, point 1) — the argument SC-3-02 made for absences
(point 5 of its addendum): a separate `ADDITIONAL_COST_*` would be granularity with no subject to
exercise it. **A router of its own** anyway, for the reason every nested scenario path has one: a
verb on the staffing router would put a money figure under a module that promises none.

**The address carries both identifiers** (ADR-0001, addendum 2026-09-19): the scope predicate
lives on the project and `project_for_caller` is its only entry point.

**Every "nothing here for you" answers with one body** (`ADDITIONAL_COST_NOT_FOUND_DETAIL`): a
project outside the caller's scope, a project that does not exist, a scenario of another project, a
cost of another scenario and a position of another scenario are five facts and one `404` — on the
read and on every write, and before any `409` can be reached (ADR-0005, addendum SC-5-05, point 4;
criterion K-08).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import shape_additional_cost, shape_scenario_additional_costs
from app.api.schemas.additional_cost import (
    AdditionalCostCreateRequest,
    AdditionalCostDeleteRequest,
    AdditionalCostEditRequest,
    AdditionalCostRead,
    ScenarioAdditionalCosts,
)
from app.core.identity import CallerIdentity, Permission
from app.data.additional_cost import (
    AdditionalCostNotFound,
    AdditionalCostWriteRefused,
    AdditionalCostWriteRejected,
    additional_costs_for_caller,
    create_additional_cost,
    delete_additional_cost,
    update_additional_cost,
)
from app.db.session import get_session

router = APIRouter(
    prefix="/projects/{project_id}/scenarios/{scenario_id}/additional-costs",
    tags=["additional-costs"],
)

ADDITIONAL_COST_NOT_FOUND_DETAIL = "Scenario additional costs not found."
"""One message and one status for every "there is nothing here for you" case on this path.

Deliberately vague about *what* was not found, for the reason `STAFFING_NOT_FOUND_DETAIL` gives: a
message naming the scenario, the cost or the position would depend on which of them exists. The
indistinguishability is not maintained by this constant alone — `app.data.additional_cost` answers
every such case with `None` or `AdditionalCostNotFound`, and both become this body."""

_REFUSED_BY_THE_DATABASE = "Refused by the database."


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=ADDITIONAL_COST_NOT_FOUND_DETAIL
    )


def _conflict(refusal: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal))


def _refused(refusal: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=f"{_REFUSED_BY_THE_DATABASE} {refusal}"
    )


_WRITE_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": ADDITIONAL_COST_NOT_FOUND_DETAIL},
    409: {
        "description": "Refused: the scenario is approved (copy it to change its costs), the cost "
        "changed since it was read (ADR-0007 marker), or the database refused the row — a CHECK on "
        "its shape or a category that does not exist, named by SQLSTATE and constraint."
    },
}


@router.get(
    "",
    response_model=ScenarioAdditionalCosts,
    summary="Read a scenario's additional costs and their sum, spread per month",
    responses={404: {"description": ADDITIONAL_COST_NOT_FOUND_DETAIL}},
)
def read_additional_costs(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> ScenarioAdditionalCosts:
    """The rows and the sum — or a `404` that says nothing about whether the scenario exists.

    - **200** — the sum is `"calculated"` with an amount and a currency, or a named state
      (`currency_mismatch`, `no_cost_currency`) with `"n/a"` — never `0`, never a partial sum.
    - **404** — the scenario is not the caller's, does not exist, or belongs to another project.
    - **403** — the permission dependency (`STAFFING_READ`), before the database.
    """
    view = additional_costs_for_caller(session, caller, project_id, scenario_id)
    if view is None:
        raise _not_found()
    return shape_scenario_additional_costs(view)


@router.post(
    "",
    response_model=AdditionalCostRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add an additional cost to a scenario (optionally to one of its staffing positions)",
    responses=_WRITE_RESPONSES,
)
def create_scenario_additional_cost(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    payload: AdditionalCostCreateRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> AdditionalCostRead:
    """Create one cost in one guarded statement — or refuse.

    - **201** — the row as stored, with its marker.
    - **404** — the scenario is not the caller's (or does not exist, or belongs to another project),
      or `position_id` (or, since SC-6-08, `risk_id`) names no position (risk) of this scenario.
      Decided before any `409`.
    - **409, "approved"** — refused inside the `INSERT … SELECT … FOR UPDATE` (ADR-0004).
    - **409, refused by the database** — a CHECK on the row's shape or a missing category.
    - **422** — the request schema: more than four decimal places, a non-positive amount, a period
      that does not match the type.
    - **500** — a write that broke for a reason no SQLSTATE classified; never dressed as a `409`.
    """
    try:
        created = create_additional_cost(
            session,
            caller,
            project_id,
            scenario_id,
            category_id=payload.category_id,
            position_id=payload.position_id,
            risk_id=payload.risk_id,
            amount=payload.amount,
            currency=payload.currency,
            cost_type=payload.cost_type,
            start_month=payload.start_month,
            end_month=payload.end_month,
            funding_source=payload.funding_source,
        )
    except AdditionalCostNotFound:
        raise _not_found() from None
    except AdditionalCostWriteRejected as refusal:
        raise _conflict(refusal) from None
    except AdditionalCostWriteRefused as refusal:
        raise _refused(refusal) from None
    if created is None:
        raise _not_found()
    return shape_additional_cost(created)


@router.patch(
    "/{cost_id}",
    response_model=AdditionalCostRead,
    summary="Edit one additional cost of a scenario",
    responses=_WRITE_RESPONSES,
)
def edit_scenario_additional_cost(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    cost_id: uuid.UUID,
    payload: AdditionalCostEditRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> AdditionalCostRead:
    """Change one cost, and answer with the row and its new marker.

    The two `409` reasons are two independent mechanisms, told apart by the message: the scenario is
    `approved` (permanent — copy it) or this cost changed since it was read (re-read it). A cost id
    of another scenario is a `404`, even under an `approved` scenario.
    """
    try:
        edited = update_additional_cost(
            session,
            caller,
            project_id,
            scenario_id,
            cost_id,
            expected_updated_at=payload.updated_at,
            changes=payload.changes(),
        )
    except AdditionalCostNotFound:
        raise _not_found() from None
    except AdditionalCostWriteRejected as refusal:
        raise _conflict(refusal) from None
    except AdditionalCostWriteRefused as refusal:
        raise _refused(refusal) from None
    if edited is None:
        raise _not_found()
    return shape_additional_cost(edited)


@router.delete(
    "/{cost_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove one additional cost of a scenario",
    responses=_WRITE_RESPONSES,
)
def delete_scenario_additional_cost(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    cost_id: uuid.UUID,
    payload: AdditionalCostDeleteRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Remove one cost — guarded exactly like every other write
    (ADR-0004, addendum SC-5-05, point 3).

    `204`, with no body: the marker is per cost row, so no other row's marker moved and there is
    no new token a client needs back (unlike an absence, whose delete rotates the position's token).
    """
    try:
        deleted = delete_additional_cost(
            session,
            caller,
            project_id,
            scenario_id,
            cost_id,
            expected_updated_at=payload.updated_at,
        )
    except AdditionalCostNotFound:
        raise _not_found() from None
    except AdditionalCostWriteRejected as refusal:
        raise _conflict(refusal) from None
    except AdditionalCostWriteRefused as refusal:
        raise _refused(refusal) from None
    if deleted is None:
        raise _not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
