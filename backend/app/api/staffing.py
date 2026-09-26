"""Scenario staffing endpoints (F-04, SC-3-01; F-05, SC-3-02).

Under `/projects/{project_id}/scenarios/{scenario_id}/staffing-positions`:

- `GET    ""` — the whole grid of one scenario, with each month's derived capacity
  (`STAFFING_READ`)
- `POST   ""` — one position with the months it plans for (`STAFFING_WRITE`; also
  `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs` when the request's `cost_basis` is
  `fixed_amount` — F-07, SC-5-03; Security-Auditor finding, bramka 1 SC-5-03)
- `PATCH  "/{position_id}"` — the position's personnel-cost basis (`STAFFING_WRITE` ∧
  `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs`, F-07, SC-5-03; Security-Auditor finding,
  bramka 1 SC-5-03)
- `PATCH  "/{position_id}/allocations/{period_month}"` — one month of one position
  (`STAFFING_WRITE`)
- `GET    "/{position_id}/absences"` — the absences of one position (`STAFFING_READ`)
- `POST   "/{position_id}/absences"` — add one absence (`STAFFING_WRITE`)
- `DELETE "/{position_id}/absences/{absence_id}"` — remove one absence (`STAFFING_WRITE`)

**The absence endpoints declare `STAFFING_READ`/`STAFFING_WRITE` and no new permission**
(ADR-0005, addendum 2026-09-22, point 5). The granularity argument that split staffing from the
project header — "planning staffing is routinely a different person's right" — does not separate
planning absences from planning staffing: it is the same activity. A separate `ABSENCE_*` would be
granularity with no subject to exercise it.

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
response schema has no such field at all, so there is no gate to apply and none is applied on the
*read* side of any endpoint on this router. Two write paths are the exception (F-07, SC-5-03): the
cost-basis `PATCH` unconditionally, and the position `POST` when its `cost_basis` is `fixed_amount`
— both persist `fixed_amount`, a personnel-cost figure, so both reinstate the SC-1-08 conjunction on
the write itself, even though neither response carries such a field either (bramka 1 SC-5-03,
Security-Auditor finding — see `_require_personnel_cost_write_access`).
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import (
    shape_staffing_absence_list,
    shape_staffing_position,
    shape_staffing_position_list,
)
from app.api.schemas.staffing import (
    StaffingAbsenceCreateRequest,
    StaffingAbsenceDeleteRequest,
    StaffingAbsenceList,
    StaffingAllocationEditRequest,
    StaffingPositionCostBasisEditRequest,
    StaffingPositionCreateRequest,
    StaffingPositionList,
    StaffingPositionRead,
)
from app.core.identity import CallerIdentity, Permission
from app.data.staffing import (
    AbsenceNotFound,
    AllocationMonthNotFound,
    PositionNotFound,
    StaffingWriteRefused,
    StaffingWriteRejected,
    create_absence,
    create_position,
    delete_absence,
    list_absences,
    list_positions,
    scenario_view_in_scope,
    update_allocation,
    update_position_cost_basis,
)
from app.db.session import get_session
from app.models.staffing import COST_BASIS_FIXED_AMOUNT

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


_PERSONNEL_COST_WRITE_DENIED_DETAIL = (
    "Caller lacks the personnel-cost visibility needed to write a position's cost basis."
)
"""`fixed_amount` is, at `headcount = 1`, directly the cost of the position (ADR-0005, aneks
2026-09-25 SC-5-03, Q4) — the same figure the read side never shows without the SC-1-08
conjunction. Security-Auditor finding, bramka 1 SC-5-03: a caller could set that figure while
holding only `STAFFING_WRITE`, without ever holding `PERSONNEL_COSTS_READ` or this project's
`can_view_personnel_costs`, and never read it back — a write-side hole beside a read side that is
fully gated. Shared by both write paths that can persist `fixed_amount`: `create_staffing_position`
(only when the request's `cost_basis='fixed_amount'` — a `worked_time` create writes no
personnel-cost figure and needs no conjunction) and `edit_staffing_position_cost_basis`
(unconditionally, since it edits *only* the three cost-basis fields)."""


def _require_personnel_cost_write_access(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> None:
    """Refuse a write that would persist `fixed_amount` unless the caller holds the SC-1-08
    conjunction for this scenario's project — the read side's own gate, applied here to a write.

    `404` before `403` (the same precedence every refusal on this path keeps, ADR-0005 addendum
    2026-09-19 point 4): the scope is resolved through `scenario_view_in_scope`, the same function
    the read path (`app.data.personnel_cost.scenario_cost_for_caller`) uses, so a caller outside the
    project's scope never learns from the status code alone that the project or scenario exists.
    Only once the scenario is confirmed in scope is the conjunction itself checked.
    """
    in_scope = scenario_view_in_scope(session, caller, project_id, scenario_id)
    if in_scope is None:
        raise _not_found()
    project_view, _scenario = in_scope
    if not (caller.has(Permission.PERSONNEL_COSTS_READ) and project_view.can_view_personnel_costs):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_PERSONNEL_COST_WRITE_DENIED_DETAIL
        )


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
        403: {"description": _PERSONNEL_COST_WRITE_DENIED_DETAIL},
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

    **The SC-1-08 conjunction, but only when the request would persist `fixed_amount`**
    (Security-Auditor finding, bramka 1 SC-5-03, extended from `PATCH` to this endpoint on the
    human's explicit follow-up: `create_position` has the identical gap `edit_staffing_position_
    cost_basis` had). A request whose `cost_basis` is (still) `worked_time` — the default, and every
    request this repository's tests sent before SC-5-03 — writes no personnel-cost figure at all, so
    it needs no conjunction and is unaffected: `_require_personnel_cost_write_access` is not even
    called. Only `cost_basis == 'fixed_amount'` calls it, before `create_position` runs, through the
    same `scenario_view_in_scope` the `PATCH` endpoint and the personnel-cost read use.

    Every other decision this endpoint reports is taken one layer down, in `create_position`:

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
    - **403** — the permission dependency, before any of the above and before the database; for a
      `fixed_amount` create, the SC-1-08 conjunction above, before `create_position` as well.

    The `409` bodies name the mechanism and quote no row value (NF-11) — the exception carries the
    SQLSTATE and the constraint name only, through `app.data.write_errors`.
    """
    if payload.cost_basis == COST_BASIS_FIXED_AMOUNT:
        _require_personnel_cost_write_access(session, caller, project_id, scenario_id)
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
            cost_basis=payload.cost_basis,
            fixed_amount=payload.fixed_amount,
            fixed_amount_currency=payload.fixed_amount_currency,
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


@router.patch(
    "/{position_id}",
    response_model=StaffingPositionRead,
    summary="Edit the personnel-cost basis of one staffing position",
    responses={
        403: {"description": _PERSONNEL_COST_WRITE_DENIED_DETAIL},
        404: {"description": STAFFING_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: the scenario is approved, the position changed since it was "
            "read (ADR-0007 concurrency token), or fixed_amount/fixed_amount_currency were named "
            "without cost_basis while the position's stored basis is not already fixed_amount."
        },
    },
)
def edit_staffing_position_cost_basis(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    payload: StaffingPositionCostBasisEditRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> StaffingPositionRead:
    """Change `cost_basis`/`fixed_amount`/`fixed_amount_currency` (F-07, SC-5-03), and answer with
    the position and its new token.

    **`STAFFING_WRITE` on the endpoint, `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs` on the
    write itself** (Security-Auditor finding, bramka 1 SC-5-03) — unlike every other write on this
    path, because this one persists a personnel-cost figure rather than a dimension, a headcount or
    an hours count. The conjunction is the read side's own (`app.data.personnel_cost`,
    `app.api.response_shaping`), resolved here through the same `scenario_view_in_scope` the read
    path calls, not a second predicate invented at this call site. Refused as a `403` naming no
    project and no scenario (NF-11), evaluated **before** the write and before the `404`/`409`
    branches below can be reached — a caller who cannot view personnel costs learns nothing about
    whether the position exists from the status code it gets instead, because both a missing
    position and a denied write short-circuit before `update_position_cost_basis` ever runs. This
    is a stricter rule than the catalogue's own `default_cost_rate` write (`CATALOG_WRITE` may set
    it without `PERSONNEL_COSTS_READ`, ADR-0014 point 11 Q-7=B): a catalogue rate is organisational
    data shared by every dimension tuple, while `fixed_amount` is one identified position's own cost
    figure — the two are not the same write and this task does not claim they must agree.

    The three `409` reasons, decided by the database inside the single statement that writes both
    the guard and the change (`app.data.staffing.update_position_cost_basis`):

    - the scenario is `approved` — permanent;
    - `fixed_amount`/`fixed_amount_currency` were named without `cost_basis` while the position's
      stored basis is not already `fixed_amount` (Guardian finding, bramka 1 SC-5-03) — resolved by
      also naming `cost_basis='fixed_amount'`, or by re-reading to confirm the basis first;
    - the position changed since it was read — resolved by re-reading.

    `cost_basis`/`fixed_amount`/`fixed_amount_currency` are never part of the response body
    (`StaffingPositionRead` has no such field — ADR-0005, aneks 2026-09-25 SC-5-03, Q4): this
    endpoint answers with the same dimension-and-hours shape every other write on this path does, so
    a caller reading back what it just wrote here has to go through the personnel-cost endpoint,
    exactly as it would for a resolved catalogue rate.
    """
    _require_personnel_cost_write_access(session, caller, project_id, scenario_id)
    try:
        edited = update_position_cost_basis(
            session,
            caller,
            project_id,
            scenario_id,
            position_id,
            expected_updated_at=payload.updated_at,
            changes=payload.changes(),
        )
    except PositionNotFound:
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


# --- absences (F-05, SC-3-02) -------------------------------------------------------------------
#
# Three endpoints, one body for every absence (`STAFFING_NOT_FOUND_DETAIL`) and not one new
# mechanism. Everything they report is decided one layer down, in `app.data.staffing`: the scope,
# the `approved` refusal, the ADR-0007 token and the lock that serialises them against an approval.


@router.get(
    "/{position_id}/absences",
    response_model=StaffingAbsenceList,
    summary="List the planned absences of one staffing position",
    responses={404: {"description": STAFFING_NOT_FOUND_DETAIL}},
)
def list_staffing_absences(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> StaffingAbsenceList:
    """The absences of one position, or a `404` that says nothing about whether it exists.

    **There is no `403` branch here and nothing to build one from** (criterion K-10). The scope
    filter is inside the query that fetches the rows (`list_absences` → `scenario_in_scope` →
    `project_for_caller`), so an absence of a project outside the caller's scope never reaches this
    function, and the answer is identical — status, body and length — to the answer for a position
    id nobody ever created.

    **An empty list and a `404` are two different answers**: a position with no absences planned is
    `200 {"absences": []}`, and only a position that is not there (or not the caller's) is a `404`.
    Collapsing the two would make "nothing planned" indistinguishable from "mistyped id".
    """
    absences = list_absences(session, caller, project_id, scenario_id, position_id)
    if absences is None:
        raise _not_found()
    return shape_staffing_absence_list(absences)


@router.post(
    "/{position_id}/absences",
    response_model=StaffingPositionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a planned absence to one staffing position",
    responses={
        404: {"description": STAFFING_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: the scenario is approved (its staffing is part of an approved "
            "calculation), the position changed since it was read (ADR-0007 concurrency token), or "
            "the state of the data refused the write — an absence type id that names no dictionary "
            "row. The message says which."
        },
    },
)
def create_staffing_absence(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    payload: StaffingAbsenceCreateRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> StaffingPositionRead:
    """Plan one absence against one position — or refuse.

    Answers with the **whole position**, not with the absence alone, for the reason the allocation
    edit does: the caller needs the position's *new* token for its next write, and a screen showing
    a grid has no second schema for "the grid after a write". The response therefore also carries
    the recomputed `derived_capacity_hours` of every month, which is the point of adding an absence
    in the first place.

    The three refusals, all decided inside the one statement that writes (`create_absence`):

    - **404** — the scenario or the position is not the caller's, does not exist, or belongs
      somewhere else. Indistinguishable by construction.
    - **409, "approved"** — the scenario is frozen (ADR-0004). Permanent; the way forward is a copy.
    - **409, "changed since it was read"** — the ADR-0007 token moved. Resolved by re-reading.
    - **409, refused by the database** — an `absence_type_id` naming no dictionary row is rejected
      by the foreign key, not by this schema.
    - **403** — the permission dependency, before any of the above and before the database.

    A body carrying a person's name, a note or a justification is a `422` (`extra="forbid"`), and
    there is no column it could have reached anyway (ADR-0005, addendum 2026-09-22, point 6).
    """
    try:
        created = create_absence(
            session,
            caller,
            project_id,
            scenario_id,
            position_id,
            expected_updated_at=payload.updated_at,
            absence_type_id=payload.absence_type_id,
            start_date=payload.start_date,
            end_date=payload.end_date,
        )
    except AbsenceNotFound:
        raise _not_found() from None
    except StaffingWriteRejected as refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal)) from None
    except StaffingWriteRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Refused by the database. {refusal}"
        ) from None
    if created is None:
        raise _not_found()
    return shape_staffing_position(created)


@router.delete(
    "/{position_id}/absences/{absence_id}",
    response_model=StaffingPositionRead,
    summary="Remove a planned absence from one staffing position",
    responses={
        404: {"description": STAFFING_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: the scenario is approved, or the position changed since it "
            "was read (ADR-0007 concurrency token)."
        },
    },
)
def delete_staffing_absence(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    absence_id: uuid.UUID,
    payload: StaffingAbsenceDeleteRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.STAFFING_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> StaffingPositionRead:
    """Remove one absence, and answer with the position and its new token.

    **The first `DELETE` in this API, and it is guarded exactly like every other write** — because
    ADR-0004 does not distinguish changing an approved calculation from removing a row of it. The
    `approved` predicate, the ADR-0007 token and the lock against a concurrent approval are all in
    the `WHERE` of the statement that deletes (criteria K-13, K-12, K-20).

    **The `404`-before-`409` precedence is structural**, twice over: the scenario is resolved
    through the scope-filtered read path first, and the existence of the absence row is a condition
    of the guarded statement itself, so deleting an id that is not there under an `approved`
    scenario is a `404` and not a `409` advising a copy against which the same request would be a
    `404` as well (the R-01 correction).

    **A body on a `DELETE`**, carrying the token — see `StaffingAbsenceDeleteRequest` for why that
    rather than an `If-Match` header or no token at all.
    """
    try:
        remaining = delete_absence(
            session,
            caller,
            project_id,
            scenario_id,
            position_id,
            absence_id,
            expected_updated_at=payload.updated_at,
        )
    except AbsenceNotFound:
        raise _not_found() from None
    except StaffingWriteRejected as refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal)) from None
    except StaffingWriteRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Refused by the database. {refusal}"
        ) from None
    if remaining is None:
        raise _not_found()
    return shape_staffing_position(remaining)
