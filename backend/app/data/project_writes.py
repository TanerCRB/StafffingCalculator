"""The only path by which project rows enter or change in the database (SC-1-01, SC-1-02).

The counterpart to `app.data.project_reads`. ADR-0001 (addendum 2026-09-18, variant B) makes one
shared function the single *read* path so the `project_access` filter cannot be forgotten; the
same reasoning applies on the way in — creating a project and granting its creator access are one
unit of work. Split across two call sites they eventually drift apart, and a project nobody can
see is indistinguishable from a project that was never written (see `project_for_caller`).

Scope of this module: create (SC-1-01), edit (SC-1-02), copy (SC-1-03) and archive (SC-1-04) —
including any "update if it already exists" convenience the API might otherwise be tempted to add
outside these four named actions.
"""

import uuid
from collections.abc import Callable, Mapping
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


# --- copying (SC-1-03) -------------------------------------------------------------------------
# ADR-0004, addendum 2026-09-18 ("kopiowanie Projektu jako trzeci punkt wejścia"): duplicating a
# scenario (F-09), opening a new version of an approved one (F-12) and copying a whole project
# (F-01) are *one* mechanism with three entry points, not three copy routines. `copy_scenario`
# below is that mechanism; `copy_project` is the third entry point and delegates to it.

ScenarioChildCopier = Callable[[Session, Scenario, Scenario], None]
"""Copies the rows of one child table from a source scenario to its copy, in that order."""

SCENARIO_CHILD_COPIERS: tuple[ScenarioChildCopier, ...] = ()
"""The cascade, as data rather than as prose (ADR-0004, addendum, point 4).

Empty today because no child table of `scenarios` exists yet: staffing, costs, rates and
commercial-model rules (ADR-0003) are later plan blocks. **Each of those tasks must append its
copier here in the same task that creates its table.** A table left out does not raise anything —
it yields a copy that shares the source's data, which is exactly what AC-02 forbids. The registry
exists so that adding a table is one append in one named place instead of a search for every
place that copies something.
"""

SCENARIO_COLUMNS_NOT_COPIED: frozenset[str] = frozenset(
    {"id", "project_id", "status", "created_at", "updated_at"}
)
"""Scenario attributes the copy does **not** inherit, and why each one is here:

- `id` — a copy is a new row, not a second name for the source one (criterion 1/2).
- `project_id` — set from the target project, which is what makes this usable both for copying
  into another project and for duplicating inside the same one.
- `status` — the copy is always `draft` (ADR-0004: "dalsze zmiany wymagają nowej wersji (kopii)
  scenariusza ze statusem draft"), including when the source is `approved`.
- `created_at` / `updated_at` — the copy is created now; inheriting the source's timestamps would
  backdate a row that did not exist.

Everything else is copied by reflection over the mapper rather than by a hand-written field list,
so a column added to `Scenario` later is copied by default instead of being silently dropped. The
accompanying test asserts every mapped attribute is either copied or named here, so a new column
forces the decision instead of inheriting one.
"""

PROJECT_COLUMNS_NOT_COPIED: frozenset[str] = frozenset(
    {"id", "status", "created_at", "updated_at"}
)
"""Project attributes the copy does not inherit. `status` in particular: the copy starts Active
(the column default) even when the source is Archived — archiving is a visibility state of the
source (ADR-0004, addendum "archiwizacja Projektu"), and a copy nobody can act on is not what
copying an archived project is for. Descriptive fields, including `name`, are copied verbatim:
renaming is the edit action (SC-1-02), not a side effect of copying."""


def _values_to_copy(instance: object, *, excluded: frozenset[str]) -> dict[str, object]:
    """Mapped column values of `instance`, minus the excluded attribute names."""
    mapper = sa.inspect(type(instance))
    return {
        attribute.key: getattr(instance, attribute.key)
        for attribute in mapper.column_attrs
        if attribute.key not in excluded
    }


def copy_scenario(session: Session, source: Scenario, *, into_project: Project) -> Scenario:
    """Copy one scenario into `into_project` as a fresh `draft`, with its child rows.

    The single copy mechanism ADR-0004 requires (addendum, point 1). Copying a project calls it
    once per source scenario; duplicating a scenario (F-09) and opening a new version of an
    approved one (F-12) will call it with `into_project=source.project`. Nothing about this
    function is specific to the project-copy entry point.

    Two deliberate properties:

    - **The status is not a parameter.** Every copy is a `draft`, whatever the source was. A
      parameter would make "copy an approved scenario and keep it approved" expressible, and that
      is precisely the one-way, human-performed approval step of ADR-0004.
    - **No snapshot is carried over** (ADR-0004, addendum, point 3). `approved_snapshot_*` rows
      belong to the approval the copy has not been through; the copy of an approved calculation
      is therefore *not* reproducible the way its source is, and regains reproducibility only at
      its own approval. This is a named consequence against F-12, not an omission — and it is
      also why the source's `approved` row is only ever read here, never written: the copy is a
      new row, so the immutability of the approved source is untouched.
    """
    copy = Scenario(
        id=uuid.uuid4(),
        project=into_project,
        status=ScenarioStatus.DRAFT,
        **_values_to_copy(source, excluded=SCENARIO_COLUMNS_NOT_COPIED),
    )
    session.add(copy)
    # Flush before the children so the copy has a row for them to point at; the enclosing
    # transaction is still the caller's, so a failure further down undoes this too.
    session.flush()
    for copy_child_rows in SCENARIO_CHILD_COPIERS:
        copy_child_rows(session, source, copy)
    return copy


def copy_project(session: Session, caller: CallerIdentity, source: Project) -> Project:
    """Copy a project the caller can already see: a new project row, every scenario as a draft.

    One transaction, like `create_project`: the project row, the caller's access grant and every
    copied scenario either all land or none do. A half-copied project is worse than a failed
    copy — it looks finished.

    `source` is a row `app.data.project_reads.project_for_caller` has already returned, i.e. one
    inside the caller's `project_access` scope. This function does not check access and must not
    be asked to: "may this caller see the source?" is answered by the shared read path, so there
    is no second, local scope check here to forget or to get subtly different.

    **Access to the copy is granted to `caller.user_id` and to nobody else** (ADR-0005, addendum
    2026-09-18, point 4). The source's `project_access` rows are deliberately not replicated:
    granting access is its own action, not a side effect of copying. The consequence accepted
    together with that decision is that the copy of a team project is initially invisible to the
    team — which is something to tell the person copying, not something for them to discover.
    """
    copy = Project(
        id=uuid.uuid4(),
        **_values_to_copy(source, excluded=PROJECT_COLUMNS_NOT_COPIED),
    )
    try:
        session.add(copy)
        session.flush()
        session.add(ProjectAccess(user_id=caller.user_id, project_id=copy.id))
        # `list(...)` because `copy_scenario` appends to a project's `scenarios` collection, and
        # the F-09 entry point copies into the *source* project — iterating the live collection
        # there would append while iterating it.
        for scenario in list(source.scenarios):
            copy_scenario(session, scenario, into_project=copy)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # Same reasoning as in `create_project`: `from None` keeps psycopg's `DETAIL: Failing row
        # contains (…)` — the whole row, owner's name included — out of the traceback (NF-11).
        raise ProjectWriteFailed(_describe_without_values(error)) from None
    return copy
