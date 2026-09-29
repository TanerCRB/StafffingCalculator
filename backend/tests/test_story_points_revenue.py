"""SC-4-04, K-01/K-02/K-03/K-05/R-01 — Story Points revenue of a scenario (F-06.4, Issue #68).

The fourth commercial model, and the first real second entry in `REVENUE_BY_MODEL` /
`DETAIL_TABLE_BY_MODEL` (K-03): SC-4-01 proved the dispatcher's shape with one real model
(Time & Material) and a `monkeypatch`ed stand-in for a second; this file is the first proof that
uses two real rows of two real models in the same database instead.

**R-01 (reviewer, gate 2 of SC-4-04)** — `story_points_terms.currency` is now checked against
`scenarios.currency`, the same `currency_mismatch` rule already applied identically in
`revenue_time_and_material.py`, `personnel_cost.py`, `paid_absence_cost.py` and
`additional_cost.py` (ADR-0003, point 8). Before this fix a Story Points rule priced in a currency
other than the scenario's own was returned as `calculated` unconditionally, which
`scenario_profitability` would then silently mix with costs in the scenario's currency.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.data.commercial_terms import DETAIL_TABLE_BY_MODEL, REVENUE_BY_MODEL
from app.domain.revenue import RevenueResult, RevenueUnavailable
from app.domain.revenue_story_points import story_points_revenue
from app.models import CommercialTerms, Scenario, StaffingPositionAllocation
from app.models.commercial_terms import MODEL_TYPES
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    commercial_terms_path,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    make_story_points_terms,
)
from tests.test_commercial_terms_access import (  # reuse the pinned field sets (K-05)
    ASSUMPTIONS_FIELDS,
    RESPONSE_FIELDS,
    REVENUE_FIELDS,
    TERMS_FIELDS,
)

MAR = date(2026, 3, 1)
APR = date(2026, 4, 1)

SP = {
    "model_type": "story_points",
    "price_per_point": "1000.0000",
    "accepted_points": 25,
    "currency": "PLN",
}
TM = {"model_type": "time_and_material"}


def _set_sp_rule(
    client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID, **overrides: Any
) -> dict:
    body = {**SP, **overrides}
    response = client.post(
        commercial_terms_path(project_id, scenario_id), json=body, headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_tm_rule(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    response = client.post(
        commercial_terms_path(project_id, scenario_id), json=TM, headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _revenue(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    response = client.get(
        commercial_terms_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    return response.json()["revenue"]


# --- K-01: price_per_point × accepted_points, exactly ------------------------------------------


def test_k_01_twenty_five_accepted_points_at_a_thousand_pln_is_exactly_25000_pln() -> None:
    """K-01, the pure formula — `Decimal`, rounded once through `round_money`."""
    result = story_points_revenue(
        price_per_point=Decimal("1000.0000"),
        accepted_points=25,
        currency="PLN",
        scenario_currency="PLN",
    )
    assert isinstance(result, RevenueResult)
    assert (result.revenue, result.currency) == (Decimal("25000.00"), "PLN")


def test_k_01_contrast_twenty_accepted_points_of_the_same_price_is_20000_not_25000() -> None:
    """K-01's contrast — the formula reacts to the *actual* `accepted_points` it is given: 20
    accepted at the same price is 20000.00, never 25000.00. `accepted_points` is the only figure
    this MVP stores (ADR-0003 addendum 2026-09-25, D-5/A: a single value, not a richer "planned vs.
    accepted" split) — this is the formula-level proof that it reads exactly that value and nothing
    fixed. Mutation: a formula that ignores `accepted_points` and always multiplies by a constant.
    """
    full = story_points_revenue(
        price_per_point=Decimal("1000.0000"),
        accepted_points=25,
        currency="PLN",
        scenario_currency="PLN",
    )
    partial = story_points_revenue(
        price_per_point=Decimal("1000.0000"),
        accepted_points=20,
        currency="PLN",
        scenario_currency="PLN",
    )
    assert isinstance(full, RevenueResult)
    assert isinstance(partial, RevenueResult)
    assert full.revenue == Decimal("25000.00")
    assert partial.revenue == Decimal("20000.00") != full.revenue


def test_k_01_end_to_end_through_the_real_endpoints(
    client: TestClient, db_session: Session
) -> None:
    """K-01, through `POST`/`GET` — the write path carries the three domain values into the same
    guarded statement T&M uses (D-6/A), and the read prices them."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")

    created = _set_sp_rule(client, project.id, scenario.id)
    assert created["commercial_terms"]["model_type"] == "story_points"
    assert (created["revenue"]["state"], created["revenue"]["amount"]) == (
        "calculated", "25000.00",
    )

    read = _revenue(client, project.id, scenario.id)
    assert (read["state"], read["amount"], read["currency"]) == ("calculated", "25000.00", "PLN")


# --- K-02: no Story Points ↔ hours conversion ---------------------------------------------------


def test_k_02_a_drastic_change_of_billable_hours_does_not_move_the_story_points_revenue(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — a Story Points revenue is blind to the staffing plan.

    The contrast is the *same shape of plan*, T&M-ruled, in a sibling scenario of the same project:
    it must react to the same change. Mutation: a hidden hourly multiplier on the Story Points
    branch of the dispatcher (`app.data.commercial_terms._story_points` reading `_billable_months`).
    """
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    sp_scenario = make_scenario(db_session, project, name="Story Points")
    tm_scenario = make_scenario(db_session, project, name="Time & Material")
    dimensions = make_dimension_tuple(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_selling_rate=Decimal("200.0000"), currency="PLN",
    )
    sp_position = make_staffing_position(db_session, sp_scenario, dimensions, start_date=MAR)
    make_allocation(db_session, sp_position, period_month=MAR, billable_hours=Decimal("10.00"))
    tm_position = make_staffing_position(db_session, tm_scenario, dimensions, start_date=MAR)
    make_allocation(db_session, tm_position, period_month=MAR, billable_hours=Decimal("10.00"))

    _set_sp_rule(client, project.id, sp_scenario.id)
    _set_tm_rule(client, project.id, tm_scenario.id)

    sp_before = _revenue(client, project.id, sp_scenario.id)
    tm_before = _revenue(client, project.id, tm_scenario.id)
    assert sp_before["amount"] == "25000.00"
    # 10h × 200 PLN/h = 2000.00 — `billable_hours` already includes headcount (ADR-0003, point 6),
    # not multiplied a second time.
    assert tm_before["amount"] == "2000.00"

    # A drastic change of the plan: the same allocation rows, ten thousand times the hours.
    db_session.execute(
        sa.update(StaffingPositionAllocation)
        .where(StaffingPositionAllocation.position_id == sp_position.id)
        .values(billable_hours=Decimal("100000.00"))
    )
    db_session.execute(
        sa.update(StaffingPositionAllocation)
        .where(StaffingPositionAllocation.position_id == tm_position.id)
        .values(billable_hours=Decimal("100000.00"))
    )
    db_session.flush()
    db_session.expire_all()

    sp_after = _revenue(client, project.id, sp_scenario.id)
    tm_after = _revenue(client, project.id, tm_scenario.id)

    assert sp_after["amount"] == sp_before["amount"] == "25000.00", (
        "the Story Points revenue moved when the staffing plan changed"
    )
    assert sp_after["assumptions_used"]["hours_source"] == "not_applicable"
    assert tm_after["amount"] != tm_before["amount"], (
        "the contrast (T&M) did not react to the same change — the fixture proves nothing"
    )


# --- K-03: the dispatcher, on two real models in one database ------------------------------------


def test_k_03_time_and_material_and_story_points_coexist_without_interference(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — a real T&M rule and a real Story Points rule of two scenarios of one project, priced
    independently and correctly in the same read and the same query.

    Before SC-4-04 the dispatcher's two-model shape was proven only with a `monkeypatch`ed stand-in
    (`docs/architecture/capabilities.md`, "drugi model komercyjny jeszcze nie istnieje w bazie") —
    this is the first test with two real rows.
    """
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    tm_scenario = make_scenario(db_session, project, name="Time & Material")
    sp_scenario = make_scenario(db_session, project, name="Story Points")
    dimensions = make_dimension_tuple(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_selling_rate=Decimal("200.0000"), currency="PLN",
    )
    position = make_staffing_position(db_session, tm_scenario, dimensions, start_date=MAR)
    make_allocation(db_session, position, period_month=MAR)

    _set_tm_rule(client, project.id, tm_scenario.id)
    _set_sp_rule(client, project.id, sp_scenario.id)

    tm_revenue = _revenue(client, project.id, tm_scenario.id)
    sp_revenue = _revenue(client, project.id, sp_scenario.id)
    assert (tm_revenue["state"], tm_revenue["amount"]) == ("calculated", "20000.00")
    assert (sp_revenue["state"], sp_revenue["amount"]) == ("calculated", "25000.00")

    # One query, both rows, correct discriminators — "the same query/list" the criterion asks for.
    rows = db_session.execute(
        sa.select(CommercialTerms.model_type)
        .where(CommercialTerms.scenario_id.in_((tm_scenario.id, sp_scenario.id)))
    ).scalars().all()
    assert set(rows) == {"time_and_material", "story_points"}


def test_k_03_the_registries_are_keyed_by_exactly_the_two_real_models() -> None:
    """K-03's drift guard — reading the production registries directly, no `monkeypatch`.

    Mutation named by the criterion: removing the Story Points (or Fixed Price) entry from
    `REVENUE_BY_MODEL` or `DETAIL_TABLE_BY_MODEL` while the discriminator CHECK still admits it must
    fail this assertion — a silent skip is exactly what this equality forbids.
    """
    # SC-4-02 added 'fixed_price' as a real model — the set is widened and the comparison is still
    # an equality (not `<=`), so every further model has to widen this test deliberately.
    assert set(REVENUE_BY_MODEL) == set(MODEL_TYPES) == set(DETAIL_TABLE_BY_MODEL)
    # Widened to include `outcome_based` at the SC-4-03 merge (human decision 2026-09-25);
    # the test name stays, because the SC-4-04 mutation-log row in capabilities.md refers to it.
    assert {"time_and_material", "story_points", "outcome_based", "fixed_price"} == set(MODEL_TYPES)


def test_k_03_unsupported_model_type_and_copy_refusal_still_correct_with_two_real_models(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — a genuinely unknown third `model_type` is still a named state, and a copy of it is
    still refused, in a database that also holds a real T&M row and a real Story Points row.

    Staged the same way `test_commercial_terms_schema.py`'s K-04 tests stage a third model: the
    discriminator CHECK is dropped inside the test's own transaction (never committed), a
    `'not_a_model'` rule is inserted directly, and the CHECK is implicitly restored when the test's
    transaction rolls back. `not_a_model` has no entry in `DETAIL_TABLE_BY_MODEL` — the same
    contract `time_and_material` and `story_points` satisfy is what this rule fails.
    """
    # Placeholder 'not_a_model' instead of the former 'fixed_price' (a real model since SC-4-02) — a
    # value that will never be an F-06 model, so the test is not disarmed by any real model
    # ('outcome_based' since SC-4-03 included). The test name ("two real models") describes the two
    # real T&M and SP rows in the database — still true.
    aurora = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    tm_scenario = make_scenario(db_session, aurora, name="Time & Material")
    sp_scenario = make_scenario(db_session, aurora, name="Story Points")
    dimensions = make_dimension_tuple(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_selling_rate=Decimal("200.0000"), currency="PLN",
    )
    position = make_staffing_position(db_session, tm_scenario, dimensions, start_date=MAR)
    make_allocation(db_session, position, period_month=MAR)
    _set_tm_rule(client, aurora.id, tm_scenario.id)
    _set_sp_rule(client, aurora.id, sp_scenario.id)

    borealis = make_project(db_session, name="Borealis", accessible_to=(IN_SCOPE_USER,))
    staged_scenario = make_scenario(db_session, borealis, name="Unknown model variant")
    db_session.execute(
        sa.text("ALTER TABLE commercial_terms DROP CONSTRAINT ck_commercial_terms_model_type_known")
    )
    db_session.execute(
        sa.text(
            "INSERT INTO commercial_terms (id, scenario_id, model_type)"
            " VALUES (gen_random_uuid(), :scenario_id, 'not_a_model')"
        ),
        {"scenario_id": staged_scenario.id},
    )
    db_session.flush()

    unsupported = _revenue(client, borealis.id, staged_scenario.id)
    assert unsupported["state"] == "unsupported_model_type"
    assert unsupported["amount"] == "n/a"
    assert unsupported["assumptions_used"]["model_type"] == "not_a_model"

    # The two real models are unaffected by the unknown third row living in another project.
    assert _revenue(client, aurora.id, tm_scenario.id)["amount"] == "20000.00"
    assert _revenue(client, aurora.id, sp_scenario.id)["amount"] == "25000.00"

    refused = client.post(
        f"/projects/{borealis.id}/copy", headers=as_caller(IN_SCOPE_USER)
    )
    assert refused.status_code == 409, refused.text
    assert "not_a_model" in refused.json()["detail"]

    accepted = client.post(f"/projects/{aurora.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert accepted.status_code == 201, accepted.text
    copied_project_id = uuid.UUID(accepted.json()["id"])
    copied_model_types = db_session.execute(
        sa.select(CommercialTerms.model_type)
        .join(Scenario, Scenario.id == CommercialTerms.scenario_id)
        .where(Scenario.project_id == copied_project_id)
    ).scalars().all()
    assert set(copied_model_types) == {"time_and_material", "story_points"}


# --- K-05: no cost field, by the same field-set equality T&M already proves ----------------------


def test_k_05_the_story_points_response_carries_the_same_field_set_as_time_and_materials(
    client: TestClient, db_session: Session
) -> None:
    """K-05 — a Story Points response fits the *same* pinned field set T&M's K-11 already proves has
    no cost field (`tests/test_commercial_terms_access.py`), by equality, not by a `not in` check.

    No new top-level field was added for this model (`price_per_point`/`accepted_points`/`currency`
    are request-only, D-6/A) — the read side reuses `hours_source`/`vendor_axis`/`rate_source` with
    different, honestly-named values (`"not_applicable"`, `"story_points_terms"`) rather than
    growing the shape, which is what makes this equality meaningful rather than vacuous.
    """
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")

    created = _set_sp_rule(client, project.id, scenario.id)

    assert set(created) == RESPONSE_FIELDS
    assert set(created["commercial_terms"]) == TERMS_FIELDS
    assert set(created["revenue"]) == REVENUE_FIELDS
    assert set(created["revenue"]["assumptions_used"]) == ASSUMPTIONS_FIELDS
    assert "price_per_point" not in created["revenue"]["assumptions_used"]
    assert "1000.0000" not in client.get(
        commercial_terms_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    ).text


# --- R-01: a currency other than the scenario's own is `currency_mismatch` (reviewer, gate 2) ----


def test_r_01_a_rule_priced_in_a_currency_other_than_the_scenarios_own_is_currency_mismatch() -> (
    None
):
    """R-01, the pure formula — the same `currency_mismatch` rule as the other four components
    (`revenue_time_and_material.py`, `personnel_cost.py`, `paid_absence_cost.py`,
    `additional_cost.py`), applied to `story_points_terms.currency` vs. the scenario's own.

    Before this fix `story_points_revenue` returned `RevenueResult` unconditionally — this asserts
    the named state instead of a silently cross-currency `RevenueResult`.
    """
    result = story_points_revenue(
        price_per_point=Decimal("1000.0000"),
        accepted_points=25,
        currency="EUR",
        scenario_currency="PLN",
    )
    assert isinstance(result, RevenueUnavailable)
    assert result.reason == "currency_mismatch"
    assert result.assumptions_used.currencies == ("EUR",)


def test_r_01_contrast_a_rule_in_the_scenarios_own_currency_is_still_calculated() -> None:
    """R-01's contrast — one element changed (the scenario's declared currency now matches the
    rule's): the same inputs that were `currency_mismatch` above are `RevenueResult` here.

    Guards against the exact regression the K-10 contrast (`test_commercial_revenue.py`) named for
    Time & Material: a comparison of a tuple against a bare string
    (`currencies != scenario_currency` instead of `currencies != (scenario_currency,)`) would make
    every declared currency mismatch forever, and this test would be the one that goes red.
    """
    result = story_points_revenue(
        price_per_point=Decimal("1000.0000"),
        accepted_points=25,
        currency="PLN",
        scenario_currency="PLN",
    )
    assert isinstance(result, RevenueResult)
    assert (result.revenue, result.currency) == (Decimal("25000.00"), "PLN")


def test_r_01_a_scenario_with_no_declared_currency_still_prices_the_rule() -> None:
    """R-01's other contrast — `scenario_currency=None` (the scenario declares none) must not be
    mistaken for a mismatch: the rule's own currency is used, exactly as before this fix, and
    exactly as `time_and_material_revenue` already does for `scenario_currency=None`.
    """
    result = story_points_revenue(
        price_per_point=Decimal("1000.0000"),
        accepted_points=25,
        currency="EUR",
        scenario_currency=None,
    )
    assert isinstance(result, RevenueResult)
    assert (result.revenue, result.currency) == (Decimal("25000.00"), "EUR")


def test_r_01_end_to_end_a_story_points_rule_in_a_different_currency_than_the_scenario(
    client: TestClient, db_session: Session
) -> None:
    """R-01, through `POST`/`GET` — a scenario declared in PLN with a Story Points rule priced in
    EUR is `currency_mismatch`, never a `calculated` PLN figure that is quietly a EUR one
    (`scenario_profitability` would otherwise subtract this revenue from PLN costs as if it were
    PLN, without converting — incorrect billing that still looks like an ordinary number).

    The contrast, a sibling scenario of the same project declared in the rule's own currency (EUR),
    proves the mismatch above is about the currency comparison, not about the endpoint refusing
    EUR rules altogether.
    """
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    pln_scenario = make_scenario(db_session, project, name="Declared PLN", currency="PLN")
    eur_scenario = make_scenario(db_session, project, name="Declared EUR", currency="EUR")

    _set_sp_rule(client, project.id, pln_scenario.id, currency="EUR")
    _set_sp_rule(client, project.id, eur_scenario.id, currency="EUR")

    mismatched = _revenue(client, project.id, pln_scenario.id)
    assert mismatched["state"] == "currency_mismatch"
    assert mismatched["amount"] == "n/a"
    assert mismatched["assumptions_used"]["currencies"] == ["EUR"]

    matched = _revenue(client, project.id, eur_scenario.id)
    assert (matched["state"], matched["amount"], matched["currency"]) == (
        "calculated", "25000.00", "EUR",
    )


# --- R-01 (SC-4-07 verification, round 1): one source triple on every Story Points answer --------

STORY_POINTS_SOURCE_TRIPLE = ("story_points_terms", "not_applicable", "not_applicable")
"""`rate_source` / `hours_source` / `vendor_axis` of a Story Points answer (ADR-0003, addendum
SC-4-07, point 5a) — the pairing the frontend renders, identical on the priced and the named-state
shape."""


def _source_triple(revenue: dict) -> tuple[str, str, str]:
    used = revenue["assumptions_used"]
    return (used["rate_source"], used["hours_source"], used["vendor_axis"])


def test_r_01_sc_4_07_an_incomplete_story_points_rule_names_its_own_sources_not_tm_defaults(
    client: TestClient, db_session: Session
) -> None:
    """R-01 (SC-4-07, verification round 1) — a Story Points rule **without** its
    `story_points_terms` row is `incomplete_commercial_terms` and still says `story_points_terms` /
    `not_applicable` / `not_applicable`, never the T&M defaults of `AssumptionsUsed`
    (`billable_hours` / `internal`).

    Contrast: a sibling scenario whose Story Points rule has its details row is `calculated` with
    **the same** triple — so the assertion above is about the named-state branch, not about a triple
    this model never emits. Mutation this kills: `_story_points` building `AssumptionsUsed` with
    only `model_type`/`rate_source` again (hybrid `story_points_terms` + `billable_hours` +
    `internal`, contradicting point 5a).
    """
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    incomplete = make_scenario(db_session, project, name="No details", currency="PLN")
    complete = make_scenario(db_session, project, name="With details", currency="PLN")
    make_story_points_terms(db_session, incomplete, with_details=False)
    make_story_points_terms(db_session, complete)

    missing = _revenue(client, project.id, incomplete.id)
    assert (missing["state"], missing["amount"]) == ("incomplete_commercial_terms", "n/a")
    assert missing["assumptions_used"]["model_type"] == "story_points"
    assert _source_triple(missing) == STORY_POINTS_SOURCE_TRIPLE

    priced = _revenue(client, project.id, complete.id)
    assert (priced["state"], priced["amount"], priced["currency"]) == (
        "calculated", "25000.00", "PLN",
    )
    assert _source_triple(priced) == STORY_POINTS_SOURCE_TRIPLE
