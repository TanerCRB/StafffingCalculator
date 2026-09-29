"""SC-4-03, K-06 — copying a scenario copies its Outcome-based rule with the full set of details,
and code with no branch for the model refuses on a **real** row of the other model (ADR-0004,
addendum 2026-09-25 SC-4-03, point 3; ADR-0003, addendum SC-4-03, O-6).

- copy through the only existing entry point (`POST /projects/{id}/copy`): a new rule, a new
  `outcome_terms` row pointing at **this** rule, every domain column identical, an identical result;
- `unsupported_model_type` (read) and `409` with nothing copied (copy) — proven so far only by
  simulation on a T&M row with an emptied registry (`tests/test_commercial_revenue_gate_2.py`,
  R-02/R-03), here on an `outcome_based` row stored in the database, read by a code version that
  knows only T&M — exactly the mixed-version window of ADR-0001.
"""

from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.data import commercial_terms as commercial_terms_module
from app.data.commercial_terms import TM_TERMS_COLUMNS_NOT_COPIED
from app.models import CommercialTerms, OutcomeTerms, Scenario, TmTerms
from app.models.commercial_terms import MODEL_TYPE_TIME_AND_MATERIAL
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    commercial_terms_path,
    make_outcome_terms,
    make_project,
    make_scenario,
)

FULL_DETAILS = {
    "currency": "PLN",
    "fixed_fee": Decimal("20000"),
    "success_bonus": Decimal("10000"),
    "unit_rate": Decimal("100"),
    "revenue_min": Decimal("22000"),
    "revenue_max": Decimal("40000"),
    "not_achieved_units": Decimal("0"),
    "partial_units": Decimal("50"),
    "achieved_units": Decimal("80"),
    "exceeded_units": Decimal("150"),
    "not_achieved_probability": Decimal("10.00"),
    "partial_probability": Decimal("20.00"),
    "achieved_probability": Decimal("40.00"),
    "exceeded_probability": Decimal("30.00"),
}
"""Every domain column set and different from the fixture's default value — a copier that skips
any of them changes the result or the row comparison."""


def _details_of(session: Session, rule_id) -> dict[str, object]:
    row = session.execute(
        sa.select(OutcomeTerms).where(OutcomeTerms.commercial_terms_id == rule_id)
    ).scalar_one_or_none()
    assert row is not None, "the rule has no outcome details row — an incomplete rule"
    return {
        attribute.key: getattr(row, attribute.key)
        for attribute in sa.inspect(OutcomeTerms).column_attrs
        if attribute.key not in TM_TERMS_COLUMNS_NOT_COPIED
    }


def test_k_06_copying_a_scenario_copies_its_outcome_rule_with_every_detail_and_new_identifiers(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — the copy has its own `outcome_based` rule, its own `outcome_terms` row pointing at
    this rule, every domain column equal to the source, and responds with the same revenue
    (guaranteed, expected, per category). The source untouched.

    Mutations: a copier with no details row (`incomplete_commercial_terms` on the copy); a domain
    column omitted (e.g. probabilities → `no_probabilities`, `revenue_max` → a different "exceeded"
    revenue); a details row pointing at the source rule.
    """
    project = make_project(db_session, name="Outcome copy", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", currency="PLN")
    source_rule = make_outcome_terms(db_session, scenario, **FULL_DETAILS)
    source_read = client.get(
        commercial_terms_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    ).json()

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    db_session.expire_all()
    copy = db_session.execute(
        sa.select(Scenario).where(Scenario.project_id == response.json()["id"])
    ).scalar_one()

    copied_rule = db_session.execute(
        sa.select(CommercialTerms).where(CommercialTerms.scenario_id == copy.id)
    ).scalar_one()
    assert copied_rule.id != source_rule.id
    assert copied_rule.model_type == "outcome_based"
    assert _details_of(db_session, copied_rule.id) == _details_of(db_session, source_rule.id)
    assert _details_of(db_session, source_rule.id) == {"model_type": "outcome_based",
    **FULL_DETAILS}

    copy_read = client.get(
        commercial_terms_path(copy.project_id, copy.id), headers=as_caller(IN_SCOPE_USER)
    ).json()
    assert copy_read["commercial_terms"]["id"] == str(copied_rule.id)
    assert copy_read["revenue"] == source_read["revenue"]
    assert copy_read["revenue"]["state"] == "calculated"
    assert copy_read["revenue"]["expected_state"] == "calculated"


def test_every_outcome_details_column_is_either_copied_or_explicitly_excluded() -> None:
    """A drift guard for the new details table — a column added later forces a decision instead of
    being silently copied or skipped (the `TM_TERMS_COLUMNS_NOT_COPIED` convention)."""
    columns = {attribute.key for attribute in sa.inspect(OutcomeTerms).column_attrs}

    assert columns == {"model_type", *FULL_DETAILS} | TM_TERMS_COLUMNS_NOT_COPIED


# --- registries with no branch for a real row of the second model ---------------------------------


def test_k_06_a_real_outcome_rule_read_by_code_without_its_branch_is_unsupported_model_type(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K-06/O-6 — a real `outcome_based` row (accepted by the widened CHECK), read by a dispatcher
    that knows only T&M: `200`, `unsupported_model_type`, `"n/a"`, the stored model named in the
    response — never `KeyError`/`500`, never a price computed by the T&M formula. Contrast: the
    same read with the full registry → `calculated`."""
    project = make_project(db_session, name="Outcome mixed", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", currency="PLN")
    make_outcome_terms(db_session, scenario)
    path = commercial_terms_path(project.id, scenario.id)
    assert client.get(path, headers=as_caller(IN_SCOPE_USER)).json()["revenue"]["state"] == (
        "calculated"
    )

    monkeypatch.setattr(
        commercial_terms_module,
        "REVENUE_BY_MODEL",
        {
            MODEL_TYPE_TIME_AND_MATERIAL: commercial_terms_module.REVENUE_BY_MODEL[
                MODEL_TYPE_TIME_AND_MATERIAL
            ]
        },
    )
    body = client.get(path, headers=as_caller(IN_SCOPE_USER)).json()

    assert body["commercial_terms"]["model_type"] == "outcome_based"
    assert (body["revenue"]["state"], body["revenue"]["amount"]) == (
        "unsupported_model_type",
        "n/a",
    )
    assert body["revenue"]["expected_amount"] == "n/a"
    assert body["revenue"]["assumptions_used"]["model_type"] == "outcome_based"


def test_k_06_copying_a_real_outcome_rule_with_code_that_cannot_copy_it_is_409_and_copies_nothing(
    committing_client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K-06/O-6 — a real `outcome_based` row, committed to the database; a copier that knows only
    `tm_terms`: the whole project copy is a `409` naming the model, and nothing new lands in the
    database — no project, no scenario, no rule, no details row (counted from a separate
    connection). Contrast after restoring the registry: `201` and the whole set copied, including
    `outcome_terms`."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Outcome copy 409", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Baseline", currency="PLN")
        make_outcome_terms(setup, scenario, **FULL_DETAILS)
        project_id = project.id
        setup.commit()

    def counts() -> tuple[int, ...]:
        with engine.connect() as connection:
            return tuple(
                connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in ("projects", "scenarios", "commercial_terms", "outcome_terms")
            )

    before = counts()
    monkeypatch.setattr(
        commercial_terms_module,
        "DETAIL_TABLE_BY_MODEL",
        {MODEL_TYPE_TIME_AND_MATERIAL: TmTerms.__table__},
    )
    refused = committing_client.post(
        f"/projects/{project_id}/copy", headers=as_caller(IN_SCOPE_USER)
    )

    assert refused.status_code == 409, refused.text
    assert "outcome_based" in refused.json()["detail"]
    assert counts() == before == (1, 1, 1, 1)

    monkeypatch.undo()
    accepted = committing_client.post(
        f"/projects/{project_id}/copy", headers=as_caller(IN_SCOPE_USER)
    )
    assert accepted.status_code == 201, accepted.text
    assert counts() == (2, 2, 2, 2)
