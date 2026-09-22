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
"""

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    SNAPSHOT_TABLES,
    ApprovedSnapshotAbsenceType,
    ApprovedSnapshotWorkingCalendar,
    ApprovedSnapshotWorkingCalendarDay,
    Scenario,
    WorkingCalendar,
    WorkingCalendarDayKind,
)
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    count_snapshot_rows,
    make_absence,
    make_absence_type,
    make_allocation,
    make_calendar_day,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
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
    assert not any("absence" in table and "type" not in table for table in snapshot_tables)


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
