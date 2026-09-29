"""SC-4-03 — contrast tests added by QA where a mutation survived the first version of the suite
(K-02, K-03, K-04; ADR-0003, addendum 2026-09-25 SC-4-03, points 1, 2, 4, 5b).

Every test names the mutation that survived without it, and the reason it survived:

- **K-02** — "the expected value is rounded once, at the end": rounding each `p_k · r_k` term
  separately used to survive, because in the existing case only one term has a fraction below a
  cent.
- **K-03** — a third decimal place: the existing case (33.333/33.333/33.334), after the
  `NUMERIC(5,2)` column's silent rounding, sums to 99.99, so the database's `CHECK` also refused
  it — the test measured the wider boundary (the database), not the narrower one (the API schema).
  Here the values, after rounding, sum to exactly 100.00, so without the schema rule the database
  would accept numbers other than the ones entered.
- **K-04** — a `0` entered by the user is a value, not the absence of a component: neither storing
  `0` as `NULL`, nor reading a maximum of `0` as "no maximum", had a test (the existing test checks
  only the other side: omitted → `NULL`).
- an Outcome-based rule with no details row → `incomplete_commercial_terms`, never an amount
  (point 1).

Real PostgreSQL, a real migration; a write through `POST`, a read through `GET`.
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
    """K-02 — a fee of 100, 0.001 PLN/unit, no bonus; "not achieved" and "achieved" at 5 units
    each → both r = 100.005; probabilities 50/0/50/0.

    One rounding at the end: 50.0025 + 50.0025 = 100.005 → **100.01**. The mutation "round each
    `p_k · r_k` term" (the second rounding rule): 50.00 + 50.00 → 100.00. It used to survive
    `test_k_02_the_expected_revenue_is_rounded_once_from_unrounded_category_revenues`, where only
    one term has a fraction below a cent.
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
    """K-03 — 25.001/25.001/24.999/24.999: the entered sum is exactly 100.000, and after the
    `NUMERIC(5,2)` column's silent rounding each value is 25.00 — the sum is 100.00 again, so the
    database's `CHECK` **would accept** the row. Only the "at most two decimal places" rule in the
    API schema can refuse it: `422`, zero rows in both tables.

    Contrast, one thing changed (the entry's precision): 25/25/25/25 → `201`, stored as 25.00.

    Mutation: `decimal_places` removed from `Probability` — here `201` with probabilities other
    than the ones entered. It used to survive the `third_decimal_place` case in
    `test_outcome_revenue.py`, in the sense that that test only turned red on the database's `409`
    from `CHECK`, not on the rule K-03 is about.
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
    """K-04 / ADR-0003 addendum SC-4-03 point 2 — the other side of the test
    `test_k_04_an_absent_optional_component_is_stored_as_null_never_0`: a `0` entered by the user
    is `0` in the database, not `NULL`, and a maximum of `0` bounds the whole revenue (and the
    guaranteed one) to 0.00 — it is not "no maximum".

    Contrast, one thing changed (the maximum omitted instead of `0`): guaranteed value 20000.00.

    Mutations that used to survive without this test: `if terms.revenue_max and …` in `_bounded`
    (the truthiness of `Decimal("0")` is false) → 20000.00; `payload.revenue_max or None` /
    `payload.success_bonus or None` on write → `NULL` in the row and 20000.00.
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
    """ADR-0003 addendum SC-4-03 point 1 — an `outcome_based` rule with no `outcome_terms` row (the
    database guards the details row's type, not its existence): `incomplete_commercial_terms`,
    `"n/a"` in both amounts, no category revenues. Contrast: the same rule with a row →
    `calculated`.

    Mutation: the `details is None` branch removed from `_outcome_based` (a read through
    `scalar_one`) → `NoResultFound`, i.e. `500` instead of a named state.
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
