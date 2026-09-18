"""The only path by which project rows enter or change in the database (SC-1-01, SC-1-04).

The counterpart to `app.data.project_reads`. ADR-0001 (addendum 2026-09-18, variant B) makes one
shared function the single *read* path so the `project_access` filter cannot be forgotten; the
same reasoning applies on the way in — creating a project and granting its creator access are one
unit of work. Split across two call sites they eventually drift apart, and a project nobody can
see is indistinguishable from a project that was never written (see `project_for_caller`).

Scope of this module: creating a project (SC-1-01) and archiving one (SC-1-04). Editing and
copying are SC-1-02/03 and are deliberately absent — including any "update if it already exists"
convenience.
"""

import uuid
from datetime import date

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.project_reads import project_for_caller
from app.models.project import Project, ProjectStatus
from app.models.project_access import ProjectAccess


class ProjectWriteFailed(RuntimeError):
    """The write failed, described without quoting anything that was being written (NF-11).

    Raised instead of the driver's own exception, which is not safe to let propagate: even with
    `hide_parameters=True` on the engine (which removes SQLAlchemy's `[parameters: …]` echo),
    PostgreSQL attaches its own `DETAIL: Failing row contains (…)` line to a constraint
    violation — the whole row, owner's name and description included — and whatever prints the
    unhandled exception prints that too.
    """


def _describe_without_values(error: SQLAlchemyError) -> str:
    """A diagnosis built only from identifiers: error class, SQLSTATE, constraint name.

    These come from psycopg's `diag` fields, which name *what* was violated and never carry
    column values, so the message stays loggable. Everything else about the failure is dropped
    on purpose — a failure is not worth a personal-data leak, and the SQLSTATE plus the
    constraint name is what a reader actually acts on.
    """
    diagnostics = getattr(getattr(error, "orig", None), "diag", None)
    parts = [type(error).__name__]
    for label, value in (
        ("sqlstate", getattr(diagnostics, "sqlstate", None)),
        ("constraint", getattr(diagnostics, "constraint_name", None)),
    ):
        if value:
            parts.append(f"{label}={value}")
    return "Writing the project failed: " + ", ".join(parts)


def create_project(
    session: Session,
    caller: CallerIdentity,
    *,
    name: str,
    client: str,
    owner: str,
    delivery_period_start: date,
    delivery_period_end: date,
    reporting_currency: str,
    description: str = "",
) -> Project:
    """Insert one project and the `project_access` row that lets its creator see it.

    Two things this function deliberately does not take as arguments:

    - **who gets access.** It is `caller.user_id` and nothing else. The caller identity is
      resolved per request from the request's own auth context (`app.api.deps`), so a client
      cannot name the beneficiary of the grant by putting a user id in the request body.
    - **`status`.** A new project is Active by the column default; setting it here would be the
      archive action, which belongs to SC-1-03/04.

    `owner` is F-01's business owner of the project — a free-text field shown to people, not an
    access grant. Access is the `project_access` row below; conflating the two would make a
    typo in a name an authorization decision.

    The commit is here rather than in the endpoint: "the project is persisted" is this module's
    claim to make, and a caller that forgets to commit would otherwise get a Project object that
    silently never reaches the database.
    """
    project = Project(
        id=uuid.uuid4(),
        name=name,
        client=client,
        owner=owner,
        delivery_period_start=delivery_period_start,
        delivery_period_end=delivery_period_end,
        reporting_currency=reporting_currency,
        description=description,
    )
    try:
        session.add(project)
        session.flush()
        session.add(ProjectAccess(user_id=caller.user_id, project_id=project.id))
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`, not `from error`: a chained exception is printed together with its cause,
        # so re-raising *with* the original would put the very message this is here to suppress
        # back into the traceback, one line further down. The cost is the original stack — paid
        # deliberately, and softened by the SQLSTATE and constraint name kept above.
        raise ProjectWriteFailed(_describe_without_values(error)) from None
    return project


def archive_project(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID
) -> Project | None:
    """Set one project's `status` to `archived` — or return `None` if the caller cannot see it.

    Three properties this function is built to have, each of them load-bearing:

    - **It resolves its target through the shared read path** (`project_for_caller`), not through
      a `select(Project).where(Project.id == ...)` of its own. ADR-0005's addendum of 2026-09-18
      (point 3) requires the write action to use the same access-layer function as the read, and
      that function cannot say *why* it returned nothing. So this one cannot either: "outside your
      scope" and "does not exist" arrive here as the same `None`, and the endpoint has nothing
      from which to build an "exists, but not yours" answer (criterion 3).
    - **It writes `status` and nothing else.** No scenario row is touched, loaded for mutation or
      cascaded into, because archiving is a visibility state and not a freeze (ADR-0004, addendum
      2026-09-18, point 3, and point 2: archiving removes and hides no row — not the project's,
      not a scenario's, not a snapshot's). `Project.updated_at` does move, via the column's
      `onupdate`; that is the project row's own bookkeeping, not a change to a scenario.
    - **It is one-way and idempotent.** There is no `status` argument and no un-archive path:
      F-01 names only "archive" (ADR-0004, addendum, point 4), so the transition cannot be
      reversed by passing the other value, and a second archive of the same project is a no-op
      that reports the state rather than an error. That also means there is no lost update to
      protect against here and hence no `updated_at` concurrency token (ADR-0007): two callers
      archiving at once agree on the result, and a caller editing descriptive fields at the same
      time loses nothing, because this statement never writes those fields.
    """
    project = project_for_caller(session, caller, project_id)
    if project is None:
        return None
    try:
        project.status = ProjectStatus.ARCHIVED
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # As in `create_project`: `from None` so the driver's `DETAIL: Failing row contains (…)`
        # cannot reappear in the traceback as this exception's cause (NF-11).
        raise ProjectWriteFailed(_describe_without_values(error)) from None
    return project
