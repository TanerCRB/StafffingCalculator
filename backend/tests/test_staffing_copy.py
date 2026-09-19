"""SC-3-01, K-08 — AC-02 proven in full, for the first time.

AC-02 ("staffing is changed in a duplicated scenario → the source scenario remains unchanged") has
been undecidable since SC-1-03, and that file says so in its own module docstring: no child table of
`scenarios` existed, so "no shared mutable reference" was proven for the scenario *row* and for
nothing below it. This file is what closes it, on real rows.

Three claims, and each of them fails differently:

1. The copy carries the same rows on new identifiers — both levels of the aggregate, with the month
   rows attached to the *right* copied position. Two positions × two months with pairwise different
   figures, so a copier that attached every month row to one position, or that copied one position's
   grid twice, is visible.
2. A change on the copy leaves the source untouched — the AC-02 sentence itself.
3. The sharpest contrast available: the source is `approved` (so K-07 refuses the edit) while its
   copy is `draft` (so the same edit is applied). That is the pair of properties ADR-0004 needs —
   an approved calculation is frozen, and a copy is the legal way to keep changing it. A guard that
   also blocked the copy would make the freeze a dead end.

**The two mutations this file has to kill**, one per level of the copying cascade: deleting the
position half of `copy_staffing_positions` and deleting the month-row half. Both are counted rather
than inferred, so neither can hide behind the other.

`test_project_copy.py::test_sc_1_03_02_registered_child_table_copiers_run_once_per_copied_scenario`
is deliberately **not** counted as evidence here: it substitutes the registry with a recording
stand-in, so it stays green with this copier deleted as long as *something* is registered. It proves
the seam is live; the rows are proven here.
"""

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.data.project_writes import SCENARIO_CHILD_COPIERS
from app.data.staffing import (
    ALLOCATION_COLUMNS_NOT_COPIED,
    POSITION_COLUMNS_NOT_COPIED,
    copy_staffing_positions,
)
from app.models import (
    Scenario,
    ScenarioStatus,
    StaffingPosition,
    StaffingPositionAllocation,
)
from tests.conftest import (
    IN_SCOPE_USER,
    allocation_path,
    as_caller,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    staffing_path,
)

MARCH = date(2026, 3, 1)
APRIL = date(2026, 4, 1)

COPIED_POSITION_FIELDS: tuple[str, ...] = (
    "role_id",
    "seniority_id",
    "location_id",
    "engagement_type_id",
    "headcount",
    "start_date",
    "end_date",
)
"""Position attributes the copy must carry over, written out by hand on purpose.

The production code copies by reflection over the mapper; repeating that reflection here would
compare the mechanism with itself and pass whatever it did. The drift guard at the bottom of this
file ties this list to the model, so a new column cannot join `StaffingPosition` without someone
deciding which side it belongs on."""

COPIED_ALLOCATION_FIELDS: tuple[str, ...] = (
    "period_month",
    "availability_hours",
    "planned_allocation_hours",
    "billable_hours",
)


def _source_with_two_positions_and_two_months(
    session: Session, *, status: ScenarioStatus = ScenarioStatus.DRAFT
):
    """A project with one scenario, two positions, two months each — every figure different.

    Sixteen distinct numbers across four rows, which is what makes an aliasing defect in the copier
    visible: with equal figures, a copier that duplicated one month row four times would produce a
    copy that compares equal to the source.
    """
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline", status=status)
    first = make_staffing_position(
        session,
        scenario,
        make_dimension_tuple(session),
        headcount=3,
        start_date=MARCH,
        end_date=date(2026, 6, 30),
    )
    second = make_staffing_position(
        session,
        scenario,
        make_dimension_tuple(session, suffix=" (second)"),
        headcount=1,
        start_date=APRIL,
        end_date=None,
    )
    make_allocation(
        session,
        first,
        period_month=MARCH,
        availability_hours=Decimal("160.00"),
        planned_allocation_hours=Decimal("120.00"),
        billable_hours=Decimal("100.00"),
    )
    make_allocation(
        session,
        first,
        period_month=APRIL,
        availability_hours=Decimal("152.00"),
        planned_allocation_hours=Decimal("140.00"),
        billable_hours=Decimal("130.00"),
    )
    make_allocation(
        session,
        second,
        period_month=MARCH,
        availability_hours=Decimal("80.00"),
        planned_allocation_hours=Decimal("60.00"),
        billable_hours=Decimal("40.00"),
    )
    make_allocation(
        session,
        second,
        period_month=APRIL,
        availability_hours=Decimal("70.00"),
        planned_allocation_hours=Decimal("50.00"),
        billable_hours=Decimal("30.00"),
    )
    return project, scenario, (first, second)


def _grid(session: Session, scenario_id: uuid.UUID) -> dict[date, dict[date, tuple[Decimal, ...]]]:
    """One scenario's staffing as plain values, keyed by the position's start date and the month.

    Keyed by *values* rather than by identifier on purpose: the source and the copy must compare
    equal in this shape while sharing no identifier, and a comparison keyed by id could not express
    that.
    """
    session.expire_all()
    positions = (
        session.execute(
            sa.select(StaffingPosition)
            .where(StaffingPosition.scenario_id == scenario_id)
            .order_by(StaffingPosition.start_date)
        )
        .scalars()
        .all()
    )
    return {
        position.start_date: {
            allocation.period_month: (
                allocation.availability_hours,
                allocation.planned_allocation_hours,
                allocation.billable_hours,
            )
            for allocation in position.allocations
        }
        for position in positions
    }


def _copied_scenario_id(session: Session, copy_project_id: uuid.UUID) -> uuid.UUID:
    session.expire_all()
    return session.execute(
        sa.select(Scenario.id).where(Scenario.project_id == copy_project_id)
    ).scalar_one()


def test_k_08_the_copy_carries_every_position_and_month_row_on_identifiers_of_its_own(
    client: TestClient, db_session: Session
) -> None:
    """K-08, claim 1 — both levels of the aggregate copied, nothing shared, nothing merged.

    Four assertions that fail for four different reasons: the counts (a missing level of the
    cascade), the disjointness of the identifiers (a copy pointing at the source's rows), the
    field-by-field comparison (a copy with reset values), and the grid keyed by value (month rows
    attached to the wrong copied position — the defect the local old-id→new-id mapping exists to
    prevent, and the one a per-table copier could not even avoid).
    """
    source_project, source_scenario, positions = _source_with_two_positions_and_two_months(
        db_session
    )
    source_position_ids = {position.id for position in positions}
    source_allocation_ids = set(
        db_session.execute(sa.select(StaffingPositionAllocation.id)).scalars().all()
    )

    response = client.post(
        f"/projects/{source_project.id}/copy", headers=as_caller(IN_SCOPE_USER)
    )

    assert response.status_code == 201, response.text
    copy_project_id = uuid.UUID(response.json()["id"])
    copy_scenario_id = _copied_scenario_id(db_session, copy_project_id)

    copied_positions = (
        db_session.execute(
            sa.select(StaffingPosition)
            .where(StaffingPosition.scenario_id == copy_scenario_id)
            .order_by(StaffingPosition.start_date)
        )
        .scalars()
        .all()
    )
    assert len(copied_positions) == 2, (
        "the copy has a different number of positions — the position half of the cascade is missing"
    )
    copied_allocation_ids = {
        allocation.id for position in copied_positions for allocation in position.allocations
    }
    assert len(copied_allocation_ids) == 4, (
        "the copy has a different number of month rows — the allocation half of the cascade is "
        "missing"
    )
    assert {position.id for position in copied_positions}.isdisjoint(source_position_ids)
    assert copied_allocation_ids.isdisjoint(source_allocation_ids)

    for copied, original in zip(copied_positions, positions, strict=True):
        assert copied.scenario_id == copy_scenario_id
        for field in COPIED_POSITION_FIELDS:
            assert getattr(copied, field) == getattr(original, field), field

    assert _grid(db_session, copy_scenario_id) == _grid(db_session, source_scenario.id), (
        "the copied grid differs from the source's — a month row is attached to the wrong position"
    )


def test_k_08_changing_an_allocation_on_the_copy_leaves_the_source_scenario_unchanged(
    client: TestClient, db_session: Session
) -> None:
    """K-08, claim 2 — AC-02's own sentence, through the endpoint that changes staffing.

    The edit goes through `PATCH …/allocations/{month}` rather than a raw `UPDATE`: AC-02 is about
    what happens when a *user* changes the staffing of a duplicated scenario, and a raw statement
    would prove independence of the rows while saying nothing about the path a user takes to them.

    Checked in both directions, because a shared row shows up asymmetrically depending on which side
    is written.
    """
    source_project, source_scenario, positions = _source_with_two_positions_and_two_months(
        db_session
    )
    copy_project_id = uuid.UUID(
        client.post(f"/projects/{source_project.id}/copy", headers=as_caller(IN_SCOPE_USER)).json()[
            "id"
        ]
    )
    copy_scenario_id = _copied_scenario_id(db_session, copy_project_id)
    grid_before = _grid(db_session, source_scenario.id)

    copied = client.get(
        staffing_path(copy_project_id, copy_scenario_id), headers=as_caller(IN_SCOPE_USER)
    ).json()["positions"][0]
    edited = client.patch(
        allocation_path(copy_project_id, copy_scenario_id, uuid.UUID(copied["id"]), MARCH),
        json={"updated_at": copied["updated_at"], "planned_allocation_hours": "1.00"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert edited.status_code == 200, edited.text
    assert _grid(db_session, source_scenario.id) == grid_before, (
        "an edit on the copy changed the source — AC-02"
    )
    assert _grid(db_session, copy_scenario_id)[MARCH][MARCH][1] == Decimal("1.00")

    # Direction two: a change on the source does not reach the copy.
    db_session.execute(
        sa.update(StaffingPositionAllocation)
        .where(
            StaffingPositionAllocation.position_id == positions[0].id,
            StaffingPositionAllocation.period_month == APRIL,
        )
        .values(billable_hours=Decimal("2.00"))
    )
    db_session.flush()
    assert _grid(db_session, copy_scenario_id)[MARCH][APRIL][2] == Decimal("130.00")


def test_k_08_an_approved_source_refuses_the_edit_while_its_draft_copy_accepts_it(
    client: TestClient, db_session: Session
) -> None:
    """K-08, claim 3 — the sharpest contrast: the freeze and the way around it are both real.

    One request shape, two targets, two answers. On the `approved` source the edit is refused by the
    guard of K-07; on the copy — a `draft`, because `copy_scenario` takes no status parameter — the
    same edit is applied. Either half alone would be misleading: a guard that refused both would
    make an approved calculation unchangeable *forever*, which is not what ADR-0004 says (further
    changes require a new version, and a copy is that new version); a guard that accepted both would
    not be a guard.

    The copy also had to be made *from* an approved scenario, so this doubles as the proof that the
    copier reads the source without writing it — the source is still `approved` at the end.
    """
    source_project, source_scenario, positions = _source_with_two_positions_and_two_months(
        db_session, status=ScenarioStatus.APPROVED
    )
    source_grid_before = _grid(db_session, source_scenario.id)

    copy_project_id = uuid.UUID(
        client.post(f"/projects/{source_project.id}/copy", headers=as_caller(IN_SCOPE_USER)).json()[
            "id"
        ]
    )
    copy_scenario_id = _copied_scenario_id(db_session, copy_project_id)
    source_position = client.get(
        staffing_path(source_project.id, source_scenario.id), headers=as_caller(IN_SCOPE_USER)
    ).json()["positions"][0]
    copied_position = client.get(
        staffing_path(copy_project_id, copy_scenario_id), headers=as_caller(IN_SCOPE_USER)
    ).json()["positions"][0]
    body = {"planned_allocation_hours": "7.00"}

    refused = client.patch(
        allocation_path(
            source_project.id, source_scenario.id, uuid.UUID(source_position["id"]), MARCH
        ),
        json={"updated_at": source_position["updated_at"], **body},
        headers=as_caller(IN_SCOPE_USER),
    )
    accepted = client.patch(
        allocation_path(
            copy_project_id, copy_scenario_id, uuid.UUID(copied_position["id"]), MARCH
        ),
        json={"updated_at": copied_position["updated_at"], **body},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"], refused.text
    assert accepted.status_code == 200, accepted.text
    assert _grid(db_session, source_scenario.id) == source_grid_before, (
        "the approved source's staffing changed"
    )
    assert _grid(db_session, copy_scenario_id)[MARCH][MARCH][1] == Decimal("7.00")
    db_session.expire_all()
    reread = db_session.get(Scenario, source_scenario.id)
    assert reread is not None
    assert reread.status is ScenarioStatus.APPROVED, "copying spent the source's approval"
    assert positions[0].scenario_id == source_scenario.id


def test_the_staffing_copier_is_the_registered_entry_of_the_scenario_cascade() -> None:
    """ADR-0004's forward commitment, checked as identity rather than as a count.

    "Each task creating a child table of `scenarios` appends its copier in that same task" is worth
    nothing if the registry contains *something*. This asserts it contains **this** function — the
    mutation "delete the entry" is then killed here as well as by the row-level tests above, and for
    a reason that names the decision.
    """
    assert copy_staffing_positions in SCENARIO_CHILD_COPIERS


def test_every_staffing_position_column_is_either_copied_or_explicitly_excluded() -> None:
    """A drift guard, aimed at the failure ADR-0004 (addendum, point 4) names: something new that
    quietly stays behind when a scenario is copied.

    The copier reflects over the mapper, so a column added later is carried over automatically — and
    silently either way. This test makes the decision visible: a new column fails here until it is
    added to `COPIED_POSITION_FIELDS` (and therefore compared value by value above) or named in
    `POSITION_COLUMNS_NOT_COPIED` with a reason.
    """
    mapped = {attribute.key for attribute in sa.inspect(StaffingPosition).column_attrs}

    assert mapped == set(COPIED_POSITION_FIELDS) | POSITION_COLUMNS_NOT_COPIED


def test_every_allocation_column_is_either_copied_or_explicitly_excluded() -> None:
    """The same guard for the month row — the level that has no registry entry of its own and would
    therefore be the easier of the two to forget."""
    mapped = {attribute.key for attribute in sa.inspect(StaffingPositionAllocation).column_attrs}

    assert mapped == set(COPIED_ALLOCATION_FIELDS) | ALLOCATION_COLUMNS_NOT_COPIED
