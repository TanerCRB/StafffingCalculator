"""SC-7-09 — expected profit and margin for Outcome-based scenario results."""

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from tests.conftest import (
    IN_SCOPE_USER,
    caller_holding,
    make_fixed_price_terms,
    make_outcome_terms,
    make_project,
    make_story_points_terms,
)
from tests.test_scenario_results import (
    EVERYTHING,
    _ensure_statutory_bypass,
    _full_scenario,
    results_path,
)
from tests.test_scenario_results_compare import _scenario_in_project, compare_path
from tests.test_scenario_what_if import what_if_path

PROBABILITIES_70_30 = {
    "not_achieved_probability": Decimal("70"),
    "partial_probability": Decimal("0"),
    "achieved_probability": Decimal("30"),
    "exceeded_probability": Decimal("0"),
}


def _results(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    with caller_holding(*EVERYTHING):
        response = client.get(results_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return response.json()


def test_k_01_expected_profit_and_margin_use_expected_revenue_and_included_cost(
    client: TestClient, db_session: Session
) -> None:
    """Expected profit uses the expected amount; guaranteed profit remains based on the fee."""
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(
        db_session,
        name="Expected profitability K01",
        create_commercial_terms=False,
        additional_amount=Decimal("9000.00"),
    )
    make_outcome_terms(db_session, scenario, **PROBABILITIES_70_30)
    contrast_project, contrast_scenario, _ = _full_scenario(
        db_session,
        name="Expected profitability K01 contrast",
        create_commercial_terms=False,
        additional_amount=Decimal("9000.00"),
    )
    make_outcome_terms(
        db_session,
        contrast_scenario,
        not_achieved_probability=Decimal("90"),
        partial_probability=Decimal("0"),
        achieved_probability=Decimal("10"),
        exceeded_probability=Decimal("0"),
    )

    body = _results(client, project.id, scenario.id)
    contrast = _results(client, contrast_project.id, contrast_scenario.id)

    assert body["revenue"]["expected_amount"] == "23000.00"
    assert body["included_cost"] == "21000.00"
    assert (body["profit"], body["expected_profit"], body["expected_margin"]) == (
        "-1000.00",
        "2000.00",
        "8.70",
    )
    assert contrast["revenue"]["expected_amount"] == "21000.00"
    assert (contrast["profit"], contrast["expected_profit"], contrast["expected_margin"]) == (
        "-1000.00",
        "0.00",
        "0.00",
    )


def test_k_02_missing_probabilities_keep_expected_metrics_not_applicable(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(
        db_session, name="Expected profitability K02", create_commercial_terms=False
    )
    make_outcome_terms(db_session, scenario)
    contrast_project, contrast_scenario, _ = _full_scenario(
        db_session, name="Expected profitability K02 contrast", create_commercial_terms=False
    )
    make_outcome_terms(db_session, contrast_scenario, **PROBABILITIES_70_30)

    body = _results(client, project.id, scenario.id)
    contrast = _results(client, contrast_project.id, contrast_scenario.id)

    assert body["revenue"]["expected_state"] == "no_probabilities"
    assert (body["expected_profit"], body["expected_margin"]) == ("n/a", "n/a")
    assert body["profit"] == "6000.00"
    assert contrast["revenue"]["expected_state"] == "calculated"
    assert contrast["expected_profit"] == "9000.00"


def test_k_03_models_without_expected_revenue_return_not_applicable_fields(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    tm_project, tm_scenario, _ = _full_scenario(db_session, name="Expected profitability T&M")
    sp_project, sp_scenario, _ = _full_scenario(
        db_session,
        name="Expected profitability Story Points",
        create_commercial_terms=False,
    )
    make_story_points_terms(db_session, sp_scenario)
    fp_project, fp_scenario, _ = _full_scenario(
        db_session,
        name="Expected profitability Fixed Price",
        create_commercial_terms=False,
    )
    make_fixed_price_terms(db_session, fp_scenario)
    outcome_project, outcome_scenario, _ = _full_scenario(
        db_session,
        name="Expected profitability Outcome",
        create_commercial_terms=False,
    )
    make_outcome_terms(db_session, outcome_scenario, **PROBABILITIES_70_30)

    tm = _results(client, tm_project.id, tm_scenario.id)
    sp = _results(client, sp_project.id, sp_scenario.id)
    fp = _results(client, fp_project.id, fp_scenario.id)
    outcome = _results(client, outcome_project.id, outcome_scenario.id)

    for body in (tm, sp, fp):
        assert body["revenue"]["expected_state"] == "not_applicable"
        assert (body["expected_profit"], body["expected_margin"]) == ("n/a", "n/a")
        assert body["expected_profit"] != body["profit"]
    assert outcome["revenue"]["expected_state"] == "calculated"
    assert outcome["expected_profit"] == "9000.00"


def test_k_04_expected_fields_follow_the_existing_personnel_cost_gate(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(
        db_session, name="Expected profitability K04", create_commercial_terms=False
    )
    make_outcome_terms(db_session, scenario, **PROBABILITIES_70_30)
    path = results_path(project.id, scenario.id)

    with caller_holding(*(EVERYTHING - {Permission.PERSONNEL_COSTS_READ})):
        denied = client.get(path)
    with caller_holding(*EVERYTHING):
        allowed = client.get(path)

    assert denied.status_code == allowed.status_code == 200
    denied_body = denied.json()
    assert denied_body["expected_profit"] is None
    assert denied_body["expected_margin"] is None
    assert denied_body["profit"] is None
    assert denied_body["margin"] is None
    allowed_body = allowed.json()
    assert allowed_body["expected_profit"] == "9000.00"
    assert allowed_body["expected_margin"] == "39.13"


def test_k_05_currency_mismatch_withholds_expected_metrics(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(
        db_session,
        name="Expected profitability K05 mismatch",
        create_commercial_terms=False,
    )
    scenario.currency = None
    db_session.flush()
    make_outcome_terms(db_session, scenario, currency="EUR", **PROBABILITIES_70_30)
    match_project, match_scenario, _ = _full_scenario(
        db_session,
        name="Expected profitability K05 match",
        create_commercial_terms=False,
    )
    make_outcome_terms(db_session, match_scenario, **PROBABILITIES_70_30)

    mismatch = _results(client, project.id, scenario.id)
    matched = _results(client, match_project.id, match_scenario.id)

    assert mismatch["revenue"]["state"] == "calculated"
    assert mismatch["profitability_state"] == "currency_mismatch"
    assert (mismatch["expected_profit"], mismatch["expected_margin"]) == ("n/a", "n/a")
    assert matched["profitability_state"] == "calculated"
    assert matched["expected_profit"] == "9000.00"


def test_k_06_zero_expected_revenue_keeps_profit_numeric_and_margin_not_applicable(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(
        db_session,
        name="Expected profitability K06 zero",
        create_commercial_terms=False,
    )
    make_outcome_terms(
        db_session,
        scenario,
        fixed_fee=Decimal("0"),
        success_bonus=Decimal("0"),
        not_achieved_probability=Decimal("70"),
        partial_probability=Decimal("0"),
        achieved_probability=Decimal("30"),
        exceeded_probability=Decimal("0"),
    )
    contrast_project, contrast_scenario, _ = _full_scenario(
        db_session,
        name="Expected profitability K06 nonzero",
        create_commercial_terms=False,
    )
    make_outcome_terms(db_session, contrast_scenario, **PROBABILITIES_70_30)

    zero = _results(client, project.id, scenario.id)
    nonzero = _results(client, contrast_project.id, contrast_scenario.id)

    assert zero["revenue"]["expected_amount"] == "0.00"
    assert zero["expected_profit"] == "-14000.00"
    assert zero["expected_margin"] == "n/a"
    assert nonzero["expected_margin"] == "39.13"


def test_k_07_compare_composes_expected_fields_per_scenario(
    client: TestClient, db_session: Session
) -> None:
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session,
        name="Expected profitability K07 comparison",
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    outcome_scenario = _scenario_in_project(
        db_session,
        project,
        name="Outcome",
        additional_amount=Decimal("9000.00"),
        create_commercial_terms=False,
    )
    make_outcome_terms(db_session, outcome_scenario, **PROBABILITIES_70_30)
    tm_scenario = _scenario_in_project(db_session, project, name="T&M")

    with caller_holding(*EVERYTHING):
        outcome_response = client.get(results_path(project.id, outcome_scenario.id))
        comparison = client.get(compare_path(project.id, outcome_scenario.id, tm_scenario.id))
        what_if = client.get(what_if_path(project.id, outcome_scenario.id, "0"))

    assert outcome_response.status_code == comparison.status_code == what_if.status_code == 200
    assert "expected_profit" not in what_if.json()
    assert "expected_margin" not in what_if.json()
    rows = comparison.json()["results"]
    assert rows[0] == outcome_response.json()
    assert (rows[0]["expected_profit"], rows[0]["expected_margin"]) == ("2000.00", "8.70")
    assert (rows[1]["expected_profit"], rows[1]["expected_margin"]) == ("n/a", "n/a")
