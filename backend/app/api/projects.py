"""Project endpoints.

- `GET /projects` — the caller's project list (SC-1-05), read only.
- `POST /projects` — create a project (SC-1-01).
- `GET /projects/{id}` — read one project (SC-1-01).
- `POST /projects/{id}/archive` — archive a project (SC-1-04).

No endpoint here builds a query of its own: reads go through `app.data.project_reads`, writes
through `app.data.project_writes` (ADR-0001, addendum 2026-09-18). Editing and copying are
SC-1-02/03 and do not exist.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import shape_project_detail, shape_project_list
from app.api.schemas.project import ProjectCreateRequest, ProjectDetail, ProjectListResponse
from app.core.identity import CallerIdentity, Permission
from app.data.project_reads import list_projects_for_caller, project_for_caller
from app.data.project_writes import archive_project, create_project
from app.db.session import get_session

router = APIRouter(prefix="/projects", tags=["projects"])

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
) -> ProjectListResponse:
    """Projects in the caller's `project_access` scope, archived ones included and marked.

    The scope filter lives in `app.data.project_reads.accessible_projects` — the one shared
    read path ADR-0001 (addendum) requires. This endpoint writes no query of its own.
    """
    projects = list_projects_for_caller(session, caller)
    return shape_project_list(projects, caller)


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
    project = create_project(
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
    return shape_project_detail(project, caller)


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
    project = project_for_caller(session, caller, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=PROJECT_NOT_FOUND_DETAIL
        )
    return shape_project_detail(project, caller)


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
    project = archive_project(session, caller, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=PROJECT_NOT_FOUND_DETAIL
        )
    return shape_project_detail(project, caller)
