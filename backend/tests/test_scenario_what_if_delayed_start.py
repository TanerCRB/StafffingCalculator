"""SC-6-06: delayed-start what-if shifts staffing months without persisting schedule changes."""

import uuid
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.data.scenario_what_if as what_if_data
from app.core.identity import Permission
from app.data.rate_windows import shifted_calendar_month
from app.models import ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    caller_holding,
    make_absence,
    make_absence_type,
    make_additional_cost,
    make_allocation,
    make_commercial_terms,
    make_cost_category,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    make_story_points_terms,
    make_working_calendar,
    staffing_path,
)
from tests.test_scenario_results import (
    EVERYTHING,
    _ensure_statutory_bypass,
    results_path,
)

MAR = date(2026, 3, 1)
APR = date(2026, 4, 1)
FEB = date(2026, 2, 1)
JAN = date(2026, 1, 1)
WITHOUT_PERSONNEL_COSTS_READ = EVERYTHING - {Permission.PERSONNEL_COSTS_READ}
WITHOUT_RESULTS_READ = EVERYTHING - {Permission.RESULTS_READ}


def test_k_01_sql_month_shift_uses_calendar_month_boundary(db_session: Session) -> None:
    shifted = db_session.scalar(sa.select(shifted_calendar_month(sa.literal(FEB), 1)))
    assert shifted == MAR
    assert shifted != FEB + timedelta(days=30)


def delayed_path(project_id: uuid.UUID, scenario_id: uuid.UUID, months: str) -> str:
    return (
        f"/projects/{project_id}/scenarios/{scenario_id}/what-if/delayed-start"
        f"?delay_months={months}"
    )


def _scenario(
    session: Session,
    name: str,
    *,
    first_rate_ends: date | None = date(2026, 3, 31),
    second_rate: bool = True,
    calendar_enabled: bool = True,
    destination_cost_unit: str = "hour",
    second_position: bool = False,
    absence_period: tuple[date, date] | None = None,
    allocation_month: date = MAR,
    commercial_model: str = "time_and_material",
):
    project = make_project(
        session,
        name=name,
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario = make_scenario(session, project, name="Baseline", currency="PLN")
    calendar = (
        make_working_calendar(session, name=f"Calendar {name}") if calendar_enabled else None
    )
    dimensions = make_dimension_tuple(session, suffix=f" {name}", calendar=calendar)
    destination_month = date(
        allocation_month.year + (allocation_month.month == 12),
        allocation_month.month % 12 + 1,
        1,
    )
    position = make_staffing_position(
        session, scenario, dimensions, start_date=allocation_month
    )
    make_allocation(
        session,
        position,
        period_month=allocation_month,
        planned_allocation_hours=Decimal("100.00"),
        billable_hours=Decimal("100.00"),
    )
    if second_position:
        another_position = make_staffing_position(
            session, scenario, dimensions, start_date=allocation_month
        )
        make_allocation(
            session,
            another_position,
            period_month=allocation_month,
            planned_allocation_hours=Decimal("100.00"),
            billable_hours=Decimal("100.00"),
        )
    make_rate(
        session,
        dimensions,
        effective_from=date(2026, 1, 1),
        effective_to=first_rate_ends,
        default_cost_rate=Decimal("120.0000"),
        default_selling_rate=Decimal("200.0000"),
        currency="PLN",
    )
    if second_rate:
        make_rate(
            session,
            dimensions,
            effective_from=destination_month,
            default_cost_rate=Decimal("240.0000"),
            default_selling_rate=Decimal("260.0000"),
            currency="PLN",
            cost_rate_unit=destination_cost_unit,
        )
    if commercial_model == "story_points":
        make_story_points_terms(session, scenario)
    else:
        make_commercial_terms(session, scenario)
    category = make_cost_category(session, name=f"Travel {name}")
    make_additional_cost(
        session,
        scenario,
        category,
        amount=Decimal("2000.00"),
        start_month=MAR,
        currency="PLN",
    )
    if absence_period is not None:
        absence_type = make_absence_type(
            session,
            name=f"Leave {name}",
            generates_cost=True,
            generates_revenue=False,
        )
        make_absence(
            session,
            position,
            absence_type,
            start_date=absence_period[0],
            end_date=absence_period[1],
        )
    return project, scenario, position


def test_k_01_calendar_month_shift_zero_identity_and_destination_windows(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _position = _scenario(
        db_session, "DelayCalendarMonth", second_position=True
    )
    with caller_holding(*EVERYTHING):
        baseline = client.get(results_path(project.id, scenario.id))
        zero = client.get(delayed_path(project.id, scenario.id, "0"))
        one = client.get(delayed_path(project.id, scenario.id, "1"))
    assert baseline.status_code == zero.status_code == one.status_code == 200
    baseline_body = baseline.json()
    zero_body = zero.json()
    zero_body.pop("delay_months")
    assert zero_body == baseline_body

    shifted = one.json()
    assert shifted["delay_months"] == 1
    assert shifted["revenue"]["amount"] == "52000.00"
    assert shifted["revenue"]["assumptions_used"]["rate_windows"][0]["effective_from"] == (
        "2026-04-01"
    )
    assert shifted["personnel_cost"]["amount"] == "48000.00"
    assert shifted["personnel_cost"]["assumptions_used"]["rate_windows"][0]["effective_from"] == (
        "2026-04-01"
    )
    assert [
        period["period_month"]
        for period in shifted["additional_cost"]["assumptions_used"]["periods"]
    ] == ["2026-03-01"]


def test_k_02_shifted_result_uses_destination_inputs_and_existing_composition(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _position = _scenario(db_session, "DelayComposedResult")
    with caller_holding(*EVERYTHING):
        response = client.get(delayed_path(project.id, scenario.id, "1"))
    assert response.status_code == 200
    body = response.json()
    assert body["revenue"]["amount"] == "26000.00"
    assert body["personnel_cost"]["amount"] == "24000.00"
    assert body["additional_cost"]["amount"] == "2000.00"
    assert body["included_cost"] == "26000.00"
    assert body["profit"] == "0.00"
    assert body["margin"] == "0.00"
    assert body["markup"] == "0.00"
    assert body["profitability_state"] == "calculated"
    assert [
        period["period_month"]
        for period in body["additional_cost"]["assumptions_used"]["periods"]
    ] == ["2026-03-01"]


def test_k_02_staffing_independent_revenue_retains_existing_result_semantics(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _position = _scenario(
        db_session, "DelayStoryPoints", commercial_model="story_points"
    )
    with caller_holding(*EVERYTHING):
        response = client.get(delayed_path(project.id, scenario.id, "1"))
    assert response.status_code == 200
    body = response.json()
    assert body["revenue"]["state"] == "calculated"
    assert body["revenue"]["amount"] == "25000.00"
    assert body["revenue"]["assumptions_used"]["rate_source"] == "story_points_terms"
    assert body["personnel_cost"]["amount"] == "24000.00"
    assert body["profit"] == "-1000.00"


def test_k_01_shift_uses_calendar_month_length_not_a_fixed_day_count(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _scenario(
        db_session,
        "DelayCalendarMonthLength",
        first_rate_ends=date(2026, 1, 31),
        allocation_month=JAN,
    )
    with caller_holding(*EVERYTHING):
        response = client.get(delayed_path(project.id, scenario.id, "1"))
    assert response.status_code == 200
    assert response.json()["revenue"]["amount"] == "26000.00"
    assert response.json()["revenue"]["assumptions_used"]["rate_windows"][0][
        "effective_from"
    ] == "2026-02-01"


def test_k_05_missing_destination_rate_or_calendar_keeps_named_component_states(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _position = _scenario(
        db_session, "DelayMissingRates", first_rate_ends=date(2026, 3, 31), second_rate=False
    )
    with caller_holding(*EVERYTHING):
        inside = client.get(delayed_path(project.id, scenario.id, "0"))
        outside = client.get(delayed_path(project.id, scenario.id, "1"))
    assert inside.status_code == outside.status_code == 200
    assert inside.json()["revenue"]["state"] == "calculated"
    assert outside.json()["revenue"]["state"] == "no_rate"
    assert outside.json()["personnel_cost"]["state"] == "no_cost_rate"
    assert outside.json()["profit"] == "n/a"
    assert outside.json()["included_cost"] == "n/a"

    no_calendar_project, no_calendar_scenario, _ = _scenario(
        db_session,
        "DelayMissingCalendar",
        calendar_enabled=False,
        destination_cost_unit="month",
    )
    with caller_holding(*EVERYTHING):
        no_calendar = client.get(
            delayed_path(no_calendar_project.id, no_calendar_scenario.id, "1")
        )
    assert no_calendar.status_code == 200
    assert no_calendar.json()["personnel_cost"]["state"] == "no_calendar"
    assert no_calendar.json()["revenue"]["state"] == "calculated"


def test_k_03_saved_absence_dates_only_affect_overlapping_shifted_months(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    overlapping, overlapping_scenario, _ = _scenario(
        db_session,
        "DelayOverlapAbsence",
        absence_period=(date(2026, 4, 6), date(2026, 4, 6)),
    )
    non_overlapping, non_overlapping_scenario, _ = _scenario(
        db_session,
        "DelayNoOverlapAbsence",
        absence_period=(date(2026, 5, 4), date(2026, 5, 4)),
    )
    with caller_holding(*EVERYTHING):
        overlap = client.get(delayed_path(overlapping.id, overlapping_scenario.id, "1"))
        no_overlap = client.get(
            delayed_path(non_overlapping.id, non_overlapping_scenario.id, "1")
        )
    assert overlap.status_code == no_overlap.status_code == 200
    assert overlap.json()["personnel_cost"]["paid_absence_amount"] != "0.00"
    assert no_overlap.json()["personnel_cost"]["paid_absence_amount"] == "0.00"


def test_k_04_no_saved_allocation_or_additional_cost_period_changes(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, position = _scenario(db_session, "DelayReadOnly")
    query = sa.text(
        "SELECT period_month FROM staffing_position_allocation "
        "WHERE position_id = :position_id ORDER BY period_month"
    )
    before_db = db_session.execute(query, {"position_id": position.id}).scalars().all()
    with caller_holding(*EVERYTHING):
        results_before = client.get(results_path(project.id, scenario.id)).json()
        staffing_before = client.get(
            staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        ).json()
        what_if = client.get(delayed_path(project.id, scenario.id, "1"))
        results_after = client.get(results_path(project.id, scenario.id)).json()
        staffing_after = client.get(
            staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        ).json()
    after_db = db_session.execute(query, {"position_id": position.id}).scalars().all()
    assert what_if.status_code == 200
    assert results_after == results_before
    assert staffing_after == staffing_before
    assert after_db == before_db == [MAR]
    assert what_if.json()["additional_cost"]["assumptions_used"]["periods"][0][
        "period_month"
    ] == MAR.isoformat()


def test_k_06_scope_status_permission_gate_and_existing_race_refusal(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _scenario(db_session, "DelayAccessRace")
    path = delayed_path(project.id, scenario.id, "1")
    with caller_holding(*EVERYTHING):
        allowed = client.get(path)
    assert allowed.status_code == 200

    denied = client.get(path, headers=as_caller(OUT_OF_SCOPE_USER))
    missing = client.get(
        delayed_path(project.id, uuid.uuid4(), "1"), headers=as_caller(IN_SCOPE_USER)
    )
    approved = make_scenario(
        db_session, project, name="Approved", status=ScenarioStatus.APPROVED
    )
    approved_response = client.get(
        delayed_path(project.id, approved.id, "1"), headers=as_caller(IN_SCOPE_USER)
    )
    with caller_holding(*WITHOUT_PERSONNEL_COSTS_READ):
        gated = client.get(path)
    with caller_holding(*WITHOUT_RESULTS_READ):
        permission_denied = client.get(path)
    invalid_negative = client.get(
        delayed_path(project.id, scenario.id, "-1"), headers=as_caller(IN_SCOPE_USER)
    )
    invalid_fraction = client.get(
        delayed_path(project.id, scenario.id, "1.5"), headers=as_caller(IN_SCOPE_USER)
    )
    assert denied.status_code == missing.status_code == approved_response.status_code == 404
    assert denied.json() == missing.json() == approved_response.json() == {
        "detail": "Scenario not found."
    }
    assert gated.status_code == 200
    assert gated.json()["personnel_cost"]["amount"] is None
    assert gated.json()["profit"] is None
    assert permission_denied.status_code == 403
    assert invalid_negative.status_code == invalid_fraction.status_code == 422

    real_cost_read = what_if_data.scenario_cost_for_caller

    def disagreeing_cost_read(*args, **kwargs):
        view = real_cost_read(*args, **kwargs)
        assert view is not None
        return replace(view, status_at_read=ScenarioStatus.APPROVED)

    monkeypatch.setattr(what_if_data, "scenario_cost_for_caller", disagreeing_cost_read)
    with caller_holding(*EVERYTHING):
        raced = client.get(path)
    assert raced.status_code == 409
    assert raced.json() == {
        "detail": "The scenario's approval status changed while its result was being computed. "
        "Retry the request."
    }


def test_delay_outside_supported_date_range_returns_client_error(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _scenario(db_session, "DelayOutOfRange")
    with caller_holding(*EVERYTHING):
        response = client.get(delayed_path(project.id, scenario.id, "119988"))
    assert response.status_code == 422
