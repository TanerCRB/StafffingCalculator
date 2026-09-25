"""SC-4-03 — testy kontrastowe dopisane przez QA tam, gdzie mutacja przeżyła pierwszą wersję
zestawu (K-02, K-03, K-04; ADR-0003, aneks 2026-09-25 SC-4-03, pkt 1, 2, 4, 5b).

Każdy test nazywa mutację, która przeżyła bez niego, i powód, dla którego przeżyła:

- **K-02** — "oczekiwany zaokrąglany raz, na końcu": zaokrąglenie każdego składnika `p_k · r_k`
  osobno przeżywało, bo w istniejącym przypadku tylko jeden składnik ma ułamek poniżej grosza.
- **K-03** — trzecie miejsce po przecinku: istniejący przypadek (33.333/33.333/33.334) po cichym
  zaokrągleniu `NUMERIC(5,2)` sumuje się do 99.99, więc odmawiał go także `CHECK` bazy — test
  mierzył szerszą granicę (baza), a nie węższą (schemat API). Tu wartości po zaokrągleniu dają
  dokładnie 100.00, więc bez reguły schematu baza przyjęłaby inne liczby niż wpisane.
- **K-04** — `0` wpisane przez użytkownika to wartość, nie brak składnika: ani zapis `0` jako
  `NULL`, ani odczyt maksimum `0` jako "bez maksimum" nie miały testu (istniejący test sprawdza
  tylko drugą stronę: pominięty → `NULL`).
- reguła Outcome-based bez wiersza szczegółów → `incomplete_commercial_terms`, nigdy kwota (pkt 1).

Prawdziwy PostgreSQL, prawdziwa migracja; zapis przez `POST`, odczyt przez `GET`.
"""

from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    commercial_terms_path,
    count_outcome_rows,
    make_outcome_terms,
    make_project,
    make_scenario,
    outcome_payload,
)


def _scenario(session: Session, name: str = "Outcome QA"):
    project = make_project(session, name=name, accessible_to=(IN_SCOPE_USER,))
    return project, make_scenario(session, project, name="Baseline", currency="PLN")


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


def test_k_02_the_expected_revenue_is_not_a_sum_of_separately_rounded_terms(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — opłata 100, 0.001 PLN/j., bez premii; "nieosiągnięty" i "osiągnięty" po 5 j. → oba
    r = 100.005; prawdopodobieństwa 50/0/50/0.

    Jedno zaokrąglenie na końcu: 50.0025 + 50.0025 = 100.005 → **100.01**. Mutacja "zaokrąglij każdy
    składnik `p_k · r_k`" (druga reguła zaokrąglenia): 50.00 + 50.00 → 100.00. Przeżywała test
    `test_k_02_the_expected_revenue_is_rounded_once_from_unrounded_category_revenues`, gdzie ułamek
    poniżej grosza ma tylko jeden składnik.
    """
    project, scenario = _scenario(db_session)
    payload = outcome_payload(
        fixed_fee="100",
        success_bonus=None,
        unit_rate="0.001",
        units=("5", "0", "5", "0"),
        probabilities=("50", "0", "50", "0"),
    )
    written = _create(client, project, scenario, payload)
    assert written.status_code == 201, written.text

    revenue = _revenue(client, project, scenario)

    assert _by_category(revenue) == {
        "not_achieved": "100.01",
        "partial": "100.00",
        "achieved": "100.01",
        "exceeded": "100.00",
    }
    assert (revenue["expected_state"], revenue["expected_amount"]) == ("calculated", "100.01")
    assert revenue["amount"] == "100.00"


def test_k_03_a_third_decimal_place_that_the_database_would_round_to_exactly_100_is_still_422(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — 25.001/25.001/24.999/24.999: suma wpisana to dokładnie 100.000, a po cichym
    zaokrągleniu kolumny `NUMERIC(5,2)` każda wartość to 25.00 — suma znowu 100.00, więc `CHECK`
    bazy **przyjąłby** wiersz. Odmawiać może tylko reguła "najwyżej dwa miejsca po przecinku" w
    schemacie API: `422`, zero wierszy w obu tabelach.

    Kontrast, jedna zmieniona rzecz (precyzja wpisu): 25/25/25/25 → `201`, zapisane 25.00.

    Mutacja: `decimal_places` usunięte z `Probability` — tu `201` z prawdopodobieństwami innymi niż
    wpisane. Przeżywała przypadek `third_decimal_place` w `test_outcome_revenue.py` w tym sensie, że
    tamten test czerwieniał dopiero na `409` z `CHECK` bazy, nie na regule, której dotyczy K-03.
    """
    project, scenario = _scenario(db_session)
    before = count_outcome_rows(db_session)

    refused = _create(
        client,
        project,
        scenario,
        outcome_payload(probabilities=("25.001", "25.001", "24.999", "24.999")),
    )

    assert refused.status_code == 422, refused.text
    assert count_outcome_rows(db_session) == before
    assert _revenue(client, project, scenario)["state"] == "no_commercial_terms"

    accepted = _create(
        client, project, scenario, outcome_payload(probabilities=("25", "25", "25", "25"))
    )
    assert accepted.status_code == 201, accepted.text
    assert [
        entry["probability"] for entry in accepted.json()["revenue"]["category_revenues"]
    ] == ["25.00"] * 4


def test_k_04_an_explicit_0_is_stored_as_0_and_a_maximum_of_0_bounds_the_revenue(
    client: TestClient, db_session: Session
) -> None:
    """K-04 / ADR-0003 aneks SC-4-03 pkt 2 — druga strona testu
    `test_k_04_an_absent_optional_component_is_stored_as_null_never_0`: `0` wpisane przez
    użytkownika jest w bazie `0`, nie `NULL`, a maksimum `0` ogranicza cały przychód (i
    gwarantowany) do 0.00 — nie jest "brakiem maksimum".

    Kontrast, jedna zmieniona rzecz (maksimum pominięte zamiast `0`): gwarantowany 20000.00.

    Mutacje, które bez tego testu przeżywały: `if terms.revenue_max and …` w `_bounded`
    (prawdziwość `Decimal("0")` to fałsz) → 20000.00; `payload.revenue_max or None` /
    `payload.success_bonus or None` przy zapisie → `NULL` w wierszu i 20000.00.
    """
    zeros = {"success_bonus": "0", "unit_rate": "0", "revenue_min": "0", "revenue_max": "0"}
    project, scenario = _scenario(db_session)
    written = _create(client, project, scenario, outcome_payload(**zeros))
    assert written.status_code == 201, written.text

    row = db_session.execute(
        sa.text(
            "SELECT success_bonus, unit_rate, revenue_min, revenue_max"
            " FROM outcome_terms o JOIN commercial_terms c ON c.id = o.commercial_terms_id"
            " WHERE c.scenario_id = :id"
        ),
        {"id": scenario.id},
    ).one()
    assert all(value is not None and value == 0 for value in row), tuple(row)

    revenue = _revenue(client, project, scenario)
    assert revenue["amount"] == "0.00"
    assert set(_by_category(revenue).values()) == {"0.00"}

    other_project, unbounded = _scenario(db_session, "Outcome QA contrast")
    without_max = {key: value for key, value in zeros.items() if key != "revenue_max"}
    _create(client, other_project, unbounded, outcome_payload(**without_max))
    contrast = _revenue(client, other_project, unbounded)
    assert contrast["amount"] == "20000.00"
    assert _by_category(contrast)["achieved"] == "20000.00"


def test_an_outcome_rule_without_its_details_row_is_incomplete_never_an_amount(
    client: TestClient, db_session: Session
) -> None:
    """ADR-0003 aneks SC-4-03 pkt 1 — reguła `outcome_based` bez wiersza `outcome_terms` (baza
    pilnuje typu wiersza szczegółów, nie jego istnienia): `incomplete_commercial_terms`, `"n/a"` w
    obu kwotach, żadnych przychodów kategorii. Kontrast: ta sama reguła z wierszem → `calculated`.

    Mutacja: gałąź `details is None` usunięta z `_outcome_based` (odczyt przez `scalar_one`) →
    `NoResultFound`, czyli `500` zamiast nazwanego stanu.
    """
    project, scenario = _scenario(db_session)
    make_outcome_terms(db_session, scenario, with_details=False)

    revenue = _revenue(client, project, scenario)

    assert (revenue["state"], revenue["amount"]) == ("incomplete_commercial_terms", "n/a")
    assert (revenue["expected_state"], revenue["expected_amount"]) == ("not_applicable", "n/a")
    assert revenue["category_revenues"] == []
    assert revenue["assumptions_used"]["model_type"] == "outcome_based"

    other_project, complete = _scenario(db_session, "Outcome QA complete")
    make_outcome_terms(db_session, complete)
    assert _revenue(client, other_project, complete)["state"] == "calculated"
