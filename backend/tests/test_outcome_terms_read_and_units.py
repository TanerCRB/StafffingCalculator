"""SC-4-03, runda 2 weryfikacji — parametry reguły w odczycie (R-04), jednostki opcjonalne bez
stawki za jednostkę (pkt 5) i `_details_of` bez cichego `{}` (R-02).

- **R-04**: `GET …/commercial-terms` reguły Outcome-based niesie jej parametry dokładnie tak, jak je
  zapisano (`null` za brak, nigdy `"0"`); reguła T&M ma `outcome_terms: null`.
- **pkt 5**: bez `unit_rate` jednostki można pominąć — zapis `201`, odczyt `null`; ze stawką i bez
  jednostek → `422` i zero wierszy; ta sama reguła w bazie jako `CHECK`
  `ck_outcome_terms_units_given_with_unit_rate` (zapis z pominięciem API odrzucony, kontrast
  zapisywalny).
- **R-02**: typ ciała żądania bez gałęzi w `_details_of` → `TypeError`, nie reguła bez parametrów.
"""

from dataclasses import replace as dataclass_replace
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.commercial_terms import _details_of
from app.api.schemas.commercial_terms import TimeAndMaterialTermsCreateRequest
from app.domain.revenue import RevenueResult, RevenueUnavailable
from app.domain.revenue_outcome_based import (
    OutcomeCategoryInput,
    OutcomeTermsInput,
    outcome_based_revenue,
)
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    commercial_terms_path,
    count_outcome_rows,
    make_commercial_terms,
    make_outcome_terms,
    make_project,
    make_scenario,
    outcome_payload,
)


def _scenario(session: Session, name: str = "Outcome read"):
    project = make_project(session, name=name, accessible_to=(IN_SCOPE_USER,))
    return project, make_scenario(session, project, name="Baseline", currency="PLN")


def _post(client: TestClient, project, scenario, payload: dict[str, Any]):
    return client.post(
        commercial_terms_path(project.id, scenario.id),
        json=payload,
        headers=as_caller(IN_SCOPE_USER),
    )


def _get(client: TestClient, project, scenario) -> dict[str, Any]:
    response = client.get(
        commercial_terms_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- R-04: parametry reguły w odczycie ------------------------------------------------------------


def test_r_04_the_read_carries_the_outcome_rule_parameters_as_written(
    client: TestClient, db_session: Session
) -> None:
    """R-04 — każdy parametr wraca w odczycie: kwoty jako napisy stałoprzecinkowe ze skalą kolumny,
    prawdopodobieństwa i jednostki per kategoria; nieobecne `revenue_max` to `null`, nie `"0"`.
    Odpowiedź `POST` i `GET` mówią to samo."""
    project, scenario = _scenario(db_session)
    payload = outcome_payload(
        probabilities=("10", "20", "30", "40"),
        units=("0", "5", "10", "15.5"),
        unit_rate="100",
        revenue_min="1000",
    )

    written = _post(client, project, scenario, payload)
    assert written.status_code == 201, written.text
    read = _get(client, project, scenario)

    expected = {
        "currency": "PLN",
        "fixed_fee": "20000.0000",
        "success_bonus": "10000.0000",
        "unit_rate": "100.0000",
        "revenue_min": "1000.0000",
        "revenue_max": None,
        "categories": {
            "not_achieved": {"units": "0.0000", "probability": "10.00"},
            "partial": {"units": "5.0000", "probability": "20.00"},
            "achieved": {"units": "10.0000", "probability": "30.00"},
            "exceeded": {"units": "15.5000", "probability": "40.00"},
        },
    }
    assert written.json()["commercial_terms"]["outcome_terms"] == expected
    assert read["commercial_terms"]["outcome_terms"] == expected


def test_r_04_contrast_a_tm_rule_reads_outcome_terms_as_null(
    client: TestClient, db_session: Session
) -> None:
    """R-04, kontrast — reguła T&M: jedyna zmiana jej kształtu to `outcome_terms: null`."""
    project, scenario = _scenario(db_session, "TM read")
    make_commercial_terms(db_session, scenario)

    terms = _get(client, project, scenario)["commercial_terms"]

    assert terms["model_type"] == "time_and_material"
    assert terms["outcome_terms"] is None


def test_r_04_an_outcome_rule_without_its_details_row_reads_outcome_terms_as_null(
    client: TestClient, db_session: Session
) -> None:
    """R-04 — reguła Outcome-based bez wiersza szczegółów: `outcome_terms: null` obok stanu
    `incomplete_commercial_terms`, nigdy zmyślone parametry."""
    project, scenario = _scenario(db_session, "Incomplete read")
    make_outcome_terms(db_session, scenario, with_details=False)

    body = _get(client, project, scenario)

    assert body["commercial_terms"]["outcome_terms"] is None
    assert body["revenue"]["state"] == "incomplete_commercial_terms"


# --- pkt 5: jednostki opcjonalne bez stawki za jednostkę -----------------------------------------


def test_units_may_be_omitted_without_a_unit_rate_and_read_back_as_null_never_0(
    client: TestClient, db_session: Session
) -> None:
    """Pkt 5 — bez `unit_rate` jednostki pominięte: `201`, w bazie `NULL`, w odczycie `null` — w
    parametrach reguły i w przychodach per kategoria; przychód AC-08 bez zmian (20000/30000).
    Mutacja: wartość domyślna `0` dla pominiętych jednostek → `"0.0000"` zamiast `null`."""
    project, scenario = _scenario(db_session, "Units omitted")

    written = _post(
        client, project, scenario, outcome_payload(units=(None, None, None, None))
    )

    assert written.status_code == 201, written.text
    body = _get(client, project, scenario)
    categories = body["commercial_terms"]["outcome_terms"]["categories"]
    assert {name: entry["units"] for name, entry in categories.items()} == dict.fromkeys(
        ("not_achieved", "partial", "achieved", "exceeded")
    )
    assert [entry["units"] for entry in body["revenue"]["category_revenues"]] == [None] * 4
    assert {
        entry["category"]: entry["amount"] for entry in body["revenue"]["category_revenues"]
    } == {
        "not_achieved": "20000.00",
        "partial": "20000.00",
        "achieved": "30000.00",
        "exceeded": "30000.00",
    }


def test_units_are_required_when_a_unit_rate_is_given_422_and_no_row(
    client: TestClient, db_session: Session
) -> None:
    """Pkt 5 — `unit_rate` podane, jednostki jednej kategorii pominięte → `422`, zero wierszy.
    Kontrast: ta sama reguła z kompletem jednostek → `201`."""
    project, scenario = _scenario(db_session, "Units required")

    refused = _post(
        client, project, scenario, outcome_payload(unit_rate="100", units=("1", "2", None, "4"))
    )

    assert refused.status_code == 422, refused.text
    assert "units must be given" in refused.text
    assert count_outcome_rows(db_session) == (0, 0)

    accepted = _post(
        client, project, scenario, outcome_payload(unit_rate="100", units=("1", "2", "3", "4"))
    )
    assert accepted.status_code == 201, accepted.text


def test_the_database_refuses_a_unit_rate_without_units_and_accepts_null_units_without_one(
    db_session: Session,
) -> None:
    """Pkt 5, część bazodanowa — zapis z pominięciem API: stawka za jednostkę i `NULL` w jednostkach
    odrzucone przez `ck_outcome_terms_units_given_with_unit_rate`; kontrast: `NULL` we wszystkich
    jednostkach bez stawki zapisywalny. Mutacja: kolumny `NOT NULL` bez `CHECK` — kontrast pada;
    `CHECK` usunięty — odmowa nie następuje."""
    _, scenario = _scenario(db_session, "Units DB")

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            make_outcome_terms(
                db_session, scenario, unit_rate=Decimal("100"), achieved_units=None
            )
    diagnostics = refused.value.orig.diag  # type: ignore[union-attr]
    assert (diagnostics.sqlstate, diagnostics.constraint_name) == (
        "23514",
        "ck_outcome_terms_units_given_with_unit_rate",
    )

    _, other = _scenario(db_session, "Units DB contrast")
    make_outcome_terms(
        db_session,
        other,
        unit_rate=None,
        not_achieved_units=None,
        partial_units=None,
        achieved_units=None,
        exceeded_units=None,
    )


# --- R-02: `_details_of` bez cichego `{}` ---------------------------------------------------------


def test_r_02_details_of_an_unknown_payload_type_raises_instead_of_returning_no_details() -> None:
    """R-02 — ciało żądania modelu bez gałęzi w `_details_of` (np. model dodany do unii bez niej)
    to błąd programisty: `TypeError`, nigdy `{}`, które zapisałoby regułę bez parametrów.
    Kontrast: T&M jawnie `{}`."""

    class UnknownModelTermsCreateRequest(BaseModel):
        model_type: str = "unknown_model_for_test"

    with pytest.raises(TypeError, match="No details mapping"):
        _details_of(UnknownModelTermsCreateRequest())  # type: ignore[arg-type]

    assert _details_of(TimeAndMaterialTermsCreateRequest(model_type="time_and_material")) == {}


def test_the_pure_function_never_multiplies_a_unit_rate_by_missing_units() -> None:
    """Pkt 5, warstwa dziedziny — wejście spoza bazy (stawka bez jednostek jednej kategorii) to
    `incomplete_commercial_terms`, nigdy stawka × `0`. Kontrast: bez stawki te same `None` →
    wynik."""
    categories = tuple(
        OutcomeCategoryInput(category=name, units=units, probability=None)
        for name, units in (
            ("not_achieved", Decimal("0")),
            ("partial", Decimal("1")),
            ("achieved", None),
            ("exceeded", Decimal("3")),
        )
    )
    terms = OutcomeTermsInput(
        currency="PLN",
        fixed_fee=Decimal("20000"),
        success_bonus=None,
        unit_rate=Decimal("100"),
        revenue_min=None,
        revenue_max=None,
        categories=categories,
    )

    refused = outcome_based_revenue(terms, scenario_currency="PLN")
    assert isinstance(refused, RevenueUnavailable)
    assert refused.reason == "incomplete_commercial_terms"

    without_rate = outcome_based_revenue(
        dataclass_replace(terms, unit_rate=None), scenario_currency="PLN"
    )
    assert isinstance(without_rate, RevenueResult)
    assert without_rate.revenue == Decimal("20000.00")
