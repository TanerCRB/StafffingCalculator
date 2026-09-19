"""The only path by which staffing positions and their monthly hours are read or written (SC-3-01).

Three mechanisms live here, and none of them is new — each is an existing mechanism of this
repository applied to the first table that inherits its scope from a parent:

1. **Scope.** There is no `scenario_for_caller` and there must not be one (ADR-0001, addendum
   2026-09-19). The address carries both identifiers, the scope comes from
   `app.data.project_reads.project_for_caller` — the very same entry point the project endpoints
   use — and the scenario's membership of that project is checked against the `Project.scenarios`
   collection that function already eager-loads. One function, not two composing on one query; and
   "not yours", "no such project" and "that scenario belongs to another project" arrive at the API
   as the same absence, so none of them can be told apart from the others (ADR-0005, addendum
   2026-09-19, point 4 — for the write path as much as for the read one).

2. **The refusal of a write to an `approved` scenario** (ADR-0004). Two write paths, two statements,
   each carrying its own guard *inside* the statement that writes:

   - `INSERT ... SELECT ... FROM scenarios WHERE id = :scenario_id AND status <> 'approved'` —
     an `INSERT` has no `WHERE` to hang a predicate on, so the predicate lives in the `SELECT` the
     row comes from (ADR-0004, addendum 2026-09-19, point 2). Refusal is zero rows affected; the
     reason is diagnosed only *after* the refusal, exactly as `project_writes._diagnose_refusal`
     does it. **Named, dated, accepted limit:** an approval committing concurrently with this
     `INSERT` is not excluded — under `READ COMMITTED` the statement's read of the parent takes no
     lock on it. Nothing in the running system sets `approved` today, so the window cannot even be
     provoked; the first real approval path must close it for every child table of `scenarios` at
     once.
   - the allocation edit, a single statement in which the position's concurrency token, the
     scenario's status and the existence of the month row are all part of the `WHERE` — see
     `update_allocation`.

3. **Describing a failed write without quoting what was written** (NF-11): `app.data.write_errors`,
   the same classification by SQLSTATE the catalogue write path uses. Not re-implemented here.

What this module deliberately does **not** do: resolve a rate, read a cost, or join the catalogue
for anything but a foreign key (ADR-0005, addendum 2026-09-19, point 5). A position carries the four
dimension ids and nothing derived from them, which is why there is no cost gate on this path and no
`CallerStaffingView` — a view object exists to carry a per-(caller, row) flag, and there is no such
flag here to carry.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.identity import CallerIdentity
from app.data.column_copy import values_to_copy
from app.data.project_reads import project_for_caller
from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.models.scenario import Scenario, ScenarioStatus
from app.models.staffing import HOURS_COLUMNS, StaffingPosition, StaffingPositionAllocation

_POSITION_TABLE = StaffingPosition.__table__
_ALLOCATION_TABLE = StaffingPositionAllocation.__table__

EDITABLE_ALLOCATION_FIELDS: frozenset[str] = frozenset(HOURS_COLUMNS)
"""Everything the allocation edit path will ever write: the three hour figures, and nothing else.

An allow-list rather than "whatever the request carried". `period_month` is absent on purpose —
moving a month is not an edit of a value but a different row, and an edit that could rewrite it
would be an undocumented way around `UNIQUE (position_id, period_month)`. `position_id` and `id` are
not user input at all, and there is no `updated_at` on this table to write (ADR-0007, addendum
2026-09-19: the token is the position's)."""


class StaffingWriteFailed(WriteFailed):
    """A staffing write failed for a reason nothing here established — a `500`.

    Same division as in the catalogue path (R-01): a numeric overflow, a statement timeout or a
    dropped connection must not reach a caller as a `409` describing a conflict nobody observed. The
    message is built from identifiers only (SQLSTATE, constraint name), because PostgreSQL's own
    `DETAIL: Failing row contains (…)` line carries the whole row (NF-11).
    """


class StaffingWriteRefused(StaffingWriteFailed, WriteRefused):
    """The database refused the write for a reason its SQLSTATE names — a `409`.

    A subclass of both, so `except StaffingWriteFailed` still catches every failure of this path
    while `except StaffingWriteRefused` catches only what a caller can act on.
    """


class StaffingWriteRejected(RuntimeError):
    """The write was understood, reached this layer, and was refused *by state* — a `409`.

    Distinct from `StaffingWriteFailed`, which means the statement broke. Two subclasses, two
    independent reasons, exactly as `ProjectEditRefused` splits them (ADR-0007: "jedno miejsce, dwa
    niezależne powody odmowy"). No subclass ever quotes a field value — NF-11 applies to a refusal
    as much as to a failure.
    """


class ApprovedScenarioFrozen(StaffingWriteRejected):
    """The scenario is `approved`, so its staffing is frozen (ADR-0004).

    The permanent reason: retrying with a fresh token will never help. Further changes need a new
    version, i.e. a copy of the scenario — which is a `draft` and accepts the same write
    (`app.data.project_writes.copy_scenario`).
    """


class ConcurrentStaffingEditConflict(StaffingWriteRejected):
    """The position changed since the caller read it (ADR-0007, NF-05).

    Carries nothing about the competing change: this is a refusal, not a merge. The token is the
    *position's* `updated_at`, so an edit of another month of the same position invalidates it — a
    false collision accepted by name in ADR-0007's addendum of 2026-09-19, because the position is
    the unit of editing and the month is not.
    """


class AllocationFieldNotEditable(RuntimeError):
    """A caller asked to write a column outside `EDITABLE_ALLOCATION_FIELDS`, or asked for nothing.

    A programming error, not a client error — the counterpart of
    `app.data.project_writes.ProjectFieldNotEditable`: the request schema cannot express either
    case, so this guards the *next* call site (an import, a script, a future endpoint) and not the
    one that exists today. Deliberately not a `StaffingWriteRejected`, so it cannot be answered with
    a `409` that would describe a state of the data as the reason.
    """


def _failure(error: SQLAlchemyError) -> WriteFailed:
    """Classify one failed staffing write by SQLSTATE, into this module's pair of exceptions.

    Spelled once so both write paths classify identically — a per-call-site `except` block is how
    one of them ends up answering `409` for a defect while the other does not.
    """
    return failure_for(
        error,
        subject="staffing position",
        refused=StaffingWriteRefused,
        failed=StaffingWriteFailed,
    )


def _scenario_in_scope(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> Scenario | None:
    """The scenario named by the address — or `None`, with no way to tell why.

    Not a second scope function (ADR-0001, addendum 2026-09-19). It calls `project_for_caller`,
    which is where the `project_access` predicate lives, and then looks for the scenario in the
    `Project.scenarios` collection that call already loaded (`selectinload`). No `select(Scenario)`
    is issued here and none may be added: a query of its own would be a second place deciding what a
    caller may see, and it would answer for scenarios of projects the caller cannot see.

    Three different situations collapse into the same `None` — no such project, the project is
    outside the caller's scope, the scenario belongs to a different project — and that is the point:
    a caller of this function cannot tell them apart even if it wanted to, so the API has nothing
    from which to build an "exists, but not yours" answer.
    """
    view = project_for_caller(session, caller, project_id)
    if view is None:
        return None
    for scenario in view.project.scenarios:
        if scenario.id == scenario_id:
            return scenario
    return None


def list_positions(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> Sequence[StaffingPosition] | None:
    """Every staffing position of one scenario, each with its month rows — or `None` for "no such".

    `None`, not an empty list: a scenario the caller may not see and a scenario with no positions
    must not be the same answer, or the absence of staffing would be indistinguishable from the
    absence of access. The `None` itself carries no reason (see `_scenario_in_scope`).

    Ordered by period, then by id, so a grid read twice comes back in the same order; the month rows
    are ordered by `period_month` by the relationship itself. `selectinload` rather than a lazy load
    per row: the months of 200 positions are one extra statement, not 200 (NF-03 — unmeasured here,
    and named as such in the plan entry).
    """
    if _scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None
    statement = (
        sa.select(StaffingPosition)
        .where(StaffingPosition.scenario_id == scenario_id)
        .options(selectinload(StaffingPosition.allocations))
        .order_by(StaffingPosition.start_date, StaffingPosition.id)
    )
    return list(session.execute(statement).scalars().all())


def create_position(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    role_id: uuid.UUID,
    seniority_id: uuid.UUID,
    location_id: uuid.UUID,
    engagement_type_id: uuid.UUID,
    headcount: int,
    start_date: date,
    end_date: date | None,
    allocations: Sequence[Mapping[str, Any]] = (),
) -> StaffingPosition | None:
    """Insert one position, with the month rows given for it — or refuse, or answer `None`.

    `None` means "no such scenario *for this caller*" and is the same answer for every reason
    (`_scenario_in_scope`), which is also what gives the 404-before-409 precedence ADR-0007
    requires: a scenario outside the caller's scope never reaches the `approved` guard, so a refusal
    saying "approved" cannot become a side channel confirming that the scenario exists.

    **The guard against writing to an `approved` scenario is the `INSERT ... SELECT` below**, not a
    status read followed by an insert. The row's `scenario_id` *comes from* a `SELECT` over
    `scenarios` narrowed by `status <> 'approved'`: no matching parent, no row to insert, zero rows
    affected — the refusal is counted by the database in the same statement as the write (ADR-0004,
    addendum 2026-09-19, point 2). A Python pre-check has survived delivered tests three times in
    this repository (SC-1-02 twice, SC-2-01), which is why the predicate is not in Python.

    The month rows are inserted afterwards, in the same transaction, and need no guard of their own:
    their `position_id` is the id of a position this statement just created, so a refused position
    leaves nothing for them to point at and the transaction has no position to attach them to. This
    is the whole reason the monthly grid is created *with* its position rather than by a second,
    separately guarded endpoint — a third write path would need a third run of the `approved` guard,
    and it would not have one.

    `created_at`/`updated_at` are absent from the inserted columns on purpose: they come from the
    column defaults, i.e. from the database's clock.
    """
    if _scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    position_id = uuid.uuid4()
    # The literals are typed explicitly: an untyped `sa.literal(uuid)` inside a `SELECT` reaches
    # PostgreSQL as an unknown-typed parameter, and the column list of an `INSERT ... SELECT` is
    # where that becomes a cast error rather than a value.
    source = sa.select(
        sa.literal(position_id, type_=_POSITION_TABLE.c.id.type).label("id"),
        Scenario.__table__.c.id.label("scenario_id"),
        sa.literal(role_id, type_=_POSITION_TABLE.c.role_id.type).label("role_id"),
        sa.literal(seniority_id, type_=_POSITION_TABLE.c.seniority_id.type).label("seniority_id"),
        sa.literal(location_id, type_=_POSITION_TABLE.c.location_id.type).label("location_id"),
        sa.literal(
            engagement_type_id, type_=_POSITION_TABLE.c.engagement_type_id.type
        ).label("engagement_type_id"),
        sa.literal(headcount, type_=_POSITION_TABLE.c.headcount.type).label("headcount"),
        sa.literal(start_date, type_=_POSITION_TABLE.c.start_date.type).label("start_date"),
        sa.literal(end_date, type_=_POSITION_TABLE.c.end_date.type).label("end_date"),
    ).where(
        Scenario.__table__.c.id == scenario_id,
        Scenario.__table__.c.status != ScenarioStatus.APPROVED,
    )
    statement = (
        sa.insert(_POSITION_TABLE)
        .from_select(
            [
                "id",
                "scenario_id",
                "role_id",
                "seniority_id",
                "location_id",
                "engagement_type_id",
                "headcount",
                "start_date",
                "end_date",
            ],
            source,
        )
        .returning(_POSITION_TABLE.c.id)
    )

    try:
        inserted = session.execute(statement).one_or_none()
        if inserted is None:
            # No `session.rollback()`: the statement matched no parent row, so there is nothing
            # written to undo, and a rollback would additionally discard unrelated work the caller's
            # transaction may hold. The exception below is a `StaffingWriteRejected`, not a
            # `SQLAlchemyError`, so it passes through the `except` untouched.
            raise _diagnose_insert_refusal(session, scenario_id)
        if allocations:
            session.execute(
                sa.insert(_ALLOCATION_TABLE),
                [
                    # `id`/`position_id` last: they must win over anything of the same name in
                    # `allocation`, not be overridden by it. A caller-supplied `position_id` here
                    # would attach a month row to a position outside this call's own scope/guard
                    # checks (security-auditor B-01, 2026-09-19) — closed by ordering, not just by
                    # today's schema forbidding the field on the HTTP path.
                    {**dict(allocation), "id": uuid.uuid4(), "position_id": position_id}
                    for allocation in allocations
                ],
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`, as everywhere on a write path here: a chained exception is printed together
        # with its cause, so re-raising *with* the original would put PostgreSQL's `DETAIL: Failing
        # row contains (…)` back into the traceback one line further down (NF-11).
        raise _failure(error) from None
    return _position_by_id(session, position_id)


def _diagnose_insert_refusal(session: Session, scenario_id: uuid.UUID) -> StaffingWriteRejected:
    """Name the reason the `INSERT ... SELECT` found no parent row.

    Run only *after* the refusal, never as the guard itself — the guard is the `WHERE` of the
    `SELECT`. The scenario was inside the caller's scope a moment ago (`_scenario_in_scope`), so
    "approved" is the expected answer and the other branch covers a scenario that disappeared
    between the two statements: reported as a conflict, because re-reading is what resolves it.
    """
    approved = session.execute(
        sa.select(
            sa.exists().where(
                Scenario.id == scenario_id, Scenario.status == ScenarioStatus.APPROVED
            )
        )
    ).scalar_one()
    if approved:
        return ApprovedScenarioFrozen(
            "This scenario is approved, so its staffing is part of an approved calculation and "
            "cannot be changed. Copy the scenario to open a new version and change the copy."
        )
    return ConcurrentStaffingEditConflict(
        "The scenario changed since it was read. Re-read it and apply the change again."
    )


def _position_by_id(session: Session, position_id: uuid.UUID) -> StaffingPosition | None:
    """Re-read one position with its month rows after a write.

    A read-back rather than an object assembled in Python: `NUMERIC(10,2)` turns `Decimal("8")` into
    `Decimal("8.00")` and the timestamps come from the database's clock, so the payload returned by
    a write must be the row the database holds — otherwise the `POST` response and the next `GET`
    disagree about the same row. Sessions here run with `expire_on_commit=False` (`app.db.session`),
    which is exactly why this cannot be left to the ORM's expiry.
    """
    statement = (
        sa.select(StaffingPosition)
        .where(StaffingPosition.id == position_id)
        .options(selectinload(StaffingPosition.allocations))
    )
    position = session.execute(statement).scalars().one_or_none()
    if position is not None:
        session.refresh(position)
    return position


def update_allocation(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    period_month: date,
    *,
    expected_updated_at: datetime,
    changes: Mapping[str, Any],
) -> StaffingPosition | None:
    """Edit one month of one position's allocation, or refuse — `None` when there is nothing there.

    Everything that decides whether this write may happen is in **one statement**, and that is the
    whole claim of criterion K-07:

    ```
    WITH guarded_position AS (
        UPDATE staffing_position SET updated_at = now()
         WHERE id = :position_id AND scenario_id = :scenario_id
           AND updated_at = :expected                      -- ADR-0007, token on the position
           AND NOT EXISTS (… scenarios … status = 'approved')   -- ADR-0004
           AND EXISTS (… the month row …)                   -- so a missing month rotates no token
        RETURNING id
    )
    UPDATE staffing_position_allocation SET … FROM guarded_position WHERE …
    ```

    Four properties of that shape, each one load-bearing:

    - **The token is compared by the database, inside the `UPDATE`.** A Python comparison against
      the value just read leaves the window between the read and the write — the interval the guard
      exists to cover (NF-05, and the mutation that survived SC-1-02's delivered tests).
    - **The token is the position's**, and the statement that checks it is also the statement that
      rotates it, so two edits of the same position serialise on that row (ADR-0007, addendum
      2026-09-19). `now()` is the database's clock, never this process's.
    - **`scenario_id` is part of the `WHERE`.** A position id belonging to another scenario — of
      another project, possibly one the caller cannot see — matches nothing, and the answer is the
      same absence as for a position that does not exist.
    - **The month row's existence is a condition of the token rotation.** Without it, a `PATCH` on a
      month that does not exist would still bump `updated_at` and invalidate every other editor's
      token for a write that changed nothing — the defect ADR-0004's addendum of 2026-09-19 (point
      1) recorded for archiving, which is not a reason to repeat it.

    Returns the whole position, freshly read: the caller needs the *new* token, and the response of
    an edit and of a read must be the same shape (the frontend that displays a grid has no second
    schema for "the grid after a write").
    """
    forbidden = sorted(set(changes) - EDITABLE_ALLOCATION_FIELDS)
    if forbidden:
        raise AllocationFieldNotEditable(
            "These allocation fields cannot be edited through this function: "
            + ", ".join(forbidden)
            + f". Editable: {', '.join(sorted(EDITABLE_ALLOCATION_FIELDS))}."
        )
    if not changes:
        raise AllocationFieldNotEditable(
            "An allocation edit must name at least one field to change."
        )

    if _scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    month_row_exists = sa.exists().where(
        _ALLOCATION_TABLE.c.position_id == _POSITION_TABLE.c.id,
        _ALLOCATION_TABLE.c.period_month == period_month,
    )
    scenario_is_approved = sa.exists().where(
        Scenario.__table__.c.id == _POSITION_TABLE.c.scenario_id,
        Scenario.__table__.c.status == ScenarioStatus.APPROVED,
    )
    guarded_position = (
        sa.update(_POSITION_TABLE)
        .where(
            _POSITION_TABLE.c.id == position_id,
            _POSITION_TABLE.c.scenario_id == scenario_id,
            _POSITION_TABLE.c.updated_at == expected_updated_at,
            sa.not_(scenario_is_approved),
            month_row_exists,
        )
        # Explicit rather than left to the column's `onupdate`: this statement is the only one that
        # writes the position row on this path, and an `UPDATE` with no `SET` is not a statement.
        .values(updated_at=sa.func.now())
        .returning(_POSITION_TABLE.c.id)
        .cte("guarded_position")
    )
    statement = (
        sa.update(_ALLOCATION_TABLE)
        .where(
            _ALLOCATION_TABLE.c.position_id == guarded_position.c.id,
            _ALLOCATION_TABLE.c.period_month == period_month,
        )
        .values(**dict(changes))
        .returning(_ALLOCATION_TABLE.c.id)
    )

    try:
        applied = session.execute(statement).one_or_none()
        if applied is None:
            raise _diagnose_allocation_refusal(
                session,
                scenario_id,
                position_id,
                period_month,
                expected_updated_at=expected_updated_at,
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return _position_by_id(session, position_id)


class AllocationMonthNotFound(RuntimeError):
    """There is no such month row under that position, inside that scenario.

    Answered as the same `404` as "no such scenario": the month, the position and the scenario are
    one aggregate as far as a caller's ability to tell them apart goes (ADR-0005, addendum
    2026-09-19, point 4). Raised rather than returned as `None` so that "outside your scope" and
    "no such month" stay two distinct facts *inside* this layer, while the API answers both with one
    body.
    """


def _diagnose_allocation_refusal(
    session: Session,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    period_month: date,
    *,
    expected_updated_at: datetime,
) -> Exception:
    """Name the reason the conditional edit matched no row — after the refusal, never as the guard.

    **"Does the target exist at all?" comes before "may it be written?"**: first the position, then
    the month row, and only then the state of the scenario and the token. That is the
    404-before-409 precedence of ADR-0007 (addendum 2026-09-19) applied one level below the
    scenario, and it is a correction (R-01, reviewer 2026-09-19): checking `approved` first answered
    a `PATCH` on a month with no row with `409 "this scenario is approved — copy it and change it"`,
    advice that cannot work, because the same request against the copy is a `404` for a month that
    never existed. A refusal must not name a reason it has not established, and "frozen" is a
    statement about a row — so it needs the row.

    Among the reasons that do apply to an existing row the permanent one comes first, exactly as
    `project_writes._diagnose_refusal` orders them: an `approved` scenario can never be satisfied by
    a retry with a fresh token, while a concurrency conflict is resolved by re-reading. A caller
    told only "conflict" would keep retrying a write that cannot succeed.

    Reordering leaks nothing across the scope boundary: every branch below is reached only for a
    scenario `_scenario_in_scope` has already returned, and the position lookup is narrowed to that
    scenario — so "no such month" is never an answer about a row in a project the caller cannot see,
    and it reaches the caller as the same `404` body as every other absence on this path
    (`app.api.staffing.STAFFING_NOT_FOUND_DETAIL`).
    """
    position = session.execute(
        sa.select(StaffingPosition).where(
            StaffingPosition.id == position_id, StaffingPosition.scenario_id == scenario_id
        )
    ).scalars().one_or_none()
    if position is None:
        return AllocationMonthNotFound("No such staffing position in this scenario.")
    month_exists = session.execute(
        sa.select(
            sa.exists().where(
                StaffingPositionAllocation.position_id == position_id,
                StaffingPositionAllocation.period_month == period_month,
            )
        )
    ).scalar_one()
    if not month_exists:
        return AllocationMonthNotFound("No allocation row for that month on this position.")
    approved = session.execute(
        sa.select(
            sa.exists().where(
                Scenario.id == scenario_id, Scenario.status == ScenarioStatus.APPROVED
            )
        )
    ).scalar_one()
    if approved:
        return ApprovedScenarioFrozen(
            "This scenario is approved, so its staffing is part of an approved calculation and "
            "cannot be changed. Copy the scenario to open a new version and change the copy."
        )
    return ConcurrentStaffingEditConflict(
        "The staffing position changed since it was read. Re-read it and apply the edit again."
    )


# --- the copying cascade (ADR-0004, addendum 2026-09-19) ----------------------------------------

POSITION_COLUMNS_NOT_COPIED: frozenset[str] = frozenset(
    {"id", "scenario_id", "created_at", "updated_at"}
)
"""Position attributes a copy does **not** inherit, and why each one is here:

- `id` — a copy is a new row, not a second name for the source one (AC-02).
- `scenario_id` — set from the scenario being copied into; that is what makes the copier usable for
  all three entry points of the one copy mechanism (project copy, scenario duplication, new
  version).
- `created_at` / `updated_at` — the copy is created now, and its ADR-0007 token is its own.
  Inheriting the source's token would hand a caller a token issued for a different row.

Everything else is copied by reflection (`app.data.column_copy.values_to_copy`), so a column added
later is copied by default rather than silently dropped; the accompanying drift-guard test asserts
every mapped attribute is either copied or named here."""

ALLOCATION_COLUMNS_NOT_COPIED: frozenset[str] = frozenset({"id", "position_id", "created_at"})
"""The same for a month row. `position_id` is the one value that cannot be reflected: it has to be
the id of the *copied* position, which is why this table needs no separate registry entry — see
`copy_staffing_positions`."""


def copy_staffing_positions(session: Session, source: Scenario, copy: Scenario) -> None:
    """Copy one scenario's staffing positions **and their month rows** into the copy (AC-02).

    The single entry in `app.data.project_writes.SCENARIO_CHILD_COPIERS` for this aggregate, and
    deliberately one entry for two tables (ADR-0004, addendum 2026-09-19, point 1). The registry's
    contract (`Session`, source scenario, copy) carries no room for a mapping from old position ids
    to new ones, and a separately registered copier for the month rows would have nowhere to get
    one: the month row points at a `position_id` whose value is new on the copy. So the mapping is
    held here, locally, between the two halves of this function — the price being that "one registry
    entry per table" stops being literally true and becomes "one entry per aggregate whose root is a
    child of the scenario". A completeness test over that registry, if one is ever written, has to
    know the difference, or the next grandchild table will look registered while it is not.

    `flush()` between the two halves for the same reason `copy_scenario` flushes before calling the
    copiers: the child needs a parent row to point at. The enclosing transaction stays the caller's,
    so a failure in the second half undoes the first.

    Nothing here is guarded against writing to an `approved` scenario, and nothing here needs to be:
    every copy is a `draft` (`copy_scenario` takes no status parameter), so this function only ever
    writes into a scenario that accepts writes. The *source* is read and never written, which is why
    copying an approved scenario leaves its approval intact — and why a copy is the legal way to
    continue changing an approved calculation (ADR-0004).
    """
    positions = list(
        session.execute(
            sa.select(StaffingPosition)
            .where(StaffingPosition.scenario_id == source.id)
            .order_by(StaffingPosition.start_date, StaffingPosition.id)
        )
        .scalars()
        .all()
    )
    if not positions:
        return

    new_position_ids: dict[uuid.UUID, uuid.UUID] = {}
    for position in positions:
        new_position_id = uuid.uuid4()
        session.add(
            StaffingPosition(
                id=new_position_id,
                scenario_id=copy.id,
                **values_to_copy(position, excluded=POSITION_COLUMNS_NOT_COPIED),
            )
        )
        new_position_ids[position.id] = new_position_id
    session.flush()

    allocations = list(
        session.execute(
            sa.select(StaffingPositionAllocation).where(
                StaffingPositionAllocation.position_id.in_(new_position_ids)
            )
        )
        .scalars()
        .all()
    )
    for allocation in allocations:
        session.add(
            StaffingPositionAllocation(
                id=uuid.uuid4(),
                position_id=new_position_ids[allocation.position_id],
                **values_to_copy(allocation, excluded=ALLOCATION_COLUMNS_NOT_COPIED),
            )
        )
    session.flush()
