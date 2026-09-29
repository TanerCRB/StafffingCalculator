"""SC-4-03, K-01..K-04 — the Outcome-based scenario revenue through real endpoints (F-06.3,
AC-08).

- **K-01** AC-08: a fixed fee of 20000 PLN + a success bonus of 10000 PLN → 20000 for "not achieved"
  and "partial", 30000 for "achieved"; a rule in a currency other than the scenario's currency →
  `currency_mismatch`, no amount in any revenue field.
- **K-02** guaranteed and expected are two values: probabilities change only the expected one
  (70/30 → 20000/23000, 10/90 → 20000/29000); no probabilities → a named state, never `0` nor a
  copy of the guaranteed value; the expected value is rounded once, at the end.
- **K-03** a probability set summing to ≠ 100.00, incomplete or with a third decimal place →
  `422`, zero rows; contrast 33.34/33.33/33.33 → `201`. (The sum enforced by the database's
  `CHECK` — `tests/test_outcome_terms_schema.py`.)
- **K-04** the unit rate and min/max bound the whole category revenue and the guaranteed one; `min >
  max` → `422`, zero rows.

Real PostgreSQL, a real migration (ADR-0001). Every write through `POST`, every read through
`GET` — the result is what the client sees.
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


# --- K-01: AC-08 and the rule's currency -------------------------------------------------------


def test_k_01_ac_08_the_bonus_is_paid_for_achieved_and_not_for_not_achieved_or_partial(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — AC-08 literally: 20000 (not achieved), 20000 (partial), 30000 (achieved).

    The `POST` response and the later `GET` carry the same result (a write and a read are the same
    calculation path). "Exceeded" also gets the bonus (ADR-0003, addendum SC-4-03, point 2). The
    guaranteed revenue (`amount`) is the fixed fee — the bonus is not guaranteed.

    Mutations: a bonus for "partial" (20000 → 30000 in this field); a bonus omitted for "achieved"
    (30000 → 20000); the bonus folded into the guaranteed value (`amount` 30000).
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
    """K-01 — a rule in EUR, a scenario in PLN: `currency_mismatch`, `"n/a"` in both amounts, no
    category revenues, and none of the rule's numbers anywhere in the response (O-4).

    Contrast within the same test: the same rule in EUR on a scenario **without** a currency →
    `calculated` in EUR — so the refusal is about the mismatch, not about the EUR currency.
    Mutations: a 1:1 conversion (an amount in PLN), the comparison skipped (an amount in EUR despite
    the scenario's PLN).
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
    # The rule's parameters (`commercial_terms.outcome_terms`, ADR-0003 point 11) legally carry
    # 20000; the criterion: no *revenue* field carries the amount (O-4).
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
    """K-01, assumptions (F-06.5; ADR-0003, addendum SC-4-03, point 8): `rate_source`,
    `hours_source` and `vendor_axis` are `not_applicable` — the calculation reads neither the
    catalogue, nor hours, nor a subcontractor, so it names none of them. No rate windows and no
    months."""
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
    """K-01, calculation independence (F-06; task: the outcome module reads neither the staffing,
    nor the allocation, nor the catalogue). The scenario **has** a position, an allocation month and
    a catalogue rate that T&M would price; reading the outcome rule sends not a single statement
    touching those tables or the rate snapshot, and the result is AC-08, not hours × rate.

    Contrast: the same listener, reading a T&M scenario, sees `staffing_position_allocation` — so
    the listener genuinely catches that path's statements.
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
    """K-02 — for the same AC-08 rule the guaranteed value stays 20000, the expected value is
    Σ p·r.

    70% × 20000 + 30% × 30000 = 23000; 10% × 20000 + 90% × 30000 = 29000. Mutations: the expected
    value in `amount` (the guaranteed value 23000/29000 — and `/results` profit calculated from it);
    probabilities taken as fractions, not percentages (2 300 000); category revenue with no bonus
    (20000).
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
    """K-02 — no probabilities: `expected_state = no_probabilities`, `expected_amount = "n/a"`
    — never `"0.00"` and never `"20000.00"` (a copy of the guaranteed value). The guaranteed value
    and the per-category ones are still given (ADR-0003, addendum SC-4-03, point 5c); the category's
    probability is `null`, not `0`."""
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
    """K-02 — "rounded once, at the end" on numbers that make the distinction visible.

    A fee of 100, 0.001 PLN/unit, "achieved" 5 units → r = 100.005 (shown as 100.01), the rest 100;
    probabilities 50/0/50/0. From the unrounded values: 50 + 50.0025 = 100.0025 → **100.00**.
    Mutation "the expected value from the rounded category revenues": 50 + 50.005 = 100.005 →
    100.01.
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


# --- K-03: probabilities refused with no write -------------------------------------------------


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
    """K-03 — a sum of 99.99, an incomplete set, and a third decimal place (the sum is exactly
    100.000!): `422`, zero rows in `commercial_terms` and `outcome_terms`, and the read still says
    `no_commercial_terms`. The third case is a silent rounding that must not happen (ADR-0003,
    addendum SC-4-03, point 4) — the mutation "round to 2 places" yields a sum of 99.99 or accepts
    values other than the ones entered."""
    project, scenario = _scenario(db_session)
    before = count_outcome_rows(db_session)

    refused = _create(client, project, scenario, outcome_payload(probabilities=probabilities))

    assert refused.status_code == 422, refused.text
    assert count_outcome_rows(db_session) == before
    assert _revenue(client, project, scenario)["state"] == "no_commercial_terms"


def test_k_03_contrast_33_34_33_33_33_33_is_accepted(
    client: TestClient, db_session: Session
) -> None:
    """K-03, contrast — 33.34/33.33/33.33/0.00 sums to exactly 100.00: `201`, both tables have a
    row, and the probabilities come back exactly as entered."""
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


# --- K-04: the unit rate and min/max --------------------------------------------------------------


def test_k_04_min_and_max_bound_the_whole_category_revenue_and_the_guaranteed_revenue(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — a fee of 20000, 100 PLN/unit, min 22000, max 30000, no bonus: 0 units → 22000 (min),
    50 units → 25000, 150 units → 30000 (max), guaranteed value 22000 (min acts on the fee alone,
    point 6).

    Mutations: bounding only the variable component (0 units → 20000, 150 units → 35000 or 50000);
    the guaranteed value with no bound (20000); `min`/`max` swapped.
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
    """K-04, contrast — without `revenue_max` 150 units is 35000: the 30000 in the test above comes
    from the maximum, not from the formula. No maximum is `null`, never "bound to 0"."""
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
    """K-04 — min 40000 > max 30000: `422`, zero rows. (The same rule as a database `CHECK` —
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
    """ADR-0003, addendum SC-4-03, point 2 — a component omitted in the request is `NULL` in the
    database, not `0` (otherwise "no maximum" would be stored as "bound to zero")."""
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
