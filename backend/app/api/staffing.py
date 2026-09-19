"""Scenario staffing endpoints (F-04, SC-3-01).

Under `/projects/{project_id}/scenarios/{scenario_id}/staffing-positions`:

- `GET    ""` — the whole grid of one scenario (`STAFFING_READ`)
- `POST   ""` — one position with the months it plans for (`STAFFING_WRITE`)
- `PATCH  "/{position_id}/allocations/{period_month}"` — one month of one position
  (`STAFFING_WRITE`)

**The address carries both identifiers** (ADR-0001, addendum 2026-09-19). Not because a staffing
position needs the project id to be found — `scenario_id` is unique on its own — but because the
scope predicate lives on the project: `project_for_caller` is the one entry point that applies the
`project_access` filter, and an address without the project id would need a second scope function to
compose with it. There is none, and there must not be one.

**Every refusal on this path answers with one body** (`STAFFING_NOT_FOUND_DETAIL`). A project
outside the caller's scope, a project that does not exist, a scenario belonging to a different
project, a position belonging to a different scenario and a month row that was never written are
five different facts and one response — identical status, identical text, identical length
(ADR-0005, addendum 2026-09-19, point 4). The write-specific codes (`409` from ADR-0004/ADR-0007,
`422` from validation) must never become an oblique confirmation that a scenario or a position
exists, which is why the scope is resolved before any of them can be reached (see
`app.data.staffing`).

**Nothing here returns a rate, a cost or a currency** (ADR-0005, addendum 2026-09-19, point 5) — the
response schema has no such field at all, so there is no gate to apply and none is applied. The
first endpoint that does show a resolved rate on a position (F-07) reinstates the SC-1-08
conjunction.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import shape_staffing_position, shape_staffing_position_list
from app.api.schemas.staffing import (
    StaffingAllocationEditRequest,
    StaffingPositionCreateRequest,
    StaffingPositionList,
    StaffingPositionRead,
)
from app.core.identity import CallerIdentity, Permission
from app.data.staffing import (
    AllocationMonthNotFound,
    StaffingWriteRefused,
    StaffingWriteRejected,
    create_position,
    list_positions,
    update_allocation,
)
from app.db.session import get_session

router = APIRouter(
    prefix="/projects/{project_id}/scenarios/{scenario_id}/staffing-positions",
    tags=["staffing"],
)

STAFFING_NOT_FOUND_DETAIL = "Scenario staffing not found."
"""One message and one status code for every "there is nothing here for you" case on this path.

Deliberately vague about *what* was not found. Naming the scenario, the position or the month would
make the answer depend on which of them exists, and the existence of a scenario in a project outside
the caller's scope is precisely what must not be confirmable (SC-1-01 criterion 2, generalised to
write actions by ADR-0005's addendum of 2026-09-18, point 3). The indistinguishability is not
maintained by this constant alone: `app.data.staffing` returns the same absence for every one of
those cases, so there is nothing here to tell apart in the first place."""

_MONTH_SEGMENT_DESCRIPTION = (
    "The month whose allocation row is edited, as the first day of that month (YYYY-MM-01)."
)

PeriodMonthSegment = Annotated[date, Path(description=_MONTH_SEGMENT_DESCRIPTION)]
"""The month as a path segment, because it addresses the row (together with the position) rather
than describing it. A body field would make a `PATCH` able to move a month, which is not an edit of
a value — it is a different row, and `UNIQUE (position_id, period_month)` is the only thing that
decides whether it may exist."""


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=STAFFING_NOT_FOUND_DETAIL)


@router.get(
    "",
    response_model=StaffingPositionList,
    summary="List the staffing positions of one scenario, with their monthly hours",
    responses={404: {"description": STAFFING_NOT_FOUND_DETAIL}},
)
def list_staffing_positions(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> StaffingPositionList:
    """The scenario's whole staffing grid, or a `404` that says nothing about whether it exists.

    `STAFFING_READ`, not `PROJECT_READ`: planning staffing is routinely a different person's right
    than reading a project header (ADR-0005, addendum 2026-09-19, point 2), and a permission once
    merged into one cannot be narrowed again without breaking its callers.

    There is no `403` branch here and nothing to write one from: the scope filter is inside the
    query that fetches the rows (`app.data.staffing.list_positions` → `project_for_caller`), so a
    scenario outside the caller's scope never reaches this function.
    """
    positions = list_positions(session, caller, project_id, scenario_id)
    if positions is None:
        raise _not_found()
    return shape_staffing_position_list(positions)


@router.post(
    "",
    response_model=StaffingPositionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a staffing position to one scenario, with the months it plans for",
    responses={
        404: {"description": STAFFING_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: the scenario is approved (its staffing is part of an approved "
            "calculation), or the state of the data refused the write — a dimension id that names "
            "no catalogue row, or two rows for one month. The SQLSTATE and the constraint name say "
            "which."
        },
    },
)
def create_staffing_position(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    payload: StaffingPositionCreateRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> StaffingPositionRead:
    """Create one position and the month rows given with it — or refuse.

    Every decision this endpoint reports is taken one layer down, in `create_position`:

    - **404** — the scenario is not in the caller's scope, does not exist, or belongs to a different
      project. Indistinguishable by construction, not by a rule someone has to remember.
    - **409, "approved"** — refused by the `INSERT ... SELECT ... WHERE status <> 'approved'`, i.e.
      by the database, in the same statement as the write (ADR-0004, addendum 2026-09-19). Nothing
      is written: the refusal *is* the absence of a row to insert.
    - **409, refused by state** — a dimension id that names no catalogue row is rejected by the
      foreign key, not by this schema (criterion K-01's contrast); two rows for one month by the
      unique constraint.
    - **500** — a write that broke for a reason no SQLSTATE classified. It must not arrive as a
      `409` describing a conflict nobody observed (R-01), which is why only `StaffingWriteRefused`
      is caught here and its parent class is not.
    - **403** — the permission dependency, before any of the above and before the database.

    The `409` bodies name the mechanism and quote no row value (NF-11) — the exception carries the
    SQLSTATE and the constraint name only, through `app.data.write_errors`.
    """
    try:
        created = create_position(
            session,
            caller,
            project_id,
            scenario_id,
            role_id=payload.role_id,
            seniority_id=payload.seniority_id,
            location_id=payload.location_id,
            engagement_type_id=payload.engagement_type_id,
            headcount=payload.headcount,
            start_date=payload.start_date,
            end_date=payload.end_date,
            allocations=[
                allocation.model_dump() for allocation in payload.allocations
            ],
        )
    except StaffingWriteRejected as refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal)) from None
    except StaffingWriteRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Refused by the database. {refusal}"
        ) from None
    if created is None:
        raise _not_found()
    return shape_staffing_position(created)


@router.patch(
    "/{position_id}/allocations/{period_month}",
    response_model=StaffingPositionRead,
    summary="Edit the hours of one month of one staffing position",
    responses={
        404: {"description": STAFFING_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: the scenario is approved, or the position changed since it "
            "was read (ADR-0007 concurrency token)."
        },
    },
)
def edit_staffing_allocation(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    period_month: PeriodMonthSegment,
    payload: StaffingAllocationEditRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> StaffingPositionRead:
    """Change one month's hours, and answer with the position and its new token.

    The two `409` reasons are two independent mechanisms, distinguished by the message and not by
    the status code (ADR-0007: one place, two independent reasons for refusal):

    - the scenario is `approved` — permanent, and a retry with a fresh token can never succeed;
    - the position changed since it was read — resolved by re-reading the grid.

    Both are evaluated by the database inside the statement that writes, and both leave every row
    exactly as it was. The `404`-before-`409` precedence is structural: the scenario is resolved
    through the scope-filtered read path first, so a stale token on an invisible scenario cannot
    answer `409` and thereby confirm that it exists.

    A month that has no row yet is a `404`, not an insert: creating a month is part of creating the
    position (`POST`), because a second inserting path would need its own run of the `approved`
    guard and SC-3-01 gives it none. That is a named functional limit of this task, not an accident.
    """
    try:
        edited = update_allocation(
            session,
            caller,
            project_id,
            scenario_id,
            position_id,
            period_month,
            expected_updated_at=payload.updated_at,
            changes=payload.changes(),
        )
    except AllocationMonthNotFound:
        raise _not_found() from None
    except StaffingWriteRejected as refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal)) from None
    except StaffingWriteRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Refused by the database. {refusal}"
        ) from None
    if edited is None:
        raise _not_found()
    return shape_staffing_position(edited)
