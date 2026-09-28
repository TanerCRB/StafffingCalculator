"""SC-4-02, K-02 — a Fixed Price price belongs to exactly one scenario (F-02; ADR-0004, addendum
2026-09-25 SC-4-02, point 2; controls FPS-2, FPS-3).

- two Fixed Price scenarios of one project each read **their own** price;
- a project copy (SC-1-03) and a scenario duplicate (SC-6-01) carry the rule **and** its details row
  with new identifiers — the details row pointing at the copy's rule — with an equal price and
  currency. `fixed_price_terms` is the first details table whose copy carries domain values, copied
  by reflection with only the key and the copy's own timestamp excluded (the convention of
  `tm_terms`, per model now: `DETAIL_COLUMNS_NOT_COPIED_BY_MODEL`);
- editing the price on the copy leaves the source untouched (AC-02);
- the duplicate of an **approved** Fixed Price scenario is a draft with zero snapshot rows and the
  source's price.

Every copy is reached through its real endpoint.
"""

import uuid
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.data.commercial_terms import (
    DETAIL_COLUMNS_NOT_COPIED_BY_MODEL,
    FIXED_PRICE_TERMS_COLUMNS_NOT_COPIED,
)
from app.models import CommercialTerms, FixedPriceTerms, Scenario, ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    commercial_terms_path,
    count_snapshot_rows,
    make_fixed_price_terms,
    make_project,
    make_scenario,
)


def duplicate_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/scenarios/{scenario_id}/duplicate"


def _read(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    response = client.get(
        commercial_terms_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _rows_of(session: Session, scenario_id: uuid.UUID) -> tuple[CommercialTerms, FixedPriceTerms]:
    """The rule of one scenario and **the details row keyed by that rule's id** — both required."""
    session.expire_all()
    rule = session.execute(
        sa.select(CommercialTerms).where(CommercialTerms.scenario_id == scenario_id)
    ).scalar_one()
    details = session.execute(
        sa.select(FixedPriceTerms).where(FixedPriceTerms.commercial_terms_id == rule.id)
    ).scalar_one_or_none()
    assert details is not None, (
        "the scenario has a Fixed Price rule and no price row pointing at it — an incomplete rule, "
        "not a copied one"
    )
    return rule, details


def _edit_price(
    client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID, **changes: str
) -> dict:
    marker = _read(client, project_id, scenario_id)["commercial_terms"]["updated_at"]
    response = client.patch(
        commercial_terms_path(project_id, scenario_id),
        json={"updated_at": marker, **changes},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_k_02_two_fixed_price_scenarios_of_one_project_each_read_their_own_price(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — the price is read by **this rule's id**, never "the Fixed Price row" of the project.
    Two scenarios, two prices in two currencies, each created through the real `POST`. Mutation: the
    details read not filtered by the rule's id (any single row) — one scenario reads the other's
    price."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    first = make_scenario(db_session, project, name="Offer A")
    second = make_scenario(db_session, project, name="Offer B")
    for scenario, price, currency in ((first, "150000", "PLN"), (second, "90000.5", "EUR")):
        response = client.post(
            commercial_terms_path(project.id, scenario.id),
            json={"model_type": "fixed_price", "agreed_price": price, "currency": currency},
            headers=as_caller(IN_SCOPE_USER),
        )
        assert response.status_code == 201, response.text

    first_body = _read(client, project.id, first.id)
    second_body = _read(client, project.id, second.id)

    assert (first_body["revenue"]["amount"], first_body["revenue"]["currency"]) == (
        "150000.00",
        "PLN",
    )
    assert (second_body["revenue"]["amount"], second_body["revenue"]["currency"]) == (
        "90000.50",
        "EUR",
    )
    assert first_body["commercial_terms"]["id"] != second_body["commercial_terms"]["id"]


def test_k_02_a_project_copy_carries_the_rule_and_the_price_row_with_new_identifiers(
    client: TestClient, db_session: Session
) -> None:
    """K-02 / FPS-2 through `POST /projects/{id}/copy` — the copy's rule and details row are new
    rows, the details row points at **the copy's** rule, price and currency are equal, and the copy
    prices to the same revenue. Mutations: the copier excluding the domain columns (the insert of
    the copy's details row fails `NOT NULL`, i.e. the copy fails); the details row re-pointed at the
    source's rule (no details row for the copy's rule → the assertion in `_rows_of`)."""
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    source = make_scenario(db_session, project, name="Baseline", currency="PLN")
    make_fixed_price_terms(db_session, source, agreed_price=Decimal("150000.1234"), currency="PLN")

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    copy_project_id = uuid.UUID(response.json()["id"])
    db_session.expire_all()
    copy_id = db_session.execute(
        sa.select(Scenario.id).where(Scenario.project_id == copy_project_id)
    ).scalar_one()

    source_rule, source_details = _rows_of(db_session, source.id)
    copy_rule, copy_details = _rows_of(db_session, copy_id)
    assert copy_rule.id != source_rule.id
    assert copy_details.commercial_terms_id == copy_rule.id
    assert copy_rule.model_type == copy_details.model_type == "fixed_price"
    assert (copy_details.agreed_price, copy_details.currency) == (
        source_details.agreed_price,
        source_details.currency,
    ) == (Decimal("150000.1234"), "PLN")

    copy_body = _read(client, copy_project_id, copy_id)
    source_body = _read(client, project.id, source.id)
    assert copy_body["revenue"] == source_body["revenue"]
    assert copy_body["commercial_terms"]["agreed_price"] == "150000.1234"


def test_k_02_a_duplicate_carries_the_price_and_editing_it_leaves_the_source_untouched(
    client: TestClient, db_session: Session
) -> None:
    """K-02 / FPS-2 through `POST …/duplicate` (SC-6-01), then the real price edit on the duplicate:
    the duplicate's price changes, the source's rule — price, currency, **and its marker** — does
    not (AC-02). Mutation: the duplicate's details row sharing the source's rule id — the edit then
    rewrites the source's price."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    source = make_scenario(db_session, project, name="Baseline")
    make_fixed_price_terms(db_session, source, agreed_price=Decimal("150000.0000"), currency="PLN")
    source_before = _read(client, project.id, source.id)

    response = client.post(duplicate_path(project.id, source.id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    duplicate_id = uuid.UUID(response.json()["id"])

    source_rule, _ = _rows_of(db_session, source.id)
    duplicate_rule, duplicate_details = _rows_of(db_session, duplicate_id)
    assert duplicate_rule.id != source_rule.id
    assert duplicate_details.commercial_terms_id == duplicate_rule.id
    assert (duplicate_details.agreed_price, duplicate_details.currency) == (
        Decimal("150000.0000"),
        "PLN",
    )

    edited = _edit_price(client, project.id, duplicate_id, agreed_price="175000", currency="EUR")
    assert (edited["revenue"]["amount"], edited["revenue"]["currency"]) == ("175000.00", "EUR")
    assert _read(client, project.id, source.id) == source_before


def test_k_02_the_duplicate_of_an_approved_fixed_price_scenario_is_a_draft_with_no_snapshot(
    client: TestClient, db_session: Session
) -> None:
    """FPS-3 — the copy of an approved Fixed Price scenario is a draft, has zero snapshot rows (the
    price is never frozen, so there is nothing of it to carry), and has the source's price."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    source = make_scenario(db_session, project, name="Approved v1", status=ScenarioStatus.APPROVED)
    make_fixed_price_terms(db_session, source, agreed_price=Decimal("150000.0000"), currency="PLN")

    response = client.post(duplicate_path(project.id, source.id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    duplicate_id = uuid.UUID(response.json()["id"])

    assert response.json()["status"] == "Draft"
    assert count_snapshot_rows(db_session, duplicate_id) == 0
    body = _read(client, project.id, duplicate_id)
    assert body["commercial_terms"]["agreed_price"] == "150000.0000"
    assert body["revenue"]["amount"] == "150000.00"


def test_every_fixed_price_details_column_is_either_copied_or_explicitly_excluded() -> None:
    """Drift guard for the first details table with domain columns: its mapped attributes are the
    copied set plus the excluded set, so a column added later forces the decision. And the copier
    applies **this** table's set to it (the registry), never the T&M one by name."""
    columns = {attribute.key for attribute in sa.inspect(FixedPriceTerms).column_attrs}

    assert columns == {"model_type", "agreed_price", "currency"} | (
        FIXED_PRICE_TERMS_COLUMNS_NOT_COPIED
    )
    assert FIXED_PRICE_TERMS_COLUMNS_NOT_COPIED == {"commercial_terms_id", "created_at"}
    assert DETAIL_COLUMNS_NOT_COPIED_BY_MODEL["fixed_price"] is FIXED_PRICE_TERMS_COLUMNS_NOT_COPIED
