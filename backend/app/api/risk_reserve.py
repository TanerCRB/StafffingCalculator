"""A scenario's risk reserves (F-09 pt 4, SC-6-08; ADR-0021).

Under `/projects/{project_id}/scenarios/{scenario_id}/risk-reserves`:

- `GET    ""` - one page of the reserves, and **the reserve total of all of them**
  (`STAFFING_READ`; ADR-0017 `limit`/`offset`/`total`);
- `POST   ""` - add one reserve (`STAFFING_WRITE`);
- `PATCH  "/{reserve_id}"` - edit one reserve, with its marker (`STAFFING_WRITE`);
- `DELETE "/{reserve_id}"` - remove one reserve, with its marker (`STAFFING_WRITE`).

**The reserve total is a separate named result, reported beside the additional-cost total and
never inside it** (ADR-0021, point 3, Q-3 = A): `/results` and `/compare` do not read it, so
`included_cost`, `profit`, `margin` and `markup` are unchanged by a reserve. Folding it in is a
follow-up decision.

Access, scope and the `404` body are those of `app.api.risk` (`STAFFING_READ`/`STAFFING_WRITE`,
scenario-level only, one `404` for every "nothing here for you", before any `409` and before any
paging `422`).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.paging import validated_page
from app.api.response_shaping import shape_reserve, shape_scenario_reserves
from app.api.schemas.risk import (
    PAGE_QUERY_NOTE,
    ReserveCreateRequest,
    ReserveDeleteRequest,
    ReserveEditRequest,
    ReserveRead,
    ScenarioReserves,
)
from app.core.identity import CallerIdentity, Permission
from app.data.risk_reserve import (
    DEFAULT_RESERVE_LIST_LIMIT,
    MAX_RESERVE_LIST_LIMIT,
    MAX_RESERVE_LIST_OFFSET,
    ReserveNotFound,
    ReserveWriteRefused,
    ReserveWriteRejected,
    create_reserve,
    delete_reserve,
    reserves_for_caller,
    update_reserve,
)
from app.data.staffing import scenario_in_scope
from app.db.session import get_session

router = APIRouter(
    prefix="/projects/{project_id}/scenarios/{scenario_id}/risk-reserves",
    tags=["risk-reserves"],
)

RESERVE_NOT_FOUND_DETAIL = "Scenario risks not found."
"""The **same body** as `app.api.risk.RISK_NOT_FOUND_DETAIL`, on purpose: the risk and the reserve
paths answer "nothing here for you" identically, so the status and body of a refusal reveal nothing
about which of the two a caller asked for (ADR-0021, point 7)."""

_REFUSED_BY_THE_DATABASE = "Refused by the database."


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RESERVE_NOT_FOUND_DETAIL)


def _conflict(refusal: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal))


def _refused(refusal: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=f"{_REFUSED_BY_THE_DATABASE} {refusal}"
    )


_WRITE_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": RESERVE_NOT_FOUND_DETAIL},
    409: {
        "description": "Refused: the scenario is approved (copy it to change its reserves), the "
        "reserve changed since it was read (ADR-0007 marker), or the database refused the row - a "
        "CHECK on its shape, named by SQLSTATE and constraint."
    },
}


@router.get(
    "",
    response_model=ScenarioReserves,
    summary="Read a scenario's risk reserves and their total, reported beside additional cost",
    responses={
        404: {"description": RESERVE_NOT_FOUND_DETAIL},
        422: {"description": "limit or offset outside their bounds. " + PAGE_QUERY_NOTE},
    },
)
def read_risk_reserves(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_READ))],
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[str | None, Query(description=PAGE_QUERY_NOTE)] = None,
    offset: Annotated[str | None, Query(description=PAGE_QUERY_NOTE)] = None,
) -> ScenarioReserves:
    """The reserves and their total - `"calculated"` with an amount and a currency, or a named
    state (`currency_mismatch`, `no_cost_currency`) with `"n/a"`: never `0`, never a partial sum.
    The total covers every reserve of the scenario, not only the page."""
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        raise _not_found()
    validated_limit, validated_offset = validated_page(
        limit,
        offset,
        default_limit=DEFAULT_RESERVE_LIST_LIMIT,
        max_limit=MAX_RESERVE_LIST_LIMIT,
        max_offset=MAX_RESERVE_LIST_OFFSET,
    )
    page = reserves_for_caller(
        session, caller, project_id, scenario_id, limit=validated_limit, offset=validated_offset
    )
    if page is None:
        raise _not_found()
    return shape_scenario_reserves(page)


@router.post(
    "",
    response_model=ReserveRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a risk reserve to a scenario (optionally linked to a declared risk)",
    responses=_WRITE_RESPONSES,
)
def create_scenario_reserve(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    payload: ReserveCreateRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> ReserveRead:
    """Create one reserve in one guarded statement - or refuse.

    - **201** - the row as stored, with its marker.
    - **404** - the scenario is not the caller's, or `risk_id` names no risk of this scenario.
      Decided before any `409`.
    - **409** - refused inside the `INSERT ... SELECT ... FOR UPDATE` because the scenario is
      approved, or a CHECK on the row's shape.
    - **422** - the request schema: more than four decimal places, a non-positive amount, a period
      that does not match the type.
    """
    try:
        created = create_reserve(
            session,
            caller,
            project_id,
            scenario_id,
            risk_id=payload.risk_id,
            amount=payload.amount,
            currency=payload.currency,
            reserve_type=payload.reserve_type,
            start_month=payload.start_month,
            end_month=payload.end_month,
        )
    except ReserveNotFound:
        raise _not_found() from None
    except ReserveWriteRejected as refusal:
        raise _conflict(refusal) from None
    except ReserveWriteRefused as refusal:
        raise _refused(refusal) from None
    if created is None:
        raise _not_found()
    return shape_reserve(created)


@router.patch(
    "/{reserve_id}",
    response_model=ReserveRead,
    summary="Edit one risk reserve of a scenario",
    responses=_WRITE_RESPONSES,
)
def edit_scenario_reserve(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    reserve_id: uuid.UUID,
    payload: ReserveEditRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> ReserveRead:
    """Change one reserve, answering with the row and its new marker. A reserve id of another
    scenario is a `404`, even under an `approved` scenario."""
    try:
        edited = update_reserve(
            session,
            caller,
            project_id,
            scenario_id,
            reserve_id,
            expected_updated_at=payload.updated_at,
            changes=payload.changes(),
        )
    except ReserveNotFound:
        raise _not_found() from None
    except ReserveWriteRejected as refusal:
        raise _conflict(refusal) from None
    except ReserveWriteRefused as refusal:
        raise _refused(refusal) from None
    if edited is None:
        raise _not_found()
    return shape_reserve(edited)


@router.delete(
    "/{reserve_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove one risk reserve of a scenario",
    responses=_WRITE_RESPONSES,
)
def delete_scenario_reserve(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    reserve_id: uuid.UUID,
    payload: ReserveDeleteRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Remove one reserve, guarded exactly like every other write. `204`, no body."""
    try:
        deleted = delete_reserve(
            session,
            caller,
            project_id,
            scenario_id,
            reserve_id,
            expected_updated_at=payload.updated_at,
        )
    except ReserveNotFound:
        raise _not_found() from None
    except ReserveWriteRejected as refusal:
        raise _conflict(refusal) from None
    except ReserveWriteRefused as refusal:
        raise _refused(refusal) from None
    if deleted is None:
        raise _not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
