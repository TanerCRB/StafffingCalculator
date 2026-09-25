"""SC-4-03, runda 2 weryfikacji — testy QA domykające dowód R-01 i pkt 5 (jednostki).

Każdy test jest kontrastem: ta sama sytuacja z jedną zmienioną rzeczą i odwróconym wynikiem.

- **R-01, każde źródło kosztu osobno**: `scenario_profitability` z czterema `calculated`
  składnikami, z których **dokładnie jeden** ma inną walutę — po kolei przychód, koszt bazowy,
  koszt nieobecności płatnych, koszt dodatkowy → `currency_mismatch`; te same cztery w jednej
  walucie → liczby. Testy endpointowe (`tests/test_profitability_currency.py`) różnicują tylko
  przychód i koszt dodatkowy. Waluty kosztu nieobecności przez endpoint nie da się rozjechać z
  walutą kosztu bazowego: dostaje on stawki miesięcy kosztu bazowego (`MonthCostRate`,
  `app.data.paid_absence_cost`), a koszt bazowy w kilku walutach sam jest `currency_mismatch` —
  więc udział tego źródła w porównaniu dowodzi się tutaj, na czystej funkcji (obrona w głąb, nie
  scenariusz osiągalny dziś).
- **R-01, kolejność reguł**: składnik nie `calculated` → `not_applicable`, nawet gdy pozostałe mają
  różne waluty (brak liczby wygrywa z niezgodnością walut); przez endpoint — scenariusz bez reguły
  handlowej ma `profitability_state = not_applicable`, ten sam scenariusz z regułą — `calculated`.
- **Pkt 5, model i baza**: zestaw nazw `CHECK` tabeli `outcome_terms` w bazie (po migracji) równy
  zestawowi z modelu — usunięcie `ck_outcome_terms_units_given_with_unit_rate` tylko z modelu albo
  tylko z migracji jest widoczne.
- **R-04, `revenue_max` w odczycie**: reguła z górną granicą, bez dolnej — lustro testu dewelopera.
"""

from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.additional_cost import AdditionalCostResult
from app.domain.paid_absence_cost import PaidAbsenceCostResult, PaidAbsenceCostUnavailable
from app.domain.personnel_cost import PersonnelCostResult
from app.domain.revenue import RevenueResult
from app.domain.scenario_results import scenario_profitability
from app.models.commercial_terms import OutcomeTerms
from tests.conftest import caller_holding, outcome_payload
from tests.test_outcome_terms_read_and_units import _get, _post, _scenario
from tests.test_scenario_results import (
    EVERYTHING,
    _ensure_statutory_bypass,
    _full_scenario,
    results_path,
)

_ASSUMPTIONS: object = object()
"""`scenario_profitability` nie czyta `assumptions_used` — zaślepka, nie zmyślone założenia."""

_SOURCES = ("revenue", "base_cost", "paid_absence", "additional_cost")


def _components(**currencies: str) -> dict[str, object]:
    """Cztery `calculated` składniki: przychód 20000, koszt bazowy 12000, nieobecności 500, koszt
    dodatkowy 1500 — każdy w PLN, chyba że `currencies` mówi inaczej."""
    currency = {source: currencies.get(source, "PLN") for source in _SOURCES}
    return {
        "revenue": RevenueResult(
            revenue=Decimal("20000.00"),
            currency=currency["revenue"],
            assumptions_used=_ASSUMPTIONS,  # type: ignore[arg-type]
        ),
        "base_cost": PersonnelCostResult(
            cost=Decimal("12000.00"),
            currency=currency["base_cost"],
            assumptions_used=_ASSUMPTIONS,  # type: ignore[arg-type]
        ),
        "paid_absence": PaidAbsenceCostResult(
            cost=Decimal("500.00"),
            budget_cost=Decimal("0.00"),
            currency=currency["paid_absence"],
            assumptions_used=_ASSUMPTIONS,  # type: ignore[arg-type]
        ),
        "additional_cost": AdditionalCostResult(
            amount=Decimal("1500.00"),
            currency=currency["additional_cost"],
            assumptions_used=_ASSUMPTIONS,  # type: ignore[arg-type]
        ),
    }


def _four(result) -> tuple[object, ...]:
    return (result.included_cost, result.profit, result.margin, result.markup, result.state)


WITHHELD_MISMATCH = ("n/a", "n/a", "n/a", "n/a", "currency_mismatch")


@pytest.mark.parametrize("odd_source", _SOURCES)
def test_r_01_one_source_in_another_currency_is_currency_mismatch_whichever_it_is(
    odd_source: str,
) -> None:
    """R-01 — dokładnie jedno z czterech źródeł w EUR, pozostałe w PLN → `currency_mismatch` i
    `"n/a"` na czterech polach. Mutacje: pominięcie w porównaniu waluty którego­kolwiek źródła
    (np. tylko przychód vs koszt bazowy) — parametr dla tego źródła daje liczbę."""
    result = scenario_profitability(**_components(**{odd_source: "EUR"}))  # type: ignore[arg-type]

    assert _four(result) == WITHHELD_MISMATCH


def test_r_01_contrast_the_same_four_sources_in_one_currency_give_numbers() -> None:
    """R-01, kontrast — te same kwoty, wszystkie w PLN (albo wszystkie w EUR): liczby i
    `calculated`. Koszt włączony 14000.00, zysk 6000.00, marża 30.00, narzut 42.86."""
    for currency in ("PLN", "EUR"):
        result = scenario_profitability(
            **_components(**dict.fromkeys(_SOURCES, currency))  # type: ignore[arg-type]
        )
        assert _four(result) == (
            Decimal("14000.00"),
            Decimal("6000.00"),
            Decimal("30.00"),
            Decimal("42.86"),
            "calculated",
        )


def test_r_01_a_component_not_calculated_is_not_applicable_even_beside_mixed_currencies() -> None:
    """R-01, kolejność reguł — koszt nieobecności nie `calculated`, a przychód w EUR obok kosztów w
    PLN: stan `not_applicable`, nie `currency_mismatch` — nie da się mówić o walucie liczby, której
    nie ma. Mutacja: sprawdzenie walut przed bramką `calculated` (albo stała `currency_mismatch` w
    gałęzi "nie calculated") → `currency_mismatch`."""
    components = _components(revenue="EUR")
    components["paid_absence"] = PaidAbsenceCostUnavailable(
        reason="no_cost_rate",
        assumptions_used=_ASSUMPTIONS,  # type: ignore[arg-type]
    )

    result = scenario_profitability(**components)  # type: ignore[arg-type]

    assert _four(result) == ("n/a", "n/a", "n/a", "n/a", "not_applicable")


def test_r_01_a_scenario_without_a_commercial_rule_reads_profitability_state_not_applicable(
    client: TestClient, db_session: Session
) -> None:
    """R-01 przez endpoint — scenariusz w PLN bez reguły handlowej: przychód `no_commercial_terms`,
    cztery pola `"n/a"`, `profitability_state = not_applicable`. Kontrast w tym samym teście: ten
    sam scenariusz **z** regułą T&M → `calculated`. Jedyna różnica to istnienie reguły.

    Pierwsza wersja dowodu (testy dewelopera) nie sprawdzała wartości `not_applicable` nigdzie —
    mutacja "każde wstrzymanie opisane jako `currency_mismatch`" przeżywała."""
    _ensure_statutory_bypass(db_session)
    without, without_rule, _ = _full_scenario(
        db_session, name="R01 QA no rule", create_commercial_terms=False
    )
    with_rule_project, with_rule, _ = _full_scenario(db_session, name="R01 QA with rule")

    with caller_holding(*EVERYTHING):
        refused = client.get(results_path(without.id, without_rule.id))
        stated = client.get(results_path(with_rule_project.id, with_rule.id))

    assert refused.status_code == 200, refused.text
    body = refused.json()
    assert body["revenue"]["state"] == "no_commercial_terms"
    assert (
        body["included_cost"],
        body["profit"],
        body["margin"],
        body["markup"],
        body["profitability_state"],
    ) == ("n/a", "n/a", "n/a", "n/a", "not_applicable")

    assert stated.status_code == 200, stated.text
    assert stated.json()["profitability_state"] == "calculated"


def test_units_check_the_database_and_the_model_declare_the_same_outcome_terms_checks(
    db_session: Session,
) -> None:
    """Pkt 5 — nazwy `CHECK` tabeli `outcome_terms` w bazie po migracji to dokładnie nazwy z modelu
    (`OutcomeTerms.__table__`), w tym `ck_outcome_terms_units_given_with_unit_rate`. Test równości
    wyrażeń (`test_the_model_and_the_outcome_migration_agree_on_every_sql_expression`) porównuje
    stałe, nie to, czy model i migracja w ogóle deklarują `CHECK` — usunięcie deklaracji z samego
    modelu przeżywało."""
    in_database = set(
        db_session.execute(
            sa.text(
                "SELECT conname FROM pg_constraint"
                " WHERE conrelid = 'outcome_terms'::regclass AND contype = 'c'"
            )
        ).scalars()
    )
    in_model = {
        constraint.name
        for constraint in OutcomeTerms.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }

    assert "ck_outcome_terms_units_given_with_unit_rate" in in_database
    assert in_database == in_model


def test_r_04_the_read_carries_revenue_max_when_it_was_written(
    client: TestClient, db_session: Session
) -> None:
    """R-04, lustro `test_r_04_the_read_carries_the_outcome_rule_parameters_as_written` — tamten
    zapisuje `revenue_min` bez `revenue_max`, więc `null` w `revenue_max` był tam wynikiem
    oczekiwanym i mutacja "odczyt zawsze `revenue_max = null`" przeżywała. Tutaj odwrotnie:
    `revenue_max` podane, `revenue_min` pominięte → `"25000.0000"` i `null`. Jedyna zmiana względem
    tamtego testu to to, która granica jest podana."""
    project, scenario = _scenario(db_session, "Outcome read max")

    written = _post(client, project, scenario, outcome_payload(revenue_max="25000"))
    assert written.status_code == 201, written.text
    read = _get(client, project, scenario)["commercial_terms"]["outcome_terms"]

    assert (read["revenue_min"], read["revenue_max"]) == (None, "25000.0000")
    assert written.json()["commercial_terms"]["outcome_terms"] == read
