"""SC-4-03, runda 2 weryfikacji, R-01 — zysk, marża i narzut nigdy z kwot w dwóch walutach.

Scenariusz **bez waluty** (`scenarios.currency IS NULL`): każdy z czterech składników porównuje
swoją walutę tylko z walutą scenariusza, więc sam z siebie niczego nie odrzuci. Jedyne miejsce,
które widzi wszystkie cztery waluty, to `app.domain.scenario_results.scenario_profitability` —
wołane przez `/results`, what-if i porównanie SC-6-02. Każdy test ma kontrast: te same dane w
jednej walucie dają liczbę, więc nazwany stan nie pochodzi z czegoś innego niż waluta.

- reguła Outcome-based w EUR + koszty w PLN → `profitability_state = currency_mismatch`, cztery pola
  `"n/a"`; przychód sam w sobie `calculated` (EUR) — to nie jego stan;
- ta sama reguła w PLN → liczby (zysk 6000.00);
- T&M: koszt dodatkowy w EUR + koszt osobowy w PLN → `currency_mismatch` (przypadek sprzed SC-4-03);
  wszystko w PLN → liczby;
- what-if i porównanie — ta sama odpowiedź.

Prawdziwy PostgreSQL, prawdziwe endpointy.
"""

import uuid
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import caller_holding, make_outcome_terms
from tests.test_scenario_results import (
    EVERYTHING,
    _ensure_statutory_bypass,
    _full_scenario,
    results_path,
)
from tests.test_scenario_results_compare import compare_path
from tests.test_scenario_what_if import what_if_path

WITHHELD = {
    "included_cost": "n/a",
    "profit": "n/a",
    "margin": "n/a",
    "markup": "n/a",
    "profitability_state": "currency_mismatch",
}
# 100 h × 200 = 20000 przychodu T&M (i gwarantowany Outcome AC-08: 20000); 100 h × 120 = 12000
# kosztu osobowego, 2000 kosztu dodatkowego, 0 nieobecności → 14000; zysk 6000; 30.00%; 42.86%.
CALCULATED = {
    "included_cost": "14000.00",
    "profit": "6000.00",
    "margin": "30.00",
    "markup": "42.86",
    "profitability_state": "calculated",
}


def _aggregate(body: dict[str, Any]) -> dict[str, Any]:
    return {field: body[field] for field in WITHHELD}


def _without_currency(session: Session, *, name: str, **kwargs: Any):
    """`_full_scenario` (stawka PLN, koszt dodatkowy 2000), potem waluta scenariusza zdjęta —
    składniki zachowują walutę swoich danych, scenariusz żadnej nie deklaruje."""
    project, scenario, _ = _full_scenario(session, name=name, **kwargs)
    scenario.currency = None
    session.flush()
    return project, scenario


def _results(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict[str, Any]:
    with caller_holding(*EVERYTHING):
        response = client.get(results_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return response.json()


def test_r_01_an_eur_outcome_rule_with_pln_costs_is_currency_mismatch_never_a_number(
    client: TestClient, db_session: Session
) -> None:
    """R-01 — reguła Outcome-based w EUR, koszty w PLN, scenariusz bez waluty: przychód 20000.00 EUR
    `calculated`, koszt osobowy i dodatkowy `calculated` w PLN — a zysk, marża, narzut i koszt
    włączony to `"n/a"` ze stanem `currency_mismatch`. Mutacja: usunięcie porównania walut w
    `scenario_profitability` → zysk `6000.00` (20000 EUR − 14000 PLN)."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="R01 outcome EUR", create_commercial_terms=False
    )
    make_outcome_terms(db_session, scenario, currency="EUR")

    body = _results(client, project.id, scenario.id)

    assert (body["revenue"]["state"], body["revenue"]["currency"]) == ("calculated", "EUR")
    assert (body["personnel_cost"]["state"], body["personnel_cost"]["currency"]) == (
        "calculated",
        "PLN",
    )
    assert (body["additional_cost"]["state"], body["additional_cost"]["currency"]) == (
        "calculated",
        "PLN",
    )
    assert _aggregate(body) == WITHHELD


def test_r_01_contrast_the_same_outcome_rule_in_pln_gives_numbers(
    client: TestClient, db_session: Session
) -> None:
    """R-01, kontrast — ta sama reguła i te same koszty, reguła w PLN: liczby i stan `calculated`.
    Dowodzi, że stan wyżej pochodzi z waluty, nie z braku waluty scenariusza ani z modelu."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="R01 outcome PLN", create_commercial_terms=False
    )
    make_outcome_terms(db_session, scenario, currency="PLN")

    assert _aggregate(_results(client, project.id, scenario.id)) == CALCULATED


def test_r_01_an_eur_additional_cost_with_pln_personnel_cost_is_currency_mismatch(
    client: TestClient, db_session: Session
) -> None:
    """R-01 — przypadek sprzed SC-4-03, T&M: koszt dodatkowy 2000 EUR obok kosztu osobowego i
    przychodu w PLN, scenariusz bez waluty → `currency_mismatch`, nie `6000.00` z sumy PLN i EUR."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="R01 TM additional EUR", additional_currency="EUR"
    )

    body = _results(client, project.id, scenario.id)

    assert body["revenue"]["state"] == "calculated"
    assert body["additional_cost"]["currency"] == "EUR"
    assert _aggregate(body) == WITHHELD


def test_r_01_contrast_tm_all_in_pln_without_a_scenario_currency_gives_numbers(
    client: TestClient, db_session: Session
) -> None:
    """R-01, kontrast — T&M, wszystko w PLN, scenariusz bez waluty: liczby jak dotąd. Dowodzi, że
    poprawka nie zamienia każdego scenariusza bez waluty w stan nazwany."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(db_session, name="R01 TM PLN")

    assert _aggregate(_results(client, project.id, scenario.id)) == CALCULATED


def test_r_01_the_what_if_and_the_comparison_answer_the_same_named_state(
    client: TestClient, db_session: Session
) -> None:
    """R-01 — what-if (SC-6-04, `+0`) i porównanie (SC-6-02) składają zysk tą samą funkcją: EUR
    reguła + PLN koszty → `currency_mismatch` w obu; wiersz PLN w tym samym porównaniu — liczby."""
    _ensure_statutory_bypass(db_session)
    project, mismatched = _without_currency(
        db_session, name="R01 what-if EUR", create_commercial_terms=False
    )
    make_outcome_terms(db_session, mismatched, currency="EUR")

    with caller_holding(*EVERYTHING):
        what_if = client.get(what_if_path(project.id, mismatched.id, "0"))
        compared = client.get(compare_path(project.id, mismatched.id))

    assert what_if.status_code == 200, what_if.text
    assert _aggregate(what_if.json()) == WITHHELD
    assert compared.status_code == 200, compared.text
    assert [_aggregate(row) for row in compared.json()["results"]] == [WITHHELD]


def test_r_01_the_state_is_not_gated_while_the_four_figures_are(
    client: TestClient, db_session: Session
) -> None:
    """R-01 — `profitability_state` nie jest liczbą, więc zostaje dla wołającego bez prawa do
    kosztów osobowych; cztery pola są `null` jak dotąd (bramka SC-7-01 bez zmian)."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="R01 gated", create_commercial_terms=False, cost_visible=False
    )
    make_outcome_terms(db_session, scenario, currency="EUR")

    body = _results(client, project.id, scenario.id)

    assert _aggregate(body) == {
        "included_cost": None,
        "profit": None,
        "margin": None,
        "markup": None,
        "profitability_state": "currency_mismatch",
    }
    assert Decimal(body["revenue"]["amount"]) == Decimal("20000.00")
