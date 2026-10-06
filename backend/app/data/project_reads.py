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
from dataclasses import dataclass

from sqlalchemy import Select, func, select, true
from sqlalchemy.orm import Session, selectinload

from app.core.identity import CallerIdentity
from app.data.organization_defaults import OrganizationLevel, organization_level_for
from app.models.project import Project, ProjectStatus
from app.models.project_access import ProjectAccess
from app.models.scenario import Scenario


@dataclass(frozen=True)
class CallerProjectView:
    """One project row *as one caller may see it*: whose view it is, the row, plus that caller's
    own `project_access.can_view_personnel_costs` flag for it.

    Why the flag travels with the row (ADR-0005, addendum 2026-09-19, point 6): the
    personnel-cost gate lives in `app.api.response_shaping`, which never receives a `Session` and
    must never grow a query of its own. So the flag has to arrive from here, in the same statement
    that decided the caller may see the project at all — one query per request, not one per list
    row, and no ORM relationship that would load *other* users' `project_access` rows and thereby
    reveal who else has access.

    The pairing is per (caller, project), not per caller: two projects in the same response can
    disagree about the flag, which is exactly what the column means. That is also why this is a
    two-field value object rather than a single boolean carried alongside a list of projects — a
    single boolean would have to be resolved from *some* row, and the obvious "first one" is a
    per-caller answer to a per-assignment question.

    A view is only ever constructed from a row this module's scope-filtered statement returned,
    so "no `project_access` row" is not a case to represent: it arrives as `None`/absence, not as
    a view with the flag defaulted.

    `user_id` names the caller the flag was resolved *for*. It is not used to decide anything here;
    it exists so that the layer applying the gate can assert it is shaping this view for the same
    caller it was built for (`app.api.response_shaping._without_personnel_costs`). Without it, a
    view and an identity are two independent arguments that nothing relates, and "they always match"
    is a convention of the single call path rather than a property of the type — a distinction that
    starts to matter as soon as a second producer of views appears (F-11 export, server-to-server).
    """

    user_id: str
    project: Project
    can_view_personnel_costs: bool
    organization_level: OrganizationLevel
    """The organisation level of the assumption chain for this project's scenarios (SC-1-10) —
    the live defaults for its drafts and the frozen ones for its approved scenarios, loaded in the
    same read for the reason the cost flag is: readiness (`app.domain.scenario_readiness`) is
    shaped in a layer that never receives a `Session`. It carries no scope and decides none; it is
    loaded only for scenarios this module's scope-filtered statement already returned."""


def accessible_projects(caller: CallerIdentity) -> Select[tuple[Project, bool]]:
    """A `SELECT` over projects already narrowed to the caller's `project_access` rows.

    Callers that need something more specific (one project, an export subset, a future filter)
    add their own `.where(...)` to this statement instead of starting a new one — that way the
    scope filter cannot be forgotten by omission.

    The filter is a join, not a post-read check: a project outside the caller's scope never
    reaches Python, so it cannot be leaked as a placeholder, a tombstone or a count.

    The statement selects `ProjectAccess.can_view_personnel_costs` alongside the project rather
    than discarding the joined row: the join is already narrowed to `caller.user_id`, so the flag
    it carries is the caller's own, for this project, at no extra query and with no second place
    that could disagree about scope. `(user_id, project_id)` is the primary key of
    `project_access`, so adding the column cannot multiply rows.
    """
    return (
        select(Project, ProjectAccess.can_view_personnel_costs)
        .join(ProjectAccess, ProjectAccess.project_id == Project.id)
        .where(ProjectAccess.user_id == caller.user_id)
    )


def _as_view(
    row: tuple[Project, bool], caller: CallerIdentity, organization_level: OrganizationLevel
) -> CallerProjectView:
    """Pair one row of `accessible_projects(caller)` with the caller that statement was built for.

    `caller` is the same object the statement's `WHERE` was narrowed by, so the view's `user_id` and
    the flag on it cannot come from different callers.
    """
    project, can_view_personnel_costs = row
    return CallerProjectView(
        user_id=caller.user_id,
        project=project,
        can_view_personnel_costs=bool(can_view_personnel_costs),
        organization_level=organization_level,
    )


def list_projects_for_caller(
    session: Session,
    caller: CallerIdentity,
    *,
    search: str | None = None,
    status: ProjectStatus | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[Sequence[CallerProjectView], int]:
    """Every project the caller has access to, archived ones included.

    Archived projects stay on the default list, clearly marked, rather than being hidden
    (gate-1 decision 8). Ordering is by name — stable, and deliberately not a filter,
    a search or a page: those are out of scope for SC-1-05.

    Each row carries its own cost-visibility flag, resolved by the join above for that project.
    One statement for the whole list: the alternative — reading the flag per row where it is
    needed — is both an N+1 and a second scope decision (SC-1-08, ADR-0005 addendum point 6).
    """
    filtered = accessible_projects(caller)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        filtered = filtered.where(
            Project.name.ilike(pattern, escape="\\")
            | Project.client.ilike(pattern, escape="\\")
            | Project.owner.ilike(pattern, escape="\\")
        )
    if status is not None:
        filtered = filtered.where(Project.status == status)
    base = filtered.subquery()
    page = (
        select(base.c.id, base.c.can_view_personnel_costs, base.c.name)
        .order_by(base.c.name, base.c.id)
        .limit(limit)
        .offset(offset)
        .subquery()
    )
    total = select(func.count()).select_from(base).scalar_subquery()
    total_row = select(total.label("total")).subquery()
    statement = (
        select(Project, page.c.can_view_personnel_costs, total_row.c.total)
        .select_from(total_row.outerjoin(page, true()).outerjoin(Project, Project.id == page.c.id))
        .order_by(page.c.name, page.c.id)
        .options(selectinload(Project.scenarios))
    )
    result = session.execute(statement).unique().all()
    total_count = int(result[0][2]) if result else 0
    rows = [(row[0], row[1]) for row in result if row[0] is not None]
    # One organisation level for the whole list — two statements, not two per project (SC-1-10).
    level = organization_level_for(
        session, (scenario for project, _ in rows for scenario in project.scenarios)
    )
    return [_as_view(row, caller, level) for row in rows], total_count


def caller_has_accessible_projects(session: Session, caller: CallerIdentity) -> bool:
    """Resolve the caller's project scope before the API validates list-page parameters."""
    return session.execute(select(accessible_projects(caller).exists())).scalar_one()


def project_for_caller(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID
) -> CallerProjectView | None:
    """One project by id — or `None`, with no way to tell *why* it is `None`.

    "Not in your scope" and "does not exist" collapse into the same return value here, on
    purpose, and one step earlier than the API: the scope filter is part of the `WHERE` clause
    (via `accessible_projects`), so an out-of-scope row is never fetched and the distinction is
    not available to be leaked. A caller of this function *cannot* answer "does it exist?" even
    if it wanted to — which is why the API layer can only answer 404 (SC-1-01, criterion 2).

    A `select(Project).where(Project.id == ...)` written at the point of use would return the row
    and leave the scope check to whoever remembered to write it; that is the ADR-0001 (addendum)
    violation this function exists to make unnecessary.

    Returns a `CallerProjectView`, not a bare `Project`: the caller's cost-visibility flag for
    this project comes out of the same statement, so no later layer has to go looking for it.
    The write paths (`app.data.project_writes`) resolve their target through this function, which
    is how `PATCH`, `POST …/archive` and `POST …/copy` inherit the flag as well as the scope
    filter.
    """
    statement = (
        accessible_projects(caller)
        .options(selectinload(Project.scenarios))
        .where(Project.id == project_id)
    )
    row = session.execute(statement).unique().one_or_none()
    if row is None:
        return None
    project, _ = row
    return _as_view(row, caller, organization_level_for(session, project.scenarios))


def scenario_for_caller(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
) -> tuple[CallerProjectView, Scenario] | None:
    """Read one assigned project's addressed scenario without loading its siblings.

    This composes on `accessible_projects()` so the project-access predicate and the caller's
    personnel-cost flag come from the same scoped statement. Unlike `scenario_view_in_scope`,
    which builds a full project view for callers that need all scenarios, this targeted read only
    loads the requested scenario and its organization-level defaults.
    """
    statement = (
        accessible_projects(caller)
        .join(Scenario, Scenario.project_id == Project.id)
        .where(Project.id == project_id, Scenario.id == scenario_id)
        .add_columns(Scenario)
    )
    row = session.execute(statement).unique().one_or_none()
    if row is None:
        return None
    project, can_view_personnel_costs, scenario = row
    view = _as_view(
        (project, can_view_personnel_costs), caller, organization_level_for(session, [scenario])
    )
    return view, scenario
