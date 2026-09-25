"""Merge SC-4-03 z SC-4-04 — `/results`, what-if i porównanie dla scenariusza Story Points po
zawężeniu strażnika wyścigu do źródeł zależnych od statusu (decyzja człowieka 2026-09-25, pkt 2;
ADR-0003, aneks 2026-09-25 SC-4-03, pkt 8).

Na `main` przed tym merge `refuse_a_status_race` porównywał `rate_source` przez samą równość, więc
przychód Story Points (`story_points_terms`) i koszt (`live_catalog`/`approved_snapshot`) zawsze
się różniły — każdy odczyt `/results`, what-if i porównania scenariusza Story Points kończył się
stałym `409` (defekt na `main`). Po merge:

- zatwierdzony scenariusz Story Points → `200`, zysk od jego przychodu (25 × 1000 = 25000);
- szkic Story Points → `200` na `/results`, what-if i porównaniu;
- zatwierdzenie wpadające między odczyt przychodu Story Points a odczyt kosztu → `200`, spójny zysk;
- **prawdziwy wyścig zatwierdzenia scenariusza T&M nadal daje `409`** — strażnik nie został
  osłabiony dla `live_catalog`/`approved_snapshot`;
- reguła Story Points w EUR + koszty w PLN (scenariusz bez waluty) → `profitability_state =
  currency_mismatch`; kontrast w PLN → liczby.

Prawdziwy PostgreSQL, prawdziwe endpointy; wyścigi na dwóch połączeniach i osobnym wątku, jak w
`tests/test_scenario_results_race.py` i `tests/test_outcome_scenario_results.py`.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.identity import Permission
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    caller_holding,
    make_absence_type,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    make_story_points_terms,
    make_working_calendar,
)
from tests.test_outcome_scenario_results import _results_with_an_approval_after
from tests.test_profitability_currency import WITHHELD, _aggregate, _without_currency
from tests.test_scenario_results import (
    EVERYTHING,
    _ensure_statutory_bypass,
    _full_scenario,
    results_path,
)
from tests.test_scenario_results_compare import compare_path
from tests.test_scenario_results_race import _committed_scenario
from tests.test_scenario_what_if import what_if_path

MAR = date(2026, 3, 1)

# 25 punktów × 1000 = 25000 przychodu Story Points; 100 h × 120 = 12000 kosztu osobowego, 2000
# kosztu dodatkowego, 0 nieobecności → 14000; zysk 11000; marża 44.00%; narzut 78.57%.
SP_CALCULATED = {
    "included_cost": "14000.00",
    "profit": "11000.00",
    "margin": "44.00",
    "markup": "78.57",
    "profitability_state": "calculated",
}


def _results(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID):
    with caller_holding(*EVERYTHING):
        return client.get(results_path(project_id, scenario_id))


def _story_points_scenario(session: Session, *, name: str):
    """`_full_scenario` (stawka PLN, koszt dodatkowy 2000) z regułą Story Points zamiast T&M."""
    _ensure_statutory_bypass(session)
    project, scenario, _ = _full_scenario(session, name=name, create_commercial_terms=False)
    make_story_points_terms(session, scenario)
    return project, scenario


def test_merge_an_approved_story_points_scenario_answers_200_with_profit_from_its_revenue(
    client: TestClient, db_session: Session
) -> None:
    """Zatwierdzony scenariusz Story Points: przychód 25000.00 (`story_points_terms`), koszt z
    migawki (`approved_snapshot`) → `200`, zysk 11000.00.

    Mutacja: strażnik porównujący `rate_source` przez samą równość (stan `main` sprzed merge) →
    `409` zamiast `200`."""
    project, scenario = _story_points_scenario(db_session, name="SP results approved")
    approval = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
    assert approval.status_code == 200, approval.text

    response = _results(client, project.id, scenario.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scenario_status"] == "Approved"
    assert (body["revenue"]["state"], body["revenue"]["amount"]) == ("calculated", "25000.00")
    assert body["revenue"]["assumptions_used"]["rate_source"] == "story_points_terms"
    assert body["personnel_cost"]["assumptions_used"]["rate_source"] == "approved_snapshot"
    assert _aggregate(body) == SP_CALCULATED


def test_merge_a_story_points_draft_answers_200_on_results_what_if_and_comparison(
    client: TestClient, db_session: Session
) -> None:
    """Szkic Story Points: przychód `story_points_terms`, koszt `live_catalog` — `/results`,
    what-if (`+10%`: 12000 → 13200) i porównanie (SC-6-02) odpowiadają `200` z liczbami, nie `409`.

    Mutacja: dowolne z trzech miejsc porównujące `rate_source` przez samą równość → `409`."""
    project, scenario = _story_points_scenario(db_session, name="SP results draft")

    with caller_holding(*EVERYTHING, Permission.PERSONNEL_COSTS_READ):
        results = client.get(results_path(project.id, scenario.id))
        what_if = client.get(what_if_path(project.id, scenario.id, "10"))
        compared = client.get(compare_path(project.id, scenario.id))

    assert results.status_code == 200, results.text
    assert results.json()["personnel_cost"]["assumptions_used"]["rate_source"] == "live_catalog"
    assert _aggregate(results.json()) == SP_CALCULATED
    assert what_if.status_code == 200, what_if.text
    assert what_if.json()["revenue"]["amount"] == "25000.00"
    assert what_if.json()["personnel_cost"]["amount"] == "13200.00"
    assert compared.status_code == 200, compared.text
    assert [_aggregate(row) for row in compared.json()["results"]] == [SP_CALCULATED]


def _committed_story_points_scenario(engine: Engine) -> dict[str, uuid.UUID]:
    """Zatwierdzony w bazie szkic Story Points z kosztem: 100 godzin planu po 120 (12000), reguła
    25 × 1000 (25000), bez kosztu dodatkowego — zysk 13000. Kształt `_committed_scenario` z
    `tests/test_scenario_results_race.py`, z regułą Story Points zamiast T&M."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(
            setup,
            name="Story Points race",
            accessible_to=(IN_SCOPE_USER,),
            cost_visible_to=(IN_SCOPE_USER,),
        )
        scenario = make_scenario(setup, project, name="Baseline", currency="PLN")
        calendar = make_working_calendar(setup, name="Story Points race calendar")
        make_absence_type(
            setup,
            name="Story Points race statutory (no cost)",
            generates_cost=False,
            generates_revenue=False,
            is_statutory_leave=True,
        )
        dimensions = make_dimension_tuple(setup, suffix=" Story Points race", calendar=calendar)
        position = make_staffing_position(setup, scenario, dimensions, start_date=MAR)
        make_allocation(
            setup,
            position,
            period_month=MAR,
            planned_allocation_hours=Decimal("100.00"),
            billable_hours=Decimal("100.00"),
        )
        make_rate(
            setup,
            dimensions,
            effective_from=date(2026, 1, 1),
            default_cost_rate=Decimal("120.0000"),
            default_selling_rate=Decimal("200.0000"),
            currency="PLN",
        )
        make_story_points_terms(setup, scenario)
        state = {"project_id": project.id, "scenario_id": scenario.id}
        setup.commit()
    return state


def test_merge_an_approval_between_the_story_points_revenue_and_the_cost_read_is_not_a_race(
    committing_client: TestClient, engine: Engine
) -> None:
    """Zatwierdzenie commitowane po zapytaniu o `story_points_terms` (odczyt przychodu), a przed
    odczytem kosztu: `200`, zysk 13000 spójny. Przychód Story Points czyta wyłącznie własny,
    strzeżony wiersz scenariusza, więc jest ten sam przed i po zatwierdzeniu."""
    state = _committed_story_points_scenario(engine)

    response, _, _ = _results_with_an_approval_after(
        committing_client, engine, state, "story_points_terms"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revenue"]["assumptions_used"]["rate_source"] == "story_points_terms"
    assert body["personnel_cost"]["assumptions_used"]["rate_source"] == "approved_snapshot"
    assert (body["revenue"]["amount"], body["personnel_cost"]["amount"], body["profit"]) == (
        "25000.00",
        "12000.00",
        "13000.00",
    )


def test_merge_a_real_tm_approval_race_between_the_two_reads_is_still_409(
    committing_client: TestClient, engine: Engine
) -> None:
    """Kontrast — prawdziwy wyścig zatwierdzenia scenariusza T&M (przychód z żywego katalogu, koszt
    z migawki) nadal `409` i żadna z liczb 20000/12000/8000. Mutacja: strażnik, który przestaje
    porównywać, gdy którekolwiek źródło spoza `STATUS_DEPENDENT_SOURCES` jest znane w systemie (albo
    wcale) → `200` z pomieszanymi połówkami."""
    state = _committed_scenario(engine)

    response, _, _ = _results_with_an_approval_after(
        committing_client, engine, state, "selling_rate"
    )

    assert response.status_code == 409, response.text
    for figure in ("20000.00", "12000.00", "8000.00"):
        assert figure not in response.text


def test_merge_an_eur_story_points_rule_with_pln_costs_is_currency_mismatch(
    client: TestClient, db_session: Session
) -> None:
    """Reguła Story Points w EUR, koszty w PLN, scenariusz bez waluty: przychód 25000.00 EUR
    `calculated`, a zysk, marża, narzut i koszt włączony `"n/a"` ze stanem `currency_mismatch`.
    Mutacja: usunięcie porównania walut w `scenario_profitability` → zysk `11000.00`."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="SP currency EUR", create_commercial_terms=False
    )
    make_story_points_terms(db_session, scenario, currency="EUR")

    response = _results(client, project.id, scenario.id)

    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert (body["revenue"]["state"], body["revenue"]["currency"]) == ("calculated", "EUR")
    assert body["personnel_cost"]["currency"] == "PLN"
    assert _aggregate(body) == WITHHELD


def test_merge_contrast_the_same_story_points_rule_in_pln_gives_numbers(
    client: TestClient, db_session: Session
) -> None:
    """Kontrast — ta sama reguła w PLN, te same koszty, scenariusz bez waluty: liczby. Dowodzi, że
    stan wyżej pochodzi z waluty reguły, nie z braku waluty scenariusza ani z modelu."""
    _ensure_statutory_bypass(db_session)
    project, scenario = _without_currency(
        db_session, name="SP currency PLN", create_commercial_terms=False
    )
    make_story_points_terms(db_session, scenario, currency="PLN")

    response = _results(client, project.id, scenario.id)

    assert response.status_code == 200, response.text
    assert _aggregate(response.json()) == SP_CALCULATED
