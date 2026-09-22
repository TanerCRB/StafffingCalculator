"""SC-3-01 — criteria K-01, K-02, K-03 and K-05: what a staffing position is, who may see it.

One test per criterion (plus each criterion's own contrast, which is what keeps a refusal test from
passing by refusing everything):

- **K-01** the position is persisted with the full dimension tuple, its headcount and its own
  period; a dimension id that names no catalogue row is refused by the *foreign key*, not only by
  the request schema.
- **K-02** the scenario is resolved through the `project_access` scope, never through
  `session.get`: `404`, indistinguishable from an id that never existed, and taking precedence over
  the `409` an approved scenario would otherwise produce.
- **K-03** deny by default on the new `STAFFING_READ`/`STAFFING_WRITE` pair, with nothing written.
- **K-05** the three hour figures are three independent values.

The database is a real PostgreSQL migrated with Alembic (`conftest`), and the persistence claims are
checked on a **separate connection**, which by definition sees committed rows only.

K-04 (what the database refuses whoever is writing), K-06/K-07 (the approved-scenario write guard)
and K-08 (AC-02 through the copy) live in `test_staffing_schema_constraints.py`,
`test_staffing_approved_guards.py` and `test_staffing_copy.py` — separate files because they are
separate mechanisms with separate mutations.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas.staffing import (
    MAX_ALLOCATION_MONTHS,
    MAX_HEADCOUNT,
    StaffingAllocation,
    StaffingPositionRead,
)
from app.api.staffing import STAFFING_NOT_FOUND_DETAIL
from app.core.identity import CallerIdentity, Permission
from app.data.staffing import create_position
from app.models import ScenarioStatus, StaffingPosition, StaffingPositionAllocation
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    allocation_path,
    as_caller,
    caller_holding,
    count_allocations,
    count_positions,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    staffing_path,
    staffing_position_payload,
)

STAFFING_PERMISSIONS = frozenset({Permission.STAFFING_READ, Permission.STAFFING_WRITE})

PERMISSIONS_THAT_EXISTED_BEFORE_THIS_TASK = frozenset(Permission) - STAFFING_PERMISSIONS
"""Every permission in the vocabulary except the two this task adds — computed, not typed out.

K-03 words this as "a caller holding all nine existing permissions". The enum in fact held **eight**
before SC-3-01 (`PROJECT_READ`, `PROJECT_CREATE`, `PERSONNEL_COSTS_READ`, `PROJECT_EDIT`,
`PROJECT_COPY`, `PROJECT_ARCHIVE`, `CATALOG_READ`, `CATALOG_WRITE`), so the criterion's count is one
too high. Deriving the set instead of transcribing it keeps the *claim* the criterion makes — "every
permission that already existed is not enough" — true whatever the count is, and makes a permission
added by a later task join this test automatically instead of quietly escaping it."""


def _dimensions_and_draft_scenario(session: Session, *, suffix: str = ""):
    """A project in `IN_SCOPE_USER`'s scope, one draft scenario, one full catalogue tuple."""
    project = make_project(
        session, name=f"Aurora migration{suffix}", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(session, project, name="Baseline")
    return project, scenario, make_dimension_tuple(session, suffix=suffix)


# --- K-01: the position is persisted with the whole tuple ----------------------------------------


def test_k_01_a_position_is_persisted_with_its_dimension_tuple_headcount_and_own_period(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-01 — two positions × two months, every value different, read back from another connection.

    Two positions rather than one, and two months each with three differing figures, because a
    single row cannot distinguish "the row was stored" from "one row was stored and aliased onto
    every read": with one position and one month, a write path that ignored its arguments and
    returned the last row it saw would pass.

    Read back with raw SQL on a separate connection, which sees committed data only — a `POST` that
    flushed without committing leaves nothing for these queries, and a position whose months were
    never flushed leaves a row with no children.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project, scenario, first_tuple = _dimensions_and_draft_scenario(setup)
        second_tuple = make_dimension_tuple(setup, suffix=" (second)")
        project_id, scenario_id = project.id, scenario.id
        setup.commit()

    created_first = committing_client.post(
        staffing_path(project_id, scenario_id),
        json=staffing_position_payload(
            first_tuple,
            headcount=3,
            start_date="2026-03-01",
            end_date="2026-04-30",
            allocations=[
                {
                    "period_month": "2026-03-01",
                    "availability_hours": "160.00",
                    "planned_allocation_hours": "120.50",
                    "billable_hours": "100.25",
                },
                {
                    "period_month": "2026-04-01",
                    "availability_hours": "150.00",
                    "planned_allocation_hours": "140.75",
                    "billable_hours": "130.50",
                },
            ],
        ),
        headers=as_caller(IN_SCOPE_USER),
    )
    created_second = committing_client.post(
        staffing_path(project_id, scenario_id),
        json=staffing_position_payload(
            second_tuple,
            headcount=1,
            start_date="2026-05-01",
            # Open-ended: `NULL`, not a sentinel date.
            end_date=None,
            allocations=[
                {
                    "period_month": "2026-05-01",
                    "availability_hours": "80.00",
                    "planned_allocation_hours": "40.00",
                    "billable_hours": "20.00",
                },
                {
                    "period_month": "2026-06-01",
                    "availability_hours": "0.00",
                    "planned_allocation_hours": "0.00",
                    "billable_hours": "0.00",
                },
            ],
        ),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert created_first.status_code == 201, created_first.text
    assert created_second.status_code == 201, created_second.text

    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT id, scenario_id, role_id, seniority_id, location_id, engagement_type_id,"
                " headcount, start_date, end_date FROM staffing_position ORDER BY start_date"
            )
        ).all()
        months = connection.execute(
            sa.text(
                "SELECT position_id, period_month, availability_hours,"
                " planned_allocation_hours, billable_hours FROM staffing_position_allocation"
                " ORDER BY period_month"
            )
        ).all()

    assert len(rows) == 2, "one row per position, not one row reused"
    stored_first, stored_second = rows
    assert stored_first.scenario_id == scenario_id
    assert (
        stored_first.role_id,
        stored_first.seniority_id,
        stored_first.location_id,
        stored_first.engagement_type_id,
    ) == (
        first_tuple.role_id,
        first_tuple.seniority_id,
        first_tuple.location_id,
        first_tuple.engagement_type_id,
    )
    assert stored_first.headcount == 3
    assert (stored_first.start_date, stored_first.end_date) == (date(2026, 3, 1), date(2026, 4, 30))
    # The second position keeps its *own* tuple and its own period — the aliasing check.
    assert stored_second.role_id == second_tuple.role_id
    assert stored_second.role_id != stored_first.role_id
    assert stored_second.headcount == 1
    assert (stored_second.start_date, stored_second.end_date) == (date(2026, 5, 1), None)

    assert [month.period_month for month in months] == [
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
        date(2026, 6, 1),
    ]
    assert [month.planned_allocation_hours for month in months] == [
        Decimal("120.50"),
        Decimal("140.75"),
        Decimal("40.00"),
        Decimal("0.00"),
    ], "four month rows, four different values — no row aliased onto another"
    assert {month.position_id for month in months} == {stored_first.id, stored_second.id}


def test_k_01_a_dimension_id_that_names_no_catalogue_row_is_refused_by_the_database(
    client: TestClient, db_session: Session
) -> None:
    """K-01's contrast, and the reason it is a *foreign key* rather than a validator.

    Two paths, one refusal. The endpoint answers `409` naming the foreign-key constraint, and the
    direct write — a fixture, a seed script, a future import, anything that never sees
    `StaffingPositionCreateRequest` — is refused by the database itself. A `SELECT`-then-`INSERT`
    check in the request schema would satisfy only the first half, and two callers racing would both
    pass it.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)

    response = client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(dimensions, role_id=str(uuid.uuid4())),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 409, response.text
    assert "fk_staffing_position_role_id" in response.json()["detail"], response.text
    # The message names the mechanism and quotes no value of the row (NF-11) — the exception carries
    # the SQLSTATE and the constraint name and nothing else (`app.data.write_errors`).
    assert "120.00" not in response.text


def test_k_01_a_dimension_id_that_names_no_catalogue_row_is_refused_on_the_direct_write_too(
    db_session: Session,
) -> None:
    """K-01's contrast on the path that never reaches Pydantic — a fixture, a script, an import.

    A separate test from the endpoint one above, and not by preference: the write path rolls its
    session back when the *database* refuses, which inside this suite's shared transaction discards
    the rows the fixture had flushed. Two claims about two paths therefore cannot share one
    transaction — and a single test that tried would be asserting against rows that are no longer
    there.
    """
    _, scenario, dimensions = _dimensions_and_draft_scenario(db_session)

    with pytest.raises(IntegrityError) as error:
        db_session.add(
            StaffingPosition(
                id=uuid.uuid4(),
                scenario_id=scenario.id,
                role_id=dimensions.role_id,
                # One dimension wrong, the other three real: the refusal is about the id that names
                # nothing, not about a malformed row.
                seniority_id=uuid.uuid4(),
                location_id=dimensions.location_id,
                engagement_type_id=dimensions.engagement_type_id,
                headcount=1,
                start_date=date(2026, 3, 1),
            )
        )
        db_session.flush()

    assert "fk_staffing_position_seniority_id" in str(error.value)
    db_session.rollback()


# --- K-02: scope comes from project_access, not from a local lookup ------------------------------


def test_k_02_staffing_of_a_scenario_outside_the_callers_scope_is_not_found(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — `404` for read *and* write, indistinguishable from an id that never existed.

    Compared body for body, header for header: the same status, the same text, the same
    `content-length`. A `403` would confirm that the scenario exists, and so would a message naming
    the project (ADR-0005, addendum 2026-09-19, point 4 — the write path included).

    The contrast at the end is what keeps "always 404" from passing: the very same address, called
    by the caller who does hold `project_access`, answers `200` and `201`.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    path = staffing_path(project.id, scenario.id)
    unknown_path = staffing_path(uuid.uuid4(), uuid.uuid4())

    denied_read = client.get(path, headers=as_caller(OUT_OF_SCOPE_USER))
    never_existed_read = client.get(unknown_path, headers=as_caller(OUT_OF_SCOPE_USER))
    denied_write = client.post(
        path,
        json=staffing_position_payload(dimensions),
        headers=as_caller(OUT_OF_SCOPE_USER),
    )
    never_existed_write = client.post(
        unknown_path,
        json=staffing_position_payload(dimensions),
        headers=as_caller(OUT_OF_SCOPE_USER),
    )

    for denied, never_existed in (
        (denied_read, never_existed_read),
        (denied_write, never_existed_write),
    ):
        assert denied.status_code == 404, denied.text
        assert denied.status_code == never_existed.status_code
        assert denied.text == never_existed.text
        assert denied.json()["detail"] == STAFFING_NOT_FOUND_DETAIL
        assert denied.headers.get("content-length") == never_existed.headers.get("content-length")
        assert "Aurora migration" not in denied.text
        assert str(scenario.id) not in denied.text
    assert count_positions(db_session) == 0, "a refused write wrote nothing"

    granted_read = client.get(path, headers=as_caller(IN_SCOPE_USER))
    granted_write = client.post(
        path, json=staffing_position_payload(dimensions), headers=as_caller(IN_SCOPE_USER)
    )

    assert granted_read.status_code == 200, granted_read.text
    assert granted_write.status_code == 201, granted_write.text


def test_k_02_an_approved_scenario_outside_the_callers_scope_is_not_found_not_a_refusal(
    client: TestClient, db_session: Session
) -> None:
    """K-02, second run: the `404` takes precedence over the `409` (ADR-0007).

    The scenario is both invisible to this caller *and* approved, so the two refusals compete. The
    answer must be the scope one: a `409` saying "this scenario is approved" would tell a caller who
    may not see the project that the scenario exists and what state it is in — a side channel built
    out of a write-specific status code, which is exactly what ADR-0005's addendum (point 4) rules
    out. The precedence is structural rather than remembered: the guard is never reached, because
    the scenario is resolved through the scope-filtered read path first.
    """
    project = make_project(db_session, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,))
    approved = make_scenario(
        db_session, project, name="Baseline", status=ScenarioStatus.APPROVED
    )
    dimensions = make_dimension_tuple(db_session)

    response = client.post(
        staffing_path(project.id, approved.id),
        json=staffing_position_payload(dimensions),
        headers=as_caller(OUT_OF_SCOPE_USER),
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == STAFFING_NOT_FOUND_DETAIL
    assert "approved" not in response.text.lower()
    assert count_positions(db_session) == 0

    # Contrast, so the assertion above is not satisfied by "everything is a 404": the caller who can
    # see the project gets the *other* refusal, and it names the reason.
    refused_for_the_right_reason = client.post(
        staffing_path(project.id, approved.id),
        json=staffing_position_payload(dimensions),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert refused_for_the_right_reason.status_code == 409, refused_for_the_right_reason.text
    assert "approved" in refused_for_the_right_reason.json()["detail"]


def test_k_02_a_scenario_of_another_project_is_not_found_under_a_project_the_caller_can_see(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — the membership check, which is what the `project_id` in the address is *for*.

    Both projects are in the caller's scope, so nothing about access is in question here: the
    scenario simply belongs to the other one. Resolved by `session.get(Scenario, id)` — the mutation
    this criterion names — the request would succeed and write a position into a scenario the
    address did not describe, tying a row to a project through a path nobody audited. Checked for
    the read and for the write, because the membership test lives in one shared place and a mutation
    there has to fail on both.
    """
    first = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    second = make_project(db_session, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,))
    scenario_of_second = make_scenario(db_session, second, name="Baseline")
    dimensions = make_dimension_tuple(db_session)
    mismatched = staffing_path(first.id, scenario_of_second.id)

    read = client.get(mismatched, headers=as_caller(IN_SCOPE_USER))
    write = client.post(
        mismatched, json=staffing_position_payload(dimensions), headers=as_caller(IN_SCOPE_USER)
    )

    assert read.status_code == 404, read.text
    assert write.status_code == 404, write.text
    assert read.json()["detail"] == STAFFING_NOT_FOUND_DETAIL
    assert count_positions(db_session) == 0

    # Contrast: the same scenario id under its own project works.
    matched = client.post(
        staffing_path(second.id, scenario_of_second.id),
        json=staffing_position_payload(dimensions),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert matched.status_code == 201, matched.text


# --- K-03: deny by default on the new permission pair -------------------------------------------


def test_k_03_staffing_is_denied_to_a_caller_holding_every_permission_that_existed_before(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — the refusal test ADR-0005 makes mandatory for each of the two new permissions.

    The caller holds **every** permission the vocabulary had before this task — project read,
    create, edit, copy, archive, catalogue read and write, and even `PERSONNEL_COSTS_READ` — and
    holds `project_access` to the project, so neither scope nor another permission can be what stops
    them. Only the absence of `STAFFING_READ`/`STAFFING_WRITE` can, which is the whole point of
    adding them rather than widening `PROJECT_READ`/`PROJECT_EDIT`.

    "Zero rows after the refusal", checked with a `SELECT` rather than inferred from the status
    code, and the contrast at the end adds the missing permission to the very same caller.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    path = staffing_path(project.id, scenario.id)

    with caller_holding(*PERMISSIONS_THAT_EXISTED_BEFORE_THIS_TASK):
        refused_read = client.get(path, headers=as_caller(IN_SCOPE_USER))
        refused_write = client.post(
            path, json=staffing_position_payload(dimensions), headers=as_caller(IN_SCOPE_USER)
        )

    assert refused_read.status_code == 403, refused_read.text
    assert refused_write.status_code == 403, refused_write.text
    assert count_positions(db_session) == 0
    assert count_allocations(db_session) == 0

    with caller_holding(*PERMISSIONS_THAT_EXISTED_BEFORE_THIS_TASK, Permission.STAFFING_READ):
        allowed_read = client.get(path, headers=as_caller(IN_SCOPE_USER))
        still_refused_write = client.post(
            path, json=staffing_position_payload(dimensions), headers=as_caller(IN_SCOPE_USER)
        )

    # Read permission opens the read and nothing else: the two are separate rights (NF-10's
    # reasoning for `CATALOG_*`, applied to staffing).
    assert allowed_read.status_code == 200, allowed_read.text
    assert still_refused_write.status_code == 403, still_refused_write.text
    assert count_positions(db_session) == 0

    with caller_holding(Permission.STAFFING_READ, Permission.STAFFING_WRITE):
        allowed_write = client.post(
            path, json=staffing_position_payload(dimensions), headers=as_caller(IN_SCOPE_USER)
        )

    assert allowed_write.status_code == 201, allowed_write.text


def test_k_03_the_allocation_edit_is_denied_to_a_caller_without_staffing_write(
    client: TestClient, db_session: Session
) -> None:
    """K-03 for the second write path: the token in the body does not buy the permission.

    A caller with `STAFFING_READ` can see the position's `updated_at`, and carrying it in a `PATCH`
    must still not let them write. The old value is asserted to be still in the database — a `403`
    that had already applied the change would satisfy a status-only assertion.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    position = make_staffing_position(db_session, scenario, dimensions)
    make_allocation(db_session, position, period_month=date(2026, 3, 1))
    path = allocation_path(project.id, scenario.id, position.id, date(2026, 3, 1))

    with caller_holding(Permission.STAFFING_READ):
        token = client.get(
            staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        ).json()["positions"][0]["updated_at"]
        refused = client.patch(
            path,
            json={"updated_at": token, "planned_allocation_hours": "1.00"},
            headers=as_caller(IN_SCOPE_USER),
        )

    assert refused.status_code == 403, refused.text
    db_session.expire_all()
    stored = db_session.execute(
        sa.text(
            "SELECT planned_allocation_hours FROM staffing_position_allocation"
            " WHERE position_id = :id"
        ),
        {"id": position.id},
    ).scalar_one()
    assert stored == Decimal("120.00"), "the refused edit changed the row anyway"


# --- K-05: three independent values -------------------------------------------------------------


def test_k_05_the_three_hour_figures_are_three_independent_values(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05 — availability, planned allocation and billable hours, written and read back distinct.

    Two months with three different figures each, and the six values are pairwise distinct *across*
    the two rows as well, so neither a derivation inside one row (`billable := planned`) nor a copy
    of one row onto the other can pass. Asserted on the committed rows **and** on the payload, which
    is what makes the mutation kill the test wherever it is applied — in the write path, in the
    read-back or in the response shaping.

    The figures deliberately do not describe a plausible ratio: planned exceeds availability in the
    second month. Over-allocation is a legal state to be seen, not a write to refuse (out of scope,
    point 5), and picking the plausible numbers instead would let a "planned := min(planned,
    availability)" clamp survive.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project, scenario, dimensions = _dimensions_and_draft_scenario(setup)
        project_id, scenario_id = project.id, scenario.id
        setup.commit()

    created = committing_client.post(
        staffing_path(project_id, scenario_id),
        json=staffing_position_payload(
            dimensions,
            allocations=[
                {
                    "period_month": "2026-03-01",
                    "availability_hours": "168.00",
                    "planned_allocation_hours": "120.00",
                    "billable_hours": "96.00",
                },
                {
                    "period_month": "2026-04-01",
                    "availability_hours": "152.00",
                    "planned_allocation_hours": "160.00",
                    "billable_hours": "144.00",
                },
            ],
        ),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert created.status_code == 201, created.text
    read_back = committing_client.get(
        staffing_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    )

    assert read_back.status_code == 200, read_back.text
    allocations = read_back.json()["positions"][0]["allocations"]
    assert [
        (
            allocation["availability_hours"],
            allocation["planned_allocation_hours"],
            allocation["billable_hours"],
        )
        for allocation in allocations
    ] == [("168.00", "120.00", "96.00"), ("152.00", "160.00", "144.00")]
    # Fixed-point strings, not JSON floats (NF-01, ADR-0002) — `96.00`, never `96.0`.
    assert '"96.00"' in read_back.text

    with engine.connect() as connection:
        stored = connection.execute(
            sa.text(
                "SELECT availability_hours, planned_allocation_hours, billable_hours"
                " FROM staffing_position_allocation ORDER BY period_month"
            )
        ).all()
    assert [tuple(row) for row in stored] == [
        (Decimal("168.00"), Decimal("120.00"), Decimal("96.00")),
        (Decimal("152.00"), Decimal("160.00"), Decimal("144.00")),
    ]


def test_an_allocation_mapping_cannot_carry_a_position_id_of_its_own(
    db_session: Session,
) -> None:
    """The security fix B-01 (security-auditor, 2026-09-19), pinned by a test — added by QA.

    `create_position` builds each month row as `{**allocation, "id": …, "position_id": …}`, with the
    two identifiers **last** so they win over anything of the same name in the caller's mapping. The
    other order — the one this branch carried before B-01 — lets an `allocation` bring its own
    `position_id` and attach a month row to a *different* position: one in another scenario, of
    another project, possibly one the caller cannot see, reached without the `approved` guard and
    without the scope filter the position this call created went through.

    Measured, not predicted: swapping the two spellings back left all 249 tests green, so the fix
    stood on review alone.

    The call is direct rather than over HTTP, and that is the point — `StaffingAllocation` sets
    `extra="forbid"`, so today's client cannot express the field at all. The exposure is the *next*
    call site (an import, a seed script, a second endpoint), which is the population the fix was
    written for; a test going through Pydantic would prove Pydantic and leave the fix unguarded.
    """
    victim_project = make_project(
        db_session, name="Borealis rollout", accessible_to=(OUT_OF_SCOPE_USER,)
    )
    victim_scenario = make_scenario(db_session, victim_project, name="Baseline")
    victim_position = make_staffing_position(
        db_session, victim_scenario, make_dimension_tuple(db_session, suffix=" (victim)")
    )

    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    created = create_position(
        db_session,
        CallerIdentity(user_id=IN_SCOPE_USER, permissions=STAFFING_PERMISSIONS),
        project.id,
        scenario.id,
        role_id=dimensions.role_id,
        seniority_id=dimensions.seniority_id,
        location_id=dimensions.location_id,
        engagement_type_id=dimensions.engagement_type_id,
        headcount=1,
        start_date=date(2026, 3, 1),
        end_date=None,
        allocations=[
            {
                # The hostile field: a position this caller has no address for and no right to.
                "position_id": victim_position.id,
                "period_month": date(2026, 3, 1),
                "availability_hours": Decimal("8.00"),
                "planned_allocation_hours": Decimal("8.00"),
                "billable_hours": Decimal("8.00"),
            }
        ],
    )

    assert created is not None
    # `create_position` answers with a `StaffingPositionView` since SC-3-02 (the derived
    # capacity is not on the row); the row itself is `view.position`. Accessor only — the
    # claim below is unchanged.
    created_id = created.position.id
    owners = (
        db_session.execute(sa.select(StaffingPositionAllocation.position_id)).scalars().all()
    )
    assert owners == [created_id], (
        "the month row was attached to the position named in the caller's own mapping instead of "
        "the one this call created and guarded"
    )


def test_the_staffing_response_carries_no_rate_cost_or_currency_field() -> None:
    """Gate-1 decision 9 / ADR-0005 addendum point 5, pinned as a test rather than as prose.

    SC-3-01 returns no resolved rate and no cost — not "removed for callers without the permission",
    but absent from the schema. That is what leaves the conjunction gate of SC-1-08 *unactivated* by
    this task, and the reason the first task showing a rate on a position has to prove that gate
    itself. A field quietly added here later would silently claim a gate nobody implemented, so the
    absence is asserted on the schema's own field names.
    """
    fields = set(StaffingPositionRead.model_fields) | set(StaffingAllocation.model_fields)

    money_ish = sorted(
        field
        for field in fields
        if any(word in field for word in ("rate", "cost", "currency", "amount", "price"))
    )
    assert money_ish == [], f"a money-shaped field appeared on the staffing response: {money_ish}"


# --- R-04: the request is bounded, so an out-of-range body is a 422 and not a 500 ----------------


@pytest.mark.parametrize("headcount", [2**31, 2**31 - 1, MAX_HEADCOUNT + 1])
def test_r_04_a_headcount_past_the_bound_is_a_422_about_the_field_not_a_500(
    client: TestClient, db_session: Session, headcount: int
) -> None:
    """R-04 (reviewer 2026-09-19) — the bound is at the boundary, not in the database's error log.

    `headcount` lands in an `INTEGER` column. Before the bound, `2**31` travelled the whole way down
    and came back as SQLSTATE `22003` `numeric_field_overflow` — a code deliberately outside
    `REFUSAL_BY_SQLSTATE` (R-01's lesson from the catalogue), so the API served a `500` for a
    request that is merely out of range. `2**31 - 1` is in the list because it *fits* the column and
    is still not a plan: the refusal is a statement about plausible input, not about the storage.

    Asserted with the row count, as everywhere on this path: a `500` would also have written
    nothing, so the status code is the whole difference and the message has to name the field.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)

    response = client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(dimensions, headcount=headcount),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert "headcount" in response.text
    assert count_positions(db_session) == 0
    assert count_allocations(db_session) == 0


def test_r_04_the_contrast_the_largest_accepted_headcount_is_still_written(
    client: TestClient, db_session: Session
) -> None:
    """R-04's contrast — the bound refuses what is past it, not what is at it.

    Without this half, `le=0` (or any bound tightened by accident) would satisfy the refusals above
    while making every realistic request fail. The value is read back from the row, not from the
    response, so a boundary that clamped instead of refusing would be visible here too.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)

    response = client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(dimensions, headcount=MAX_HEADCOUNT),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 201, response.text
    stored = db_session.execute(sa.select(StaffingPosition.headcount)).scalar_one()
    assert stored == MAX_HEADCOUNT


def test_r_04_an_allocation_list_longer_than_the_bound_is_refused_before_any_work(
    client: TestClient, db_session: Session
) -> None:
    """R-04 — a body whose size the client chooses must not buy unbounded work.

    `_months_are_distinct` used to ask `months.count(month)` inside a comprehension over the same
    list, i.e. O(n²) on input nobody bounded: a body with tens of thousands of months held a worker
    for the whole scan of a request that was never going to be accepted (NF-08 is about a caller
    waiting; this is about everyone else waiting behind them). Two independent fixes, and this test
    covers the first: `max_length` refuses the list before any validator runs.

    The contrast immediately below is the accepted length, so the bound cannot pass by refusing
    every grid. The second fix — the linear algorithm — is asserted by
    `test_r_04_a_repeated_month_is_refused_by_the_request_schema`, which is what still has to work
    once `n` is bounded.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    one_month = {
        "period_month": "2026-03-01",
        "availability_hours": "1.00",
        "planned_allocation_hours": "1.00",
        "billable_hours": "1.00",
    }

    response = client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(
            dimensions, allocations=[one_month] * (MAX_ALLOCATION_MONTHS + 1)
        ),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert "allocations" in response.text
    assert count_positions(db_session) == 0
    assert count_allocations(db_session) == 0

    # Contrast: a grid at the bound — every month distinct, as a real five-year plan would be — is
    # accepted and written in full.
    at_the_bound = [
        {
            "period_month": date(2026, 1, 1).replace(
                year=2026 + index // 12, month=index % 12 + 1
            ).isoformat(),
            "availability_hours": "160.00",
            "planned_allocation_hours": "120.00",
            "billable_hours": "100.00",
        }
        for index in range(MAX_ALLOCATION_MONTHS)
    ]
    accepted = client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(dimensions, allocations=at_the_bound),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert accepted.status_code == 201, accepted.text
    assert count_allocations(db_session) == MAX_ALLOCATION_MONTHS


def test_r_04_a_repeated_month_is_refused_by_the_request_schema(
    client: TestClient, db_session: Session
) -> None:
    """The rule the linear rewrite had to keep: one month named twice in one body is a `422`.

    `UNIQUE (position_id, period_month)` stays the guarantee (criterion K-04a proves it against a
    path that never sees this schema); this is the status code, and the reason it is worth having is
    that the database's refusal would be a `409` about a row the client cannot see, for what is
    plainly a malformed body. The repeated month is named in the message, so the caller can find it.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    month = {
        "period_month": "2026-03-01",
        "availability_hours": "160.00",
        "planned_allocation_hours": "120.00",
        "billable_hours": "100.00",
    }

    response = client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(dimensions, allocations=[month, month]),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert "2026-03-01" in response.text
    assert count_positions(db_session) == 0
    assert count_allocations(db_session) == 0
