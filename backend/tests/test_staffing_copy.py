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
    ABSENCE_COLUMNS_NOT_COPIED,
    ALLOCATION_COLUMNS_NOT_COPIED,
    POSITION_COLUMNS_NOT_COPIED,
    copy_staffing_positions,
)
from app.models import (
    Scenario,
    ScenarioStatus,
    StaffingPosition,
    StaffingPositionAbsence,
    StaffingPositionAllocation,
)
from tests.conftest import (
    IN_SCOPE_USER,
    allocation_path,
    as_caller,
    make_absence,
    make_absence_type,
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
    # SC-5-03 (ADR-0013, addendum 2026-09-25 SC-5-03, point 3; ADR-0004, addendum of the same
    # date, point 3): the personnel-cost basis travels with the position row the existing copier
    # already copies by reflection — no new entry in `SCENARIO_CHILD_COPIERS`, and this drift
    # guard is what proves it rather than assumes it.
    "cost_basis",
    "fixed_amount",
    "fixed_amount_currency",
    # SC-5-04 (ADR-0013, addendum 2026-09-29 SC-5-04, point 8; ADR-0004, same date): the stored FTE
    # travels with the row the existing reflective copier already copies — own data of the
    # scenario, no new copier entry; the drift guard is what proves it rather than assumes it.
    "assigned_fte",
    # Re-armed in SC-2-06 (Issue #31, gate 1 decision 6), not loosened: the named person travels
    # with the position the existing copier copies by reflection — the copy points at the *same*
    # person (ADR-0004, addendum 2026-09-27 SC-2-06, point 4), no new `SCENARIO_CHILD_COPIERS`
    # entry.
    "person_id",
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


# --- SC-5-03, K-04: cost_basis/fixed_amount travel with the position, on an independent row -------


def test_k_04_a_fixed_amount_positions_basis_and_amount_copy_onto_an_independent_row(
    client: TestClient, db_session: Session
) -> None:
    """SC-5-03, K-04 — a `fixed_amount` position's `cost_basis`/`fixed_amount`/
    `fixed_amount_currency` land on the copy with the source's values, on the copy's **own** row: a
    change to the source's `fixed_amount` after copying does not change the copy's.

    The general drift guard (`test_every_staffing_position_column_is_either_copied_or_
    explicitly_excluded`) proves the *columns* are not silently dropped from the copy; every
    other copy test in this file uses positions whose `cost_basis` is the default
    (`worked_time`, `fixed_amount NULL`), so none of them would notice a copier that returned
    `cost_basis`'s **default** regardless of the source (ADR-0004, addendum 2026-09-25 SC-5-03,
    point 3's own named mutation). This test's fixture is built so that mutation fails it
    specifically.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    position = make_staffing_position(
        db_session,
        scenario,
        make_dimension_tuple(db_session),
        start_date=MARCH,
        cost_basis="fixed_amount",
        fixed_amount=Decimal("4321.0000"),
        fixed_amount_currency="EUR",
    )
    make_allocation(db_session, position, period_month=MARCH)
    db_session.commit()

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    copy_project_id = uuid.UUID(response.json()["id"])
    copy_scenario_id = _copied_scenario_id(db_session, copy_project_id)

    copied = db_session.execute(
        sa.select(StaffingPosition).where(StaffingPosition.scenario_id == copy_scenario_id)
    ).scalar_one()
    assert copied.id != position.id
    assert (copied.cost_basis, copied.fixed_amount, copied.fixed_amount_currency) == (
        "fixed_amount",
        Decimal("4321.0000"),
        "EUR",
    )

    # The contrast K-04 asks for by name: the source changes after the copy exists, the copy does
    # not follow it — an independent value, not a shared reference or a re-read of the source.
    db_session.execute(
        sa.update(StaffingPosition)
        .where(StaffingPosition.id == position.id)
        .values(fixed_amount=Decimal("1.0000"))
    )
    db_session.flush()
    db_session.expire(copied)
    assert copied.fixed_amount == Decimal("4321.0000"), (
        "the copy's fixed_amount followed a change made to the source after copying"
    )


# --- SC-3-02, K-14: the third pass of the cascade ------------------------------------------------


COPIED_ABSENCE_FIELDS: tuple[str, ...] = ("absence_type_id", "start_date", "end_date")
"""Absence attributes the copy must carry over, written out by hand for the reason
`COPIED_POSITION_FIELDS` gives: reflecting here would compare the mechanism with itself.

`absence_type_id` *is* copied — the type is an organisational dictionary entry shared by both
scenarios, and re-pointing the copy at a different type would change what the copy says."""


def _absences(session: Session, scenario_id: uuid.UUID) -> dict[date, list[tuple]]:
    """One scenario's absences as plain values, keyed by the owning position's start date.

    Keyed by values rather than by identifier, like `_grid`: the source and the copy must compare
    equal in this shape while sharing no identifier of their own.
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
        position.start_date: sorted(
            (absence.absence_type_id, absence.start_date, absence.end_date)
            for absence in position.absences
        )
        for position in positions
    }


def _source_with_absences(session: Session, *, status: ScenarioStatus = ScenarioStatus.DRAFT):
    """The two-position fixture above, plus three absences with pairwise different ranges.

    Three and not two: the first position carries **two overlapping** absences (which is legal —
    criterion K-06) and the second carries one. A copier that de-duplicated, or that attached every
    absence to the first position it copied, is visible in the counts as well as in the values.
    """
    project, scenario, (first, second) = _source_with_two_positions_and_two_months(
        session, status=status
    )
    holiday = make_absence_type(session, name="Paid holiday")
    training = make_absence_type(
        session, name="Billable training", generates_cost=True, generates_revenue=True
    )
    make_absence(
        session, first, holiday, start_date=date(2026, 3, 2), end_date=date(2026, 3, 6)
    )
    make_absence(
        session, first, training, start_date=date(2026, 3, 4), end_date=date(2026, 3, 10)
    )
    make_absence(
        session, second, holiday, start_date=date(2026, 4, 13), end_date=date(2026, 4, 17)
    )
    return project, scenario, (first, second)


def test_k_14_copying_a_scenario_copies_the_absences_onto_the_copied_positions(
    client: TestClient, db_session: Session
) -> None:
    """K-14 — the third pass of the cascade, with the absences on the **copied** positions.

    Four claims in one test, because each is one of the criterion's contrasts and they are all
    about one copy:

    1. **the rows are there** — three absences on the copy, the same values, grouped under the same
       positions. The mutation "the third pass deleted from `copy_staffing_positions`" leaves zero
       and fails here;
    2. **`position_id` is the id of the COPY** — asserted directly, not inferred from the grouping.
       The mutation "mapped through the source `position_id`" leaves the copy's absences hanging off
       the *source's* positions, where the grouping by start date would still look right from the
       copy's side while the source silently grew three extra rows;
    3. **the source is untouched** — its own three absences, unchanged, and no identifier shared
       with the copy. This is the AC-02 sentence for the third table;
    4. **deleting on the copy does not touch the source** — the operational form of (3), and the one
       a shared reference would break.
    """
    project, scenario, (first, second) = _source_with_absences(db_session)
    source_absence_ids = {
        absence.id for position in (first, second) for absence in position.absences
    }
    before = _absences(db_session, scenario.id)
    assert sum(len(rows) for rows in before.values()) == 3

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    copy_scenario_id = _copied_scenario_id(db_session, uuid.UUID(response.json()["id"]))

    # (1) same values, same grouping.
    assert _absences(db_session, copy_scenario_id) == before

    # (2) the copy's absences belong to the copy's positions, and to no row of the source.
    copied_positions = {
        position.id
        for position in db_session.execute(
            sa.select(StaffingPosition).where(StaffingPosition.scenario_id == copy_scenario_id)
        ).scalars()
    }
    copied_absences = (
        db_session.execute(
            sa.select(StaffingPositionAbsence).where(
                StaffingPositionAbsence.position_id.in_(copied_positions)
            )
        )
        .scalars()
        .all()
    )
    assert len(copied_absences) == 3
    assert {absence.id for absence in copied_absences}.isdisjoint(source_absence_ids), (
        "the copy shares absence identifiers with its source"
    )
    assert {absence.position_id for absence in copied_absences} <= copied_positions

    # (3) the source still holds exactly its own three rows.
    assert _absences(db_session, scenario.id) == before
    assert (
        db_session.execute(
            sa.select(sa.func.count())
            .select_from(StaffingPositionAbsence)
            .where(StaffingPositionAbsence.id.in_(source_absence_ids))
        ).scalar_one()
        == 3
    )

    # (4) a change on the copy leaves the source alone.
    db_session.delete(copied_absences[0])
    db_session.flush()
    assert _absences(db_session, scenario.id) == before, (
        "deleting an absence on the copy changed the source — the copy shares its rows"
    )
    assert sum(len(rows) for rows in _absences(db_session, copy_scenario_id).values()) == 2


def test_k_14_an_approved_source_still_yields_a_copy_with_the_full_set_of_absences(
    client: TestClient, db_session: Session
) -> None:
    """K-14's third contrast — an `approved` source is copied in full, and stays approved.

    This is the pair of properties ADR-0004 needs and that a guard written one line too wide would
    break: an approved calculation is frozen, *and* copying it is the legal way to keep changing it.
    `copy_staffing_positions` writes only into the copy (always a `draft`), so no guard applies to
    it — and if one did, the freeze would become a dead end and F-12's "further changes require a
    new version" would be unsatisfiable.

    Asserted on the absences specifically, because the third pass is the one added last and is the
    one a guard added "for symmetry with the other write paths" would catch.
    """
    project, scenario, _ = _source_with_absences(db_session, status=ScenarioStatus.APPROVED)
    before = _absences(db_session, scenario.id)

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    copy_scenario_id = _copied_scenario_id(db_session, uuid.UUID(response.json()["id"]))

    assert _absences(db_session, copy_scenario_id) == before
    reread = db_session.get(Scenario, scenario.id)
    assert reread is not None
    assert reread.status is ScenarioStatus.APPROVED, "copying spent the source's approval"
    copy = db_session.get(Scenario, copy_scenario_id)
    assert copy is not None
    assert copy.status is ScenarioStatus.DRAFT


def test_k_14_every_mapped_absence_attribute_is_either_copied_or_named_as_not_copied() -> None:
    """K-14's drift guard — the third table joins the obligation the other two already carry.

    ADR-0004's addendum of 2026-09-18 (point 4) names the failure: a column that quietly stays
    behind when a scenario is copied. The copier reflects over the mapper, so a column added later
    is carried over automatically and silently either way; this makes the decision visible. A new
    column fails here until it is either added to `COPIED_ABSENCE_FIELDS` (and therefore compared
    value by value above) or named in `ABSENCE_COLUMNS_NOT_COPIED` with a reason.

    Sharper here than for the other two tables, for a reason worth writing down: which columns this
    table may have at all is fixed by criterion K-22 (no person, no note), so a column reaching this
    guard has already failed a different test — and if it somehow has not, this is the second place
    it is refused.
    """
    mapped = {attribute.key for attribute in sa.inspect(StaffingPositionAbsence).column_attrs}

    assert mapped == set(COPIED_ABSENCE_FIELDS) | ABSENCE_COLUMNS_NOT_COPIED
