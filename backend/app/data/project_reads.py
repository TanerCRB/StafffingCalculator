"""The only path by which project rows leave the database.

ADR-0001, addendum 2026-09-18 (variant B): per-project isolation is enforced by one shared
data-access function applying the `project_access` filter (ADR-0005), not by PostgreSQL RLS.
The condition attached to that decision is that this function is the *single* path — interactive
reads, PDF/spreadsheet export (F-11) and any future server-to-server interface all compose on
`accessible_projects()`. A `select(Project)` written anywhere else violates ADR-0001.

Nothing here writes; the write paths (create — SC-1-01, edit — SC-1-02, copy — SC-1-03, archive —
SC-1-04) live in `app.data.project_writes` and resolve their target by calling `project_for_caller`
below, so a write action inherits this scope filter instead of repeating it.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from app.core.identity import CallerIdentity
from app.models.project import Project
from app.models.project_access import ProjectAccess


def accessible_projects(caller: CallerIdentity) -> Select[tuple[Project]]:
    """A `SELECT` over projects already narrowed to the caller's `project_access` rows.

    Callers that need something more specific (one project, an export subset, a future filter)
    add their own `.where(...)` to this statement instead of starting a new one — that way the
    scope filter cannot be forgotten by omission.

    The filter is a join, not a post-read check: a project outside the caller's scope never
    reaches Python, so it cannot be leaked as a placeholder, a tombstone or a count.
    """
    return (
        select(Project)
        .join(ProjectAccess, ProjectAccess.project_id == Project.id)
        .where(ProjectAccess.user_id == caller.user_id)
    )


def list_projects_for_caller(session: Session, caller: CallerIdentity) -> Sequence[Project]:
    """Every project the caller has access to, archived ones included.

    Archived projects stay on the default list, clearly marked, rather than being hidden
    (gate-1 decision 8). Ordering is by name — stable, and deliberately not a filter,
    a search or a page: those are out of scope for SC-1-05.
    """
    statement = (
        accessible_projects(caller)
        .options(selectinload(Project.scenarios))
        .order_by(Project.name, Project.id)
    )
    return session.execute(statement).scalars().unique().all()


def project_for_caller(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID
) -> Project | None:
    """One project by id — or `None`, with no way to tell *why* it is `None`.

    "Not in your scope" and "does not exist" collapse into the same return value here, on
    purpose, and one step earlier than the API: the scope filter is part of the `WHERE` clause
    (via `accessible_projects`), so an out-of-scope row is never fetched and the distinction is
    not available to be leaked. A caller of this function *cannot* answer "does it exist?" even
    if it wanted to — which is why the API layer can only answer 404 (SC-1-01, criterion 2).

    A `select(Project).where(Project.id == ...)` written at the point of use would return the row
    and leave the scope check to whoever remembered to write it; that is the ADR-0001 (addendum)
    violation this function exists to make unnecessary.
    """
    statement = (
        accessible_projects(caller)
        .options(selectinload(Project.scenarios))
        .where(Project.id == project_id)
    )
    return session.execute(statement).scalars().unique().one_or_none()
