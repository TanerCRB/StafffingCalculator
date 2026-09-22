"""Approving a scenario: one transaction, snapshot first, status last (ADR-0004).

The one-way, human-performed step of ADR-0004, and the **first** path in this repository that ever
sets `ScenarioStatus.APPROVED`. Until now every proof about the immutability of an approved
calculation stood on a fixture writing that status directly; from here on there is a real path, and
with it a real race for the child-write guards to lose or win (`app.data.scenario_guard`).

**The order inside the transaction is mandated, not chosen** (ADR-0004, addendum 2026-09-22,
point 4):

1. `SELECT id FROM scenarios WHERE id = :id AND status = 'draft' FOR UPDATE` — the lock and the
   question in one statement. Zero rows means "there is no draft here to approve", which is the
   whole of criterion K-19: a second approval writes **nothing at all**, not even a snapshot row it
   would afterwards roll back.
2. the snapshot rows, each inserted by an `INSERT ... SELECT … FROM scenarios WHERE id = :id AND
   status <> 'approved'` — the same child-write guard shape every other table of this scenario
   uses. That is what makes the mandated order *checkable* rather than a comment: with the status
   update moved first, these inserts match no parent and write nothing, and the approval ends with
   a frozen scenario and an empty snapshot (criterion K-18, mutation b).
3. `UPDATE scenarios SET status = 'approved' WHERE id = :id AND status = 'draft'` — last, and still
   carrying the predicate although the lock above already answered it. Two statements holding one
   rule is not redundancy: the lock makes the window impossible, the predicate makes the statement
   correct on its own, and removing either is a mutation a test kills.

One transaction, one commit, at the end. A failure anywhere leaves the scenario `draft` with zero
snapshot rows — not "mostly approved" (criterion K-18).

**The shape of one snapshot insert**, written out here rather than kept as a helper nothing calls
(R-03, reviewer 2026-09-22)::

    INSERT INTO approved_snapshot_<table> (id, scenario_id, …)
    SELECT gen_random_uuid(), copied.*
      FROM (SELECT DISTINCT open.id AS scenario_id, …
              FROM (SELECT id FROM scenarios
                     WHERE id = :id AND status <> 'approved' FOR UPDATE) AS open
              JOIN … ) AS copied

The snapshot tables are children of `scenarios` like any other, so their `scenario_id` comes *from*
the guarded select rather than from a bound parameter — which is what makes the mandated "snapshot
before status" order a property the statements enforce rather than a comment. With the status
already `approved` the innermost select matches nothing and the insert writes nothing.

A callable helper for that shape is not worth having: the three inserts join different tables and
copy different columns, so only the two outer wrappers are shared (`_deduplicated_with_new_ids`
below). The version that stood here until 2026-09-22 was worse than useless — it bound the guard to
a placeholder scenario id (`uuid.UUID(int=0)`) that matches no row, so anything copying it as the
pattern for a fourth snapshot table would have got a guard selecting zero rows: a snapshot that is
always empty, written without an error.

**What this module does not do, deliberately.**

- **It does not decide who may approve.** ADR-0005's addendum of 2026-09-22 (point 9) says so
  explicitly: the placeholder identity carries no role dimension, and none of the ten permissions
  distinguishes "may plan" from "may approve". The endpoint declares a permission so that the
  deny-by-default rule holds at all, and that is the whole of the authorisation on this path. The
  risk was accepted at gate 1 with a named closing condition — the authentication ADR — and it is
  compounded by the absence of `audit_log` (plan block 8): an approval is irreversible, and nothing
  records who performed it.
- **It does not decide scope.** `app.data.staffing.scenario_in_scope` (hence `project_for_caller`)
  does, before anything here runs, so a refusal built here can never be the answer that confirms a
  scenario exists (criterion K-21).
- **It does not read the snapshot.** Nothing does, yet. This module proves the rows are written and
  that editing the source afterwards does not move them (K-16); the first reader is the reproducible
  report of plan block 8.
"""

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.scenario_guard import draft_scenario, unapproved_scenario
from app.data.staffing import scenario_in_scope
from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.models.approved_snapshot import (
    ApprovedSnapshotAbsenceType,
    ApprovedSnapshotWorkingCalendar,
    ApprovedSnapshotWorkingCalendarDay,
)
from app.models.catalog import AbsenceType, CatalogLocation, WorkingCalendar, WorkingCalendarDay
from app.models.scenario import Scenario, ScenarioStatus
from app.models.staffing import StaffingPosition, StaffingPositionAbsence

_SCENARIOS = Scenario.__table__


class ScenarioApprovalFailed(WriteFailed):
    """The approval broke for a reason nothing here established — a `500`, never a `409`.

    The same division as on every other write path in this repository (R-01, reviewer 2026-09-19):
    a statement timeout, a dropped connection or a numeric overflow must not reach a caller as a
    conflict nobody observed.
    """


class ScenarioApprovalRefused(ScenarioApprovalFailed, WriteRefused):
    """The database refused the approval for a reason its SQLSTATE names — a `409`.

    A subclass of both, so `except ScenarioApprovalFailed` still catches everything while
    `except ScenarioApprovalRefused` catches only what a caller can act on.
    """


class ScenarioApprovalRejected(RuntimeError):
    """The approval was understood, reached this layer, and was refused *by state* — a `409`.

    Distinct from `ScenarioApprovalFailed`, which means the statement broke. The same split
    `StaffingWriteRejected` makes one module over, and named separately rather than reused: an
    approval is not a staffing write, and a handler catching one should not silently catch the
    other.
    """


class ScenarioAlreadyApproved(ScenarioApprovalRejected):
    """There is no draft left to approve (ADR-0004: approving is one-way).

    The permanent kind of refusal: a retry can never succeed, and the way forward is a copy, which
    is a `draft` and can be approved on its own (`app.data.project_writes.copy_scenario`).
    """


def _failure(error: SQLAlchemyError) -> WriteFailed:
    """Classify one failed approval by SQLSTATE, into this module's pair of exceptions.

    Spelled once so every statement of the approval classifies identically — a per-call-site
    `except` block is how one of them ends up answering `409` for a defect while another does not.
    """
    return failure_for(
        error,
        subject="scenario approval",
        refused=ScenarioApprovalRefused,
        failed=ScenarioApprovalFailed,
    )


@dataclass(frozen=True)
class ApprovalResult:
    """What the approval wrote, counted per snapshot table.

    Counts, not rows: the endpoint answers with numbers a caller can act on ("this calculation was
    frozen against two calendars and eleven exceptional days"), and nothing yet *reads* a snapshot
    row back (see the module docstring). Returning the rows would advertise a read path that does
    not exist, and would be the first place a snapshot column could leak into a payload without
    anyone deciding it should.
    """

    scenario_id: uuid.UUID
    working_calendars: int
    working_calendar_days: int
    absence_types: int

    @property
    def snapshot_rows(self) -> int:
        """Every snapshot row this approval wrote — what criterion K-18's contrast counts."""
        return self.working_calendars + self.working_calendar_days + self.absence_types


_NEW_ID = sa.func.gen_random_uuid()
"""The primary key of a snapshot row, generated **by the database** inside the `INSERT ... SELECT`.

Unavoidable rather than preferred: a Python-side column default (`default=uuid.uuid4`) is applied by
SQLAlchemy only to rows it builds itself, and an `INSERT ... SELECT` never passes through that path
— the id would arrive as `NULL`. `gen_random_uuid()` has been a core function since PostgreSQL 13
(no `pgcrypto`, no `uuid-ossp`), and this project pins PostgreSQL 16 by digest in `tests/conftest`.

It is also the only correct choice here: generating N ids in Python would mean either N separate
statements or a `VALUES` list built from a prior `SELECT`, and both turn one guarded statement into
a read followed by a write — the shape ADR-0004 rejects by name.

**It may never appear in the select list of a `SELECT DISTINCT`** — see
`_deduplicated_with_new_ids`, which is the only place this constant is used."""


def _deduplicated_with_new_ids(values: sa.Select, name: str) -> sa.Select:
    """`SELECT gen_random_uuid(), copied.* FROM (SELECT DISTINCT <values> …) AS copied`.

    Two levels, and the order of the two is the whole point (R-01/S-01, reviewer and
    invariant-guardian, 2026-09-22). `SELECT DISTINCT` in PostgreSQL deduplicates over **every**
    column of its result list, and `gen_random_uuid()` is `VOLATILE`: it is evaluated once per input
    row *before* the `Unique`/`HashAggregate` node, not after it. With the id in the deduplicated
    select list every row is therefore unique by construction and the `DISTINCT` removes nothing::

        EXPLAIN SELECT DISTINCT gen_random_uuid() AS id, 1 AS a FROM generate_series(1,5);
        HashAggregate  … Group Key: gen_random_uuid()      -- five rows in, five rows out

    That was not a cosmetic defect: two positions in one location made the snapshot hold two
    identical calendar rows and two copies of every exceptional day, and the read key a later
    version of this data is meant to use — `(scenario_id, source_location_id)` and
    `(scenario_id, source_calendar_id)` — stopped being unique. The rows are never updated or
    deleted, so nothing could have repaired them afterwards.

    Generating the id one level up fixes it without changing what is copied: the `DISTINCT` now
    covers exactly the copied values and nothing else, so it can still never collapse two genuinely
    different rows. `name` is the alias of the deduplicating subquery — one statement may not use
    one name twice, and each of these selects already contains the guard's own subquery.

    The column order of the result is `id` followed by the subquery's own columns, which is the
    order the `from_select` column lists in this module are written in.
    """
    copied = values.distinct().subquery(name)
    return sa.select(_NEW_ID.label("id"), *copied.c)


def _copy_calendars(session: Session, scenario_id: uuid.UUID) -> tuple[int, int]:
    """Freeze the calendars the scenario's positions read, and every one of their days.

    **Values, not references** (ADR-0004, addendum 2026-09-22, point 3): the calendar's name, its
    standard working day and its week pattern are copied into columns of
    `approved_snapshot_working_calendar`, and the source ids travel as plain `uuid` values with no
    foreign key behind them. Editing the source calendar afterwards moves nothing here — AC-04,
    AC-10, criterion K-16.

    **Scope of the copy: the calendars this scenario's calculation actually reads**, i.e. those of
    the locations of its positions. Not the whole calendar catalogue: a snapshot of rows no position
    points at grows with the organisation instead of with the calculation, and its absence changes
    no answer. A scenario with no positions therefore freezes no calendar, which is the honest state
    of a draft with nothing planned in it.

    A location whose `calendar_id` is `NULL` contributes nothing either: there is no calendar to
    freeze, and the named `no_calendar` state is derived at read time from the live schema rather
    than stored (criterion K-23).
    """
    scenario_source = unapproved_scenario(scenario_id).subquery("open_scenario")
    # Two positions in one location are one calendar to freeze, not two identical rows — and two
    # locations sharing one calendar are two rows, because `source_location_id` differs and is what
    # says which position reads which. The deduplication is over the copied values only; the id is
    # generated above it (`_deduplicated_with_new_ids`).
    calendars = _deduplicated_with_new_ids(
        sa.select(
            scenario_source.c.id.label("scenario_id"),
            WorkingCalendar.id.label("source_calendar_id"),
            CatalogLocation.id.label("source_location_id"),
            WorkingCalendar.name,
            WorkingCalendar.standard_hours_per_day,
            WorkingCalendar.week_pattern,
        )
        .select_from(scenario_source)
        .join(StaffingPosition, StaffingPosition.scenario_id == scenario_source.c.id)
        .join(CatalogLocation, CatalogLocation.id == StaffingPosition.location_id)
        .join(WorkingCalendar, WorkingCalendar.id == CatalogLocation.calendar_id),
        "copied_calendars",
    )
    calendar_rows = len(
        session.execute(
            sa.insert(ApprovedSnapshotWorkingCalendar.__table__)
            .from_select(
                [
                    "id",
                    "scenario_id",
                    "source_calendar_id",
                    "source_location_id",
                    "name",
                    "standard_hours_per_day",
                    "week_pattern",
                ],
                calendars,
            )
            .returning(ApprovedSnapshotWorkingCalendar.__table__.c.id)
        ).all()
    )

    days_source = unapproved_scenario(scenario_id).subquery("open_scenario_days")
    # The **complete** set of days of each frozen calendar (ADR-0004, addendum, point 3c) — not only
    # those inside the scenario's period. A snapshot narrowed to a period would have to be
    # re-derived the day the period is read differently, and the period of a position is not the
    # period of the scenario. Each of those days once per calendar, however many positions or
    # locations reach it.
    days = _deduplicated_with_new_ids(
        sa.select(
            days_source.c.id.label("scenario_id"),
            WorkingCalendarDay.calendar_id.label("source_calendar_id"),
            WorkingCalendarDay.day,
            WorkingCalendarDay.kind,
        )
        .select_from(days_source)
        .join(StaffingPosition, StaffingPosition.scenario_id == days_source.c.id)
        .join(CatalogLocation, CatalogLocation.id == StaffingPosition.location_id)
        .join(WorkingCalendarDay, WorkingCalendarDay.calendar_id == CatalogLocation.calendar_id),
        "copied_days",
    )
    day_rows = len(
        session.execute(
            sa.insert(ApprovedSnapshotWorkingCalendarDay.__table__)
            .from_select(["id", "scenario_id", "source_calendar_id", "day", "kind"], days)
            .returning(ApprovedSnapshotWorkingCalendarDay.__table__.c.id)
        ).all()
    )
    return calendar_rows, day_rows


def _copy_absence_types(session: Session, scenario_id: uuid.UUID) -> int:
    """Freeze the name and both flags of every absence type this scenario's absences name.

    **The dictionary entry, never the instance** (ADR-0004, addendum 2026-09-22, point 1). The
    instances are the scenario's own data: nothing outside the scenario can change them, so there is
    nothing to freeze, and what protects them is the refusal of a write. The *type* is
    organisational — its name and its two flags can be edited after the approval by somebody who has
    never heard of this calculation — which is exactly the direction AC-04/AC-10 are about.

    Criterion K-17's first mutation is adding a snapshot table for the instances, and this function
    is where it would go.
    """
    scenario_source = unapproved_scenario(scenario_id).subquery("open_scenario_types")
    # One row per absence *type*, however many absences of it the scenario holds — the dictionary
    # entry is what is being frozen, and a scenario with ten holidays booked has one "Paid holiday"
    # to freeze, not ten copies of it.
    types = _deduplicated_with_new_ids(
        sa.select(
            scenario_source.c.id.label("scenario_id"),
            AbsenceType.id.label("source_absence_type_id"),
            AbsenceType.name,
            AbsenceType.generates_cost,
            AbsenceType.generates_revenue,
        )
        .select_from(scenario_source)
        .join(StaffingPosition, StaffingPosition.scenario_id == scenario_source.c.id)
        .join(
            StaffingPositionAbsence,
            StaffingPositionAbsence.position_id == StaffingPosition.id,
        )
        .join(AbsenceType, AbsenceType.id == StaffingPositionAbsence.absence_type_id),
        "copied_types",
    )
    return len(
        session.execute(
            sa.insert(ApprovedSnapshotAbsenceType.__table__)
            .from_select(
                [
                    "id",
                    "scenario_id",
                    "source_absence_type_id",
                    "name",
                    "generates_cost",
                    "generates_revenue",
                ],
                types,
            )
            .returning(ApprovedSnapshotAbsenceType.__table__.c.id)
        ).all()
    )


def approve_scenario(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> ApprovalResult | None:
    """Approve one scenario: write its snapshot, then freeze it. One transaction, one commit.

    `None` means "no such scenario *for this caller*" and is the same answer for every reason, which
    is what gives criterion K-21's 404-before-409 precedence structurally rather than by a rule
    somebody has to remember: a scenario outside the caller's scope never reaches the `draft` lock,
    so an "already approved" refusal cannot become a side channel confirming that it exists — not
    even for a scenario that really is approved.

    Raises `ScenarioAlreadyApproved` when there is no draft left to approve. Nothing is written in
    that case and nothing needs rolling back: the locking `SELECT` matched no row, so no insert was
    ever issued (criterion K-19).

    The final `UPDATE` affecting no row is treated as a failure rather than as a refusal, and the
    transaction is rolled back: with the lock held since step 1 that combination cannot arise from a
    competing approval, so it means the locked row vanished under a foreign key that says it cannot
    — a defect, and "something broke" is the honest answer (R-01).
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    try:
        locked = session.execute(draft_scenario(scenario_id)).one_or_none()
        if locked is None:
            # No write has been issued at this point — not a snapshot row, not a status update.
            # That is the claim of K-19, and it is a property of the *order* rather than of a
            # rollback: a rollback would undo rows that were written, this never writes them.
            raise ScenarioAlreadyApproved(
                "This scenario is already approved. Approving is one-way; copy the scenario to "
                "open a new version and change the copy."
            )

        calendars, days = _copy_calendars(session, scenario_id)
        absence_types = _copy_absence_types(session, scenario_id)

        frozen = session.execute(
            sa.update(_SCENARIOS)
            .where(_SCENARIOS.c.id == scenario_id, _SCENARIOS.c.status == ScenarioStatus.DRAFT)
            .values(status=ScenarioStatus.APPROVED)
            .returning(_SCENARIOS.c.id)
        ).one_or_none()
        if frozen is None:
            session.rollback()
            raise ScenarioApprovalFailed(
                "Approving the scenario failed: the draft row locked for this approval could not "
                "be frozen."
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`, as on every write path here: a chained exception is printed with its cause,
        # so re-raising *with* the original would put PostgreSQL's `DETAIL: Failing row contains
        # (…)` back into the traceback one line further down (NF-11).
        raise _failure(error) from None
    return ApprovalResult(
        scenario_id=scenario_id,
        working_calendars=calendars,
        working_calendar_days=days,
        absence_types=absence_types,
    )
