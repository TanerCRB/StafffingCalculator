"""GET /projects — the caller's project list (SC-1-05).

Read only. No write, no state change: the edit/copy/archive actions are SC-1-02..04.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import shape_project_list
from app.api.schemas.project import ProjectListResponse
from app.core.identity import CallerIdentity, Permission
from app.data.project_reads import list_projects_for_caller
from app.db.session import get_session

router = APIRouter(prefix="/projects", tags=["projects"])


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
