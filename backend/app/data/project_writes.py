"""The only path by which project rows enter or change in the database (SC-1-01..04).

The counterpart to `app.data.project_reads`. ADR-0001 (addendum 2026-09-18, variant B) makes one
shared function the single *read* path so the `project_access` filter cannot be forgotten; the
same reasoning applies on the way in — creating a project and granting its creator access are one
unit of work. Split across two call sites they eventually drift apart, and a project nobody can
see is indistinguishable from a project that was never written (see `project_for_caller`).

Scope of this module: create (SC-1-01), edit (SC-1-02), copy (SC-1-03) and archive (SC-1-04) —
and nothing else. In particular, no "update if it already exists" convenience: that would be an
upsert with no prior row to take an ADR-0007 concurrency token from, hence no 409 and a silent
lost update.
"""

import uuid
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.additional_cost import copy_scenario_additional_costs
from app.data.column_copy import values_to_copy
from app.data.commercial_terms import CommercialTermsNotCopyable, copy_commercial_terms
from app.data.organization_defaults import organization_level_for
from app.data.project_reads import CallerProjectView, project_for_caller
from app.data.scenario_guard import copying_source_scenario, project_group_two_lock
from app.data.staffing import copy_staffing_positions
from app.data.write_errors import WriteFailed, describe_without_values
from app.models.project import Project, ProjectStatus
from app.models.project_access import ProjectAccess
from app.models.scenario import Scenario, ScenarioStatus


class ProjectWriteFailed(WriteFailed):
    """The write failed, described without quoting anything that was being written (NF-11).

    The mechanism itself lives in `app.data.write_errors` — one implementation, shared with the
    catalogue write path, because ADR-0008 ("Konsekwencje") requires the `EXCLUDE` violation on
    `catalog_default_rates` to be wrapped by the same thing rather than by a second copy of it. This
    subclass stays so a caller can still catch "the *project* write broke" specifically.

    Raised instead of the driver's own exception, which is not safe to let propagate: even with
    `hide_parameters=True` on the engine (which removes SQLAlchemy's `[parameters: …]` echo),
    PostgreSQL attaches its own `DETAIL: Failing row contains (…)` line to a constraint
    violation — the whole row, owner's name and description included — and whatever prints the
    unhandled exception prints that too.
    """


def _describe_without_values(error: SQLAlchemyError) -> str:
    """`app.data.write_errors.describe_without_values` bound to this table's subject.

    A one-line alias rather than four edited call sites: the message ("Writing the project
    failed: …", then SQLSTATE and constraint name, and nothing else) is unchanged by the
    extraction."""
    return describe_without_values(error, subject="project")


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
) -> CallerProjectView:
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

    Returns a `CallerProjectView` so the creation response goes through the same personnel-cost
    gate as every other project representation (SC-1-08, K-04). The flag is read off the grant row
    this function just inserted, not assumed: `can_view_personnel_costs` is not an argument here
    (ADR-0005, addendum 2026-09-19, point 4 — granting the flag is its own action, not a side
    effect of creating), so the value is the column default, `false`. The named consequence: the
    creator of a project does not see its personnel costs until someone grants the flag.
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
        grant = ProjectAccess(user_id=caller.user_id, project_id=project.id)
        session.add(grant)
        # Flushed before the commit so the flag below is the value that was actually inserted,
        # read while the object is guaranteed unexpired. Hard-coding `False` here would make the
        # response agree with the gate even if the insert stopped agreeing with either.
        session.flush()
        grants_cost_visibility = bool(grant.can_view_personnel_costs)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`, not `from error`: a chained exception is printed together with its cause,
        # so re-raising *with* the original would put the very message this is here to suppress
        # back into the traceback, one line further down. The cost is the original stack — paid
        # deliberately, and softened by the SQLSTATE and constraint name kept above.
        raise ProjectWriteFailed(_describe_without_values(error)) from None
    return CallerProjectView(
        user_id=caller.user_id,
        project=project,
        can_view_personnel_costs=grants_cost_visibility,
        # A new project has no scenario, so only the live half of the level can ever be read; it is
        # loaded anyway so that the view is complete by construction rather than by that fact.
        organization_level=organization_level_for(session, project.scenarios),
    )


# --- SC-1-02, editing an existing project ------------------------------------------------------


DESCRIPTIVE_FIELDS: frozenset[str] = frozenset({"name", "client", "owner", "description"})
"""Group 1 of ADR-0004's addendum "zakres migawki wobec pól Projektu": editable whatever the
status of the project's scenarios. They are the report *header*, they enter no calculation, and an
approved version shows their current value — a named, accepted limit on header reproducibility."""

FROZEN_BY_APPROVED_SCENARIO: frozenset[str] = frozenset(
    {
        "reporting_currency",
        "delivery_period_start",
        "delivery_period_end",
        # The project level of the assumption chain (SC-1-10, ADR-0012, point 5). Not frozen by
        # the snapshot — the approval freezes only the organisation's raw defaults (gate 1, P-A) and
        # a reader resolves an approved scenario against the *live* project row — so this refusal,
        # together with `project_group_two_lock`, is the only thing keeping them still.
        "target_margin_percent",
        "overload_threshold_percent",
    }
)
"""Group 2 of the same addendum: inherited by the calculation or bounding it. Once any scenario of
the project is `approved`, these stop being project fields and become part of that calculation, so
editing them is refused here — in the data-access layer, by the same rule and in the same place as
a write to an approved calculation (ADR-0004).

**Serialised against a concurrent approval, for every field in this set at once** (SC-1-10, gate 1
P-C; criterion K-08). The `NOT EXISTS (… approved)` predicate below excludes an approval that has
already *committed*; on its own it does not exclude one committing *alongside* the write, because
under `READ COMMITTED` it reads `scenarios` without a lock. `update_project` therefore takes
`app.data.scenario_guard.project_group_two_lock` before its `UPDATE`, and the approval takes the
conflicting `approving_project_lock` — see that module for the two orders and why each is safe.

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
) -> CallerProjectView | None:
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

    The returned `CallerProjectView` carries the caller's cost-visibility flag for this project,
    taken from the `project_for_caller` row below — the same statement that decided the caller may
    see it. The `PATCH` response is therefore gated exactly like the `GET` one (SC-1-08, K-04),
    with no second query and no second scope decision.
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

    view = project_for_caller(session, caller, project_id)
    if view is None:
        return None
    project = view.project

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
        if touches_frozen_fields:
            # The P-C seam (SC-1-10): wait for any approval of this project's scenarios that is in
            # flight, *then* let the `UPDATE` below — a new statement, hence a new snapshot under
            # `READ COMMITTED` — evaluate `NOT EXISTS (… approved)`. The guard itself stays inside
            # the `UPDATE`; this statement only decides *when* it is evaluated. Not a
            # check-then-act: nothing is read here and acted upon later (`app.data.scenario_guard`).
            session.execute(project_group_two_lock(project_id))
        applied = session.execute(statement).one_or_none()
        if applied is None:
            # Deliberately no `session.rollback()` here: the single statement above matched no
            # row, so there is nothing written to undo, and a rollback would additionally discard
            # unrelated work the caller's transaction may already hold. "Zero saved changes" is a
            # property of the statement not matching, not of a cleanup afterwards. The exception
            # raised here is `ProjectEditRefused`, not `SQLAlchemyError`, so it passes through the
            # `except` below untouched.
            raise _diagnose_refusal(
                session, project_id, touches_frozen_fields=touches_frozen_fields
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # Same reasoning as in `create_project`: the driver's own message would carry
        # `DETAIL: Failing row contains (…)`, i.e. the whole row (NF-11). Covering `commit()` here
        # too, not just `execute()`: a failure at commit is otherwise a raw, unwrapped
        # `SQLAlchemyError` that a retry cannot tell apart from "never applied" — and on a token
        # that already changed, the retry would land on a misleading 409 for a change that was in
        # fact the caller's own.
        raise ProjectWriteFailed(_describe_without_values(error)) from None
    # The in-memory object still holds the pre-update values (and `updated_at` was computed by the
    # database), so it is reloaded before anyone shapes a response out of it.
    session.refresh(project)
    return view


# --- copying (SC-1-03) -------------------------------------------------------------------------
# ADR-0004, addendum 2026-09-18 ("kopiowanie Projektu jako trzeci punkt wejścia"): duplicating a
# scenario (F-09), opening a new version of an approved one (F-12) and copying a whole project
# (F-01) are *one* mechanism with three entry points, not three copy routines. `copy_scenario`
# below is that mechanism; `copy_project` is the third entry point and delegates to it.

ScenarioChildCopier = Callable[[Session, Scenario, Scenario], None]
"""Copies the rows of one child table from a source scenario to its copy, in that order."""

SCENARIO_CHILD_COPIERS: tuple[ScenarioChildCopier, ...] = (
    copy_staffing_positions,
    # SC-4-01 (ADR-0004, addendum 2026-09-23 SC-4-01, point 1b): the commercial rule and its
    # details row — one entry for one aggregate, like the staffing entry above.
    copy_commercial_terms,
    # SC-5-05 (ADR-0014, point 10, Q-6 = A; ADR-0004, aneks SC-5-05, point 4): the additional costs
    # with **no position**. The costs attached to a position are not here — they are the fourth
    # pass of `copy_staffing_positions`, which holds the old-to-new position ids. Two halves, two
    # places, and a canary for each (`tests/test_additional_cost_copy.py`, criterion K-07).
    copy_scenario_additional_costs,
)
"""The cascade, as data rather than as prose (ADR-0004, addendum, point 4).

**Each task creating a child table of `scenarios` must append its copier here, in that same task.**
A table left out raises nothing — it yields a copy that shares the source's data, which is exactly
what AC-02 forbids. The registry exists so that adding a table is one append in one named place
instead of a search for every place that copies something.

Two entries since SC-4-01: the commercial-rule aggregate (`commercial_terms` + `tm_terms`, one entry
for two tables for the same reason as below — ADR-0004, addendum 2026-09-23 SC-4-01, point 1b), and
the staffing aggregate of SC-3-01 (F-04). The staffing entry is **one entry for two tables** —
positions and their monthly allocation rows — because the allocation row is a *grandchild* of the
scenario and this contract carries no mapping from old position ids to new ones (ADR-0004, addendum
2026-09-19, point 1). The consequence is named there and repeated here: "one entry per table" is no
longer literally true, it is "one entry per aggregate whose root is a child of the scenario", and a
future completeness test over this registry has to know the difference or the next grandchild table
will look registered while it is not.

Three entries since SC-5-05: the scenario-level additional costs (ADR-0014, point 10) joined as an
entry of their own, while the position-attached ones joined the staffing entry — the first child
table of `scenarios` whose rows are split between two copiers, by `position_id IS NULL`.

Still absent, and owed by the tasks that create them: scenario-level rate overrides and the details
tables of the other commercial models (ADR-0003, "Odłożone" — each joins the
commercial-rule copier, not the registry). The approval snapshot is absent on purpose — the third
group, never copied. The company catalogue is **not** absent by omission — a catalogue
row belongs to the organisation and not to a scenario, so it has no entry here on purpose (ADR-0004,
addendum 2026-09-19 "katalog organizacyjny nie jest dzieckiem scenariusza").
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
    # The source row, `FOR SHARE`, before anything of it is read (SC-5-05, reviewer R-01; ADR-0014,
    # point 10): no child write of the source can commit between the copiers' separate statements,
    # so every pass below reads one state. See `app.data.scenario_guard.copying_source_scenario`.
    session.execute(copying_source_scenario(source.id))
    copy = Scenario(
        id=uuid.uuid4(),
        project=into_project,
        status=ScenarioStatus.DRAFT,
        **values_to_copy(source, excluded=SCENARIO_COLUMNS_NOT_COPIED),
    )
    session.add(copy)
    # Flush before the children so the copy has a row for them to point at; the enclosing
    # transaction is still the caller's, so a failure further down undoes this too.
    session.flush()
    for copy_child_rows in SCENARIO_CHILD_COPIERS:
        copy_child_rows(session, source, copy)
    return copy


def copy_project(
    session: Session, caller: CallerIdentity, source: CallerProjectView
) -> CallerProjectView:
    """Copy a project the caller can already see: a new project row, every scenario as a draft.

    One transaction, like `create_project`: the project row, the caller's access grant and every
    copied scenario either all land or none do. A half-copied project is worse than a failed
    copy — it looks finished.

    `source` is a view `app.data.project_reads.project_for_caller` has already returned, i.e. a
    project inside the caller's `project_access` scope — the parameter type says so, rather than
    the docstring asking the next caller to remember it. This function does not check access and
    must not be asked to: "may this caller see the source?" is answered by the shared read path, so
    there is no second, local scope check here to forget or to get subtly different.

    **Access to the copy is granted to `caller.user_id` and to nobody else** (ADR-0005, addendum
    2026-09-18, point 4). The source's `project_access` rows are deliberately not replicated:
    granting access is its own action, not a side effect of copying. The consequence accepted
    together with that decision is that the copy of a team project is initially invisible to the
    team — which is something to tell the person copying, not something for them to discover.

    **The copy's cost-visibility flag comes from the *new* grant inserted below**, i.e. `false` by
    the column default (SC-1-08, K-04; ADR-0005, addendum 2026-09-19, point 4).
    `source.can_view_personnel_costs` plays no part in it and is never read here at all — only
    `source.project` is. Inheriting the flag would make copying a way to carry a cost-visibility
    grant onto a row nobody granted anything on — the same class of mistake as replicating the
    source's access rows, one dimension over.
    """
    copy = Project(
        id=uuid.uuid4(),
        **values_to_copy(source.project, excluded=PROJECT_COLUMNS_NOT_COPIED),
    )
    try:
        session.add(copy)
        session.flush()
        grant = ProjectAccess(user_id=caller.user_id, project_id=copy.id)
        session.add(grant)
        # `list(...)` because `copy_scenario` appends to a project's `scenarios` collection, and
        # the F-09 entry point copies into the *source* project — iterating the live collection
        # there would append while iterating it.
        for scenario in list(source.project.scenarios):
            copy_scenario(session, scenario, into_project=copy)
        session.flush()
        grants_cost_visibility = bool(grant.can_view_personnel_costs)
        session.commit()
    except CommercialTermsNotCopyable:
        # A scenario's rule names a model this version cannot copy (R-03, SC-4-01 gate 2): the whole
        # copy is undone — a project copied without one scenario's commercial details is worse than
        # a copy refused — and the refusal travels up unchanged, for the endpoint to answer `409`.
        session.rollback()
        raise
    except SQLAlchemyError as error:
        session.rollback()
        # Same reasoning as in `create_project`: `from None` keeps psycopg's `DETAIL: Failing row
        # contains (…)` — the whole row, owner's name included — out of the traceback (NF-11).
        raise ProjectWriteFailed(_describe_without_values(error)) from None
    return CallerProjectView(
        user_id=caller.user_id,
        project=copy,
        can_view_personnel_costs=grants_cost_visibility,
        # Every copied scenario is a draft, so it reads the live defaults; no snapshot row is
        # carried over (ADR-0004, addendum 2026-09-18, point 3), including the organisation's.
        organization_level=organization_level_for(session, copy.scenarios),
    )


# --- SC-1-04, archiving a project --------------------------------------------------------------


def archive_project(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID
) -> CallerProjectView | None:
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
      that reports the state rather than an error — setting `status` to the value it already
      holds leaves the ORM's change set empty, so `onupdate` does not fire and `updated_at` does
      not move either.

    What this function does **not** protect: a caller mid-edit elsewhere. A first archive *does*
    move `updated_at` (the column's `onupdate`), and `updated_at` is ADR-0007's concurrency token —
    so a concurrent `PATCH` carrying the pre-archive token is correctly refused with a 409, but for
    the wrong stated reason: nothing that caller edited was touched, yet the message reads "the
    project changed since it was read." There is no separate concurrency token here because this
    statement writes only `status`, but it still invalidates the one token editors rely on.

    Like `update_project`, it returns the `CallerProjectView` the read path produced, so the
    archive response is personnel-cost gated on the same flag as every other representation of the
    project (SC-1-08, K-04). Archiving does not touch that flag: it is a column of
    `project_access`, and this function writes `status` on `projects` and nothing else.
    """
    view = project_for_caller(session, caller, project_id)
    if view is None:
        return None
    try:
        view.project.status = ProjectStatus.ARCHIVED
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # As in `create_project`: `from None` so the driver's `DETAIL: Failing row contains (…)`
        # cannot reappear in the traceback as this exception's cause (NF-11).
        raise ProjectWriteFailed(_describe_without_values(error)) from None
    return view
