"""SC-3-01, S-02 — the two guards of `app.data.staffing` that no HTTP request can reach.

Invariant Guardian S-02 (2026-09-19): two strong claims in `app/data/staffing.py` had no test of
their own, while the identical mechanism one table over (`app.data.project_writes`,
`tests/test_project_edit.py::test_the_data_layer_refuses_to_write_a_column_outside_the_editable_allow_list`)
did. Both claims are about the *next* call site — an import, a seed script, a second endpoint —
because the request schemas of today make them unreachable over HTTP: `StaffingAllocation` sets
`extra="forbid"`, and `StaffingAllocationEditRequest.changes()` can only ever name hour fields. A
guard whose only evidence is the schema in front of it is evidence about the schema.

Every call here is therefore **direct**, with `db_session` and a hand-built `CallerIdentity`, and
never through `TestClient`:

1. **The merge order of the allocation insert** (`create_position`). The month row is built as
   `{**dict(allocation), "id": …, "position_id": …}` — the two identifiers last, so a
   caller-supplied mapping cannot decide which position a month row belongs to or which key it takes
   (security-auditor B-01). The `position_id` half is already pinned by
   `test_staffing_positions.py::test_an_allocation_mapping_cannot_carry_a_position_id_of_its_own`;
   the `id` half was not pinned by anything, and it is the half here.

2. **`EDITABLE_ALLOCATION_FIELDS` / `AllocationFieldNotEditable`** (`update_allocation`). An
   allow-list, not "whatever the caller's mapping carried": `update_allocation` takes a `Mapping`,
   which is convenient and is exactly how an "edit" grows into a way to write any column of the
   table — `period_month` (a different row, and a way around `UNIQUE (position_id, period_month)`),
   `position_id` (a month row moved under another position, in another scenario, of a project the
   caller cannot see), `id`, `created_at`. The refused columns are derived from the table itself
   rather than typed out, so a column added later joins this test instead of quietly escaping it.

The contrast that keeps the refusals from passing by refusing everything is at the bottom: each of
the three hour columns really is writable through the same function, one at a time, leaving the
other two alone.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity, Permission
from app.data.staffing import (
    EDITABLE_ALLOCATION_FIELDS,
    AllocationFieldNotEditable,
    create_position,
    update_allocation,
)
from app.models.staffing import HOURS_COLUMNS, StaffingPosition, StaffingPositionAllocation
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
)

MARCH = date(2026, 3, 1)
APRIL = date(2026, 4, 1)

CALLER = CallerIdentity(
    user_id=IN_SCOPE_USER,
    permissions=frozenset({Permission.STAFFING_READ, Permission.STAFFING_WRITE}),
)
"""The caller these tests write as: in scope for the project, holding both staffing permissions.

The permission set is irrelevant to the data layer by design (the permission gate is a FastAPI
dependency, `require_permission`), and that is why it is spelled out here rather than left empty: a
refusal in any of these tests must be the guard under test, never a caller who could not have
written anything anyway.
"""

ALLOCATION_COLUMNS: tuple[str, ...] = tuple(
    column.name for column in StaffingPositionAllocation.__table__.columns
)

A_VALUE_THE_COLUMN_WOULD_ACCEPT: dict[str, Any] = {
    "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
    "position_id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
    "period_month": APRIL,
    "created_at": datetime(2020, 1, 1, tzinfo=UTC),
}
"""One plausible value per column outside the allow-list — plausible on purpose.

A malformed value would be refused by the database whatever the allow-list said, and the test would
then pass for the wrong reason. These are values the column itself would take, so the only thing
standing between them and the row is the guard.
"""


def _draft_scenario(session: Session, *, suffix: str = ""):
    """A project in `IN_SCOPE_USER`'s scope, one draft scenario, one full catalogue tuple."""
    project = make_project(
        session, name=f"Aurora migration{suffix}", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(session, project, name="Baseline")
    return project, scenario, make_dimension_tuple(session, suffix=suffix)


def _allocation_row(session: Session, allocation_id: uuid.UUID) -> dict[str, Any]:
    """One month row, every column, read straight from the table — not from the identity map.

    A table-level `SELECT` rather than `session.get`: the sessions in this suite run with
    `expire_on_commit=False`, so an ORM object can agree with the assertion while the row does not.
    Every column, because "the row is unchanged" is a claim about the whole row, and a guard that
    let one column through would otherwise be invisible.
    """
    return dict(
        session.execute(
            sa.select(StaffingPositionAllocation.__table__).where(
                StaffingPositionAllocation.__table__.c.id == allocation_id
            )
        )
        .mappings()
        .one()
    )


def _token(session: Session, position_id: uuid.UUID) -> datetime:
    """The position's `updated_at` — ADR-0007's token for the whole month grid."""
    return session.execute(
        sa.select(StaffingPosition.updated_at).where(StaffingPosition.id == position_id)
    ).scalar_one()


# --- B-01, the other half: a caller-supplied `id` does not win either ----------------------------


def test_s_02_an_allocation_mapping_cannot_carry_an_id_of_its_own(db_session: Session) -> None:
    """S-02 (1) — the identifiers come last in the merge, so **both** of them are computed.

    `test_an_allocation_mapping_cannot_carry_a_position_id_of_its_own` pins the `position_id` half.
    This is the `id` half, and it is a separate exposure rather than the same one twice: a month row
    whose primary key the caller chooses can be aimed at a key that already exists — the insert then
    fails as a duplicate and takes the whole position with it, a refusal a caller can provoke for a
    row they cannot see — or at a key the caller keeps a note of, which makes the identifier of a
    row inside someone else's scenario a value chosen outside it.

    Two months in one call, so both failure modes of the reversed merge order are covered at once:
    the first carries the primary key of an existing allocation row in a scenario this caller has no
    access to (with the identifiers merged first, the `INSERT` raises `IntegrityError` and no
    position is created at all), the second carries a free id together with a foreign `position_id`
    (with the identifiers merged first, the month row lands under the victim's position).

    The victim's own row is compared column by column before and after: the claim is not only that
    this call's rows are correct but that nothing outside this call's scope was touched.
    """
    victim_project = make_project(
        db_session, name="Borealis rollout", accessible_to=(OUT_OF_SCOPE_USER,)
    )
    victim_scenario = make_scenario(db_session, victim_project, name="Baseline")
    victim_position = make_staffing_position(
        db_session, victim_scenario, make_dimension_tuple(db_session, suffix=" (victim)")
    )
    victim_month = make_allocation(db_session, victim_position, period_month=MARCH)
    victim_before = _allocation_row(db_session, victim_month.id)
    chosen_id = uuid.UUID("33333333-3333-3333-3333-333333333333")

    project, scenario, dimensions = _draft_scenario(db_session)
    created = create_position(
        db_session,
        CALLER,
        project.id,
        scenario.id,
        role_id=dimensions.role_id,
        seniority_id=dimensions.seniority_id,
        location_id=dimensions.location_id,
        engagement_type_id=dimensions.engagement_type_id,
        headcount=1,
        start_date=MARCH,
        end_date=None,
        allocations=[
            {
                # The primary key of a row in a scenario this caller cannot address.
                "id": victim_month.id,
                "period_month": MARCH,
                "availability_hours": Decimal("8.00"),
                "planned_allocation_hours": Decimal("8.00"),
                "billable_hours": Decimal("8.00"),
            },
            {
                "id": chosen_id,
                "position_id": victim_position.id,
                "period_month": APRIL,
                "availability_hours": Decimal("4.00"),
                "planned_allocation_hours": Decimal("4.00"),
                "billable_hours": Decimal("4.00"),
            },
        ],
    )

    assert created is not None
    # `create_position` answers with a `StaffingPositionView` since SC-3-02 (the derived
    # capacity is not on the row); the row itself is `view.position`. Accessor only — the
    # claim below is unchanged.
    created_id = created.position.id
    written = (
        db_session.execute(
            sa.select(
                StaffingPositionAllocation.id,
                StaffingPositionAllocation.position_id,
                StaffingPositionAllocation.period_month,
            )
            .where(StaffingPositionAllocation.position_id == created_id)
            .order_by(StaffingPositionAllocation.period_month)
        )
        .all()
    )

    assert [row.period_month for row in written] == [MARCH, APRIL], (
        "the two month rows of this call are not both under the position it created"
    )
    assert {row.position_id for row in written} == {created_id}
    assert {row.id for row in written}.isdisjoint({victim_month.id, chosen_id}), (
        "a month row took the primary key its caller's mapping named instead of a generated one"
    )
    assert _allocation_row(db_session, victim_month.id) == victim_before, (
        "a row outside this call's scope changed"
    )
    assert (
        db_session.execute(
            sa.select(sa.func.count())
            .select_from(StaffingPositionAllocation)
            .where(StaffingPositionAllocation.position_id == victim_position.id)
        ).scalar_one()
        == 1
    ), "a month row was attached to a position this call never addressed"


# --- the allow-list of the allocation edit -------------------------------------------------------


def test_s_02_the_data_layer_refuses_to_write_a_column_outside_the_editable_allow_list(
    db_session: Session,
) -> None:
    """S-02 (2) — every column of the table that is not an hour figure is refused by name.

    The refused set is *derived* (`ALLOCATION_COLUMNS - EDITABLE_ALLOCATION_FIELDS`), so a column
    added to `staffing_position_allocation` later is refused by this test automatically or makes it
    fail for want of a value — the shape `test_staffing_positions.py` uses for the permission
    vocabulary, for the same reason: a transcribed list stops describing the table the day the table
    moves.

    Two attempts are not of that shape. The mixed one — an hour figure *together* with a forbidden
    column — kills a guard written as "at least one editable field is present", which would pass
    every single-field attempt and then write the forbidden column alongside the legal one. The last
    one carries a **stale** `expected_updated_at`: the answer must still be
    `AllocationFieldNotEditable` and not a concurrency conflict, which is what tells the guard apart
    from the statement's own `WHERE`. Without it, an allow-list deleted outright would still
    "refuse" here — as a conflict, for a reason nobody checked.

    What is deliberately **not** asserted: that the position's `updated_at` stayed where it was. It
    is unobservable in this fixture — `now()` in PostgreSQL is the *transaction's* start time, so a
    rotation inside the one transaction `db_session` holds writes back the value the fixture's
    insert already carried, and an equality assertion here would pass whether the position was
    updated or not. The claim it makes is proven where it is observable, across real transactions:
    `test_staffing_approved_guards.py::test_a_patch_on_a_month_that_has_no_row_is_not_found_and_rotates_no_token`.
    """
    project, scenario, dimensions = _draft_scenario(db_session)
    position = make_staffing_position(db_session, scenario, dimensions, start_date=MARCH)
    allocation = make_allocation(db_session, position, period_month=MARCH)
    before = _allocation_row(db_session, allocation.id)
    token = _token(db_session, position.id)

    outside_the_allow_list = sorted(set(ALLOCATION_COLUMNS) - EDITABLE_ALLOCATION_FIELDS)
    assert set(outside_the_allow_list) <= set(A_VALUE_THE_COLUMN_WOULD_ACCEPT), (
        "a column of staffing_position_allocation has no value to try here — add it above, or the "
        "allow-list is untested for it"
    )
    assert outside_the_allow_list, "the allow-list covers every column: there is nothing to refuse"

    attempts: list[tuple[dict[str, Any], datetime]] = [
        ({column: A_VALUE_THE_COLUMN_WOULD_ACCEPT[column]}, token)
        for column in outside_the_allow_list
    ]
    attempts.append(
        (
            {
                "billable_hours": Decimal("42.00"),
                "position_id": A_VALUE_THE_COLUMN_WOULD_ACCEPT["position_id"],
            },
            token,
        )
    )
    attempts.append(
        (
            {"period_month": A_VALUE_THE_COLUMN_WOULD_ACCEPT["period_month"]},
            datetime(2020, 1, 1, tzinfo=UTC),
        )
    )

    for changes, expected_updated_at in attempts:
        with pytest.raises(AllocationFieldNotEditable) as error:
            update_allocation(
                db_session,
                CALLER,
                project.id,
                scenario.id,
                position.id,
                MARCH,
                expected_updated_at=expected_updated_at,
                changes=changes,
            )
        forbidden = sorted(set(changes) - EDITABLE_ALLOCATION_FIELDS)
        for field in forbidden:
            assert field in str(error.value), f"the refusal does not name {field}"
        assert "2026-04-01" not in str(error.value), (
            "the refusal quotes the value it refused (NF-11) — the field name is the whole message"
        )

    assert _allocation_row(db_session, allocation.id) == before, (
        "a column outside the allow-list was written anyway"
    )


def test_s_02_an_allocation_edit_that_names_no_field_at_all_is_refused_not_silently_accepted(
    db_session: Session,
) -> None:
    """S-02 (3) — `changes={}` raises; it is not a no-op and not an `UPDATE` with no `SET`.

    Which of the two it is matters, and the source settles it: `update_allocation` raises the same
    `AllocationFieldNotEditable` as the allow-list branch, with a different message. Treated as a
    no-op instead, the call would return the position and rotate nothing — indistinguishable from a
    successful edit to the caller, which is how an edit that silently changed nothing gets reported
    as applied. Passed through to SQLAlchemy instead, `sa.update(...).values()` with an empty
    mapping is not a statement at all, and the failure would arrive as a `500`.

    The empty mapping is a *separate* test from the allow-list above because it is a separate branch
    with a separate message: a guard written only as `set(changes) - EDITABLE…` accepts `{}` —
    the empty set has nothing outside the allow-list — and the loop above would not notice.

    Run twice, with a current token and with a stale one, and both must give the same answer — the
    refusal is a statement about the call, not about the state of the row.
    """
    project, scenario, dimensions = _draft_scenario(db_session)
    position = make_staffing_position(db_session, scenario, dimensions, start_date=MARCH)
    allocation = make_allocation(db_session, position, period_month=MARCH)
    before = _allocation_row(db_session, allocation.id)

    for expected_updated_at in (_token(db_session, position.id), datetime(2020, 1, 1, tzinfo=UTC)):
        with pytest.raises(AllocationFieldNotEditable) as error:
            update_allocation(
                db_session,
                CALLER,
                project.id,
                scenario.id,
                position.id,
                MARCH,
                expected_updated_at=expected_updated_at,
                changes={},
            )
        assert "at least one field" in str(error.value), (
            "the empty edit was refused with the allow-list message — two branches, two reasons, "
            "and a caller reading the message cannot tell which one it hit"
        )

    assert _allocation_row(db_session, allocation.id) == before


@pytest.mark.parametrize("column", HOURS_COLUMNS)
def test_s_02_each_field_on_the_allow_list_is_written_and_only_that_field(
    db_session: Session, column: str
) -> None:
    """The contrast the two refusals above need: the allow-list permits what it names.

    Without this half, `EDITABLE_ALLOCATION_FIELDS = frozenset()` — or a guard that raised
    unconditionally — would satisfy every assertion above while making the edit path useless. Once
    per hour column, because the allow-list is a set of three and dropping one of them is a mutation
    a test can kill.

    "And only that field": the other two hour figures are asserted to still hold the fixture's
    three *different* values, so an edit that wrote its value into all three (the aliasing defect
    K-05 is about, reached this time from the edit path) fails here. The identifiers and
    `created_at` are compared as well — the edit must not move the row it edits.

    The token rotation that accompanies an accepted edit is **not** asserted here and cannot be: see
    the note in the allow-list test above — `now()` does not advance inside one transaction. It is
    proven across real transactions in `test_staffing_approved_guards.py`
    (`test_k_07_editing_an_allocation_under_an_approved_scenario_is_refused_and_changes_nothing`
    asserts the accepted edit's response carries a token different from the one it was given).
    """
    project, scenario, dimensions = _draft_scenario(db_session)
    position = make_staffing_position(db_session, scenario, dimensions, start_date=MARCH)
    allocation = make_allocation(db_session, position, period_month=MARCH)
    before = _allocation_row(db_session, allocation.id)
    token = _token(db_session, position.id)

    edited = update_allocation(
        db_session,
        CALLER,
        project.id,
        scenario.id,
        position.id,
        MARCH,
        expected_updated_at=token,
        changes={column: Decimal("42.50")},
    )

    assert edited is not None
    after = _allocation_row(db_session, allocation.id)
    assert after[column] == Decimal("42.50"), "the field on the allow-list was not written"
    for other in HOURS_COLUMNS:
        if other == column:
            continue
        assert after[other] == before[other], f"editing {column} changed {other} as well"
    assert (after["id"], after["position_id"], after["period_month"], after["created_at"]) == (
        before["id"],
        before["position_id"],
        before["period_month"],
        before["created_at"],
    ), "the edit moved the row instead of changing a value in it"
