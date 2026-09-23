"""SC-4-01, K-07 — copying a scenario copies its rule **and** its details row, as one aggregate.

ADR-0004, addendum 2026-09-23 SC-4-01, point 1b: one entry in `SCENARIO_CHILD_COPIERS` copies
`commercial_terms` and, in the same function, `tm_terms`, with new identifiers. **A copy that has
the rule and not its details row is a regression, not a legal state**: it is
`incomplete_commercial_terms`
on the copy of a scenario that was priced — so the canary asserts the details row by id, and asserts
the revenue the copy answers with.

The copy is reached through the one copy entry point that exists (`POST /projects/{id}/copy`, which
calls `copy_scenario` per scenario — ADR-0004, addendum 2026-09-18).
"""

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.data.commercial_terms import (
    COMMERCIAL_TERMS_COLUMNS_NOT_COPIED,
    TM_TERMS_COLUMNS_NOT_COPIED,
    copy_commercial_terms,
)
from app.data.project_writes import SCENARIO_CHILD_COPIERS
from app.models import CommercialTerms, Scenario, TmTerms
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    commercial_terms_path,
    make_allocation,
    make_commercial_terms,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
)

MAR = date(2026, 3, 1)


def _copied_scenario_id(session: Session, response) -> uuid.UUID:
    assert response.status_code == 201, response.text
    session.expire_all()
    return session.execute(
        sa.select(Scenario.id).where(Scenario.project_id == uuid.UUID(response.json()["id"]))
    ).scalar_one()


def test_k_07_copying_a_scenario_copies_its_rule_and_details_row_with_new_identifiers(
    client: TestClient, db_session: Session
) -> None:
    """K-07 — the copy has its own rule, its own details row pointing at **that** rule, and prices
    to the same revenue as the source; the source's rows are untouched.

    Mutations this kills, each on its own assertion: the copier not registered (no rule on the
    copy); the copier copying `commercial_terms` only (no details row → `incomplete_…`); the details
    row re-pointed at the *source's* rule id (its `commercial_terms_id` is not the copy's rule); the
    source's id reused (a primary-key violation, i.e. a failed copy).
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session)
    position = make_staffing_position(db_session, scenario, dimensions, start_date=MAR)
    make_allocation(db_session, position, period_month=MAR)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_selling_rate=Decimal("200.0000"), currency="PLN",
    )
    source_rule = make_commercial_terms(db_session, scenario)

    copy_id = _copied_scenario_id(
        db_session, client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    )

    copied_rule = db_session.execute(
        sa.select(CommercialTerms).where(CommercialTerms.scenario_id == copy_id)
    ).scalar_one()
    assert copied_rule.id != source_rule.id
    assert copied_rule.model_type == source_rule.model_type == "time_and_material"
    copied_details = db_session.execute(
        sa.select(TmTerms).where(TmTerms.commercial_terms_id == copied_rule.id)
    ).scalar_one_or_none()
    assert copied_details is not None, (
        "the copy has a rule and no details row — an incomplete rule, not a copied one"
    )
    source_details = db_session.execute(
        sa.select(TmTerms).where(TmTerms.commercial_terms_id == source_rule.id)
    ).scalar_one()
    assert source_details.model_type == "time_and_material"
    assert db_session.execute(
        sa.select(sa.func.count()).select_from(CommercialTerms)
    ).scalar_one() == 2

    copy_project_id = db_session.get(Scenario, copy_id).project_id
    read = client.get(
        commercial_terms_path(copy_project_id, copy_id), headers=as_caller(IN_SCOPE_USER)
    ).json()
    assert read["commercial_terms"]["id"] == str(copied_rule.id)
    assert (read["revenue"]["state"], read["revenue"]["amount"]) == ("calculated", "20000.00")


def test_k_07_a_scenario_without_a_rule_is_copied_without_one(
    client: TestClient, db_session: Session
) -> None:
    """K-07's contrast — nothing to copy is nothing copied: no rule appears from nowhere, and the
    copy answers `no_commercial_terms` like its source."""
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    make_scenario(db_session, project, name="Baseline")

    copy_id = _copied_scenario_id(
        db_session, client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    )

    assert db_session.execute(
        sa.select(CommercialTerms).where(CommercialTerms.scenario_id == copy_id)
    ).scalar_one_or_none() is None


def test_k_07_the_commercial_rule_copier_is_one_registered_entry_for_the_aggregate() -> None:
    """The registry holds the aggregate's copier — once, and there is no second entry for
    `tm_terms` (ADR-0004, addendum 2026-09-19, point 1: one entry per aggregate)."""
    assert SCENARIO_CHILD_COPIERS.count(copy_commercial_terms) == 1


def test_every_rule_and_details_column_is_either_copied_or_explicitly_excluded() -> None:
    """Drift guard, per table: a column added later forces the decision instead of being silently
    copied or silently dropped (the guard `app.data.column_copy` calls for)."""
    rule_columns = {attribute.key for attribute in sa.inspect(CommercialTerms).column_attrs}
    details_columns = {attribute.key for attribute in sa.inspect(TmTerms).column_attrs}

    assert rule_columns == {"model_type"} | COMMERCIAL_TERMS_COLUMNS_NOT_COPIED
    assert details_columns == {"model_type"} | TM_TERMS_COLUMNS_NOT_COPIED
