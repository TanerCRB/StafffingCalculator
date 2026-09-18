"""The only path by which project rows enter the database (SC-1-01).

The counterpart to `app.data.project_reads`. ADR-0001 (addendum 2026-09-18, variant B) makes one
shared function the single *read* path so the `project_access` filter cannot be forgotten; the
same reasoning applies on the way in — creating a project and granting its creator access are one
unit of work. Split across two call sites they eventually drift apart, and a project nobody can
see is indistinguishable from a project that was never written (see `project_for_caller`).

Scope of this module: create (SC-1-01) and copy (SC-1-03). Editing and archiving are SC-1-02/04
and are deliberately absent — including any "update if it already exists" convenience.
"""

import uuid
from collections.abc import Callable
from datetime import date

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
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
