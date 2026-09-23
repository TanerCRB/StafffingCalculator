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
1a. `SELECT id FROM projects WHERE id = :project_id FOR SHARE` — the approving half of the P-C seam
   (SC-1-10, `app.data.scenario_guard.approving_project_lock`). It serialises this approval against
   an edit of any group-2 field of the scenario's project (`FROZEN_BY_APPROVED_SCENARIO`), which the
   scenario lock above cannot do: an edit of `projects` never asks for it. Taken after the scenario
   lock, never before — the order every path that locks both follows, so there is no cycle.
2. the snapshot rows — **every table of them in one statement** (`_snapshot_statement`, S-01
   below), each table's rows inserted by an `INSERT ... SELECT … FROM scenarios WHERE id = :id AND
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

**One statement for the whole snapshot, and why a transaction is not enough** (S-01, SC-3-03). The
lock in step 1 serialises this approval against every write to the scenario's *own* children — they
all take the same row lock (`app.data.scenario_guard`). It serialises nothing against the
**catalogue**: a calendar, its days, a location's `calendar_id`, an absence type's flag and a budget
window are organisational rows, and nobody editing them asks for this scenario's lock. Under `READ
COMMITTED` every statement takes a fresh snapshot of the database, so four copiers issued as four
statements read the catalogue at four different moments, and an edit committed between two of them
lands in one half of a pair and not in the other. Three such pairs exist, and each produces a
snapshot that describes no state the catalogue was ever in:

- **calendar ↔ its days** — a location re-pointed at another calendar between the two inserts
  freezes calendar A with the days of calendar B;
- **calendar ↔ budgets** (SC-3-02's pair, point 2 of the SC-3-03 addendum) — the same re-pointing
  freezes calendar A with the budget windows of calendar B;
- **statutory type ↔ budgets** — a budget committed between the two inserts is frozen while the
  type flagged `is_statutory_leave` is not, which the snapshot's own contract reads as "nobody had
  named a statutory type" (`ApprovedSnapshotAbsenceType`): a whole year's entitlement not deducted,
  for ever.

PostgreSQL gives one statement one snapshot, *including every data-modifying `WITH` query inside
it*. So the four inserts are four CTEs of one statement, issued after step 1 has the lock — which
also means the snapshot is taken **under** the lock, and a child write committed while this approval
was waiting for it is in the copy. Both halves matter, and the second is why a stricter isolation
level was measured and rejected (variant A, 2026-09-23): `REPEATABLE READ` fixes the snapshot at the
transaction's *first* statement, before the lock is granted, and an approval would then freeze a
scenario whose last committed child row it cannot see.

The four CTEs read none of each other's output — every one of them reads the source tables — so no
order between them is needed or implied; PostgreSQL runs each data-modifying CTE exactly once and to
completion whether or not the outer query reads it. The outer query reads each one anyway, for its
row count (`ApprovalResult`).

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

A callable helper for that shape is not worth having: the four inserts join different tables and
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
- **It does not read the snapshot.** One table of it has a reader now, and it lives elsewhere:
  `approved_snapshot_organization_defaults` is read by `app.data.organization_defaults` (SC-1-10,
  gate 1 P-B — the first snapshot reader in the repository), which resolves an approved scenario's
  assumptions from the frozen row and never from the live one. Since SC-4-01 a second one does:
  `approved_snapshot_catalog_default_rate` is read by `app.data.commercial_terms`, which prices an
  approved scenario's T&M revenue from the frozen windows, per month, with the same whole-month
  predicate the live read uses (ADR-0004, addendum 2026-09-23 SC-4-01, point 2e). The other four
  tables still have no reader; theirs is the reproducible report of plan block 8. This module proves
  the rows are written and that editing the source afterwards does not move them (K-16).
"""

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.commercial_terms import priced_month_windows
from app.data.scenario_guard import (
    approving_project_lock,
    draft_scenario,
    unapproved_scenario,
)
from app.data.staffing import scenario_in_scope
from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.models.approved_snapshot import (
    ApprovedSnapshotAbsenceBudget,
    ApprovedSnapshotAbsenceType,
    ApprovedSnapshotCatalogDefaultRate,
    ApprovedSnapshotOrganizationDefaults,
    ApprovedSnapshotWorkingCalendar,
    ApprovedSnapshotWorkingCalendarDay,
)
from app.models.catalog import (
    AbsenceBudget,
    AbsenceType,
    CatalogDefaultRate,
    CatalogLocation,
    WorkingCalendar,
    WorkingCalendarDay,
)
from app.models.organization_defaults import OrganizationDefaults
from app.models.scenario import Scenario, ScenarioStatus
from app.models.staffing import (
    StaffingPosition,
    StaffingPositionAbsence,
    StaffingPositionAllocation,
)

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
    absence_budgets: int
    """The fourth counter (SC-3-03). Adding it is a **deliberate** change to a canary: every test
    asserting the shape of this result by equality fails the day a snapshot table joins, which is
    the only moment at which noticing is cheap (ADR-0004, addendum 2026-09-22 SC-3-03, point 4).
    Zero here means one of two very different things — "no budget row covered the pairs this
    scenario reads" (a legal, named state) or "budgets existed and were not copied" (the regression
    point 2 is about) — so it is checked against a contrast, never on its own (point 6)."""

    organization_defaults: int
    """The fifth counter (SC-1-10, ADR-0012 point 6) — `1` or `0`, and both are facts: `1` means
    the organisation had a defaults row and it is frozen; `0` means it had none, and the approved
    scenario keeps "no organisation default" for ever (criterion K-06). The same deliberate canary
    growth as the fourth counter."""

    catalog_default_rates: int
    """The sixth counter (SC-4-01, ADR-0004 addendum 2026-09-23 SC-4-01, point 2): how many
    catalogue rate windows the T&M revenue of this scenario reads and this approval froze — every
    window of every priced month (`month_is_priced`: the month's windows cover it whole and share
    one selling rate and currency), so one month may contribute more than one. Zero is again two
    facts — "no position-month was priced" (legal: those months stay `no_rate`) or "windows
    existed and were not copied" — so it is read against a contrast (criterion K-08), never on its
    own. The same deliberate canary growth as the fourth and fifth."""

    @property
    def snapshot_rows(self) -> int:
        """Every snapshot row this approval wrote — what criterion K-18's contrast counts."""
        return (
            self.working_calendars
            + self.working_calendar_days
            + self.absence_types
            + self.absence_budgets
            + self.organization_defaults
            + self.catalog_default_rates
        )


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


def _copy_calendars(scenario_id: uuid.UUID) -> sa.Insert:
    """Freeze the calendars the scenario's positions read — their days are `_copy_calendar_days`.

    Returns the statement rather than executing it, like every copier here: it is one CTE of
    `_snapshot_statement`, and executing it on its own is the S-01 defect (module docstring). Until
    2026-09-23 this function also copied the days, as a second statement — and the pair
    "calendar ↔ its days" was the one nobody had named, because both halves lived in one function
    and looked atomic when they were not.

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
    return (
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
    )


def _copy_calendar_days(scenario_id: uuid.UUID) -> sa.Insert:
    """Freeze every exceptional day of every calendar `_copy_calendars` freezes.

    It does not read `_copy_calendars`' output — it walks the same chain (positions → location →
    calendar) from the source tables — and that is only correct because both are CTEs of one
    statement and so read the same catalogue (S-01, module docstring). As two statements, a location
    re-pointed between them froze calendar A with the days of calendar B.
    """
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
    return (
        sa.insert(ApprovedSnapshotWorkingCalendarDay.__table__)
        .from_select(["id", "scenario_id", "source_calendar_id", "day", "kind"], days)
        .returning(ApprovedSnapshotWorkingCalendarDay.__table__.c.id)
    )


def _copy_absence_types(scenario_id: uuid.UUID) -> sa.Insert:
    """Freeze the name and three flags of every absence type this scenario reads — from **two**
    sources.

    **The dictionary entry, never the instance** (ADR-0004, addendum 2026-09-22, point 1). The
    instances are the scenario's own data: nothing outside the scenario can change them, so there is
    nothing to freeze, and what protects them is the refusal of a write. The *type* is
    organisational — its name and its flags can be edited after the approval by somebody who has
    never heard of this calculation — which is exactly the direction AC-04/AC-10 are about.

    Criterion K-17's first mutation is adding a snapshot table for the instances, and this function
    is where it would go.

    **The second source is the type flagged `is_statutory_leave`, copied whenever this approval
    freezes a budget — whether or not the scenario booked anything against it** (reviewer, second
    round, High). It closes a gap the first version of the R-02 fix opened, and the gap was in the
    *data* rather than in any behaviour a live read could show:

    - **no type is flagged anywhere in the catalogue** → the budget is not applied at all, because
      nothing says which booked leave it already covers (`NO_STATUTORY_LEAVE_TYPE`);
    - **a type is flagged and this scenario booked nothing against it** → the budget *is* applied,
      whole, and the reader has to deduct all of it.

    Copied only through the booked absences, those two produce the **same** snapshot: one budget row
    and zero absence-type rows. A reader of the snapshot could not tell "deduct the whole
    entitlement" from "deduct none of it" — a difference of 26 days a year on an approved
    calculation, frozen for ever, because the snapshot has no `UPDATE` or `DELETE` path. Two of this
    repository's own green tests documented the contradiction side by side before anyone noticed it.

    With the second source, the presence of a row carrying `is_statutory_leave = true` **is** the
    fact: present → the type was named and the frozen budget applies; absent → nobody had named one
    when this was approved, and the frozen budget applies to nothing. How much of the entitlement
    the scenario's own bookings absorbed is then counted by the reader from the live
    `staffing_position_absence` rows, which are safe to read for exactly this purpose: they belong
    to the approved scenario and are frozen by the write guard (ADR-0004, addendum 2026-09-22,
    point 1).

    **It is conditioned on a budget being frozen, not on the scenario merely having positions.** A
    scenario that freezes no budget — no calendar, or no month rows — has nothing for the flag to
    qualify, and copying a dictionary entry it never reads would grow the snapshot with the
    organisation instead of with the calculation (point 3).

    **The second source and `_copy_absence_budgets` must read the same catalogue**, or the contract
    above breaks in the one direction nobody would see: a budget committed between the two reads is
    frozen while the flagged type is not, and the snapshot then says "no statutory type was named".
    That is why both are CTEs of one statement (`_snapshot_statement`, S-01) and why this function
    returns the statement instead of executing it.
    """
    booked_source = unapproved_scenario(scenario_id).subquery("open_scenario_types")
    # One row per absence *type*, however many absences of it the scenario holds — the dictionary
    # entry is what is being frozen, and a scenario with ten holidays booked has one "Paid holiday"
    # to freeze, not ten copies of it.
    booked = (
        sa.select(
            booked_source.c.id.label("scenario_id"),
            AbsenceType.id.label("source_absence_type_id"),
            AbsenceType.name,
            AbsenceType.generates_cost,
            AbsenceType.generates_revenue,
            AbsenceType.is_statutory_leave,
        )
        .select_from(booked_source)
        .join(StaffingPosition, StaffingPosition.scenario_id == booked_source.c.id)
        .join(
            StaffingPositionAbsence,
            StaffingPositionAbsence.position_id == StaffingPosition.id,
        )
        .join(AbsenceType, AbsenceType.id == StaffingPositionAbsence.absence_type_id)
    )

    statutory_source = unapproved_scenario(scenario_id).subquery("open_scenario_statutory")
    # The same chain `_copy_absence_budgets` walks — positions, their months, the calendar of their
    # location, the budget window covering each month — and then the flagged type, joined on the
    # flag alone. At most one row can carry it (`uq_absence_type_statutory_leave`), so this join
    # adds one row per frozen budget and the deduplication below collapses them into one.
    statutory = (
        sa.select(
            statutory_source.c.id.label("scenario_id"),
            AbsenceType.id.label("source_absence_type_id"),
            AbsenceType.name,
            AbsenceType.generates_cost,
            AbsenceType.generates_revenue,
            AbsenceType.is_statutory_leave,
        )
        .select_from(statutory_source)
        .join(StaffingPosition, StaffingPosition.scenario_id == statutory_source.c.id)
        .join(
            StaffingPositionAllocation,
            StaffingPositionAllocation.position_id == StaffingPosition.id,
        )
        .join(CatalogLocation, CatalogLocation.id == StaffingPosition.location_id)
        .join(
            AbsenceBudget,
            sa.and_(
                AbsenceBudget.calendar_id == CatalogLocation.calendar_id,
                AbsenceBudget.engagement_type_id == StaffingPosition.engagement_type_id,
                AbsenceBudget.valid_period.bool_op("@>")(
                    StaffingPositionAllocation.period_month
                ),
            ),
        )
        # `is_(True)` rather than the bare column: an onclause that mentions only the table being
        # joined leaves SQLAlchemy inferring the left side from it, and it then tries to join
        # `absence_type` to itself. Spelled as a boolean expression, the left side is the join chain
        # above and the clause reads `JOIN absence_type ON is_statutory_leave IS true`.
        .join(AbsenceType, AbsenceType.is_statutory_leave.is_(True))
    )

    # `UNION`, not `UNION ALL`: a flagged type the scenario also booked appears in both halves and
    # must be one frozen row, not two. The outer `DISTINCT` of `_deduplicated_with_new_ids` is then
    # a no-op on the values — and it stays, because the id must still be generated *above* the
    # deduplication (the SC-3-02 defect this repository already paid for once).
    both = sa.union(booked, statutory).subquery("booked_and_statutory_types")
    types = _deduplicated_with_new_ids(sa.select(*both.c), "copied_types")
    return (
        sa.insert(ApprovedSnapshotAbsenceType.__table__)
        .from_select(
            [
                "id",
                "scenario_id",
                "source_absence_type_id",
                "name",
                "generates_cost",
                "generates_revenue",
                "is_statutory_leave",
            ],
            types,
        )
        .returning(ApprovedSnapshotAbsenceType.__table__.c.id)
    )


def _copy_absence_budgets(scenario_id: uuid.UUID) -> sa.Insert:
    """Freeze the leave budget of every (calendar, engagement type) pair this scenario reads.

    **Together with the calendars, in the same statement, not as a separate step** (ADR-0004,
    addendum 2026-09-22 SC-3-03, point 2; S-01). "The same transaction" was the first reading of
    that point and it was not enough: under `READ COMMITTED` two statements of one transaction read
    two different catalogues, and a location re-pointed between them froze calendar A with the
    budgets of calendar B. One statement is one snapshot (module docstring).

    The pair "calendar + budget" is atomic in the snapshot: an approval that froze the calendars
    and not the budgets their positions read would reproduce a
    *different* billable capacity than the one approved — the working days from the frozen calendar,
    the leave days subtracted from them from the live table — and it would do so without any
    mutation in the code, simply by this function not existing.

    **Scope of the copy: what this scenario's calculation actually reads** (point 3) — the calendars
    of the locations of its positions, crossed with the engagement types of those same positions.
    Not the whole budget table: rows no position points at grow with the organisation instead of
    with the calculation and change no answer. A scenario with no positions freezes no budget, which
    is the honest state of a draft with nothing planned in it.

    **The windows are resolved by the months the scenario actually plans, not by the day of the
    approval** (ADR-0004, addendum 2026-09-22 SC-3-03, point 7 — the corrected version; the
    original wording of that point prescribed `valid_period @> CURRENT_DATE` and was found wrong in
    review, and the addendum keeps its own record of that history). The defect it produced is not
    hypothetical and cannot be repaired afterwards, because nothing updates or deletes a snapshot
    row: a scenario planned for 2027, whose 2027 budget already exists and is what the live grid
    shows on screen, approved on a day in 2026 froze the **2026** window — a value no month of that
    scenario ever reads. The join below therefore goes through
    `staffing_position_allocation.period_month` and asks `valid_period @> <that month>`: the same
    containment, against the same generated column, that
    `app.data.absence_budget.budgets_for_months`
    uses for the live read, so the frozen set is exactly the set the calculation was reading.

    **Putting `CURRENT_DATE` back here would reproduce that defect**, and it would look like a
    tidy-up towards a rule the ADR no longer states. It does not: point 7 now reads "resolve against
    the months the scenario plans", and a reader who finds a clock on this path is looking at a
    regression rather than at the decision.

    **A scenario spanning more than one window freezes more than one row, and that is decided
    rather than tolerated** (point 7a). It is the honest answer — a two-year plan across a
    regulation change reads two entitlements, and freezing one of them would misstate every month of
    the other — and it has two consequences the addendum takes with it: the read key of this table
    is `(scenario_id, source_calendar_id, source_engagement_type_id, effective_from)` rather than
    the triple, and the reader resolves per month exactly as the live path does (point 7c, which
    also binds the first reader — plan block 8 — to prove that resolution with its own criterion).

    **No clock anywhere on this path** (point 7b). With the months doing the resolving,
    `CURRENT_DATE` had nothing left to decide, and `resolved_on` — a column that existed only to say
    which day the single window had been chosen on — went with it: every frozen row now carries the
    window it is, so the attribution is in the data rather than in a timestamp. The approval is a
    pure function of the plan and the catalogue, which is also what makes this test suite
    independent of the day it runs on.

    **Raw values only** — days, unit, window, source — and never a prorated figure (point 7 and
    ADR-0008's addendum, point 10): the proration is the reader's formula and depends on a month
    grid this table does not hold.

    The same two-level `DISTINCT` as every other copier (`_deduplicated_with_new_ids`): two
    positions sharing a (calendar, engagement type) pair freeze one row per window they read, and
    thirty-six months of one window are one row and not thirty-six. Two locations pointing at one
    calendar are one budget row here and not two, because the budget hangs on the calendar and not
    on the location (ADR-0008, addendum SC-3-03, point 3a) — which is also why this table, unlike
    the frozen calendar, carries no `source_location_id`.
    """
    scenario_source = unapproved_scenario(scenario_id).subquery("open_scenario_budgets")
    budgets = _deduplicated_with_new_ids(
        sa.select(
            scenario_source.c.id.label("scenario_id"),
            AbsenceBudget.id.label("source_budget_id"),
            AbsenceBudget.calendar_id.label("source_calendar_id"),
            AbsenceBudget.engagement_type_id.label("source_engagement_type_id"),
            AbsenceBudget.budget_days,
            AbsenceBudget.unit,
            AbsenceBudget.source,
            AbsenceBudget.effective_from,
            AbsenceBudget.effective_to,
        )
        .select_from(scenario_source)
        .join(StaffingPosition, StaffingPosition.scenario_id == scenario_source.c.id)
        .join(
            StaffingPositionAllocation,
            StaffingPositionAllocation.position_id == StaffingPosition.id,
        )
        .join(CatalogLocation, CatalogLocation.id == StaffingPosition.location_id)
        .join(
            AbsenceBudget,
            sa.and_(
                AbsenceBudget.calendar_id == CatalogLocation.calendar_id,
                AbsenceBudget.engagement_type_id == StaffingPosition.engagement_type_id,
                # The one way this codebase asks "which window covers this month" — the generated
                # column the `EXCLUDE` constraint reads, never a rebuilt `daterange(...)`
                # (ADR-0008, point 3), and the same predicate the live read uses.
                AbsenceBudget.valid_period.bool_op("@>")(
                    StaffingPositionAllocation.period_month
                ),
            ),
        ),
        "copied_budgets",
    )
    return (
        sa.insert(ApprovedSnapshotAbsenceBudget.__table__)
        .from_select(
            [
                "id",
                "scenario_id",
                "source_budget_id",
                "source_calendar_id",
                "source_engagement_type_id",
                "budget_days",
                "unit",
                "source",
                "effective_from",
                "effective_to",
            ],
            budgets,
        )
        .returning(ApprovedSnapshotAbsenceBudget.__table__.c.id)
    )


def _copy_organization_defaults(scenario_id: uuid.UUID) -> sa.Insert:
    """Freeze the organisation's default assumptions — raw — for this scenario (SC-1-10).

    **The raw row, not the scenario's resolved margin** (gate 1, P-A; ADR-0012, point 6): the chain
    scenario → project → organisation is resolved when the snapshot is read, from this row plus the
    scenario's and the project's own columns, which the write guards keep still. A copier that
    stored "the value this scenario resolved to" and the level it came from would be a computed
    figure inside a record of what was approved.

    **Unconditional on the scenario's contents.** Unlike the four copiers above, this one does not
    walk positions: a target margin applies to the calculation as a whole, so a scenario with
    nothing planned in it still freezes the organisation's defaults. What it *is* conditional on is
    the row existing — no row, nothing copied, and the absence of a frozen row is itself the frozen
    fact (criterion K-06; the pattern of the statutory type in `_copy_absence_types`).

    A CTE of `_snapshot_statement`, like the others, so it reads the organisation's defaults from
    the same snapshot of the database as everything else this approval freezes (S-01). The source
    is a singleton, so the `DISTINCT` of `_deduplicated_with_new_ids` removes nothing here; it is
    used anyway because it is the one place a snapshot id is generated.
    """
    scenario_source = unapproved_scenario(scenario_id).subquery("open_scenario_defaults")
    defaults = _deduplicated_with_new_ids(
        sa.select(
            scenario_source.c.id.label("scenario_id"),
            OrganizationDefaults.target_margin_percent,
            OrganizationDefaults.overload_threshold_percent,
        )
        .select_from(scenario_source)
        .join(OrganizationDefaults, sa.true()),
        "copied_organization_defaults",
    )
    return (
        sa.insert(ApprovedSnapshotOrganizationDefaults.__table__)
        .from_select(
            ["id", "scenario_id", "target_margin_percent", "overload_threshold_percent"],
            defaults,
        )
        .returning(ApprovedSnapshotOrganizationDefaults.__table__.c.id)
    )


def _copy_catalog_default_rates(scenario_id: uuid.UUID) -> sa.Insert:
    """Freeze every catalogue rate window this scenario's T&M revenue prices a month with (SC-4-01).

    **The first rate snapshot in the repository** (ADR-0004, addendum 2026-09-23 SC-4-01, point 2):
    from SC-4-01 a result of an approved calculation depends on `default_selling_rate`, so editing a
    catalogue rate after approval must move nothing (AC-04, AC-10; criterion K-09).

    **Scope of the copy: only the windows the calculation reads** (point 2c) — for each position of
    the scenario and each month of its allocation, the organisation's own windows (`vendor_id IS
    NULL`, ADR-0003 point 4) that **price** that month: `month_is_priced` over
    `app.data.commercial_terms.priced_month_windows`, the very statement the live revenue read uses,
    so the frozen set is by construction the set the draft was priced with. Since R-01 (gate 2) that
    can be **more than one window per month** — a cost-rate change mid-month splits the month into
    two windows sharing one selling rate — and then **all** of them are frozen, because the snapshot
    reader re-asks "do they cover the month with one price?" and needs every piece to answer yes.
    A month that is not priced freezes nothing and stays `no_rate` for ever. Not the catalogue, not
    another vendor's window, not a window no priced month reaches (criterion K-08).

    The filter on `month_is_priced` sits **outside** the subquery that computes it: the predicate is
    a window function over each month's windows, and a `WHERE` inside would change the partitions it
    sees.

    **Deduplicated over the copied values** (`_deduplicated_with_new_ids`): two positions of one
    tuple, or thirty-six months of one window, are one frozen row — the SC-3-02 S-01/R-01 defect is
    not re-opened on the sixth table.

    **`default_cost_rate` is copied although nothing in SC-4-01 reads it** (point 2b): the snapshot
    has no UPDATE path, and a cost not frozen now is a cost an approved scenario never recovers. It
    makes the table a carrier of personnel cost (ADR-0005, addendum SC-4-01, point 7); nothing in
    SC-4-01 returns its rows.

    A CTE of `_snapshot_statement` like the others (point 2d): a rate edited in the catalogue while
    this approval runs cannot land between "the months this scenario plans" and "the windows frozen
    for them", because both are read from the one snapshot of the database this statement takes.
    """
    scenario_source = unapproved_scenario(scenario_id).subquery("open_scenario_rates")
    priced = priced_month_windows(from_snapshot=False, scenario_id=scenario_id).subquery(
        "priced_catalog_months"
    )
    rates = _deduplicated_with_new_ids(
        sa.select(
            scenario_source.c.id.label("scenario_id"),
            CatalogDefaultRate.id.label("source_rate_id"),
            CatalogDefaultRate.role_id.label("source_role_id"),
            CatalogDefaultRate.seniority_id.label("source_seniority_id"),
            CatalogDefaultRate.location_id.label("source_location_id"),
            CatalogDefaultRate.engagement_type_id.label("source_engagement_type_id"),
            CatalogDefaultRate.vendor_id.label("source_vendor_id"),
            CatalogDefaultRate.default_cost_rate,
            CatalogDefaultRate.default_selling_rate,
            CatalogDefaultRate.currency,
            CatalogDefaultRate.unit,
            CatalogDefaultRate.effective_from,
            CatalogDefaultRate.effective_to,
        )
        .select_from(scenario_source)
        .join(
            priced,
            sa.and_(priced.c.scenario_id == scenario_source.c.id, priced.c.month_is_priced),
        )
        .join(CatalogDefaultRate, CatalogDefaultRate.id == priced.c.window_id),
        "copied_catalog_default_rates",
    )
    return (
        sa.insert(ApprovedSnapshotCatalogDefaultRate.__table__)
        .from_select(
            [
                "id",
                "scenario_id",
                "source_rate_id",
                "source_role_id",
                "source_seniority_id",
                "source_location_id",
                "source_engagement_type_id",
                "source_vendor_id",
                "default_cost_rate",
                "default_selling_rate",
                "currency",
                "unit",
                "effective_from",
                "effective_to",
            ],
            rates,
        )
        .returning(ApprovedSnapshotCatalogDefaultRate.__table__.c.id)
    )


def _snapshot_statement(scenario_id: uuid.UUID) -> sa.Select:
    """Every snapshot insert as one statement: six data-modifying CTEs and a count of each (S-01).

    Rendered, it is::

        WITH snapshot_calendars AS (INSERT INTO approved_snapshot_working_calendar … RETURNING id),
             snapshot_calendar_days AS (INSERT INTO approved_snapshot_working_calendar_day … ),
             snapshot_absence_types AS (INSERT INTO approved_snapshot_absence_type … ),
             snapshot_absence_budgets AS (INSERT INTO approved_snapshot_absence_budget … ),
             snapshot_organization_defaults AS (INSERT INTO
                 approved_snapshot_organization_defaults … ),
             snapshot_catalog_default_rates AS (INSERT INTO
                 approved_snapshot_catalog_default_rate … )
        SELECT (SELECT count(*) FROM snapshot_calendars) AS working_calendars, …

    **Why one statement** is the module docstring's S-01 section: one statement is one snapshot of
    the catalogue, taken after `draft_scenario` has the lock, so the three pairs (calendar ↔ days,
    calendar ↔ budgets, statutory type ↔ budgets) are read from one state rather than from four.
    Splitting this back into separate `session.execute` calls — in any grouping and any order of
    the four copiers — is the regression `tests/test_scenario_approval.py::test_s_01_…` exists to
    catch, by count and by the race it reopens (R-01, 2026-09-23) — and it would look like a
    readability tidy-up, which is why this says so.

    **No CTE references another**, and none needs to: each copier reads the source tables, never a
    frozen row, so the dependency order between them is empty (checked 2026-09-23 against the four
    copiers). PostgreSQL does not order sibling data-modifying CTEs and does not have to — they all
    see the same snapshot, and none of them can see the others' rows anyway.

    **Every CTE is referenced by the outer query**, and that is load-bearing in SQLAlchemy rather
    than in PostgreSQL: SQLAlchemy renders a CTE only if something in the statement refers to it,
    so a copier added here without a counter below would be silently dropped from the SQL — the
    "snapshot that is always empty" failure `SNAPSHOT_TABLES` exists to make visible. The count per
    CTE is also what replaces `len(result.all())` per statement: the same `RETURNING id` rows,
    counted by the database.
    """
    inserts = {
        "working_calendars": _copy_calendars(scenario_id).cte("snapshot_calendars"),
        "working_calendar_days": _copy_calendar_days(scenario_id).cte("snapshot_calendar_days"),
        "absence_types": _copy_absence_types(scenario_id).cte("snapshot_absence_types"),
        "absence_budgets": _copy_absence_budgets(scenario_id).cte("snapshot_absence_budgets"),
        # The fifth (SC-1-10). Inside the one statement rather than after it: the defaults are
        # organisational rows like the catalogue, edited by people who never ask for this lock.
        "organization_defaults": _copy_organization_defaults(scenario_id).cte(
            "snapshot_organization_defaults"
        ),
        # The sixth (SC-4-01, ADR-0004 addendum 2026-09-23 point 2d): the rate windows the T&M
        # revenue reads, from the same snapshot of the catalogue as everything else frozen here.
        "catalog_default_rates": _copy_catalog_default_rates(scenario_id).cte(
            "snapshot_catalog_default_rates"
        ),
    }
    return sa.select(
        *(
            sa.select(sa.func.count()).select_from(cte).scalar_subquery().label(counter)
            for counter, cte in inserts.items()
        )
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

        # The approving half of the P-C seam (SC-1-10, criterion K-08): held to the commit, it makes
        # an edit of a group-2 field of this project wait for this approval — or this approval wait
        # for an edit already in flight. `app.data.scenario_guard` has both orders.
        session.execute(approving_project_lock(project_id))

        # One statement, not four (S-01): one snapshot of the catalogue for every snapshot table,
        # taken now — after the lock above was granted, so a child write committed while this
        # approval waited for it is in the copy. See `_snapshot_statement`.
        counts = session.execute(_snapshot_statement(scenario_id)).one()

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
        working_calendars=counts.working_calendars,
        working_calendar_days=counts.working_calendar_days,
        absence_types=counts.absence_types,
        absence_budgets=counts.absence_budgets,
        organization_defaults=counts.organization_defaults,
        catalog_default_rates=counts.catalog_default_rates,
    )
