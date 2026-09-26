"""SC-4-03, K-01..K-04 — przychód Outcome-based scenariusza przez prawdziwe endpointy (F-06.3,
AC-08).

- **K-01** AC-08: opłata 20000 PLN + premia 10000 PLN → 20000 dla "nieosiągnięty" i "częściowy",
  30000 dla "osiągnięty"; reguła w walucie innej niż waluta scenariusza → `currency_mismatch`, bez
  kwoty w żadnym polu przychodu.
- **K-02** gwarantowany i oczekiwany to dwie wartości: prawdopodobieństwa zmieniają wyłącznie
  oczekiwany (70/30 → 20000/23000, 10/90 → 20000/29000); brak prawdopodobieństw → nazwany stan,
  nigdy `0` ani kopia gwarantowanego; oczekiwany zaokrąglany raz, na końcu.
- **K-03** zestaw prawdopodobieństw o sumie ≠ 100.00, niepełny albo z trzecim miejscem po przecinku
  →
  `422`, zero wierszy; kontrast 33.34/33.33/33.33 → `201`. (Egzekwowanie sumy przez `CHECK` bazy —
  `tests/test_outcome_terms_schema.py`.)
- **K-04** stawka za jednostkę i min/max ograniczają cały przychód kategorii i gwarantowany; `min >
  max` → `422`, zero wierszy.

Prawdziwy PostgreSQL, prawdziwa migracja (ADR-0001). Każdy zapis przez `POST`, każdy odczyt przez
`GET` — wynik jest tym, co widzi klient.
"""

import json
from datetime import date
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    commercial_terms_path,
    count_outcome_rows,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    outcome_payload,
)

MAR = date(2026, 3, 1)


def _scenario(session: Session, *, currency: str | None = "PLN"):
    project = make_project(session, name="Outcome", accessible_to=(IN_SCOPE_USER,))
    return project, make_scenario(session, project, name="Baseline", currency=currency)


def _create(client: TestClient, project, scenario, payload: dict[str, Any]):
    return client.post(
        commercial_terms_path(project.id, scenario.id),
        json=payload,
        headers=as_caller(IN_SCOPE_USER),
    )


def _revenue(client: TestClient, project, scenario) -> dict[str, Any]:
    response = client.get(
        commercial_terms_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    return response.json()["revenue"]


def _by_category(revenue: dict[str, Any]) -> dict[str, str]:
    return {entry["category"]: entry["amount"] for entry in revenue["category_revenues"]}


# --- K-01: AC-08 i waluta reguły ------------------------------------------------------------------


def test_k_01_ac_08_the_bonus_is_paid_for_achieved_and_not_for_not_achieved_or_partial(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — AC-08 dosłownie: 20000 (nieosiągnięty), 20000 (częściowy), 30000 (osiągnięty).

    Odpowiedź `POST` i późniejszy `GET` niosą ten sam wynik (zapis i odczyt to ta sama ścieżka
    wyliczenia). "Przekroczony" też dostaje premię (ADR-0003, aneks SC-4-03, pkt 2). Przychód
    gwarantowany (`amount`) to opłata stała — premia nie jest gwarantowana.

    Mutacje: premia dla "częściowy" (20000 → 30000 w tym polu); premia pominięta dla "osiągnięty"
    (30000 → 20000); premia wliczona do gwarantowanego (`amount` 30000).
    """
    project, scenario = _scenario(db_session)

    written = _create(client, project, scenario, outcome_payload())
    assert written.status_code == 201, written.text
    revenue = _revenue(client, project, scenario)

    assert written.json()["revenue"] == revenue
    assert written.json()["commercial_terms"]["model_type"] == "outcome_based"
    assert (revenue["state"], revenue["amount"], revenue["currency"]) == (
        "calculated",
        "20000.00",
        "PLN",
    )
    assert _by_category(revenue) == {
        "not_achieved": "20000.00",
        "partial": "20000.00",
        "achieved": "30000.00",
        "exceeded": "30000.00",
    }


def test_k_01_a_rule_in_another_currency_than_the_scenario_is_currency_mismatch_with_no_amount(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — reguła w EUR, scenariusz w PLN: `currency_mismatch`, `"n/a"` w obu kwotach, brak
    przychodów kategorii i żadna z liczb reguły nigdzie w odpowiedzi (O-4).

    Kontrast w tym samym teście: ta sama reguła w EUR w scenariuszu **bez** waluty → `calculated`
    w EUR — więc odmowa dotyczy niezgodności, nie waluty EUR. Mutacje: przeliczenie 1:1 (kwota w
    PLN), porównanie pominięte (kwota w EUR mimo PLN scenariusza).
    """
    project, scenario = _scenario(db_session, currency="PLN")
    written = _create(client, project, scenario, outcome_payload(currency="EUR"))
    assert written.status_code == 201, written.text

    response = client.get(
        commercial_terms_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    revenue = response.json()["revenue"]
    assert revenue["state"] == "currency_mismatch"
    assert (revenue["amount"], revenue["currency"]) == ("n/a", None)
    assert (revenue["expected_state"], revenue["expected_amount"]) == ("not_applicable", "n/a")
    assert revenue["category_revenues"] == []
    assert revenue["assumptions_used"]["currencies"] == ["EUR"]
    # Parametry reguły (`commercial_terms.outcome_terms`, ADR-0003 pkt 11) legalnie niosą 20000;
    # kryterium: żadne pole *przychodu* nie niesie kwoty (O-4).
    assert "20000" not in json.dumps(revenue)
    assert "30000" not in json.dumps(revenue)

    other_project, no_currency = _scenario(db_session, currency=None)
    _create(client, other_project, no_currency, outcome_payload(currency="EUR"))
    contrast = _revenue(client, other_project, no_currency)
    assert (contrast["state"], contrast["amount"], contrast["currency"]) == (
        "calculated",
        "20000.00",
        "EUR",
    )


def test_k_01_the_outcome_revenue_names_no_catalogue_hours_or_vendor_source(
    client: TestClient, db_session: Session
) -> None:
    """K-01, założenia (F-06.5; ADR-0003, aneks SC-4-03, pkt 8): `rate_source`, `hours_source` i
    `vendor_axis` to `not_applicable` — wyliczenie nie czyta katalogu, godzin ani poddostawcy, więc
    ich nie nazywa. Brak okien stawek i miesięcy."""
    project, scenario = _scenario(db_session)
    _create(client, project, scenario, outcome_payload())

    assumptions = _revenue(client, project, scenario)["assumptions_used"]

    assert assumptions == {
        "model_type": "outcome_based",
        "hours_source": "not_applicable",
        "vendor_axis": "not_applicable",
        "rate_source": "not_applicable",
        "rate_windows": [],
        "unresolved_months": [],
        "currencies": ["PLN"],
    }


def test_k_01_the_outcome_read_touches_no_staffing_allocation_or_catalogue_table(
    client: TestClient, db_session: Session, engine: Engine
) -> None:
    """K-01, niezależność wyliczenia (F-06; zadanie: moduł outcome nie czyta obsady, alokacji ani
    katalogu). Scenariusz **ma** pozycję, miesiąc alokacji i stawkę katalogu, które T&M by wyceniło;
    odczyt reguły outcome nie wysyła ani jednej instrukcji dotykającej tych tabel ani migawki
    stawek, a wynik jest AC-08, nie godzinami × stawką.

    Kontrast: ten sam nasłuch przy odczycie scenariusza T&M widzi `staffing_position_allocation` —
    więc nasłuch naprawdę łapie instrukcje tej ścieżki.
    """
    project, scenario = _scenario(db_session)
    dimensions = make_dimension_tuple(db_session, suffix=" Outcome")
    position = make_staffing_position(db_session, scenario, dimensions)
    make_allocation(db_session, position, period_month=MAR)
    make_rate(db_session, dimensions, effective_from=date(2026, 1, 1), currency="PLN")
    _create(client, project, scenario, outcome_payload())
    forbidden = (
        "staffing_position",
        "catalog_default_rates",
        "approved_snapshot_catalog_default_rate",
    )

    def statements_of_a_read(target) -> list[str]:
        seen: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany) -> None:
            seen.append(statement.lower())

        event.listen(Engine, "before_cursor_execute", record)
        try:
            response = client.get(
                commercial_terms_path(target[0].id, target[1].id),
                headers=as_caller(IN_SCOPE_USER),
            )
        finally:
            event.remove(Engine, "before_cursor_execute", record)
        assert response.status_code == 200, response.text
        return seen

    outcome_statements = statements_of_a_read((project, scenario))
    assert any("outcome_terms" in statement for statement in outcome_statements)
    assert not [s for s in outcome_statements if any(table in s for table in forbidden)]
    assert _revenue(client, project, scenario)["amount"] == "20000.00"

    tm_project, tm_scenario = _scenario(db_session)
    tm_position = make_staffing_position(db_session, tm_scenario, dimensions)
    make_allocation(db_session, tm_position, period_month=MAR)
    _create(client, tm_project, tm_scenario, {"model_type": "time_and_material"})
    tm_statements = statements_of_a_read((tm_project, tm_scenario))
    assert any("staffing_position_allocation" in statement for statement in tm_statements)


# --- K-02: gwarantowany i oczekiwany --------------------------------------------------------------


@pytest.mark.parametrize(
    ("probabilities", "expected"),
    [
        (("70", "0", "30", "0"), "23000.00"),
        (("10", "0", "90", "0"), "29000.00"),
    ],
    ids=["70_30", "10_90"],
)
def test_k_02_probabilities_change_only_the_expected_revenue(
    client: TestClient,
    db_session: Session,
    probabilities: tuple[str, str, str, str],
    expected: str,
) -> None:
    """K-02 — przy tej samej regule AC-08 gwarantowany zostaje 20000, oczekiwany to Σ p·r.

    70% × 20000 + 30% × 30000 = 23000; 10% × 20000 + 90% × 30000 = 29000. Mutacje: oczekiwany w
    `amount` (gwarantowany 23000/29000 — a zysk `/results` liczony od niego); prawdopodobieństwa
    brane jako ułamki, nie procenty (2 300 000); przychód kategorii bez premii (20000).
    """
    project, scenario = _scenario(db_session)
    written = _create(client, project, scenario, outcome_payload(probabilities=probabilities))
    assert written.status_code == 201, written.text

    revenue = _revenue(client, project, scenario)

    assert (revenue["state"], revenue["amount"]) == ("calculated", "20000.00")
    assert (revenue["expected_state"], revenue["expected_amount"]) == ("calculated", expected)
    assert [entry["probability"] for entry in revenue["category_revenues"]] == [
        f"{p}.00" if "." not in p else p for p in probabilities
    ]


def test_k_02_without_probabilities_the_expected_revenue_is_a_named_state_not_0_nor_guaranteed(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — brak prawdopodobieństw: `expected_state = no_probabilities`, `expected_amount = "n/a"`
    — nigdy `"0.00"` i nigdy `"20000.00"` (kopia gwarantowanego). Gwarantowany i per kategoria są
    nadal podane (ADR-0003, aneks SC-4-03, pkt 5c); prawdopodobieństwo kategorii to `null`, nie
    `0`."""
    project, scenario = _scenario(db_session)
    _create(client, project, scenario, outcome_payload())

    revenue = _revenue(client, project, scenario)

    assert (revenue["state"], revenue["amount"]) == ("calculated", "20000.00")
    assert revenue["expected_state"] == "no_probabilities"
    assert revenue["expected_amount"] == "n/a"
    assert revenue["expected_amount"] not in ("0.00", "20000.00")
    assert [entry["probability"] for entry in revenue["category_revenues"]] == [None] * 4
    assert len(revenue["category_revenues"]) == 4


def test_k_02_the_expected_revenue_is_rounded_once_from_unrounded_category_revenues(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — "zaokrąglany raz, na końcu" na liczbach, które to rozróżniają.

    Opłata 100, 0.001 PLN/j., "osiągnięty" 5 j. → r = 100.005 (pokazany jako 100.01), pozostałe 100;
    prawdopodobieństwa 50/0/50/0. Z wartości niezaokrąglonych: 50 + 50.0025 = 100.0025 → **100.00**.
    Mutacja "oczekiwany z zaokrąglonych przychodów kategorii": 50 + 50.005 = 100.005 → 100.01.
    """
    project, scenario = _scenario(db_session)
    payload = outcome_payload(
        fixed_fee="100",
        success_bonus=None,
        unit_rate="0.001",
        units=("0", "0", "5", "0"),
        probabilities=("50", "0", "50", "0"),
    )
    written = _create(client, project, scenario, payload)
    assert written.status_code == 201, written.text

    revenue = _revenue(client, project, scenario)

    assert _by_category(revenue)["achieved"] == "100.01"
    assert revenue["expected_amount"] == "100.00"
    assert revenue["amount"] == "100.00"


# --- K-03: prawdopodobieństwa odrzucane bez zapisu ------------------------------------------------


@pytest.mark.parametrize(
    "probabilities",
    [
        ("33.33", "33.33", "33.33", "0"),
        ("50", "50", None, None),
        ("33.333", "33.333", "33.334", "0"),
    ],
    ids=["sum_99_99", "incomplete", "third_decimal_place"],
)
def test_k_03_a_probability_set_not_summing_to_exactly_100_is_422_and_writes_no_row(
    client: TestClient, db_session: Session, probabilities: tuple[str | None, ...]
) -> None:
    """K-03 — suma 99.99, zestaw niepełny i trzecie miejsce po przecinku (suma dokładnie 100.000!):
    `422`, zero wierszy w `commercial_terms` i `outcome_terms`, a odczyt nadal mówi
    `no_commercial_terms`. Trzeci przypadek to zaokrąglenie po cichu, którego nie wolno zrobić
    (ADR-0003, aneks SC-4-03, pkt 4) — mutacja "zaokrąglij do 2 miejsc" daje sumę 99.99 albo
    przyjęcie
    wartości innych niż wpisane."""
    project, scenario = _scenario(db_session)
    before = count_outcome_rows(db_session)

    refused = _create(client, project, scenario, outcome_payload(probabilities=probabilities))

    assert refused.status_code == 422, refused.text
    assert count_outcome_rows(db_session) == before
    assert _revenue(client, project, scenario)["state"] == "no_commercial_terms"


def test_k_03_contrast_33_34_33_33_33_33_is_accepted(
    client: TestClient, db_session: Session
) -> None:
    """K-03, kontrast — 33.34/33.33/33.33/0.00 sumuje się dokładnie do 100.00: `201`, obie tabele
    mają wiersz, a prawdopodobieństwa wracają dokładnie tak, jak je wpisano."""
    project, scenario = _scenario(db_session)
    before = count_outcome_rows(db_session)

    accepted = _create(
        client, project, scenario, outcome_payload(probabilities=("33.34", "33.33", "33.33", "0"))
    )

    assert accepted.status_code == 201, accepted.text
    assert count_outcome_rows(db_session) == (before[0] + 1, before[1] + 1)
    revenue = accepted.json()["revenue"]
    assert [entry["probability"] for entry in revenue["category_revenues"]] == [
        "33.34",
        "33.33",
        "33.33",
        "0.00",
    ]
    # 0.3334·20000 + 0.3333·20000 + 0.3333·30000 = 6668 + 6666 + 9999 = 23333.00
    assert revenue["expected_amount"] == "23333.00"


# --- K-04: stawka za jednostkę i min/max --------------------------------------------------------


def test_k_04_min_and_max_bound_the_whole_category_revenue_and_the_guaranteed_revenue(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — opłata 20000, 100 PLN/j., min 22000, max 30000, bez premii: 0 j. → 22000 (min),
    50 j. → 25000, 150 j. → 30000 (max), gwarantowany 22000 (min działa na samą opłatę, pkt 6).

    Mutacje: ograniczenie tylko składnika zmiennego (0 j. → 20000, 150 j. → 35000 albo 50000);
    gwarantowany bez ograniczenia (20000); `min`/`max` zamienione miejscami.
    """
    project, scenario = _scenario(db_session)
    payload = outcome_payload(
        success_bonus=None,
        unit_rate="100",
        revenue_min="22000",
        revenue_max="30000",
        units=("0", "50", "150", "150"),
    )
    written = _create(client, project, scenario, payload)
    assert written.status_code == 201, written.text

    revenue = _revenue(client, project, scenario)

    assert _by_category(revenue) == {
        "not_achieved": "22000.00",
        "partial": "25000.00",
        "achieved": "30000.00",
        "exceeded": "30000.00",
    }
    assert revenue["amount"] == "22000.00"


def test_k_04_contrast_without_a_maximum_150_units_are_35000(
    client: TestClient, db_session: Session
) -> None:
    """K-04, kontrast — bez `revenue_max` 150 j. to 35000: 30000 w teście wyżej pochodzi z maksimum,
    nie z formuły. Brak maksimum to `null`, nigdy "ogranicz do 0"."""
    project, scenario = _scenario(db_session)
    payload = outcome_payload(
        success_bonus=None,
        unit_rate="100",
        revenue_min="22000",
        units=("0", "50", "150", "150"),
    )
    _create(client, project, scenario, payload)

    revenue = _revenue(client, project, scenario)

    assert _by_category(revenue)["achieved"] == "35000.00"
    assert _by_category(revenue)["not_achieved"] == "22000.00"


def test_k_04_min_greater_than_max_is_422_and_writes_no_row(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — min 40000 > max 30000: `422`, zero wierszy. (Ta sama reguła jako `CHECK` bazy —
    `tests/test_outcome_terms_schema.py`.)"""
    project, scenario = _scenario(db_session)
    before = count_outcome_rows(db_session)

    refused = _create(
        client,
        project,
        scenario,
        outcome_payload(unit_rate="100", revenue_min="40000", revenue_max="30000"),
    )

    assert refused.status_code == 422, refused.text
    assert "revenue_min" in refused.text
    assert count_outcome_rows(db_session) == before


def test_k_04_an_absent_optional_component_is_stored_as_null_never_0(
    client: TestClient, db_session: Session
) -> None:
    """ADR-0003, aneks SC-4-03, pkt 2 — składnik pominięty w żądaniu jest w bazie `NULL`, nie `0`
    (inaczej "bez maksimum" zapisałoby się jako "ogranicz do zera")."""
    project, scenario = _scenario(db_session)
    _create(client, project, scenario, outcome_payload(success_bonus=None))

    row = db_session.execute(
        sa.text(
            "SELECT success_bonus, unit_rate, revenue_min, revenue_max, achieved_probability"
            " FROM outcome_terms o JOIN commercial_terms c ON c.id = o.commercial_terms_id"
            " WHERE c.scenario_id = :id"
        ),
        {"id": scenario.id},
    ).one()

    assert tuple(row) == (None, None, None, None, None)
