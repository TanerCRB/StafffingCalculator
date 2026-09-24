"""SC-3-02, K-16 and K-17 — what the approval freezes, and what it deliberately does not (ADR-0004).

The first `approved_snapshot_*` tables in this repository, so the shape proven here is the shape
exchange rates (ADR-0006), resolved rates and commercial-rule versions (ADR-0003) inherit. Two
criteria, pulling in opposite directions:

- **K-16: values, frozen.** Editing the source calendar after the approval must not move a single
  figure in the snapshot. That is AC-04/AC-10 stated for calendars — and it is proven twice, once
  behaviourally (the values do not change) and once structurally (no snapshot column is a foreign
  key to the row it copied), because the mutation "store the id as an FK and read through a join"
  is invisible to a behavioural test that never edits the source.
- **K-17: instances, not frozen.** The scenario's own absence rows do **not** enter the snapshot,
  because nothing outside the scenario can change them (ADR-0004, addendum 2026-09-22, point 1) —
  they are protected by the refusal of a write instead (criterion K-13). And a *copy* of an approved
  scenario holds zero snapshot rows, because a copy has not been through an approval.

The contrast running through both: the same approval writes a **non-zero** number of calendar, day
and absence-type rows. Without it, "the snapshot holds no absence instances" would be satisfied by
an approval that wrote nothing at all.

**SC-3-03 adds K-07** in a section of its own at the end: the leave budget is frozen **with** the
calendars, as raw values (days, window, source), never as a prorated figure — and the flag that says
which absence type a budget settles against joins the frozen absence type (ADR-0004, addendum
2026-09-22 SC-3-03, points 2, 7 and 8).
"""

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    SNAPSHOT_TABLES,
    AbsenceBudget,
    AbsenceType,
    ApprovedSnapshotAbsenceBudget,
    ApprovedSnapshotAbsenceType,
    ApprovedSnapshotWorkingCalendar,
    ApprovedSnapshotWorkingCalendarDay,
    Scenario,
    WorkingCalendar,
    WorkingCalendarDayKind,
)
from tests.conftest import (
    BUDGET_SOURCE,
    IN_SCOPE_USER,
    STATUTORY_LEAVE_TYPE_NAME,
    approve_path,
    as_caller,
    count_snapshot_rows,
    make_absence,
    make_absence_budget,
    make_absence_type,
    make_allocation,
    make_calendar_day,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
    staffing_path,
)

MARCH = date(2026, 3, 1)


def _approvable_scenario(session: Session):
    """A project in scope, a draft scenario, one position in a location with a calendar that has
    two exceptional days, and one absence of a named type.

    Everything the snapshot is supposed to freeze, and nothing it is not: the absence *instance* is
    here so K-17 has something that must stay out, and the absence *type* is here so the same
    approval has something to put in.
    """
    calendar = make_working_calendar(
        session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    make_calendar_day(
        session, calendar, day=date(2026, 12, 25), kind=WorkingCalendarDayKind.NON_WORKING
    )
    make_calendar_day(
        session, calendar, day=date(2026, 12, 12), kind=WorkingCalendarDayKind.WORKING
    )
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline")
    position = make_staffing_position(
        session,
        scenario,
        make_dimension_tuple(session, calendar=calendar),
        headcount=1,
        start_date=MARCH,
    )
    make_allocation(session, position, period_month=MARCH)
    make_absence(
        session,
        position,
        make_absence_type(session, name="Paid holiday"),
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 6),
    )
    return project, scenario, calendar, position


def _snapshot_calendar(session: Session, scenario_id: uuid.UUID) -> ApprovedSnapshotWorkingCalendar:
    session.expire_all()
    return (
        session.execute(
            sa.select(ApprovedSnapshotWorkingCalendar).where(
                ApprovedSnapshotWorkingCalendar.scenario_id == scenario_id
            )
        )
        .scalars()
        .one()
    )


# --- K-16: the snapshot carries values, and the source cannot move them --------------------------


def test_k_16_an_approved_scenario_keeps_the_hours_it_was_approved_with_after_the_calendar_changes(
    client: TestClient, db_session: Session
) -> None:
    """K-16 — AC-04/AC-10 for the working calendar, stated as a behaviour.

    After the approval, the source calendar is changed in **every** way that matters: its name, its
    standard working day, its week pattern and one of its exceptional days. Not one of those changes
    reaches the snapshot, because the snapshot holds values and not a reference.

    The contrast the criterion asks for is the last block: a **draft** scenario in the same
    database, reading the same calendar, whose derived capacity *does* move when the calendar
    changes. That is the whole point of the snapshot existing — a draft follows the organisation and
    an approved version does not — and without it this test would be satisfied by a system in which
    nothing ever changes.

    The mutation "the snapshot carries an identifier and the value is resolved at read time" makes
    the four assertions in the middle read the *new* values and fail.
    """
    project, scenario, calendar, _ = _approvable_scenario(db_session)
    # A second scenario in the same project, reading the same calendar, left as a draft.
    second = make_scenario(db_session, project, name="Variant")
    second_position = make_staffing_position(
        db_session,
        second,
        make_dimension_tuple(db_session, suffix=" (variant)", calendar=calendar),
        headcount=1,
        start_date=MARCH,
    )
    make_allocation(db_session, second_position, period_month=MARCH)

    approved = client.post(
        approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["snapshot"]["working_calendars"] == 1
    assert approved.json()["snapshot"]["working_calendar_days"] == 2

    frozen_before = _snapshot_calendar(db_session, scenario.id)
    assert frozen_before.name == "Poland 7.5h"
    assert frozen_before.standard_hours_per_day == Decimal("7.50")
    assert frozen_before.week_pattern == "1111100"

    draft_before = client.get(
        f"/projects/{project.id}/scenarios/{second.id}/staffing-positions",
        headers=as_caller(IN_SCOPE_USER),
    ).json()["positions"][0]["allocations"][0]["derived_capacity_hours"]

    # Everything about the source calendar changes.
    db_session.execute(
        sa.update(WorkingCalendar)
        .where(WorkingCalendar.id == calendar.id)
        .values(
            name="Poland 6h",
            standard_hours_per_day=Decimal("6.00"),
            week_pattern="1111110",
        )
    )
    db_session.execute(
        sa.text("UPDATE working_calendar_day SET kind = 'working' WHERE calendar_id = :id"),
        {"id": calendar.id},
    )
    db_session.flush()
    db_session.expire_all()

    frozen_after = _snapshot_calendar(db_session, scenario.id)
    assert frozen_after.id == frozen_before.id
    assert frozen_after.name == "Poland 7.5h", "the snapshot followed the source's rename"
    assert frozen_after.standard_hours_per_day == Decimal("7.50"), (
        "the snapshot followed the source's standard working day — it holds a reference, not a "
        "value (AC-04, AC-10)"
    )
    assert frozen_after.week_pattern == "1111100"
    frozen_days = {
        row.day: row.kind
        for row in db_session.execute(
            sa.select(ApprovedSnapshotWorkingCalendarDay).where(
                ApprovedSnapshotWorkingCalendarDay.scenario_id == scenario.id
            )
        ).scalars()
    }
    assert frozen_days == {
        date(2026, 12, 25): WorkingCalendarDayKind.NON_WORKING,
        date(2026, 12, 12): WorkingCalendarDayKind.WORKING,
    }, "an exceptional day of the frozen calendar followed the source"

    # The contrast: the draft scenario's derived capacity *does* move.
    draft_after = client.get(
        f"/projects/{project.id}/scenarios/{second.id}/staffing-positions",
        headers=as_caller(IN_SCOPE_USER),
    ).json()["positions"][0]["allocations"][0]["derived_capacity_hours"]
    assert draft_after != draft_before, (
        "the draft scenario's capacity did not move when the calendar changed — the contrast of "
        "this test is void and the frozen values above prove nothing"
    )


def test_k_16_no_snapshot_column_is_a_foreign_key_to_the_row_it_copied(
    db_session: Session,
) -> None:
    """K-16's structural half — asked of `pg_constraint`, not of a payload.

    ADR-0004's addendum of 2026-09-22 (point 3b) is explicit: a snapshot is a separate set of rows,
    not a reference to the organisation's current values, and the source's identifier travels as a
    plain `uuid`. A foreign key is a reference — it would let the source block a delete, and it is
    the natural first step towards reading the value "through the join", which is the mutation the
    criterion names.

    Why this cannot be a behavioural test: a snapshot built on foreign keys and read through joins
    produces *identical* behaviour until the source changes, and the test above is the only thing
    that would catch it — one test, one mutation, and no independent confirmation. So this asks the
    schema directly.

    Two assertions, because either alone is satisfiable by the wrong schema: **every** foreign key
    on the three tables points at `scenarios` (a table with none at all would pass a "no FK to
    working_calendar" check while also losing the row's owner), and the three `source_*_id` columns
    really exist (so the tables are not passing by having no source ids at all).
    """
    targets = db_session.execute(
        sa.text(
            "SELECT c.conname, c.conrelid::regclass::text AS on_table,"
            " c.confrelid::regclass::text AS references_table"
            " FROM pg_constraint c"
            " WHERE c.contype = 'f' AND c.conrelid::regclass::text = ANY(:tables)"
        ),
        {"tables": list(SNAPSHOT_TABLES)},
    ).all()

    assert targets, "the snapshot tables have no foreign keys at all — not even to the scenario"
    for name, table, referenced in targets:
        assert referenced == "scenarios", (
            f"{name} on {table} references {referenced}. A snapshot row may reference only the "
            "scenario that owns it; the identifier of the row it copied is a value, never a "
            "foreign key (ADR-0004, addendum 2026-09-22, point 3b)."
        )

    source_columns = set(
        db_session.execute(
            sa.text(
                "SELECT table_name || '.' || column_name FROM information_schema.columns"
                " WHERE table_name = ANY(:tables) AND column_name LIKE 'source_%'"
            ),
            {"tables": list(SNAPSHOT_TABLES)},
        ).scalars()
    )
    assert source_columns == {
        "approved_snapshot_working_calendar.source_calendar_id",
        "approved_snapshot_working_calendar.source_location_id",
        "approved_snapshot_working_calendar_day.source_calendar_id",
        "approved_snapshot_absence_type.source_absence_type_id",
        # SC-3-03's fourth snapshot table. This equality is a canary over `SNAPSHOT_TABLES` and it
        # fired, deliberately, on the day that tuple grew (ADR-0004, addendum 2026-09-22 SC-3-03,
        # point 4: a new snapshot table must come under the same canaries as the three existing
        # ones, because the registry is silent about omissions). The claim is unchanged — these are
        # *values*, and the assertion above still requires every foreign key of every snapshot
        # table to point at `scenarios` and at nothing else.
        "approved_snapshot_absence_budget.source_budget_id",
        "approved_snapshot_absence_budget.source_calendar_id",
        "approved_snapshot_absence_budget.source_engagement_type_id",
        # SC-4-01's sixth snapshot table — the same deliberate canary growth (ADR-0004, addendum
        # 2026-09-23 SC-4-01, point 2a: source ids are values). The assertion above still requires
        # its only foreign key to point at `scenarios`.
        "approved_snapshot_catalog_default_rate.source_rate_id",
        "approved_snapshot_catalog_default_rate.source_role_id",
        "approved_snapshot_catalog_default_rate.source_seniority_id",
        "approved_snapshot_catalog_default_rate.source_location_id",
        "approved_snapshot_catalog_default_rate.source_engagement_type_id",
        "approved_snapshot_catalog_default_rate.source_vendor_id",
    }


def test_no_snapshot_table_carries_a_concurrency_marker(db_session: Session) -> None:
    """ADR-0007's addendum of 2026-09-22 (point 5) — write-once rows need no marker.

    Not an acceptance criterion; a negative decision that nothing behavioural would notice. An
    `updated_at` here would advertise an edit path for rows that must never be edited, and would be
    the first invitation to write one.
    """
    marked = set(
        db_session.execute(
            sa.text(
                "SELECT table_name FROM information_schema.columns"
                " WHERE table_name = ANY(:tables) AND column_name = 'updated_at'"
            ),
            {"tables": list(SNAPSHOT_TABLES)},
        ).scalars()
    )

    assert marked == set(), f"a snapshot table grew a concurrency marker: {sorted(marked)}"


# --- One row per thing frozen, however many positions reach it (R-01/S-01) -----------------------
#
# Not an acceptance criterion, and the gap that let a defect through 403 green tests: every fixture
# in this suite had **one** position in **one** location and **one** absence, so every snapshot
# insert returned one row per table whether it deduplicated or not. The three inserts carried a
# `DISTINCT` with `gen_random_uuid()` in the deduplicated select list — a `VOLATILE` function
# evaluated *before* the `Unique` node, which makes every row unique and the `DISTINCT` a no-op
# (`EXPLAIN SELECT DISTINCT gen_random_uuid(), 1 FROM generate_series(1,5)`: five rows of five).
#
# It matters more than a duplicate row usually does: snapshot rows are written once and never
# updated or deleted, so nothing repairs them afterwards, and `(scenario_id, source_location_id)` /
# `(scenario_id, source_calendar_id)` — the key the reproducible report of plan block 8 is meant to
# read them by — stops being unique.
#
# Two tests, pulling opposite ways: the first says duplicates collapse, the second says distinct
# things do not. Either alone is satisfied by a wrong implementation (no deduplication at all; a
# deduplication over too few columns).


def test_two_positions_in_one_location_freeze_that_calendar_once(
    client: TestClient, db_session: Session
) -> None:
    """Two positions, one location, one calendar, three absences of one type — one row each.

    The multiplicity is on the *source* side of every join the snapshot inserts make: the calendar
    is reached through `positions → location → calendar`, its days through the same path, and the
    absence type through `positions → absences → type`. Two positions therefore reach each of them
    twice over, and three absences reach the type three times.

    Every count below is one, or one per distinct thing — not "one per position that happens to
    point at it". The last assertion is the one that says *why*: the pair a later reader joins
    these two tables by occurs exactly once, so reading the snapshot cannot return a value twice.
    """
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    make_calendar_day(
        db_session, calendar, day=date(2026, 12, 25), kind=WorkingCalendarDayKind.NON_WORKING
    )
    make_calendar_day(
        db_session, calendar, day=date(2026, 12, 12), kind=WorkingCalendarDayKind.WORKING
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    holiday = make_absence_type(db_session, name="Paid holiday")
    first = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=MARCH
    )
    second = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=date(2026, 4, 1)
    )
    make_allocation(db_session, first, period_month=MARCH)
    make_allocation(db_session, second, period_month=date(2026, 4, 1))
    make_absence(
        db_session, first, holiday, start_date=date(2026, 3, 2), end_date=date(2026, 3, 6)
    )
    make_absence(
        db_session, first, holiday, start_date=date(2026, 3, 9), end_date=date(2026, 3, 13)
    )
    make_absence(
        db_session, second, holiday, start_date=date(2026, 4, 6), end_date=date(2026, 4, 10)
    )

    response = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    assert response.json()["snapshot"] == {
        "working_calendars": 1,
        "working_calendar_days": 2,
        "absence_types": 1,
        # SC-3-03's fourth counter. Zero because this fixture has no budget row at all — the
        # deduplication of budgets has its own test, next to this one.
        "absence_budgets": 0,
        # The fifth counter, SC-1-10 (ADR-0012, point 6). A deliberate canary growth, like the
        # fourth: zero because this fixture configures no organisation defaults row (gate 1, P-D:
        # no migration seeds one). The proof that the row *is* frozen when it exists is K-05, in
        # `tests/test_assumption_approval.py`.
        "organization_defaults": 0,
        # The sixth counter, SC-4-01 (ADR-0004, addendum 2026-09-23 SC-4-01, point 2d): a
        # deliberate canary growth. Zero because this fixture has no catalogue rate; the
        # proof that the windows read *are* frozen is K-08 in `test_commercial_revenue.py`.
        "catalog_default_rates": 0,
    }, (
        "the snapshot holds one row per position that reaches a thing instead of one row per "
        "thing. These rows are never updated or deleted, so the duplicates are permanent."
    )

    db_session.expire_all()
    frozen = _snapshot_calendar(db_session, scenario.id)  # `.one()` — two rows raise here
    assert frozen.source_location_id == dimensions.location_id
    assert frozen.source_calendar_id == calendar.id
    frozen_days = sorted(
        (row.day, row.kind)
        for row in db_session.execute(
            sa.select(ApprovedSnapshotWorkingCalendarDay).where(
                ApprovedSnapshotWorkingCalendarDay.scenario_id == scenario.id
            )
        ).scalars()
    )
    assert frozen_days == [
        (date(2026, 12, 12), WorkingCalendarDayKind.WORKING),
        (date(2026, 12, 25), WorkingCalendarDayKind.NON_WORKING),
    ]
    frozen_type = (
        db_session.execute(
            sa.select(ApprovedSnapshotAbsenceType).where(
                ApprovedSnapshotAbsenceType.scenario_id == scenario.id
            )
        )
        .scalars()
        .one()
    )
    assert frozen_type.source_absence_type_id == holiday.id

    # The read key of plan block 8, asked of the rows themselves: one row per pair, not one per
    # position that reaches it.
    duplicated_keys = db_session.execute(
        sa.text(
            "SELECT count(*) FROM ("
            " SELECT scenario_id, source_location_id FROM approved_snapshot_working_calendar"
            " WHERE scenario_id = :id GROUP BY 1, 2 HAVING count(*) > 1) AS repeated"
        ),
        {"id": scenario.id},
    ).scalar_one()
    assert duplicated_keys == 0, (
        "(scenario_id, source_location_id) is not unique in the snapshot — the key a later reader "
        "joins the frozen calendar to its days by would multiply every row it returns"
    )


def test_two_locations_sharing_one_calendar_freeze_two_rows_and_one_set_of_days(
    client: TestClient, db_session: Session
) -> None:
    """The other direction: deduplication must not collapse things that genuinely differ.

    Two locations pointing at the **same** calendar are two snapshot calendar rows, because
    `source_location_id` differs and it is what says which position reads which (see
    `ApprovedSnapshotWorkingCalendar.source_location_id`). The calendar's exceptional days, keyed by
    `source_calendar_id` alone, stay one set — one calendar, one set of days, reached twice.

    Without this test the fix for the duplicate rows above could be a `DISTINCT ON (scenario_id)` or
    a deduplication over the calendar alone, and the counts here would drop to one and one. Two
    absence types for the same reason: three absences of one type are one row, one absence each of
    two types is two.
    """
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    make_calendar_day(
        db_session, calendar, day=date(2026, 12, 25), kind=WorkingCalendarDayKind.NON_WORKING
    )
    make_calendar_day(
        db_session, calendar, day=date(2026, 12, 12), kind=WorkingCalendarDayKind.WORKING
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    here = make_dimension_tuple(db_session, calendar=calendar)
    there = make_dimension_tuple(db_session, suffix=" (Kraków)", calendar=calendar)
    for dimensions, absence_type in (
        (here, make_absence_type(db_session, name="Paid holiday")),
        (there, make_absence_type(db_session, name="Sick leave", generates_cost=False)),
    ):
        position = make_staffing_position(
            db_session, scenario, dimensions, headcount=1, start_date=MARCH
        )
        make_allocation(db_session, position, period_month=MARCH)
        make_absence(
            db_session,
            position,
            absence_type,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 6),
        )

    response = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    assert response.json()["snapshot"] == {
        "working_calendars": 2,
        "working_calendar_days": 2,
        "absence_types": 2,
        # SC-3-03's fourth counter; this fixture carries no budget row.
        "absence_budgets": 0,
        # The fifth counter, SC-1-10 (ADR-0012, point 6). A deliberate canary growth, like the
        # fourth: zero because this fixture configures no organisation defaults row (gate 1, P-D:
        # no migration seeds one). The proof that the row *is* frozen when it exists is K-05, in
        # `tests/test_assumption_approval.py`.
        "organization_defaults": 0,
        # The sixth counter, SC-4-01 (ADR-0004, addendum 2026-09-23 SC-4-01, point 2d): a
        # deliberate canary growth. Zero because this fixture has no catalogue rate; the
        # proof that the windows read *are* frozen is K-08 in `test_commercial_revenue.py`.
        "catalog_default_rates": 0,
    }, (
        "the deduplication collapsed rows that differ: two locations are two calendar rows (they "
        "carry different source_location_id) and two absence types are two rows"
    )

    db_session.expire_all()
    frozen_locations = set(
        db_session.execute(
            sa.select(ApprovedSnapshotWorkingCalendar.source_location_id).where(
                ApprovedSnapshotWorkingCalendar.scenario_id == scenario.id
            )
        ).scalars()
    )
    assert frozen_locations == {here.location_id, there.location_id}


# --- K-17: the scenario's own rows stay out of the snapshot --------------------------------------


def test_k_17_approval_writes_no_snapshot_of_the_scenarios_own_absences(
    client: TestClient, db_session: Session
) -> None:
    """K-17 — the absence **type** is frozen; the absence **instances** are not.

    The criterion of ADR-0004's addendum of 2026-09-19 is the direction of inheritance, not the
    participation in a calculation: a value that can change *outside* the scenario is snapshotted, a
    value the scenario owns is protected by the refusal of a write. The absence type is
    organisational — somebody can rename it or flip its flags tomorrow — so it is frozen. The
    instances are the scenario's own, so freezing them would be a second copy of rows nothing can
    change, and the first place two copies could disagree.

    Asserted as the **absence of a table**, not merely as an empty one: there is no snapshot table
    for absence instances, and the three that exist are exactly the ones `SNAPSHOT_TABLES` names.
    The mutation the criterion names — a fourth table added for the instances — fails here the day
    it is created, which is the only moment at which removing it is cheap.

    The contrast is in the same test: the same approval writes a non-zero number of rows into each
    of the three tables that *do* exist, so this is not satisfied by an approval that snapshots
    nothing.
    """
    project, scenario, _, _ = _approvable_scenario(db_session)

    response = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    counts = response.json()["snapshot"]
    assert counts == {
        "working_calendars": 1,
        "working_calendar_days": 2,
        "absence_types": 1,
        # SC-3-03's fourth counter; this fixture carries no budget row, and the criterion that
        # proves a budget *is* frozen is K-07, below.
        "absence_budgets": 0,
        # The fifth counter, SC-1-10 (ADR-0012, point 6). A deliberate canary growth, like the
        # fourth: zero because this fixture configures no organisation defaults row (gate 1, P-D:
        # no migration seeds one). The proof that the row *is* frozen when it exists is K-05, in
        # `tests/test_assumption_approval.py`.
        "organization_defaults": 0,
        # The sixth counter, SC-4-01 (ADR-0004, addendum 2026-09-23 SC-4-01, point 2d): a
        # deliberate canary growth. Zero because this fixture has no catalogue rate; the
        # proof that the windows read *are* frozen is K-08 in `test_commercial_revenue.py`.
        "catalog_default_rates": 0,
    }, "the contrast is void: this approval snapshotted nothing"

    # The absence type is in.
    frozen_type = (
        db_session.execute(
            sa.select(ApprovedSnapshotAbsenceType).where(
                ApprovedSnapshotAbsenceType.scenario_id == scenario.id
            )
        )
        .scalars()
        .one()
    )
    assert frozen_type.name == "Paid holiday"
    assert (frozen_type.generates_cost, frozen_type.generates_revenue) == (True, False)

    # The instances are out — there is no table for them.
    snapshot_tables = set(
        db_session.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'public' AND table_name LIKE 'approved_snapshot_%'"
            )
        ).scalars()
    )
    assert snapshot_tables == set(SNAPSHOT_TABLES), (
        "the set of snapshot tables changed. The scenario's own absence instances must not be "
        "snapshotted (ADR-0004, addendum 2026-09-22, point 1): nothing outside the scenario can "
        f"change them. Unexpected: {sorted(snapshot_tables - set(SNAPSHOT_TABLES))}"
    )
    # The same claim, stated about the table it is about rather than by a name heuristic. The
    # heuristic that stood here until SC-3-03 ("no snapshot table whose name says absence and does
    # not say type") was a proxy, and the fourth snapshot table — the leave *budget*, which ADR-0004
    # requires (addendum 2026-09-22 SC-3-03, point 2) — falls under it while being exactly what the
    # criterion wants frozen. What K-17 is about is the absence **instance** table, and that is what
    # is named here: no snapshot table may be a copy of `staffing_position_absence`.
    assert not any("position_absence" in table for table in snapshot_tables), (
        "a snapshot table for the scenario's own absence instances exists. Nothing outside the "
        "scenario can change them, so there is nothing to freeze; they are protected by the "
        "refusal of a write (ADR-0004, addendum 2026-09-22, point 1)."
    )


def _every_snapshot_row_of(session: Session, scenario_id: uuid.UUID) -> int:
    """Rows belonging to one scenario across **every** `approved_snapshot_*` table in the database.

    Deliberately *not* `tests.conftest.count_snapshot_rows`, which sums over `SNAPSHOT_MODELS` — a
    list in the production code. A fifth snapshot table arriving together with its entry in that
    list is invisible to a counter that reads the list; this one asks `information_schema` and
    therefore counts tables nobody told it about.
    """
    tables = sorted(
        session.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'public' AND table_name LIKE 'approved\\_snapshot\\_%'"
            )
        ).scalars()
    )
    assert tables, "no approved_snapshot_* table exists — this counter would count nothing"
    return sum(
        session.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE scenario_id = :scenario"),  # noqa: S608
            {"scenario": scenario_id},
        ).scalar_one()
        for table in tables
    )


def test_k_17_the_size_of_a_snapshot_does_not_grow_with_the_number_of_absence_instances(
    client: TestClient, db_session: Session
) -> None:
    """K-17 stated as a quantity rather than as a table name — the claim, not a proxy for it.

    Two scenarios in one project, identical in everything the snapshot copies (one location, one
    calendar with the same two exceptional days, one position, one absence **type**) and differing
    in one element only: the first books **one** absence of that type, the second books **four**.
    Both are approved, and both snapshots have to be the same size.

    Why this test exists beside the two assertions above. The one that names
    `staffing_position_absence` is a name heuristic, and it was narrowed in SC-3-03 (the previous
    spelling, "no snapshot table whose name says absence and does not say type", caught the new
    budget table as a false positive). The narrowed form is satisfied by a fifth snapshot table
    holding the instances under any other name — `approved_snapshot_absence_instance`, say — and the
    equality against `SNAPSHOT_TABLES` does not catch it either, because the tuple is production
    code and grows in the same commit as the table it names. A count taken over whatever
    `approved_snapshot_*` tables the migrated database actually holds depends on neither.

    **The contrast is in the same test and it pulls the other way**: a third scenario, identical
    again but booking its one absence against a **second** absence type, has a snapshot one row
    *larger*. So this is not satisfied by an approval that freezes nothing, nor by a counter that
    always answers the same number — the snapshot grows with the organisational rows the scenario
    reads, and only with those.
    """
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    make_calendar_day(
        db_session, calendar, day=date(2026, 12, 25), kind=WorkingCalendarDayKind.NON_WORKING
    )
    make_calendar_day(
        db_session, calendar, day=date(2026, 12, 12), kind=WorkingCalendarDayKind.WORKING
    )
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    holiday = make_absence_type(db_session, name="Paid holiday")
    sick = make_absence_type(db_session, name="Sick leave", generates_cost=False)

    # Monday to Friday of four separate March weeks, so the four absences of the second scenario are
    # four distinct rows rather than one long one.
    weeks = [(date(2026, 3, 2), date(2026, 3, 6)), (date(2026, 3, 9), date(2026, 3, 13)),
             (date(2026, 3, 16), date(2026, 3, 20)), (date(2026, 3, 23), date(2026, 3, 27))]

    def scenario_booking(name: str, bookings: list[tuple[date, date, AbsenceType]]):
        scenario = make_scenario(db_session, project, name=name)
        position = make_staffing_position(
            db_session, scenario, dimensions, headcount=1, start_date=MARCH
        )
        make_allocation(db_session, position, period_month=MARCH)
        for start, end, absence_type in bookings:
            make_absence(db_session, position, absence_type, start_date=start, end_date=end)
        # The approval resolves the scenario through `Project.scenarios`, which the previous
        # approval in this loop already loaded into the identity map — without this the second
        # scenario is invisible to it and the request answers `404`.
        db_session.expire_all()
        response = client.post(
            approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
        assert response.status_code == 200, response.text
        return scenario

    one_absence = scenario_booking("One absence", [(*weeks[0], holiday)])
    four_absences = scenario_booking(
        "Four absences", [(*week, holiday) for week in weeks]
    )
    two_types = scenario_booking(
        "Two types", [(*weeks[0], holiday), (*weeks[1], sick)]
    )

    small = _every_snapshot_row_of(db_session, one_absence.id)
    large = _every_snapshot_row_of(db_session, four_absences.id)
    contrasted = _every_snapshot_row_of(db_session, two_types.id)

    assert small > 0, (
        "the contrast is void: approving this scenario froze nothing at all, so 'the snapshot does "
        "not grow with the absences' is true for the wrong reason"
    )
    assert large == small, (
        f"four booked absences produced {large} snapshot rows where one produced {small}. The "
        "scenario's own absence instances are being copied into the snapshot — nothing outside the "
        "scenario can change them, so there is nothing to freeze, and they are protected by the "
        "refusal of a write instead (ADR-0004, addendum 2026-09-22, point 1)."
    )
    assert contrasted == small + 1, (
        f"a second absence *type* did not add exactly one snapshot row ({contrasted} against "
        f"{small}). The dictionary entry is organisational and must be frozen; this assertion is "
        "what keeps the equality above from being satisfied by a snapshot that ignores absences "
        "altogether."
    )


def test_k_17_a_copy_of_an_approved_scenario_holds_no_snapshot_row(
    client: TestClient, db_session: Session
) -> None:
    """K-17's canary — a copy is a `draft` that has not been through an approval.

    ADR-0004's addendum of 2026-09-18 (point 3) makes this a *requirement* rather than a
    permission: the snapshot belongs to the approval, the approval is a one-way step performed by a
    person, and the copy has not performed it. The named consequence, stated in that addendum and
    worth repeating here: **a copy of an approved calculation is not reproducible the way its source
    is** — it regains reproducibility only at its own approval.

    The registry is silent about omissions, which is why the criterion asks for a canary rather than
    trusting `SCENARIO_CHILD_COPIERS` to stay short. The mutation — `copy_scenario` growing a pass
    that copies snapshot rows — makes the copy's count non-zero and fails here.

    The contrast is the first assertion: the *source* holds four snapshot rows throughout, before
    and after the copy, so this is not satisfied by a database in which no snapshot exists at all.
    """
    project, scenario, _, _ = _approvable_scenario(db_session)
    assert (
        client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
        .status_code
        == 200
    )
    assert count_snapshot_rows(db_session, scenario.id) == 4

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    copy_project_id = uuid.UUID(response.json()["id"])
    db_session.expire_all()
    copy_scenario_id = db_session.execute(
        sa.select(Scenario.id).where(Scenario.project_id == copy_project_id)
    ).scalar_one()

    assert count_snapshot_rows(db_session, copy_scenario_id) == 0, (
        "a copy of an approved scenario carries snapshot rows. The snapshot belongs to the "
        "approval, and the copy has not been through one (ADR-0004, addendum 2026-09-18, point 3)."
    )
    assert count_snapshot_rows(db_session, scenario.id) == 4, "copying moved the source's snapshot"


# --- SC-3-03, K-07: the leave budget is frozen with the calendars, raw ---------------------------
#
# Three carriers, because the claim has three independent halves and no single test can hold them:
# the values are frozen and the source can no longer move them (behaviour), no column of the frozen
# row is a reference to the row it copied (schema), and no column of it holds a computed figure
# (schema again — a prorated column would be invisible to any test that only reads values back).


BUDGET_WINDOW = (date(2026, 1, 1), date(2026, 12, 31))
"""The window every K-07 fixture below plans inside — a literal year, and no clock anywhere.

Until reviewer R-03 these fixtures had to be built around "today", because the approval resolved
which window to freeze with `valid_period @> CURRENT_DATE`. It now resolves by the months the
scenario actually plans, so the fixture names its own months and the whole file is independent of
the day it runs on — which is both a stronger test and the visible half of taking the clock out of
the approval path (R-06)."""


def _budgeted_scenario(session: Session):
    """A project in scope, a draft scenario, two positions on one (calendar, engagement type) pair,
    a flagged absence type and one budget row covering the months those positions plan.

    Two positions on **one** pair on purpose: the deduplication of the budget copy has to collapse
    them into a single frozen row, and a fixture with one position cannot tell a working `DISTINCT`
    from a missing one — which is exactly how the same defect reached `main` in SC-3-02 through 403
    green tests. Each of them plans **two** months of the same window for the same reason one level
    down: the copy now joins through `staffing_position_allocation`, so a one-month fixture could
    not tell "one row per window the plan touches" from "one row per month planned".
    """
    calendar = make_working_calendar(
        session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    dimensions = make_dimension_tuple(session, calendar=calendar)
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline")
    budget = make_absence_budget(
        session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=BUDGET_WINDOW[0],
        effective_to=BUDGET_WINDOW[1],
    )
    statutory = make_absence_type(
        session,
        name=STATUTORY_LEAVE_TYPE_NAME,
        generates_cost=True,
        generates_revenue=False,
        is_statutory_leave=True,
    )
    for _ in range(2):
        position = make_staffing_position(
            session, scenario, dimensions, headcount=1, start_date=MARCH
        )
        make_allocation(session, position, period_month=MARCH)
        make_allocation(session, position, period_month=date(2026, 4, 1))
        make_absence(
            session,
            position,
            statutory,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 2),
        )
    return project, scenario, calendar, dimensions, budget, statutory


def _frozen_budget(session: Session, scenario_id: uuid.UUID) -> ApprovedSnapshotAbsenceBudget:
    session.expire_all()
    return (
        session.execute(
            sa.select(ApprovedSnapshotAbsenceBudget).where(
                ApprovedSnapshotAbsenceBudget.scenario_id == scenario_id
            )
        )
        .scalars()
        .one()  # `.one()` — two rows for one pair raise here
    )


def test_k_07_approving_a_scenario_freezes_the_absence_budget_of_every_frozen_calendar_as_values(
    client: TestClient, db_session: Session
) -> None:
    """K-07 — the budget is frozen with the calendar, and the source can no longer move it.

    The approval reports a **non-zero** budget count next to the calendar counts: the pair
    "calendar + budget" is atomic in the snapshot, and an approval that froze one and not the other
    would reproduce a different billable capacity than the one approved — working days from the
    frozen calendar, leave days from the live table (ADR-0004, addendum 2026-09-22 SC-3-03,
    point 2).

    Then **every** source field is changed — the days, the window, the source text, and the flag
    that says which absence type the budget settles against — and not one value in the snapshot
    moves. The flag is included because it is the one that makes a formally complete snapshot wrong:
    moving `is_statutory_leave` to another type changes what the frozen budget is compared against
    (point 8).

    **The contrast is the draft scenario in the same database**, reading the same budget: its
    derived capacity *does* move under the same edit. Without it this test would be satisfied by a
    system in which the budget never reaches a calculation at all.

    Two positions share the pair and each plans two months of it, and the frozen row is fetched with
    `.one()`: the budget is deduplicated over the values it copies, so four (position, month) pairs
    reading one window freeze **one** row and the key a later reader looks it up by —
    `(scenario_id, source_calendar_id, source_engagement_type_id, effective_from)` — stays unique.
    That is the SC-3-02 defect (`gen_random_uuid()` inside a `SELECT DISTINCT`) asserted for the
    fourth table rather than assumed not to have been repeated, and since reviewer R-03 it is also
    the assertion that the month-driven join does not multiply rows by months.
    """
    project, scenario, calendar, dimensions, budget, statutory = _budgeted_scenario(db_session)
    draft = make_scenario(db_session, project, name="Variant")
    draft_position = make_staffing_position(
        db_session, draft, dimensions, headcount=1, start_date=budget.effective_from
    )
    make_allocation(db_session, draft_position, period_month=budget.effective_from)

    def draft_capacity() -> str:
        payload = client.get(
            staffing_path(project.id, draft.id), headers=as_caller(IN_SCOPE_USER)
        ).json()
        return payload["positions"][0]["allocations"][0]["derived_capacity_hours"]

    before = draft_capacity()

    approved = client.post(
        approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert approved.status_code == 200, approved.text
    counts = approved.json()["snapshot"]
    assert counts["absence_budgets"] == 1, (
        "the approval froze no budget although the scenario's positions read one — the calendar "
        f"was frozen without it: {counts}"
    )
    assert counts["working_calendars"] == 1, "the contrast is void: no calendar was frozen either"

    # Captured before the edit below: `budget` is an ORM row, and the `UPDATE` plus `expire_all()`
    # that follow make its attributes read the *new* values. Comparing the snapshot against it
    # afterwards would compare the frozen row with the source it is supposed to have stopped
    # following — a test that could only pass if the freezing had failed.
    original_window = (budget.effective_from, budget.effective_to)

    frozen = _frozen_budget(db_session, scenario.id)
    assert frozen.budget_days == Decimal("26.00")
    assert frozen.unit == "day"
    assert frozen.source == BUDGET_SOURCE
    assert (frozen.effective_from, frozen.effective_to) == original_window
    assert frozen.source_budget_id == budget.id
    assert frozen.source_calendar_id == calendar.id
    assert frozen.source_engagement_type_id == dimensions.engagement_type_id
    frozen_type = (
        db_session.execute(
            sa.select(ApprovedSnapshotAbsenceType).where(
                ApprovedSnapshotAbsenceType.scenario_id == scenario.id
            )
        )
        .scalars()
        .one()
    )
    assert frozen_type.is_statutory_leave is True, (
        "the frozen absence type does not carry is_statutory_leave — the snapshot cannot say what "
        "the frozen budget was settled against (ADR-0004, addendum 2026-09-22 SC-3-03, point 8)"
    )

    # Everything about the source changes: the figure, the window, the text, and the flag.
    moved_window = (
        date(original_window[0].year + 5, 1, 1),
        date(original_window[0].year + 5, 12, 31),
    )
    db_session.execute(
        sa.update(AbsenceBudget)
        .where(AbsenceBudget.id == budget.id)
        .values(
            budget_days=Decimal("10.00"),
            source="A different regulation entirely",
            effective_from=moved_window[0],
            effective_to=moved_window[1],
        )
    )
    other_type = make_absence_type(db_session, name="Unpaid leave", generates_cost=False)
    db_session.execute(
        sa.update(AbsenceType)
        .where(AbsenceType.id == statutory.id)
        .values(is_statutory_leave=False, generates_cost=False, generates_revenue=True)
    )
    db_session.execute(
        sa.update(AbsenceType)
        .where(AbsenceType.id == other_type.id)
        .values(is_statutory_leave=True)
    )
    db_session.flush()
    db_session.expire_all()

    after = _frozen_budget(db_session, scenario.id)
    assert after.id == frozen.id
    assert after.budget_days == Decimal("26.00"), (
        "the frozen budget followed the source's figure — it holds a reference, not a value "
        "(AC-04, AC-10)"
    )
    assert after.source == BUDGET_SOURCE
    assert (after.effective_from, after.effective_to) == original_window
    db_session.expire_all()
    frozen_type_after = (
        db_session.execute(
            sa.select(ApprovedSnapshotAbsenceType).where(
                ApprovedSnapshotAbsenceType.scenario_id == scenario.id
            )
        )
        .scalars()
        .one()
    )
    assert (
        frozen_type_after.is_statutory_leave,
        frozen_type_after.generates_cost,
        frozen_type_after.generates_revenue,
    ) == (True, True, False), "the frozen absence type followed the source's flags"

    assert draft_capacity() != before, (
        "the draft scenario's capacity did not move when the budget changed — the contrast of this "
        "test is void and the frozen values above prove nothing"
    )


def test_k_07_no_snapshot_budget_column_is_a_foreign_key_to_the_row_it_copied(
    db_session: Session,
) -> None:
    """K-07's structural half — asked of `pg_constraint`, not of a payload.

    A snapshot built on foreign keys and read through joins behaves *identically* until the source
    changes, so the behavioural test above is the only thing that would catch it — one test, one
    mutation, no independent confirmation. This asks the schema directly: the frozen budget's only
    foreign key points at `scenarios`, and its three `source_*_id` columns exist as plain `uuid`
    values.

    Both assertions are needed: a table with no foreign keys at all would pass "nothing references
    `absence_budget`" while also losing the row's owner, and a table with no `source_*` columns
    would pass it by having nothing to reference.
    """
    targets = db_session.execute(
        sa.text(
            "SELECT c.conname, c.confrelid::regclass::text AS references_table"
            " FROM pg_constraint c"
            " WHERE c.contype = 'f'"
            " AND c.conrelid::regclass::text = 'approved_snapshot_absence_budget'"
        )
    ).all()

    assert targets, "the frozen budget has no foreign key at all — not even to its scenario"
    for name, referenced in targets:
        assert referenced == "scenarios", (
            f"{name} references {referenced}. A snapshot row may reference only the scenario that "
            "owns it; the identifiers of the rows it copied are values, never foreign keys "
            "(ADR-0004, addendum 2026-09-22 SC-3-02, point 3b)."
        )

    source_columns = set(
        db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'approved_snapshot_absence_budget'"
                " AND column_name LIKE 'source_%'"
            )
        ).scalars()
    )
    assert source_columns == {
        "source_budget_id",
        "source_calendar_id",
        "source_engagement_type_id",
    }, source_columns


def test_k_07_the_snapshot_holds_the_budget_in_days_and_no_prorated_figure(
    db_session: Session,
) -> None:
    """K-07's third carrier — asserted over the **set of columns**, not over behaviour.

    ADR-0004's addendum (2026-09-22 SC-3-03, point 7) freezes the *raw* budget and leaves the
    proration to the reader: a column holding "hours removed per month" would be a computed figure
    inside a record of what was approved, and it would be silently wrong the moment the reader's
    month grid differed from the one that produced it.

    A behavioural test cannot see such a column: it would simply be present and consistent with
    everything else until the day it was not. So the column set is asserted by **equality** — a
    `prorated_hours`, `monthly_share`, `days_per_month` or anything else added later fails here on
    the day it is written, which is the only moment at which removing it is cheap.

    **There is no `resolved_on` column, and its absence is asserted here too** (reviewer R-03/R-06).
    Point 7a required one while point 7 froze a single window chosen by the approval's own date: a
    frozen number that did not say which day had picked it could not be attributed to a window. With
    the windows resolved by the months the scenario plans, every frozen row carries its own window
    and the attribution is in the data — so a date column would record a fact about a clock that no
    reader needs, and it would put the clock back on a path that no longer has one.
    """
    columns = set(
        db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'approved_snapshot_absence_budget'"
            )
        ).scalars()
    )

    assert columns == {
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
        "created_at",
    }, (
        "the columns of the frozen budget changed. It holds the raw budget and nothing else — "
        "days, unit, window, source — never a prorated or otherwise computed figure, and never a "
        "date produced by a clock (ADR-0004, addendum 2026-09-22 SC-3-03, point 7, as corrected "
        f"by reviewer R-03). Unexpected: {sorted(columns)}"
    )


def test_k_07_a_scenario_whose_location_has_no_calendar_freezes_neither_a_calendar_nor_a_budget(
    client: TestClient, db_session: Session
) -> None:
    """K-07's second contrast: no calendar, no budget — and the approval still succeeds.

    The budget hangs on the calendar (ADR-0008, addendum 2026-09-22 SC-3-03, point 3a), so a
    position in a location with `calendar_id IS NULL` reaches neither. The approval is a `200` with
    two zero counts, not an error and not a partially frozen snapshot: an incomplete catalogue is a
    state to fill in, not a failure of the approval.

    It is also the assertion that keeps the budget copy from being written as "every budget row of
    the organisation": with a budget row in the database that this scenario does not read, the count
    stays zero.

    **And it is what defends "the flagged type is copied only when a planned location has a
    calendar"** (invariant-guardian, third round, for the then budget condition; the condition is
    now the calendar — ADR-0004, aneks 2026-09-23 SC-5-06, point 5). The catalogue below therefore
    names a statutory type: the scenario has a position and an allocation row, so a copy
    conditioned on *allocation rows* rather than on *a calendar behind their location* would put
    that type into the snapshot, and the "zero snapshot rows" assertion catches it — the mutation
    "drop the join to `working_calendar` from the statutory branch of `_copy_absence_types`".
    `test_k_07_m_3_…` below carries the same contrast next to its positive half.
    """
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    budgeted = make_dimension_tuple(db_session, suffix=" (with calendar)", calendar=calendar)
    make_absence_budget(
        db_session,
        calendar,
        budgeted.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=BUDGET_WINDOW[0],
        effective_to=BUDGET_WINDOW[1],
    )
    # Named in the catalogue, and read by nothing this scenario plans — see the docstring.
    make_absence_type(
        db_session, name=STATUTORY_LEAVE_TYPE_NAME, is_statutory_leave=True
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Remote only")
    unattached = make_dimension_tuple(db_session, suffix=" (remote)")
    position = make_staffing_position(
        db_session, scenario, unattached, headcount=1, start_date=MARCH
    )
    make_allocation(db_session, position, period_month=MARCH)

    response = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    assert response.json()["snapshot"]["working_calendars"] == 0
    assert response.json()["snapshot"]["absence_budgets"] == 0, (
        "a budget nothing in this scenario reads was frozen — the copy is scoped to the whole "
        "table instead of to what the calculation reads (ADR-0004, addendum SC-3-03, point 3)"
    )
    assert count_snapshot_rows(db_session, scenario.id) == 0, (
        "this approval froze something although its position has no calendar, and therefore no "
        "budget and no month for the statutory type to qualify. The likely row is that type: it is "
        "copied when a planned location has a calendar, not whenever the scenario has a position."
    )


# --- R-03: the frozen window is the one the scenario plans in, not the one covering today --------


def _year_window(year: int) -> tuple[date, date]:
    """A whole calendar year as a month-aligned budget window."""
    return date(year, 1, 1), date(year, 12, 31)


def test_r_03_the_approval_freezes_the_window_the_scenario_plans_in_not_the_one_covering_today(
    client: TestClient, db_session: Session
) -> None:
    """R-03 (reviewer, Medium) — a scenario planned for a later year keeps *its* entitlement.

    The defect this kills is silent and permanent. The approval used to resolve the budget with
    `valid_period @> CURRENT_DATE`, so a scenario whose months all lie in a future year — whose
    budget already exists in the catalogue, and whose figures the live grid is showing on screen at
    the moment somebody approves it — froze the entitlement of **today's** window instead. Nothing
    updates or deletes a snapshot row, so the wrong number would have been the approved one for
    ever.

    The fixture is built around the database's own `CURRENT_DATE` and not around a literal year,
    and that is the only clock read in this file: the *decoy* window has to be the one that contains
    today, whenever the suite happens to run, or the rule under test would not be exercised at all.
    The scenario then plans three years later — far enough that no plausible run date makes the two
    windows the same one.

    Two assertions, and the second is what makes the first a statement about resolution rather than
    about counting: exactly one budget row is frozen, and it is the **36-day** one whose window is
    the planned year. Under the old rule it was the 26-day one, or — on a run after the decoy year —
    none at all.
    """
    today: date = db_session.execute(sa.text("SELECT CURRENT_DATE")).scalar_one()
    decoy_window = _year_window(today.year)
    planned_window = _year_window(today.year + 3)

    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=decoy_window[0],
        effective_to=decoy_window[1],
    )
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("36.00"),
        effective_from=planned_window[0],
        effective_to=planned_window[1],
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Next but one")
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=planned_window[0]
    )
    make_allocation(db_session, position, period_month=date(today.year + 3, 3, 1))

    response = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    assert response.json()["snapshot"]["absence_budgets"] == 1, (
        "the approval froze a number of budget rows other than the one window this scenario's "
        f"months fall in: {response.json()['snapshot']}"
    )
    frozen = _frozen_budget(db_session, scenario.id)
    assert frozen.budget_days == Decimal("36.00"), (
        "the approval froze the entitlement of the window containing the approval *date* rather "
        "than the window this scenario plans in (reviewer R-03). Nothing updates a snapshot row, "
        "so that number would have been the approved one for ever."
    )
    assert (frozen.effective_from, frozen.effective_to) == planned_window


def test_r_03_a_scenario_spanning_two_budget_windows_freezes_both_of_them(
    client: TestClient, db_session: Session
) -> None:
    """R-03's consequence, decided rather than tolerated: more than one window may be frozen.

    A plan that runs across a change of regulation reads two entitlements — one for the months on
    each side — and freezing either alone would misstate every month of the other. So the approval
    freezes **every window the plan's months touch**, and the read key of the snapshot grows the
    window: `(scenario_id, source_calendar_id, source_engagement_type_id, effective_from)`.

    That is ADR-0004's addendum (SC-3-03, point 7a) as it now stands. Its earlier wording said "one
    row per pair, resolved on the day of the approval" and was corrected in review; what is frozen
    here is still narrower than the "every window in the snapshot" variant the addendum rejects —
    only the windows the plan reads — and the price it names (point 7c) is that the reader resolves
    per month.

    The contrast is in the same test: a **third** window, which no month of this scenario plans in,
    is in the catalogue and is **not** frozen. Without it "freeze every window the plan touches"
    would be indistinguishable from "freeze the whole table".
    """
    first, second, unread = (
        _year_window(2026),
        _year_window(2027),
        _year_window(2028),
    )
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    dimensions = make_dimension_tuple(db_session, calendar=calendar)
    for window, days in ((first, "26.00"), (second, "36.00"), (unread, "40.00")):
        make_absence_budget(
            db_session,
            calendar,
            dimensions.engagement_type_id,
            budget_days=Decimal(days),
            effective_from=window[0],
            effective_to=window[1],
        )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Across the change")
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=first[0]
    )
    # Two months on each side of the change, so neither window is frozen by a single month.
    for month in (
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    ):
        make_allocation(db_session, position, period_month=month)

    response = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    assert response.json()["snapshot"]["absence_budgets"] == 2, (
        "a plan spanning two budget windows must freeze both — one of its months would otherwise "
        f"read an entitlement nobody approved: {response.json()['snapshot']}"
    )

    db_session.expire_all()
    frozen = {
        row.effective_from: row.budget_days
        for row in db_session.execute(
            sa.select(ApprovedSnapshotAbsenceBudget).where(
                ApprovedSnapshotAbsenceBudget.scenario_id == scenario.id
            )
        ).scalars()
    }
    assert frozen == {first[0]: Decimal("26.00"), second[0]: Decimal("36.00")}, (
        "the frozen windows are not the two the plan reads — the third window, which no month of "
        f"this scenario plans in, must stay out: {frozen}"
    )


# --- the frozen budget says whether the statutory type was named at all --------------------------


def _frozen_statutory_types(session: Session, scenario_id: uuid.UUID) -> list[str]:
    """The names of the frozen absence types carrying `is_statutory_leave`, for one scenario."""
    session.expire_all()
    return sorted(
        session.execute(
            sa.select(ApprovedSnapshotAbsenceType.name).where(
                ApprovedSnapshotAbsenceType.scenario_id == scenario_id,
                ApprovedSnapshotAbsenceType.is_statutory_leave.is_(True),
            )
        ).scalars()
    )


def _budgeted_scenario_without_bookings(
    session: Session, *, name: str, suffix: str, calendar
):
    """A scenario with one position, one planned month and a budget — and **no** booked absence."""
    dimensions = make_dimension_tuple(session, suffix=suffix, calendar=calendar)
    make_absence_budget(
        session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=BUDGET_WINDOW[0],
        effective_to=BUDGET_WINDOW[1],
    )
    project = make_project(session, name=name, accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline")
    position = make_staffing_position(
        session, scenario, dimensions, headcount=1, start_date=MARCH
    )
    make_allocation(session, position, period_month=MARCH)
    return project, scenario


def test_the_snapshot_distinguishes_a_named_statutory_type_from_none_even_with_nothing_booked(
    client: TestClient, db_session: Session
) -> None:
    """The frozen budget must say **whether it applies at all** — reviewer, second round (High).

    Two situations that a live read tells apart instantly and that the snapshot used to record
    identically:

    - **a type is flagged and this scenario booked nothing against it** — the budget applies whole,
      and the reader has to deduct all 26 days;
    - **no type is flagged anywhere in the catalogue** — the budget applies to nothing, because
      nothing says which booked leave it already covers (`no_statutory_leave_type`), and the reader
      has to deduct none of it.

    Copied only through the scenario's booked absences, both produced one budget row and **zero**
    absence-type rows. A reader of the snapshot could not tell "deduct 26 days a year" from "deduct
    nothing", and the snapshot has no `UPDATE` or `DELETE` path, so the wrong reading would be the
    approved one for ever. The defect was found by comparing two green tests of this repository
    that describe the same case in opposite terms.

    The fix copies the flagged type **unconditionally** — not through the bookings — whenever the
    scenario plans a month in a location with a calendar (since ADR-0004, aneks 2026-09-23 SC-5-06,
    point 5; under SC-3-03 it was "whenever a budget is frozen", and both scenarios here satisfy
    either), so the presence of a row with `is_statutory_leave = true` *is* the fact. Both
    halves are asserted here, in one test, because either alone is satisfied by a wrong
    implementation: "always copy the flagged type" passes the first, "never copy it" passes the
    second.

    The two scenarios live in **separate projects with separate calendars**, because the flag is a
    property of the whole catalogue: one database cannot hold both states at once, and a fixture
    that tried would be asserting something else.
    """
    named_calendar = make_working_calendar(
        db_session, name="Poland 7.5h (named)", standard_hours_per_day=Decimal("7.50")
    )
    make_absence_type(
        db_session, name=STATUTORY_LEAVE_TYPE_NAME, is_statutory_leave=True
    )
    project, scenario = _budgeted_scenario_without_bookings(
        db_session, name="Aurora (named)", suffix=" (named)", calendar=named_calendar
    )

    response = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    assert response.json()["snapshot"]["absence_budgets"] == 1
    assert (
        db_session.execute(
            sa.text("SELECT count(*) FROM staffing_position_absence")
        ).scalar_one()
        == 0
    ), "the premise of this half is that nothing was booked at all"
    assert _frozen_statutory_types(db_session, scenario.id) == [STATUTORY_LEAVE_TYPE_NAME], (
        "the approval froze a budget and no statutory type, although the catalogue named one. The "
        "snapshot then says the same thing as a catalogue that named none — 'deduct the whole "
        "entitlement' and 'deduct nothing' become one record, for ever (there is no UPDATE path)."
    )

    # The other state, in its own project: the flag is a property of the whole catalogue, so it is
    # cleared before the second half rather than set twice.
    db_session.execute(sa.text("UPDATE absence_type SET is_statutory_leave = false"))
    db_session.flush()
    unnamed_calendar = make_working_calendar(
        db_session, name="Poland 7.5h (unnamed)", standard_hours_per_day=Decimal("7.50")
    )
    other_project, other_scenario = _budgeted_scenario_without_bookings(
        db_session, name="Aurora (unnamed)", suffix=" (unnamed)", calendar=unnamed_calendar
    )

    other = client.post(
        approve_path(other_project.id, other_scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert other.status_code == 200, other.text
    assert other.json()["snapshot"]["absence_budgets"] == 1, (
        "the contrast is void: this approval froze no budget either, so the two snapshots would "
        "differ for a reason other than the flag"
    )
    assert _frozen_statutory_types(db_session, other_scenario.id) == [], (
        "a statutory type was frozen although the catalogue named none — the snapshot now claims "
        "an entitlement applies that the live read refuses to apply"
    )


def test_k_07_m_3_the_flagged_type_is_frozen_by_the_location_calendar_not_by_a_frozen_budget(
    client: TestClient, db_session: Session
) -> None:
    """The flagged type follows **the calendar of the planned location**, not the frozen budget.

    Contract S-02 as amended by ADR-0004, aneks 2026-09-23 SC-5-06, point 5 (control M-3): the
    type flagged `is_statutory_leave` is frozen whenever the scenario has an allocation row in a
    location with a calendar — independently of whether a budget is frozen. Until that aneks this
    test asserted the opposite for its first scenario ("no budget frozen → no flagged type"), which
    was the SC-3-03 contract; the paid-absence cost reads the type's `generates_cost` in every
    month with a calendar, so the aneks widened the copy, and this test now encodes the new
    contract rather than the old one.

    Three scenarios in one catalogue, one flagged type:

    1. **calendar, no applicable budget** — both budget decoys sit on the scenario's own calendar
       (its engagement type in a window outside every planned month; the planned month's window for
       an engagement type no position uses). The budget copy freezes neither, and the flagged type
       **is** frozen. Kills the mutation "back to the budget condition" (the SC-3-03 chain).
    2. **no calendar** — a position in a location with `calendar_id IS NULL` in the same catalogue:
       nothing frozen at all, the flagged type included. Kills the mutation "drop the join to
       `working_calendar`", which would copy the type for any allocation row. Without this half the
       test is satisfied by "always copy the flagged type".
    3. **calendar and its own budget** — the flagged type is frozen once, alongside the budget: the
       new condition is a superset of the old one, and the `UNION` does not duplicate it.
    """
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    planned = make_dimension_tuple(db_session, suffix=" (planned)", calendar=calendar)
    unused = make_dimension_tuple(db_session, suffix=" (unused)", calendar=calendar)
    # Decoy 1: the scenario's own pair, a window no planned month falls in.
    make_absence_budget(
        db_session,
        calendar,
        planned.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=date(2027, 1, 1),
        effective_to=date(2027, 12, 31),
    )
    # Decoy 2: the planned month's window, for an engagement type no position of it uses.
    make_absence_budget(
        db_session,
        calendar,
        unused.engagement_type_id,
        budget_days=Decimal("20.00"),
        effective_from=BUDGET_WINDOW[0],
        effective_to=BUDGET_WINDOW[1],
    )
    make_absence_type(db_session, name=STATUTORY_LEAVE_TYPE_NAME, is_statutory_leave=True)
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    no_calendar = make_dimension_tuple(db_session, suffix=" (remote)")

    def approve_a_scenario_planning_march(name: str, dimensions=planned):
        scenario = make_scenario(db_session, project, name=name)
        position = make_staffing_position(
            db_session, scenario, dimensions, headcount=1, start_date=MARCH
        )
        make_allocation(db_session, position, period_month=MARCH)
        response = client.post(
            approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
        assert response.status_code == 200, response.text
        return scenario, response.json()["snapshot"]

    # 1. Calendar, no budget this scenario reads.
    unbudgeted, counts = approve_a_scenario_planning_march("Calendar, no budget")

    assert counts["working_calendars"] == 1, (
        "the premise is void: the calendar was not reached, so this is not the 'calendar without "
        f"a budget' case: {counts}"
    )
    assert counts["absence_budgets"] == 0, (
        f"a budget this scenario does not read was frozen: {counts}"
    )
    assert _frozen_statutory_types(db_session, unbudgeted.id) == [STATUTORY_LEAVE_TYPE_NAME], (
        "the flagged type was not frozen although the scenario plans a month in a location with a "
        "calendar — the statutory branch of _copy_absence_types is still conditioned on a frozen "
        "budget (the SC-3-03 contract), which the paid-absence cost cannot read: it needs the "
        "type's generates_cost in every month with a calendar (ADR-0004, aneks SC-5-06, point 5)"
    )
    assert counts["absence_types"] == 1, counts

    # 2. No calendar in the planned location — the contrast.
    remote, remote_counts = approve_a_scenario_planning_march(
        "No calendar", dimensions=no_calendar
    )

    assert remote_counts["working_calendars"] == 0, remote_counts
    assert _frozen_statutory_types(db_session, remote.id) == [], (
        "the flagged type was frozen for a scenario whose only location has no calendar — the "
        "statutory branch no longer requires a calendar behind the location, so it copies the type "
        "for any allocation row and grows the snapshot with the organisation"
    )
    assert count_snapshot_rows(db_session, remote.id) == 0, remote_counts

    # 3. Calendar and the scenario's own budget.
    make_absence_budget(
        db_session,
        calendar,
        planned.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=BUDGET_WINDOW[0],
        effective_to=BUDGET_WINDOW[1],
    )
    read, read_counts = approve_a_scenario_planning_march("Own budget applies")

    assert read_counts["absence_budgets"] == 1, read_counts
    assert _frozen_statutory_types(db_session, read.id) == [STATUTORY_LEAVE_TYPE_NAME], (
        "a scenario that freezes a budget did not freeze the flagged type exactly once — the "
        "calendar condition must cover every month the budget condition covered"
    )
