"""SC-3-09 — position detail edits through the guarded staffing write path."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.models import ProjectAccess, Scenario, ScenarioStatus
from app.models.staffing import StaffingPosition
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    caller_holding,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    staffing_path,
)


def _edit_path(project_id: uuid.UUID, scenario_id: uuid.UUID, position_id: uuid.UUID) -> str:
    return f"{staffing_path(project_id, scenario_id)}/{position_id}/details"


def _details_payload(
    position: StaffingPosition, dimensions, **overrides: object
) -> dict[str, object]:
    return {
        "updated_at": position.updated_at.isoformat(),
        "role_id": str(dimensions.role_id),
        "seniority_id": str(dimensions.seniority_id),
        "location_id": str(dimensions.location_id),
        "engagement_type_id": str(dimensions.engagement_type_id),
        "headcount": 3,
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
    } | overrides


def _stored_details(session: Session, position_id: uuid.UUID) -> tuple[object, ...]:
    session.expire_all()
    row = session.get(StaffingPosition, position_id)
    assert row is not None
    return (
        row.role_id,
        row.seniority_id,
        row.location_id,
        row.engagement_type_id,
        row.headcount,
        row.start_date,
        row.end_date,
    )


def test_k_01_authorized_position_edit_updates_details_and_read_returns_saved_values(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(
        db_session, name="SC-3-09 authorized position edit", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, project, name="Draft")
    original = make_dimension_tuple(db_session, suffix=" original")
    replacement = make_dimension_tuple(db_session, suffix=" replacement")
    position = make_staffing_position(
        db_session,
        scenario,
        original,
        headcount=2,
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 31),
    )
    body = _details_payload(position, replacement)
    path = _edit_path(project.id, scenario.id, position.id)
    allocations_before = db_session.execute(
        sa.text("SELECT count(*) FROM staffing_position_allocation WHERE position_id = :id"),
        {"id": position.id},
    ).scalar_one()

    with caller_holding(Permission.STAFFING_READ):
        denied = client.patch(path, json=body, headers=as_caller(IN_SCOPE_USER))
        allocation_denied = client.post(
            f"{staffing_path(project.id, scenario.id)}/{position.id}/allocations",
            json={
                "updated_at": position.updated_at.isoformat(),
                "period_month": "2026-04-01",
                "availability_hours": "160.00",
                "planned_allocation_hours": "120.00",
                "billable_hours": "100.00",
            },
            headers=as_caller(IN_SCOPE_USER),
        )
    assert denied.status_code == 403, denied.text
    assert allocation_denied.status_code == 403, allocation_denied.text
    assert db_session.execute(
        sa.text("SELECT count(*) FROM staffing_position_allocation WHERE position_id = :id"),
        {"id": position.id},
    ).scalar_one() == allocations_before
    assert _stored_details(db_session, position.id) == (
        original.role_id,
        original.seniority_id,
        original.location_id,
        original.engagement_type_id,
        2,
        date(2026, 3, 1),
        date(2026, 3, 31),
    )

    saved = client.patch(path, json=body, headers=as_caller(IN_SCOPE_USER))
    assert saved.status_code == 200, saved.text
    value = saved.json()
    assert (
        value["role_id"],
        value["seniority_id"],
        value["location_id"],
        value["engagement_type_id"],
        value["headcount"],
        value["start_date"],
        value["end_date"],
    ) == (
        str(replacement.role_id),
        str(replacement.seniority_id),
        str(replacement.location_id),
        str(replacement.engagement_type_id),
        3,
        "2026-04-01",
        "2026-04-30",
    )
    assert not {"fixed_amount", "fixed_amount_currency", "cost_basis", "assigned_fte"} & set(
        value
    )

    reread = client.get(
        staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert reread.status_code == 200, reread.text
    assert reread.json()["positions"][0] == value


def test_k_03_out_of_scope_and_unknown_scenario_edits_are_same_404_and_write_nothing(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(
        db_session, name="SC-3-09 scoped position edit", accessible_to=(OUT_OF_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, project, name="Scoped")
    dimensions = make_dimension_tuple(db_session, suffix=" scoped")
    position = make_staffing_position(db_session, scenario, dimensions, headcount=2)
    body = _details_payload(position, dimensions)
    path = _edit_path(project.id, scenario.id, position.id)
    unknown_path = _edit_path(uuid.uuid4(), uuid.uuid4(), position.id)

    out_of_scope = client.patch(path, json=body, headers=as_caller(IN_SCOPE_USER))
    unknown = client.patch(unknown_path, json=body, headers=as_caller(IN_SCOPE_USER))
    assert out_of_scope.status_code == unknown.status_code == 404
    assert out_of_scope.content == unknown.content
    assert out_of_scope.headers["content-length"] == unknown.headers["content-length"]
    assert _stored_details(db_session, position.id)[4] == 2

    allocation_body = {
        "updated_at": position.updated_at.isoformat(),
        "period_month": "2026-03-01",
        "availability_hours": "160.00",
        "planned_allocation_hours": "120.00",
        "billable_hours": "100.00",
    }
    allocation_path = f"{staffing_path(project.id, scenario.id)}/{position.id}/allocations"
    unknown_allocation_path = (
        f"{staffing_path(uuid.uuid4(), uuid.uuid4())}/{position.id}/allocations"
    )
    allocation_denied = client.post(
        allocation_path, json=allocation_body, headers=as_caller(IN_SCOPE_USER)
    )
    allocation_unknown = client.post(
        unknown_allocation_path, json=allocation_body, headers=as_caller(IN_SCOPE_USER)
    )
    assert allocation_denied.status_code == allocation_unknown.status_code == 404
    assert allocation_denied.content == allocation_unknown.content
    assert db_session.execute(
        sa.text("SELECT count(*) FROM staffing_position_allocation WHERE position_id = :id"),
        {"id": position.id},
    ).scalar_one() == 0

    db_session.add(
        ProjectAccess(
            user_id=IN_SCOPE_USER,
            project_id=project.id,
            can_view_personnel_costs=False,
        )
    )
    db_session.flush()
    granted = client.patch(path, json=body, headers=as_caller(IN_SCOPE_USER))
    assert granted.status_code == 200, granted.text
    assert granted.json()["headcount"] == 3
    allocation_body["updated_at"] = granted.json()["updated_at"]
    allocation_granted = client.post(
        allocation_path, json=allocation_body, headers=as_caller(IN_SCOPE_USER)
    )
    assert allocation_granted.status_code == 201, allocation_granted.text


def test_k_02_authorized_month_allocation_can_be_added_edited_and_read_back(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(
        db_session, name="SC-3-09 allocation add and edit", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, project, name="Draft")
    dimensions = make_dimension_tuple(db_session, suffix=" allocation")
    position = make_staffing_position(db_session, scenario, dimensions)
    path = staffing_path(project.id, scenario.id)
    add_path = f"{path}/{position.id}/allocations"
    added = client.post(
        add_path,
        json={
            "updated_at": position.updated_at.isoformat(),
            "period_month": "2026-03-01",
            "availability_hours": "160.00",
            "planned_allocation_hours": "120.50",
            "billable_hours": "100.25",
        },
        headers=as_caller(IN_SCOPE_USER),
    )
    assert added.status_code == 201, added.text
    assert not {"fixed_amount", "fixed_amount_currency", "cost_basis", "assigned_fte"} & set(
        added.json()
    )
    added_month = added.json()["allocations"][0]
    assert added_month["period_month"] == "2026-03-01"
    assert (
        added_month["availability_hours"],
        added_month["planned_allocation_hours"],
        added_month["billable_hours"],
    ) == ("160.00", "120.50", "100.25")

    edited = client.patch(
        f"{add_path}/2026-03-01",
        json={"updated_at": added.json()["updated_at"], "planned_allocation_hours": "80.00"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert edited.status_code == 200, edited.text
    reread = client.get(path, headers=as_caller(IN_SCOPE_USER))
    assert reread.status_code == 200, reread.text
    month = reread.json()["positions"][0]["allocations"][0]
    assert month["planned_allocation_hours"] == "80.00"
    assert month["availability_hours"] == "160.00"
    assert month["billable_hours"] == "100.25"

    duplicate = client.post(
        add_path,
        json={
            "updated_at": edited.json()["updated_at"],
            "period_month": "2026-03-01",
            "availability_hours": "170.00",
            "planned_allocation_hours": "130.00",
            "billable_hours": "110.00",
        },
        headers=as_caller(IN_SCOPE_USER),
    )
    assert duplicate.status_code == 409, duplicate.text
    after_duplicate = client.get(path, headers=as_caller(IN_SCOPE_USER)).json()["positions"][0]
    assert after_duplicate["allocations"] == reread.json()["positions"][0]["allocations"]


def test_k_04_invalid_position_period_is_refused_unchanged_and_single_day_period_is_accepted(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(
        db_session, name="SC-3-09 position period", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, project, name="Draft")
    dimensions = make_dimension_tuple(db_session, suffix=" period")
    position = make_staffing_position(
        db_session,
        scenario,
        dimensions,
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 31),
    )
    path = _edit_path(project.id, scenario.id, position.id)
    initial = _stored_details(db_session, position.id)

    invalid = client.patch(
        path,
        json=_details_payload(
            position, dimensions, start_date="2026-04-30", end_date="2026-04-29"
        ),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert invalid.status_code == 422, invalid.text
    assert "end_date" in invalid.text
    assert _stored_details(db_session, position.id) == initial

    boundary = client.patch(
        path,
        json=_details_payload(
            position, dimensions, start_date="2026-04-30", end_date="2026-04-30"
        ),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert boundary.status_code == 200, boundary.text
    assert (boundary.json()["start_date"], boundary.json()["end_date"]) == (
        "2026-04-30",
        "2026-04-30",
    )

    add_path = f"{staffing_path(project.id, scenario.id)}/{position.id}/allocations"
    invalid_hours = client.post(
        add_path,
        json={
            "updated_at": boundary.json()["updated_at"],
            "period_month": "2026-04-01",
            "availability_hours": "160.001",
            "planned_allocation_hours": "0.00",
            "billable_hours": "0.00",
        },
        headers=as_caller(IN_SCOPE_USER),
    )
    assert invalid_hours.status_code == 422, invalid_hours.text
    assert "availability_hours" in invalid_hours.text
    assert db_session.execute(
        sa.text("SELECT count(*) FROM staffing_position_allocation WHERE position_id = :id"),
        {"id": position.id},
    ).scalar_one() == 0

    zero_hours = client.post(
        add_path,
        json={
            "updated_at": boundary.json()["updated_at"],
            "period_month": "2026-04-01",
            "availability_hours": "0.00",
            "planned_allocation_hours": "0.00",
            "billable_hours": "0.00",
        },
        headers=as_caller(IN_SCOPE_USER),
    )
    assert zero_hours.status_code == 201, zero_hours.text


def test_k_05_stale_position_and_allocation_markers_are_refused_independently(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(
        db_session, name="SC-3-09 stale markers", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, project, name="Draft")
    dimensions = make_dimension_tuple(db_session, suffix=" stale")
    position = make_staffing_position(db_session, scenario, dimensions, headcount=2)
    make_allocation(db_session, position, period_month=date(2026, 3, 1))
    stale = datetime(2000, 1, 1, tzinfo=UTC).isoformat()
    before = _stored_details(db_session, position.id)

    stale_position = client.patch(
        _edit_path(project.id, scenario.id, position.id),
        json=_details_payload(position, dimensions, updated_at=stale),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert stale_position.status_code == 409, stale_position.text
    assert "changed since it was read" in stale_position.json()["detail"]
    assert _stored_details(db_session, position.id) == before

    stale_allocation = client.patch(
        f"{staffing_path(project.id, scenario.id)}/{position.id}/allocations/2026-03-01",
        json={"updated_at": stale, "planned_allocation_hours": "33.00"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert stale_allocation.status_code == 409, stale_allocation.text
    assert "changed since it was read" in stale_allocation.json()["detail"]
    assert _stored_details(db_session, position.id) == before
    allocation = db_session.execute(
        sa.text(
            "SELECT planned_allocation_hours FROM staffing_position_allocation "
            "WHERE position_id = :position_id AND period_month = DATE '2026-03-01'"
        ),
        {"position_id": position.id},
    ).scalar_one()
    assert allocation == Decimal("120.00")


def test_k_06_position_details_write_to_approved_scenario_is_refused_after_draft_contrast(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(
        db_session, name="SC-3-09 approved guard", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, project, name="Draft")
    dimensions = make_dimension_tuple(db_session, suffix=" approved")
    position = make_staffing_position(db_session, scenario, dimensions, headcount=2)
    path = _edit_path(project.id, scenario.id, position.id)

    draft_edit = client.patch(
        path,
        json=_details_payload(position, dimensions, headcount=4),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert draft_edit.status_code == 200, draft_edit.text
    draft_allocation = client.post(
        f"{staffing_path(project.id, scenario.id)}/{position.id}/allocations",
        json={
            "updated_at": draft_edit.json()["updated_at"],
            "period_month": "2026-03-01",
            "availability_hours": "160.00",
            "planned_allocation_hours": "120.00",
            "billable_hours": "100.00",
        },
        headers=as_caller(IN_SCOPE_USER),
    )
    assert draft_allocation.status_code == 201, draft_allocation.text
    before = _stored_details(db_session, position.id)
    db_session.execute(
        sa.update(Scenario)
        .where(Scenario.id == scenario.id)
        .values(status=ScenarioStatus.APPROVED)
    )

    refused = client.patch(
        path,
        json=_details_payload(position, dimensions, headcount=5),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"].lower()
    assert _stored_details(db_session, position.id) == before

    allocation_refused = client.post(
        f"{staffing_path(project.id, scenario.id)}/{position.id}/allocations",
        json={
            "updated_at": draft_allocation.json()["updated_at"],
            "period_month": "2026-04-01",
            "availability_hours": "150.00",
            "planned_allocation_hours": "110.00",
            "billable_hours": "90.00",
        },
        headers=as_caller(IN_SCOPE_USER),
    )
    assert allocation_refused.status_code == 409, allocation_refused.text
    assert "approved" in allocation_refused.json()["detail"].lower()
    assert db_session.execute(
        sa.text("SELECT count(*) FROM staffing_position_allocation WHERE position_id = :id"),
        {"id": position.id},
    ).scalar_one() == 1
