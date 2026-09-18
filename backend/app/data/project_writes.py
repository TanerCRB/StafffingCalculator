"""The only path by which project rows enter or change in the database (SC-1-01, SC-1-02).

The counterpart to `app.data.project_reads`. ADR-0001 (addendum 2026-09-18, variant B) makes one
shared function the single *read* path so the `project_access` filter cannot be forgotten; the
same reasoning applies on the way in — creating a project and granting its creator access are one
unit of work. Split across two call sites they eventually drift apart, and a project nobody can
see is indistinguishable from a project that was never written (see `project_for_caller`).

Scope of this module: create (SC-1-01) and edit (SC-1-02). Archiving and copying are SC-1-03/04
and are deliberately absent — including any "update if it already exists" convenience.
"""

import uuid
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.project_reads import project_for_caller
from app.models.project import Project
from app.models.project_access import ProjectAccess
from app.models.scenario import Scenario, ScenarioStatus


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


# --- SC-1-02, editing an existing project ------------------------------------------------------


DESCRIPTIVE_FIELDS: frozenset[str] = frozenset({"name", "client", "owner", "description"})
"""Group 1 of ADR-0004's addendum "zakres migawki wobec pól Projektu": editable whatever the
status of the project's scenarios. They are the report *header*, they enter no calculation, and an
approved version shows their current value — a named, accepted limit on header reproducibility."""

FROZEN_BY_APPROVED_SCENARIO: frozenset[str] = frozenset(
    {"reporting_currency", "delivery_period_start", "delivery_period_end"}
)
"""Group 2 of the same addendum: inherited by the calculation or bounding it. Once any scenario of
the project is `approved`, these stop being project fields and become part of that calculation, so
editing them is refused here — in the data-access layer, by the same rule and in the same place as
a write to an approved calculation (ADR-0004).

A new project column must be assigned to one of the two groups when it is added. The addendum
says an unassigned column falls into group 1 by default, which is a silent AC-10 regression the
day it enters a calculation — hence `EDITABLE_FIELDS` below is an explicit allow-list, not
"everything on the row"."""

EDITABLE_FIELDS: frozenset[str] = DESCRIPTIVE_FIELDS | FROZEN_BY_APPROVED_SCENARIO
"""Everything this function will ever write. `status` is absent on purpose: changing it is the
archive action (SC-1-04), and `id`/`created_at`/`updated_at` are not user input at all. An
allow-list rather than a deny-list, so a column added later is unwritable until someone decides
which group it belongs to."""


class ProjectEditRefused(RuntimeError):
    """The edit was understood, reached the data-access layer, and was refused there.

    Two subclasses, two independent reasons (ADR-0007: "jedno miejsce, dwa niezależne powody
    odmowy"). Distinct from `ProjectWriteFailed`, which means the write broke rather than that it
    was refused. Like that exception, no subclass here ever quotes a field *value* — field names
    only (NF-11).
    """


class ConcurrentEditConflict(ProjectEditRefused):
    """The row changed since the caller read it (ADR-0007, NF-05).

    Carries nothing about the other writer's change: this is a refusal, not a merge, and the
    competing value may well be data this caller is not entitled to see.
    """


class ApprovedScenarioFieldsFrozen(ProjectEditRefused):
    """The edit touched group-2 fields while the project has an `approved` scenario (ADR-0004)."""


class ProjectFieldNotEditable(RuntimeError):
    """A caller asked to write a column that is not in `EDITABLE_FIELDS`, or asked for nothing.

    A programming error, not a client error: the API request schema cannot express either case,
    so this guards the *next* call site (an import, a script, a future endpoint) rather than the
    one that exists today.
    """


def _approved_scenario_exists(project_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """`EXISTS (SELECT 1 FROM scenarios WHERE project_id = … AND status = 'approved')`.

    Returned as a SQL fragment rather than a Python boolean so that it can be evaluated *inside*
    the `UPDATE ... WHERE`, in the same statement and the same snapshot as the write. Read
    separately and then acted upon, it would be a check-then-act window: a scenario approved
    between the check and the write would let a group-2 edit through.
    """
    return sa.exists().where(
        Scenario.project_id == project_id, Scenario.status == ScenarioStatus.APPROVED
    )


def _diagnose_refusal(
    session: Session,
    project_id: uuid.UUID,
    *,
    touches_frozen_fields: bool,
) -> ProjectEditRefused:
    """Name the reason the conditional `UPDATE` matched no row.

    Run only after the refusal, never as the guard itself — the guard is the `WHERE` clause. The
    order matters: the frozen-field refusal is reported first because it is the permanent one
    (retrying with a fresh token will never help), while a concurrency conflict is resolved by
    re-reading. A caller told only "conflict" would keep retrying a write that cannot succeed.
    """
    if touches_frozen_fields and session.execute(
        sa.select(_approved_scenario_exists(project_id))
    ).scalar_one():
        return ApprovedScenarioFieldsFrozen(
            "This project has an approved scenario, so these fields are part of that calculation "
            "and cannot be edited: " + ", ".join(sorted(FROZEN_BY_APPROVED_SCENARIO))
        )
    return ConcurrentEditConflict(
        "The project changed since it was read. Re-read it and apply the edit again."
    )


def update_project(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    changes: Mapping[str, Any],
) -> Project | None:
    """Apply an edit to one project, or refuse — returning `None` when there is nothing to edit.

    `None` means "no such project *for this caller*" and carries no way to tell the two cases
    apart: the row is resolved through `project_for_caller`, the same scope-filtered read path the
    `GET` uses (ADR-0001 addendum; ADR-0005 addendum, point 3). This is also why the 404/409
    precedence ADR-0007 requires is structural rather than a rule someone has to remember: a
    project outside the caller's scope never reaches the concurrency check, so a stale token on an
    invisible project cannot answer 409 and confirm that the project exists.

    The concurrency guard is the `WHERE updated_at = :expected` clause, not a Python comparison
    against the value just read. Comparing in Python would leave the window between the read and
    the write — exactly the interval the guard exists to cover — and the whole point of NF-05 is
    that the loser of a race is told, instead of overwriting silently.

    `updated_at` itself is left to the column's `onupdate=func.now()`: the new value is the
    database's clock, not this process's, so two application instances cannot disagree about
    which write came last (invariant-guardian rule on injected time sources — nothing here reads
    the system clock).
    """
    forbidden = sorted(set(changes) - EDITABLE_FIELDS)
    if forbidden:
        raise ProjectFieldNotEditable(
            "These project fields cannot be edited through this function: "
            + ", ".join(forbidden)
            + f". Editable: {', '.join(sorted(EDITABLE_FIELDS))}."
        )
    if not changes:
        raise ProjectFieldNotEditable("An edit must name at least one field to change.")

    project = project_for_caller(session, caller, project_id)
    if project is None:
        return None

    touches_frozen_fields = bool(set(changes) & FROZEN_BY_APPROVED_SCENARIO)
    conditions: list[sa.ColumnElement[bool]] = [
        Project.id == project_id,
        Project.updated_at == expected_updated_at,
    ]
    if touches_frozen_fields:
        conditions.append(sa.not_(_approved_scenario_exists(project_id)))

    # The `projects` table rather than the ORM entity: a Core `UPDATE` keeps the statement exactly
    # what is written here (no ORM-level synchronisation strategy deciding to re-fetch or to
    # evaluate the criteria in Python), while still applying the column's `onupdate` default.
    statement = (
        sa.update(Project.__table__)
        .where(*conditions)
        .values(**dict(changes))
        .returning(Project.__table__.c.updated_at)
    )
    try:
        applied = session.execute(statement).one_or_none()
    except SQLAlchemyError as error:
        session.rollback()
        # Same reasoning as in `create_project`: the driver's own message would carry
        # `DETAIL: Failing row contains (…)`, i.e. the whole row (NF-11).
        raise ProjectWriteFailed(_describe_without_values(error)) from None

    if applied is None:
        # Deliberately no `session.rollback()` here: the single statement above matched no row, so
        # there is nothing written to undo, and a rollback would additionally discard unrelated
        # work the caller's transaction may already hold. "Zero saved changes" is a property of
        # the statement not matching, not of a cleanup afterwards.
        raise _diagnose_refusal(session, project_id, touches_frozen_fields=touches_frozen_fields)

    session.commit()
    # The in-memory object still holds the pre-update values (and `updated_at` was computed by the
    # database), so it is reloaded before anyone shapes a response out of it.
    session.refresh(project)
    return project
