"""SC-4-02 — tests of the fixes decided by the human after verification (2026-09-28, Issue #66).

- **R-01 — one price per answer.** `app.data.commercial_terms._view_of` used to read the Fixed Price
  row twice (once to price the revenue, once for `commercial_terms.agreed_price`); under `READ
  COMMITTED` a price edit committed between the two statements gave an answer whose revenue and
  stated price disagreed. The price is now read once and that one value feeds both.
- **R-03 — the price edit refuses a scenario whose whole-scenario rule is not its only rule.** With
  a whole-scenario Fixed Price rule next to a segment-scoped rule (SC-4-05 `scope_ref`), the edit
  used to commit and then fail in the answer's single-rule read
  (`MultipleCommercialRulesNotSupported` — a `500` after a write that landed). It is now refused
  inside the guarded `UPDATE` (`CommercialTermsEditAmbiguous`, `409`) and nothing is written. The
  other R-03 state — the only Fixed Price rule is segment-scoped — is QA's
  `tests/test_fixed_price_edit_boundaries.py::test_k_05_the_price_edit_does_not_reach_a_segment_scoped_fixed_price_rule`
  (`404`, unchanged).

The harness is the one `tests/test_fixed_price_edit_boundaries.py` and
`tests/test_fixed_price_guards.py` use: committed rows, a cursor hook that commits a competing write
on another connection at a chosen statement, and assertions read from a separate connection.
"""

import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.models import CommercialTerms, FixedPriceTerms
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    commercial_terms_path,
    make_fixed_price_terms,
    make_project,
    make_scenario,
    make_scenario_delivery_segment,
)
from tests.test_fixed_price_guards import _committed_project, _price_of

COMPETING_PRICE = Decimal("200000.0000")

PRICE_READ = "fixed_price_terms.agreed_price"
"""The select list of the Fixed Price row read (`_fixed_price_details_of`) — the only statement of
`GET …/commercial-terms` that names this column; `_rule_of`'s `EXISTS` names none."""


# --- R-01: revenue and agreed_price come from one read -------------------------------------------


def test_r_01_a_price_edit_committed_during_the_read_cannot_split_revenue_and_agreed_price(
    committing_client: TestClient, engine: Engine
) -> None:
    """A committed draft priced at 150000 PLN. Right after the read of its Fixed Price row returns,
    another connection commits a new price (200000) and rotates the marker — what
    `update_fixed_price` itself writes. The answer must state **one** price: `revenue.amount` is
    the rounded `commercial_terms.agreed_price` of the same answer.

    Mutation killed: `_view_of` reading the row a second time for `agreed_price` (the first version)
    — the answer pairs revenue `150000.00` with `agreed_price` `200000.0000`."""
    state = _committed_project(engine, priced_draft=True)
    project_id, draft_id = state["project_id"], state["draft_id"]
    fired: list[str] = []

    def competing_edit_after_the_price_read(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if fired or PRICE_READ not in statement.lower():
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(
                sa.text(
                    "UPDATE fixed_price_terms f SET agreed_price = :price FROM commercial_terms c"
                    " WHERE c.id = f.commercial_terms_id AND c.scenario_id = :id"
                ),
                {"price": COMPETING_PRICE, "id": draft_id},
            )
            competitor.execute(
                sa.text("UPDATE commercial_terms SET updated_at = now() WHERE scenario_id = :id"),
                {"id": draft_id},
            )

    event.listen(Engine, "after_cursor_execute", competing_edit_after_the_price_read)
    try:
        response = committing_client.get(
            commercial_terms_path(project_id, draft_id), headers=as_caller(IN_SCOPE_USER)
        )
    finally:
        event.remove(Engine, "after_cursor_execute", competing_edit_after_the_price_read)

    assert fired, "the Fixed Price row was never read — nothing below is about R-01"
    assert response.status_code == 200, response.text
    body = response.json()
    stated = Decimal(body["commercial_terms"]["agreed_price"])
    assert body["revenue"]["state"] == "calculated"
    assert Decimal(body["revenue"]["amount"]) == stated.quantize(Decimal("0.01"))
    # The read began before the competing commit: the one price it states is the old one.
    assert (body["revenue"]["amount"], body["commercial_terms"]["agreed_price"]) == (
        "150000.00",
        "150000.0000",
    )
    # N-01 (Guardian, rule 21): the competing write really landed — read on a separate connection,
    # so the agreement above is not the vacuous one of a race that never happened.
    assert _price_of(engine, draft_id)[:2] == (COMPETING_PRICE, "PLN")


# --- R-03: a whole-scenario rule next to a segment-scoped one is refused before any write --------


def _rules_of(session: Session, scenario_id: uuid.UUID) -> dict[object, tuple[Decimal, object]]:
    """`scope_ref` → (agreed price, rule marker) for every Fixed Price rule of the scenario."""
    session.expire_all()
    rows = session.execute(
        sa.select(
            CommercialTerms.scope_ref, FixedPriceTerms.agreed_price, CommercialTerms.updated_at
        )
        .join(FixedPriceTerms, FixedPriceTerms.commercial_terms_id == CommercialTerms.id)
        .where(CommercialTerms.scenario_id == scenario_id)
    ).all()
    return {scope_ref: (price, marker) for scope_ref, price, marker in rows}


def test_r_03_the_price_edit_of_a_scenario_with_a_second_segment_rule_is_refused_unwritten(
    client: TestClient, db_session: Session
) -> None:
    """A draft with a whole-scenario Fixed Price rule (150000) **and** a segment-scoped Fixed Price
    rule (120000). The client sends the whole-scenario rule's real, current marker — read from the
    database, because the single-rule `GET` cannot answer for such a scenario — so neither the
    marker nor `approved` can refuse the edit. Expected: `409` naming the reason ("more than one
    set of commercial terms"), and the price and the marker of **both** rules unchanged.

    Contrast (one element changed — no segment rule): the same edit is `200`
    (`tests/test_fixed_price_edit_boundaries.py::test_k_05_the_price_edit_does_not_reach_a_segment_scoped_fixed_price_rule`,
    its whole-scenario half, and the K-05 edit tests of `test_fixed_price_guards.py`).

    Mutation killed: the `NOT EXISTS` other-rule predicate dropped from the guarded `UPDATE` — the
    whole-scenario price is written and committed, and the answer's read then raises
    `MultipleCommercialRulesNotSupported` (a `500`)."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Two rules")
    whole = make_fixed_price_terms(db_session, scenario)
    segment = make_scenario_delivery_segment(db_session, scenario)
    make_fixed_price_terms(
        db_session, scenario, agreed_price=Decimal("120000.0000"), scope_ref=segment.id
    )
    before = _rules_of(db_session, scenario.id)
    assert set(before) == {None, segment.id}

    response = client.patch(
        commercial_terms_path(project.id, scenario.id),
        json={"updated_at": whole.updated_at.isoformat(), "agreed_price": "175000"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "more than one set of commercial terms" in detail
    assert "approved" not in detail and "concurrency marker" not in detail
    assert _rules_of(db_session, scenario.id) == before
