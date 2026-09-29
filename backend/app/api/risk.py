"""A scenario's declared risks (F-09 pt 4-5, SC-6-08; ADR-0021).

Under `/projects/{project_id}/scenarios/{scenario_id}/risks`:

- `GET    ""` - one page of the declared risks, each with its representation (`none` /
  `cost_event` / `reserve` / `both`), the double-representation flag and the counts they come from
  (`STAFFING_READ`; ADR-0017 `limit`/`offset`/`total`);
- `POST   ""` - declare a risk (`STAFFING_WRITE`);
- `PATCH  "/{risk_id}"` - rename it, with its marker (`STAFFING_WRITE`);
- `DELETE "/{risk_id}"` - remove it, with its marker (`STAFFING_WRITE`); refused while a cost
  event or a reserve still points at it.

**`STAFFING_READ`/`STAFFING_WRITE`, no new permission, no cost conjunction, scenario-level only**
(ADR-0021, point 9, Q-6 = A): no `position_id` on a risk, so the `headcount = 1` exposure of
ADR-0014 point 11 is not widened. **The read carries kinds and counts only - never an amount**
(gate 1 G-1).

**Every "nothing here for you" answers with one body** (`RISK_NOT_FOUND_DETAIL`): a project outside
the caller's scope, a project that does not exist, a scenario of another project and a risk of
another scenario are four facts and one `404` - on the read and on every write, before any `409`,
and before any paging `422` (ADR-0005 addendum SC-6-08; ADR-0017, point 8). There is no `403` for
scope.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.paging import validated_page
from app.api.response_shaping import shape_risk, shape_scenario_risks
from app.api.schemas.risk import (
    PAGE_QUERY_NOTE,
    RiskCreateRequest,
    RiskDeleteRequest,
    RiskEditRequest,
    RiskRead,
    ScenarioRisks,
)
from app.core.identity import CallerIdentity, Permission
from app.data.risk import (
    DEFAULT_RISK_LIST_LIMIT,
    MAX_RISK_LIST_LIMIT,
    MAX_RISK_LIST_OFFSET,
    RiskNotFound,
    RiskWriteRefused,
    RiskWriteRejected,
    create_risk,
    delete_risk,
    risks_for_caller,
    update_risk,
)
from app.data.staffing import scenario_in_scope
from app.db.session import get_session

router = APIRouter(prefix="/projects/{project_id}/scenarios/{scenario_id}/risks", tags=["risks"])

RISK_NOT_FOUND_DETAIL = "Scenario risks not found."
"""One message and one status for every "there is nothing here for you" case on this path -
deliberately vague about *what* was not found."""

_REFUSED_BY_THE_DATABASE = "Refused by the database."


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RISK_NOT_FOUND_DETAIL)


def _conflict(refusal: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal))


def _refused(refusal: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=f"{_REFUSED_BY_THE_DATABASE} {refusal}"
    )


_WRITE_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": RISK_NOT_FOUND_DETAIL},
    409: {
        "description": "Refused: the scenario is approved (copy it to change its risks), the risk "
        "changed since it was read (ADR-0007 marker), a cost event or a reserve still points at "
        "the risk (delete only), or the database refused the row (a blank or duplicate name, "
        "named by SQLSTATE and constraint)."
    },
}


@router.get(
    "",
    response_model=ScenarioRisks,
    summary="Read a scenario's declared risks and how each is represented",
    responses={
        404: {"description": RISK_NOT_FOUND_DETAIL},
        422: {"description": "limit or offset outside their bounds. " + PAGE_QUERY_NOTE},
    },
)
def read_risks(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_READ))],
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[str | None, Query(description=PAGE_QUERY_NOTE)] = None,
    offset: Annotated[str | None, Query(description=PAGE_QUERY_NOTE)] = None,
) -> ScenarioRisks:
    """Each risk with its representation, the flag and the counts - and no amount.

    - **200** - the page; `total` counts every risk of the scenario.
    - **404** - the scenario is not the caller's, does not exist, or belongs to another project,
      whatever `limit`/`offset` say.
    - **403** - the permission dependency (`STAFFING_READ`), before the database.
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        raise _not_found()
    validated_limit, validated_offset = validated_page(
        limit,
        offset,
        default_limit=DEFAULT_RISK_LIST_LIMIT,
        max_limit=MAX_RISK_LIST_LIMIT,
        max_offset=MAX_RISK_LIST_OFFSET,
    )
    page = risks_for_caller(
        session, caller, project_id, scenario_id, limit=validated_limit, offset=validated_offset
    )
    if page is None:
        raise _not_found()
    return shape_scenario_risks(page)


@router.post(
    "",
    response_model=RiskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Declare a risk of a scenario",
    responses=_WRITE_RESPONSES,
)
def create_scenario_risk(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    payload: RiskCreateRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> RiskRead:
    """Create one risk in one guarded statement - or refuse.

    - **201** - the risk as stored, with its marker and representation `none`.
    - **404** - the scenario is not the caller's. Decided before any `409`.
    - **409** - refused inside the `INSERT ... SELECT ... FOR UPDATE` because the scenario is
      approved, or a duplicate/blank name refused by the database.
    """
    try:
        created = create_risk(session, caller, project_id, scenario_id, name=payload.name)
    except RiskNotFound:
        raise _not_found() from None
    except RiskWriteRejected as refusal:
        raise _conflict(refusal) from None
    except RiskWriteRefused as refusal:
        raise _refused(refusal) from None
    if created is None:
        raise _not_found()
    return shape_risk(created)


@router.patch(
    "/{risk_id}",
    response_model=RiskRead,
    summary="Rename one risk of a scenario",
    responses=_WRITE_RESPONSES,
)
def edit_scenario_risk(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    risk_id: uuid.UUID,
    payload: RiskEditRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> RiskRead:
    """Rename one risk, answering with the row and its new marker. The two `409` reasons are two
    independent mechanisms, told apart by the message: the scenario is `approved` (permanent) or
    this risk changed since it was read (re-read). A risk id of another scenario is a `404`, even
    under an `approved` scenario."""
    try:
        edited = update_risk(
            session,
            caller,
            project_id,
            scenario_id,
            risk_id,
            expected_updated_at=payload.updated_at,
            changes=payload.changes(),
        )
    except RiskNotFound:
        raise _not_found() from None
    except RiskWriteRejected as refusal:
        raise _conflict(refusal) from None
    except RiskWriteRefused as refusal:
        raise _refused(refusal) from None
    if edited is None:
        raise _not_found()
    return shape_risk(edited)


@router.delete(
    "/{risk_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove one risk of a scenario",
    responses=_WRITE_RESPONSES,
)
def delete_scenario_risk(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    risk_id: uuid.UUID,
    payload: RiskDeleteRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Remove one risk - guarded exactly like every other write. `409` while a cost event or a
    reserve still points at it (unlink first); `204` with no body otherwise."""
    try:
        deleted = delete_risk(
            session,
            caller,
            project_id,
            scenario_id,
            risk_id,
            expected_updated_at=payload.updated_at,
        )
    except RiskNotFound:
        raise _not_found() from None
    except RiskWriteRejected as refusal:
        raise _conflict(refusal) from None
    except RiskWriteRefused as refusal:
        raise _refused(refusal) from None
    if deleted is None:
        raise _not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
