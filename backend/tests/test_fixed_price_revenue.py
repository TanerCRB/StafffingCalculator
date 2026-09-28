"""SC-4-02 — Fixed Price revenue (F-06.2, AC-07 revenue half; Issue #66): K-01, K-06 (the named
states), K-07.

Every figure is read through the real endpoints — `GET …/commercial-terms`, `GET …/personnel-cost`,
`GET …/results`, `GET …/scenarios/compare`, `GET …/what-if` — and every change of staffing goes
through the real allocation edit (`PATCH`), so "the revenue did not move" is said about the same
path a client uses, after a write the cost path demonstrably saw.

- **K-01** — AC-07: 150000 PLN agreed → 150000.00 before and after a staffing change that raises the
  personnel cost from 100000.00 to 120000.00 (measured, as the precondition); the same change on a
  T&M twin moves its revenue; a catalogue gap does not block the Fixed Price revenue; and the
  formula module reaches nothing of T&M, `app.data.rate_windows` or any cost, at any depth.
- **K-06** — a Fixed Price rule without its details row is `incomplete_commercial_terms` (`"n/a"`,
  never `0`); a price in a currency other than `scenarios.currency` is `currency_mismatch`.
- **K-07** — `…/results`, `…/compare` (draft and approved) and the what-if (draft) of a Fixed Price
  scenario answer `200` with the revenue of `…/commercial-terms`; the Fixed Price payload's field
  set is asserted by equality at every level; the T&M payload is byte for byte the SC-4-01 one.
"""

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.models import CatalogDefaultRate, Project, Scenario
from tests.conftest import (
    IN_SCOPE_USER,
    DimensionTuple,
    allocation_path,
    approve_path,
    as_caller,
    caller_holding,
    commercial_terms_path,
    make_allocation,
    make_commercial_terms,
    make_dimension_tuple,
    make_fixed_price_terms,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    staffing_path,
)
from tests.test_scenario_results import (
    EVERYTHING,
    _ensure_statutory_bypass,
    _full_scenario,
    _reachable_from,
    results_path,
)
from tests.test_scenario_results_compare import compare_path

MAR = date(2026, 3, 1)
GAP_MONTH = date(2025, 12, 1)
"""Before every catalogue window this file creates (they start 2026-01-01): an unpriced month."""

AC_07_PRICE = Decimal("150000.0000")
AC_07_COST_RATE = Decimal("1000.0000")
AC_07_SELLING_RATE = Decimal("1500.0000")


def personnel_cost_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/scenarios/{scenario_id}/personnel-cost"


def what_if_path(project_id: uuid.UUID, scenario_id: uuid.UUID, percent: str) -> str:
    return f"/projects/{project_id}/scenarios/{scenario_id}/what-if?salary_raise_percent={percent}"


def _commercial(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    response = client.get(
        commercial_terms_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _revenue(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    return _commercial(client, project_id, scenario_id)["revenue"]


def _personnel_cost(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    with caller_holding(*Permission):
        response = client.get(personnel_cost_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return response.json()["personnel_cost"]


def _edit_month(
    client: TestClient,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    month: date,
    **hours: str,
) -> None:
    """The real allocation edit (`PATCH`), with the position's current concurrency token — the
    pattern of `tests/test_personnel_cost.py::_edit_month`."""
    token = client.get(
        staffing_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    ).json()["positions"][0]["updated_at"]
    response = client.patch(
        allocation_path(project_id, scenario_id, position_id, month),
        json={"updated_at": token, **hours},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert response.status_code == 200, response.text


def _ac_07_project(session: Session) -> tuple[Project, Any]:
    """A project in scope with the personnel-cost flag set, and one rate: cost 1000, selling 1500
    PLN per hour from 2026-01-01 — so 100 planned hours cost 100000 and 100 billable hours sell for
    150000 (the AC-07 figures)."""
    project = make_project(
        session,
        name="AC-07",
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    dimensions = make_dimension_tuple(session, suffix=" AC-07")
    make_rate(
        session,
        dimensions,
        effective_from=date(2026, 1, 1),
        default_cost_rate=AC_07_COST_RATE,
        default_selling_rate=AC_07_SELLING_RATE,
        currency="PLN",
    )
    return project, dimensions


def _staffed(
    session: Session, project: Project, dimensions: Any, *, name: str, month: date = MAR
) -> tuple[Scenario, Any]:
    """One draft scenario in PLN, one position of headcount 1, 100 planned and 100 billable
    hours."""
    scenario = make_scenario(session, project, name=name, currency="PLN")
    position = make_staffing_position(session, scenario, dimensions, headcount=1, start_date=month)
    make_allocation(
        session,
        position,
        period_month=month,
        planned_allocation_hours=Decimal("100.00"),
        billable_hours=Decimal("100.00"),
    )
    return scenario, position


# --- K-01: AC-07, revenue half -------------------------------------------------------------------


def test_k_01_ac_07_a_fixed_price_revenue_stays_150000_when_staffing_raises_the_cost_to_120000(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — AC-07's revenue half, on the real endpoints, with the cost change **measured**.

    Precondition, asserted rather than assumed: the Fixed Price scenario's personnel cost is
    100000.00 before and 120000.00 after the staffing edit (100 → 120 planned hours at 1000).
    Without it, "the revenue did not move" could be a change the scenario never received.

    Contrast: a T&M twin — same project, same catalogue, same edit — moves from 150000.00 to
    180000.00, so the edit reached the revenue path. Mutations this kills: the Fixed Price branch
    adding `billable_hours × rate` (the Fixed Price revenue moves); the dispatcher sending Fixed
    Price to the T&M formula (180000.00, and the rule's price would never be read).
    """
    project, dimensions = _ac_07_project(db_session)
    fixed, fixed_position = _staffed(db_session, project, dimensions, name="Fixed price")
    make_fixed_price_terms(db_session, fixed, agreed_price=AC_07_PRICE, currency="PLN")
    twin, twin_position = _staffed(db_session, project, dimensions, name="T&M twin")
    make_commercial_terms(db_session, twin)

    cost_before = _personnel_cost(client, project.id, fixed.id)
    revenue_before = _revenue(client, project.id, fixed.id)
    twin_before = _revenue(client, project.id, twin.id)
    assert (cost_before["state"], cost_before["amount"]) == ("calculated", "100000.00")
    assert (revenue_before["state"], revenue_before["amount"], revenue_before["currency"]) == (
        "calculated",
        "150000.00",
        "PLN",
    )
    assert (twin_before["state"], twin_before["amount"]) == ("calculated", "150000.00")

    for scenario, position in ((fixed, fixed_position), (twin, twin_position)):
        _edit_month(
            client, project.id, scenario.id, position.id, MAR,
            planned_allocation_hours="120.00", billable_hours="120.00",
        )

    cost_after = _personnel_cost(client, project.id, fixed.id)
    assert (cost_after["state"], cost_after["amount"]) == ("calculated", "120000.00"), (
        "the staffing edit never reached the Fixed Price scenario's cost — the revenue assertion "
        "below would prove nothing"
    )
    assert _revenue(client, project.id, fixed.id) == revenue_before
    twin_after = _revenue(client, project.id, twin.id)
    assert (twin_after["state"], twin_after["amount"]) == ("calculated", "180000.00")


def test_k_01_a_catalogue_gap_does_not_block_a_fixed_price_revenue(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — a month with no selling rate is `no_rate` for T&M (the contrast, same catalogue, same
    month) and nothing at all for Fixed Price, whose revenue reads no rate. Mutation: the Fixed
    Price branch gated on `month_is_priced` (or computed from `_billable_months`) — it then answers
    `no_rate` too."""
    project, dimensions = _ac_07_project(db_session)
    fixed, _ = _staffed(db_session, project, dimensions, name="Fixed price", month=GAP_MONTH)
    make_fixed_price_terms(db_session, fixed, agreed_price=AC_07_PRICE, currency="PLN")
    twin, _ = _staffed(db_session, project, dimensions, name="T&M twin", month=GAP_MONTH)
    make_commercial_terms(db_session, twin)

    assert _revenue(client, project.id, twin.id)["state"] == "no_rate"
    revenue = _revenue(client, project.id, fixed.id)
    assert (revenue["state"], revenue["amount"], revenue["currency"]) == (
        "calculated",
        "150000.00",
        "PLN",
    )
    assumptions = revenue["assumptions_used"]
    assert assumptions["rate_windows"] == [] and assumptions["unresolved_months"] == []


FORMULA_MODULE = "app.domain.revenue_fixed_price"
FORBIDDEN_FOR_THE_FORMULA = {
    "app.domain.revenue_time_and_material",
    "app.data.rate_windows",
    "app.data.commercial_terms",
    "app.data.personnel_cost",
    "app.domain.personnel_cost",
    "app.data.paid_absence_cost",
    "app.domain.paid_absence_cost",
    "app.data.additional_cost",
    "app.domain.additional_cost",
    "app.data.scenario_results",
    "app.domain.scenario_results",
}


def test_k_01_the_fixed_price_formula_reaches_no_tm_formula_no_rate_windows_and_no_cost() -> None:
    """K-01 / FP-4 — the import **graph** of the formula module, at any depth.

    Beyond the named set, no reachable module may carry "cost", "rate_windows" or
    "time_and_material" in its name, and none may be a data-layer module at all: the formula
    takes an `AgreedPrice` and reads nothing. Contrast, so the check is not vacuous: the formula
    does reach `app.domain.revenue` and `app.core.money`, and the data layer that dispatches to
    it does reach `app.data.rate_windows` and the T&M formula. Mutation: `from
    app.domain.revenue_time_and_material import …` (or any cost module, directly or through a
    module it imports) in the formula.
    """
    reached = _reachable_from(FORMULA_MODULE)

    assert not (reached & FORBIDDEN_FOR_THE_FORMULA), sorted(reached & FORBIDDEN_FOR_THE_FORMULA)
    assert not [
        module
        for module in reached
        if "cost" in module or "rate_windows" in module or "time_and_material" in module
    ]
    assert not [module for module in reached if module.startswith("app.data.")]

    assert {"app.domain.revenue", "app.core.money"} <= reached
    dispatcher = _reachable_from("app.data.commercial_terms")
    assert {"app.data.rate_windows", "app.domain.revenue_time_and_material", FORMULA_MODULE} <= (
        dispatcher
    )


# --- K-06: the named states, never 0 ------------------------------------------------------------


def test_k_06_a_fixed_price_rule_without_its_details_row_is_incomplete_never_zero(
    client: TestClient, db_session: Session
) -> None:
    """K-06 / FP-3 — the database enforces the details row's *type*, not its existence; a Fixed
    Price rule without it is `incomplete_commercial_terms`, amount `"n/a"`, currency `null`, and the
    rule shows no price. Contrast: the same rule with its row is `calculated`. Mutation: a missing
    row read as price `0` (`"0.00"`)."""
    project = make_project(db_session, name="Incomplete", accessible_to=(IN_SCOPE_USER,))
    incomplete = make_scenario(db_session, project, name="No price row", currency="PLN")
    make_fixed_price_terms(db_session, incomplete, agreed_price=None)
    complete = make_scenario(db_session, project, name="Priced", currency="PLN")
    make_fixed_price_terms(db_session, complete, agreed_price=Decimal("0.0000"), currency="PLN")

    body = _commercial(client, project.id, incomplete.id)
    revenue = body["revenue"]
    assert (revenue["state"], revenue["amount"], revenue["currency"]) == (
        "incomplete_commercial_terms",
        "n/a",
        None,
    )
    assert revenue["assumptions_used"]["model_type"] == "fixed_price"
    assert revenue["assumptions_used"]["currencies"] == []
    assert body["commercial_terms"]["model_type"] == "fixed_price"
    assert (body["commercial_terms"]["agreed_price"], body["commercial_terms"]["currency"]) == (
        None,
        None,
    )

    # The contrast is a real zero (D-5, AC-05): a stated `0.00`, which the missing row must
    # never be.
    contrast = _revenue(client, project.id, complete.id)
    assert (contrast["state"], contrast["amount"]) == ("calculated", "0.00")


def test_k_06_a_price_in_another_currency_than_the_scenarios_is_a_currency_mismatch(
    client: TestClient, db_session: Session
) -> None:
    """K-06 / FP-3 — a price in PLN on a scenario in EUR is `currency_mismatch`, `"n/a"`: nothing is
    converted (ADR-0006). Contrasts: the same price on a PLN scenario and on a scenario with no
    currency set is `calculated` in PLN — the price carries its own currency, so a `NULL`
    `scenarios.currency` is not a gap (`no_revenue_currency` is unreachable for this model)."""
    project = make_project(db_session, name="Currencies", accessible_to=(IN_SCOPE_USER,))
    scenarios = {
        currency: make_scenario(db_session, project, name=f"In {currency}", currency=currency)
        for currency in ("EUR", "PLN")
    }
    scenarios[None] = make_scenario(db_session, project, name="No currency")
    for scenario in scenarios.values():
        make_fixed_price_terms(db_session, scenario, agreed_price=AC_07_PRICE, currency="PLN")

    mismatch = _revenue(client, project.id, scenarios["EUR"].id)
    assert (mismatch["state"], mismatch["amount"], mismatch["currency"]) == (
        "currency_mismatch",
        "n/a",
        None,
    )
    assert mismatch["assumptions_used"]["currencies"] == ["PLN"]
    for key in ("PLN", None):
        revenue = _revenue(client, project.id, scenarios[key].id)
        assert (revenue["state"], revenue["amount"], revenue["currency"]) == (
            "calculated",
            "150000.00",
            "PLN",
        )


def test_k_06_the_revenue_is_the_agreed_price_rounded_once(
    client: TestClient, db_session: Session
) -> None:
    """The one arithmetic step — `round_money` on the stored price (ADR-0002): `150000.1250` is
    stated `150000.13` (half up, two places), while the rule shows the stored `150000.1250` — the
    input at full precision, which is what an edit form loads."""
    project = make_project(db_session, name="Rounding", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", currency="PLN")
    make_fixed_price_terms(db_session, scenario, agreed_price=Decimal("150000.1250"))

    body = _commercial(client, project.id, scenario.id)
    assert body["revenue"]["amount"] == "150000.13"
    assert body["commercial_terms"]["agreed_price"] == "150000.1250"


# --- K-07: the consumers of the revenue, and the field sets --------------------------------------

FP_RESPONSE_FIELDS = {"scenario_id", "scenario_status", "commercial_terms", "revenue"}
FP_TERMS_FIELDS = {
    "id",
    "model_type",
    "updated_at",
    "outcome_terms",
    "agreed_price",
    "currency",
}
"""Re-armed (not weakened) by R-05 of the SC-4-02 review (2026-09-28): the Fixed Price rule carries
the `outcome_terms` key, always `null`, like every other model's rule (SC-4-07, point 11). Still an
equality."""
FP_REVENUE_FIELDS = {
    "state",
    "amount",
    "currency",
    "assumptions_used",
    # The revenue fields SC-4-03 added to the shared `RevenueRead` for every model (sync of
    # 2026-09-28): Fixed Price has no expected revenue and no categories, asserted below.
    "expected_state",
    "expected_amount",
    "category_revenues",
}
FP_ASSUMPTIONS_FIELDS = {
    "model_type",
    "hours_source",
    "vendor_axis",
    "rate_source",
    "rate_windows",
    "unresolved_months",
    "currencies",
    "price_basis",
    "price_adjustments",
}


def _assert_fixed_price_revenue_fields(revenue: dict[str, Any]) -> None:
    """The Fixed Price revenue, by **equality** of the field set at every level — a cost, a rate or
    anything nobody decided fails here the day it is added (ADR-0005, addendum SC-4-01, point 3,
    applied to the Fixed Price shape). The Fixed Price sets live here, next to — not inside — the
    SC-4-01 sets of `test_commercial_terms_access.py`: the T&M shape is unchanged (K-07), so those
    sets stay exactly as they were and still bind every non-Fixed-Price payload."""
    assert set(revenue) == FP_REVENUE_FIELDS
    assert (
        revenue["expected_state"],
        revenue["expected_amount"],
        revenue["category_revenues"],
    ) == ("not_applicable", "n/a", [])
    assumptions = revenue["assumptions_used"]
    assert set(assumptions) == FP_ASSUMPTIONS_FIELDS
    assert assumptions["rate_windows"] == []
    assert assumptions["unresolved_months"] == []
    assert (
        assumptions["model_type"],
        assumptions["rate_source"],
        assumptions["hours_source"],
        assumptions["vendor_axis"],
        assumptions["price_basis"],
        assumptions["price_adjustments"],
    ) == (
        "fixed_price",
        "fixed_price_terms",
        "not_applicable",
        "not_applicable",
        "agreed_price",
        "not_included",
    )


def test_k_07_the_fixed_price_payload_carries_no_cost_field_on_the_write_and_on_the_read(
    client: TestClient, db_session: Session
) -> None:
    """K-07 — the Fixed Price rule and revenue, field set by equality at every level, on the `POST`
    that creates it and on the `GET`, for a caller holding **every** permission (the absence of a
    cost is the schema's, not a closed gate's). The rate behind the scenario has a cost (1000) that
    must appear nowhere. Also: `assumptions_used` says, explicitly, that adjustments are not
    included (D-3 = C), that the price is the rule's own row (`rate_source = fixed_price_terms`) and
    that no hours and no vendor axis apply (`not_applicable`) — the SC-4-04 pattern, decision of
    2026-09-25 on Issue #66."""
    project, dimensions = _ac_07_project(db_session)
    scenario, _ = _staffed(db_session, project, dimensions, name="Fixed price")

    with caller_holding(*EVERYTHING):
        written = client.post(
            commercial_terms_path(project.id, scenario.id),
            json={"model_type": "fixed_price", "agreed_price": "150000", "currency": "PLN"},
        )
        read = client.get(commercial_terms_path(project.id, scenario.id))

    assert written.status_code == 201, written.text
    assert read.status_code == 200, read.text
    for response in (written, read):
        body = response.json()
        assert set(body) == FP_RESPONSE_FIELDS
        assert set(body["commercial_terms"]) == FP_TERMS_FIELDS
        assert body["commercial_terms"]["outcome_terms"] is None
        assert (body["commercial_terms"]["agreed_price"], body["commercial_terms"]["currency"]) == (
            "150000.0000",
            "PLN",
        )
        _assert_fixed_price_revenue_fields(body["revenue"])
        assert body["revenue"]["amount"] == "150000.00"
        assert "1000.0000" not in response.text and "1500.0000" not in response.text


def _fixed_price_results_scenario(session: Session, name: str) -> tuple[Project, Scenario, Any]:
    """SC-7-01's `_full_scenario` — a rate, one position, one additional cost — with a Fixed Price
    rule of 150000 PLN instead of its T&M rule."""
    project, scenario, position = _full_scenario(session, name=name, create_commercial_terms=False)
    make_fixed_price_terms(session, scenario, agreed_price=AC_07_PRICE, currency="PLN")
    return project, scenario, position


def _results(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    with caller_holding(*EVERYTHING):
        response = client.get(results_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return response.json()


def _compared(client: TestClient, project_id: uuid.UUID, *scenario_ids: uuid.UUID) -> dict:
    with caller_holding(*EVERYTHING):
        response = client.get(compare_path(project_id, *scenario_ids))
    assert response.status_code == 200, response.text
    return response.json()


def test_k_07_results_compare_and_what_if_of_a_draft_fixed_price_scenario_answer_its_revenue(
    client: TestClient, db_session: Session
) -> None:
    """K-07 / FP-5 on a draft — `200` from all three consumers, each with exactly the revenue
    `…/commercial-terms` answers (the whole revenue object, not only the amount), and a profit built
    from it: 150000.00 − (12000.00 personnel + 2000.00 additional) = 136000.00.

    Fixed Price names its own source, `fixed_price_terms` (the SC-4-04 pattern, decision of
    2026-09-25 on Issue #66): the race guards of SC-7-01/SC-6-04 must still answer `200` for a
    draft that nothing approved in between."""
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _fixed_price_results_scenario(db_session, "FP draft")
    revenue = _revenue(client, project.id, scenario.id)
    assert revenue["assumptions_used"]["rate_source"] == "fixed_price_terms"

    results = _results(client, project.id, scenario.id)
    assert results["revenue"] == revenue
    assert (results["profit"], results["included_cost"]) == ("136000.00", "14000.00")
    _assert_fixed_price_revenue_fields(results["revenue"])

    compared = _compared(client, project.id, scenario.id)
    assert [row["revenue"] for row in compared["results"]] == [revenue]

    with caller_holding(*EVERYTHING):
        what_if = client.get(what_if_path(project.id, scenario.id, "10"))
    assert what_if.status_code == 200, what_if.text
    assert what_if.json()["revenue"] == revenue


def test_k_07_an_approved_fixed_price_scenario_answers_results_and_compare_with_the_same_price(
    client: TestClient, db_session: Session
) -> None:
    """K-07 / FP-5 / FPS-3 on an approved scenario, through the real approval endpoint.

    - `…/results` and `…/compare` answer `200` (not the race guard's `409`), with the revenue
      `…/commercial-terms` answers;
    - that revenue equals the draft's from before the approval **entirely**, `assumptions_used`
      included: `rate_source` is `fixed_price_terms` for both statuses — the price is the
      scenario's own row, never a snapshot (the snapshot has no price to hold);
    - editing the catalogue after the approval moves nothing, while a T&M draft twin on the same
      rate does move (the contrast that the edit reached the catalogue).

    The what-if of an approved scenario is not asserted `200` here: SC-6-04 answers it `404` for
    every model (ADR-0015, point 5; `test_scenario_what_if.py::test_k_06_…`), and Fixed Price does
    not change that — pinned below, so the discrepancy with K-07's wording is visible.
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, position = _fixed_price_results_scenario(db_session, "FP approved")
    # The T&M twin exists before the first request: the scope read loads the project's
    # `scenarios` once into the shared test session, and a scenario added later is not in it.
    twin = make_scenario(db_session, project, name="T&M twin", currency="PLN")
    twin_position = make_staffing_position(
        db_session, twin, _dimensions_of(position), start_date=MAR
    )
    make_allocation(db_session, twin_position, period_month=MAR)
    make_commercial_terms(db_session, twin)
    draft_revenue = _revenue(client, project.id, scenario.id)

    approval = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
    assert approval.status_code == 200, approval.text

    approved_revenue = _revenue(client, project.id, scenario.id)
    assert approved_revenue["assumptions_used"]["rate_source"] == "fixed_price_terms"
    assert approved_revenue == draft_revenue
    results = _results(client, project.id, scenario.id)
    assert results["scenario_status"] == "Approved"
    assert results["revenue"] == approved_revenue
    compared = _compared(client, project.id, scenario.id)
    assert [row["revenue"] for row in compared["results"]] == [approved_revenue]

    twin_before = _revenue(client, project.id, twin.id)["amount"]
    db_session.execute(
        sa.update(CatalogDefaultRate).values(default_selling_rate=Decimal("999.0000"))
    )
    db_session.flush()
    db_session.expire_all()
    assert _revenue(client, project.id, twin.id)["amount"] != twin_before
    assert _revenue(client, project.id, scenario.id) == approved_revenue

    with caller_holding(*EVERYTHING):
        what_if = client.get(what_if_path(project.id, scenario.id, "10"))
    assert what_if.status_code == 404, what_if.text


def _dimensions_of(position: Any) -> DimensionTuple:
    """The dimension tuple of an existing position, for a twin priced by the same catalogue row."""
    return DimensionTuple(
        role_id=position.role_id,
        seniority_id=position.seniority_id,
        location_id=position.location_id,
        engagement_type_id=position.engagement_type_id,
    )


def test_k_07_the_tm_response_is_byte_for_byte_the_sc_4_01_one(
    client: TestClient, db_session: Session
) -> None:
    """K-07 / FP-5 — the T&M payload of `…/commercial-terms` is **byte for byte** what `main`
    answered without SC-4-02: the same fields, in the same order, with the same values and
    spellings, serialised the way FastAPI serialises a response. The one server-generated value,
    `updated_at`, is taken from the answer; everything else is spelled here from SC-4-01's contract
    plus the additive fields SC-4-03 gave every model (`outcome_terms`, `expected_state`,
    `expected_amount`, `category_revenues` — re-based at the sync of 2026-09-28).

    Mutation: any Fixed Price field added to the shared T&M classes (`price_basis`,
    `agreed_price`, …), a reordered field, or `hours_source` widened on the T&M shape to anything
    but `billable_hours` — the bytes differ.
    """
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session, suffix=" Aurora")
    position = make_staffing_position(db_session, scenario, dimensions, start_date=MAR)
    make_allocation(db_session, position, period_month=MAR)
    rate = make_rate(
        db_session,
        dimensions,
        effective_from=date(2026, 1, 1),
        default_cost_rate=Decimal("120.0000"),
        default_selling_rate=Decimal("200.0000"),
        currency="PLN",
    )
    rule = make_commercial_terms(db_session, scenario)

    response = client.get(
        commercial_terms_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    updated_at = response.json()["commercial_terms"]["updated_at"]

    expected = {
        "scenario_id": str(scenario.id),
        "scenario_status": "Draft",
        "commercial_terms": {
            "id": str(rule.id),
            "model_type": "time_and_material",
            "updated_at": updated_at,
            "outcome_terms": None,
        },
        "revenue": {
            "state": "calculated",
            "amount": "20000.00",
            "currency": "PLN",
            "assumptions_used": {
                "model_type": "time_and_material",
                "hours_source": "billable_hours",
                "vendor_axis": "internal",
                "rate_source": "live_catalog",
                "rate_windows": [
                    {
                        "source_rate_id": str(rate.id),
                        "effective_from": "2026-01-01",
                        "effective_to": None,
                        "default_selling_rate": "200.0000",
                        "currency": "PLN",
                    }
                ],
                "unresolved_months": [],
                "currencies": ["PLN"],
            },
            "expected_state": "not_applicable",
            "expected_amount": "n/a",
            "category_revenues": [],
        },
    }
    assert response.content == json.dumps(
        expected, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
