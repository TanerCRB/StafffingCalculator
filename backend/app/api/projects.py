"""Project endpoints.

- `GET /projects` — the caller's project list (SC-1-05), read only.
- `POST /projects` — create a project (SC-1-01).
- `GET /projects/{id}` — read one project (SC-1-01).
- `PATCH /projects/{id}` — edit one project (SC-1-02).
- `POST /projects/{id}/copy` — copy a project with all its scenarios (SC-1-03).
- `POST /projects/{id}/archive` — archive a project (SC-1-04).

No endpoint here builds a query of its own: reads go through `app.data.project_reads`, writes
through `app.data.project_writes` (ADR-0001, addendum 2026-09-18).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import shape_project_detail, shape_project_list
from app.api.schemas.project import (
    ProjectCreateRequest,
    ProjectDetail,
    ProjectEditRequest,
    ProjectListResponse,
)
from app.core.identity import CallerIdentity, Permission
from app.data.commercial_terms import CommercialTermsNotCopyable
from app.data.project_reads import (
    caller_has_accessible_projects,
    list_projects_for_caller,
    project_for_caller,
)
from app.data.project_writes import (
    ProjectCopyRefused,
    ProjectEditRefused,
    archive_project,
    copy_project,
    create_project,
    update_project,
)
from app.db.session import get_session
from app.models.project import ProjectStatus

router = APIRouter(prefix="/projects", tags=["projects"])

DEFAULT_PROJECT_LIST_LIMIT = 20
MAX_PROJECT_LIST_LIMIT = 100
MAX_PROJECT_LIST_OFFSET = 1_000_000


def _page_integer(raw: str | None, name: str, default: int, maximum: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{name} must be an integer") from None
    if value < (1 if name == "limit" else 0) or value > maximum:
        raise HTTPException(status_code=422, detail=f"{name} is outside the allowed range")
    return value

PROJECT_NOT_FOUND_DETAIL = "Project not found."
"""One message, one status code, for both "no such project" and "not yours".

SC-1-01, criterion 2: the two cases must be indistinguishable to the caller. That means no id
echoed back, no "forbidden" variant, no differing message length — a 403 here would confirm the
existence of a project the caller may not see, and an id in the message would confirm it just as
well. The indistinguishability is not maintained by this constant alone: `project_for_caller`
returns the same `None` for both cases, so there is nothing here to tell apart in the first
place."""


@router.get(
    "",
    response_model=ProjectListResponse,
    summary="List the projects the caller has access to",
)
def list_projects(
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.PROJECT_READ))],
    session: Annotated[Session, Depends(get_session)],
    search: Annotated[str | None, Query()] = None,
    project_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[str | None, Query()] = None,
    offset: Annotated[str | None, Query()] = None,
) -> ProjectListResponse:
    """Projects in the caller's `project_access` scope, archived ones included and marked.

    The scope filter lives in `app.data.project_reads.accessible_projects` — the one shared
    read path ADR-0001 (addendum) requires. This endpoint writes no query of its own.
    """
    caller_has_accessible_projects(session, caller)
    effective_limit = _page_integer(
        limit, "limit", DEFAULT_PROJECT_LIST_LIMIT, MAX_PROJECT_LIST_LIMIT
    )
    effective_offset = _page_integer(offset, "offset", 0, MAX_PROJECT_LIST_OFFSET)
    status_by_label = {"Active": "active", "Archived": "archived"}
    if project_status is not None and project_status not in status_by_label:
        raise HTTPException(status_code=422, detail="status must be Active or Archived")
    selected_status = (
        ProjectStatus(status_by_label[project_status]) if project_status is not None else None
    )
    views, total = list_projects_for_caller(
        session,
        caller,
        search=search,
        status=selected_status,
        limit=effective_limit,
        offset=effective_offset,
    )
    return shape_project_list(views, caller, total=total)


@router.post(
    "",
    response_model=ProjectDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
)
def create_project_endpoint(
    payload: ProjectCreateRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.PROJECT_CREATE))],
    session: Annotated[Session, Depends(get_session)],
) -> ProjectDetail:
    """Create a project and grant its creator access to it.

    `PROJECT_CREATE`, not `PROJECT_READ`: ADR-0005 separates actions from reading by role, so a
    read-only viewer must not reach this endpoint. The permission is declared as a dependency —
    the endpoint has no way to run without the check having run first.
    """
    created = create_project(
        session,
        caller,
        name=payload.name,
        client=payload.client,
        owner=payload.owner,
        delivery_period_start=payload.delivery_period.start,
        delivery_period_end=payload.delivery_period.end,
        reporting_currency=payload.reporting_currency,
        description=payload.description,
    )
    return shape_project_detail(created, caller)


@router.get(
    "/{project_id}",
    response_model=ProjectDetail,
    summary="Read one project the caller has access to",
    responses={404: {"description": PROJECT_NOT_FOUND_DETAIL}},
)
def read_project(
    project_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.PROJECT_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> ProjectDetail:
    """One project by id, or a 404 that says nothing about whether it exists.

    There is no 403 branch and no "exists but forbidden" state to write one from: the scope
    filter is inside the query (`project_for_caller`), so a project outside the caller's
    `project_access` never reaches this function.
    """
    view = project_for_caller(session, caller, project_id)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=PROJECT_NOT_FOUND_DETAIL
        )
    return shape_project_detail(view, caller)


@router.patch(
    "/{project_id}",
    response_model=ProjectDetail,
    summary="Edit a project the caller has access to",
    responses={
        404: {"description": PROJECT_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: the project changed since it was read, or the edit touched "
            "fields frozen by an approved scenario."
        },
    },
)
def edit_project(
    project_id: uuid.UUID,
    payload: ProjectEditRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.PROJECT_EDIT))],
    session: Annotated[Session, Depends(get_session)],
) -> ProjectDetail:
    """Apply a partial edit, or refuse — 404, 409 or 403, and never a 200 that changed nothing.

    Every decision this endpoint reports is taken one layer down, in `update_project`:

    - **404** — `update_project` returns `None`, because it resolves the project through the same
      scope-filtered read path as `GET` and therefore cannot tell "not yours" from "no such
      project". That is also what gives ADR-0007's 404-before-409 precedence: the concurrency
      check is never reached for a project outside the caller's scope, so a 409 cannot become a
      side channel confirming that one exists.
    - **409** — either reason from ADR-0007/ADR-0004, mapped from `ProjectEditRefused`. The two
      are distinguished by the message, not by the status code: both mean "the state of the
      project, not your request, is what stopped this", and both leave the row untouched.
    - **403** — the permission dependency, before any of the above and before the database.

    `PATCH`, not `PUT`: the request body names the fields that change, so an edit screen that
    never loaded `reporting_currency` cannot re-send a stale copy of it — and a group-2 field
    absent from the body is not an edit of that field, hence not something ADR-0004 needs to
    refuse.
    """
    try:
        edited = update_project(
            session,
            caller,
            project_id,
            expected_updated_at=payload.updated_at,
            changes=payload.changes(),
        )
    except ProjectEditRefused as refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal)) from None
    if edited is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=PROJECT_NOT_FOUND_DETAIL
        )
    return shape_project_detail(edited, caller)


@router.post(
    "/{project_id}/archive",
    response_model=ProjectDetail,
    summary="Archive a project",
    responses={404: {"description": PROJECT_NOT_FOUND_DETAIL}},
)
def archive_project_endpoint(
    project_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.PROJECT_ARCHIVE))],
    session: Annotated[Session, Depends(get_session)],
) -> ProjectDetail:
    """Move a project to Archived — a visibility state, not a freeze.

    Why an action path (`POST …/archive`) and not a `PATCH` carrying `status`: the transition is
    one-way (F-01 names only "archive"; ADR-0004, addendum 2026-09-18, point 4), so a field a
    client can set to either value would advertise an un-archive this task does not have. It also
    keeps its own permission: `PROJECT_ARCHIVE`, not `PROJECT_EDIT` (ADR-0005, addendum
    2026-09-18, point 1). There is no request body at all — nothing for a client to smuggle an
    identity, an access grant or a target status into.

    The archived project stays on the list, marked, and every one of its scenarios stays
    readable and writable: the archive action writes `status` on the project row and touches
    nothing else (see `archive_project`).

    `404` for a project outside the caller's scope is not a branch written here — it is the only
    answer available, because `archive_project` resolves its target through the same access-layer
    function as the read path and returns an indistinguishable `None` for "not yours" and "no
    such project" (ADR-0005, addendum, point 3).
    """
    archived = archive_project(session, caller, project_id)
    if archived is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=PROJECT_NOT_FOUND_DETAIL
        )
    return shape_project_detail(archived, caller)


@router.post(
    "/{project_id}/copy",
    response_model=ProjectDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Copy a project with all its scenarios",
    responses={
        404: {"description": PROJECT_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: a named database constraint rejected the copy, or "
            "a scenario's commercial terms use a model this version cannot copy. "
            "Nothing was copied."
        },
    },
)
def copy_project_endpoint(
    project_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.PROJECT_COPY))],
    session: Annotated[Session, Depends(get_session)],
) -> ProjectDetail:
    """Copy a project the caller can see; the copy belongs to the caller alone.

    No request body: everything the copy needs comes from the source row and from the request's
    own auth context. In particular the beneficiary of the new `project_access` grant is
    `caller.user_id` and cannot be named by the client (ADR-0005, addendum, point 4), and the
    copy keeps the source's name — renaming is the edit action (SC-1-02), not part of copying.

    The source is resolved through `project_for_caller`, the same scope-filtered read path the
    `GET` above uses, so a project outside the caller's scope is a 404 here too — identical
    status, identical body. ADR-0005's addendum (point 3) requires that for write actions as
    well: a write-specific refusal code must not become a side-channel confirming that a project
    exists. There is no 403-for-an-existing-row branch to write, because the row never arrives.

    The copy is shaped from the view `copy_project` returns, i.e. against the *copy's* own
    `project_access` grant. The source's cost-visibility flag does not follow the copy (SC-1-08,
    K-04).
    """
    source = project_for_caller(session, caller, project_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=PROJECT_NOT_FOUND_DETAIL
        )
    try:
        copy = copy_project(session, caller, source)
    except (CommercialTermsNotCopyable, ProjectCopyRefused) as refusal:
        # R-03 (SC-4-01, gate 2): a readable, named refusal instead of an unhandled `500` — and it
        # is reached only for a project the caller can already see, so it confirms nothing.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal)) from None
    return shape_project_detail(copy, caller)
