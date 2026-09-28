"""SC-4-02, K-05 — two boundaries of the Fixed Price price edit (`PATCH …/commercial-terms`,
`app.data.commercial_terms.update_fixed_price`) that the first version of the suite did not force
(QA, mutations SC-4-02-M14 and SC-4-02-M15 survived all 1135 tests):

1. **The ADR-0007 marker is compared by the statement that writes, not before it.** The existing
   `test_fixed_price_guards.py::test_k_05_a_stale_marker_is_a_409_distinguishable_from_the_approved_409`
   runs the two edits one after the other, so the second editor's marker is already stale when its
   request starts — a Python comparison of the marker before a plain `UPDATE` (M14) refuses it just
   as well. Here the competing edit commits *between* the second editor's reads and its guarded
   `UPDATE`: only a marker predicate inside that `UPDATE` sees it.
2. **The edit reaches only the whole-scenario rule (`scope_ref IS NULL`).** No test created a
   segment-scoped Fixed Price rule, so dropping the predicate from the `UPDATE` (M15) changed no
   outcome. Here the scenario's only Fixed Price rule is segment-scoped, and the client sends its
   real, current marker: without the predicate the price is rewritten; with it the edit is the
   "no Fixed Price terms to edit" `404` the function's docstring declares (sync of 2026-09-28,
   SC-4-05 adaptation) and nothing changes.

Each refusal has its contrast differing in exactly one element: no competing commit (1); a
whole-scenario rule instead of a segment-scoped one (2) — both `200`.
"""

import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.api.commercial_terms import COMMERCIAL_TERMS_NOT_FOUND_DETAIL
from app.data.commercial_terms import revenue_of, rules_of_scenario
from app.domain.revenue import RevenueResult, RevenueUnavailable
from app.models import CommercialTerms, FixedPriceTerms
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    commercial_terms_path,
    make_commercial_terms,
    make_fixed_price_terms,
    make_project,
    make_scenario,
    make_scenario_delivery_segment,
)
from tests.test_fixed_price_guards import _committed_project, _marker, _price_of
from tests.test_fixed_price_revenue import AC_07_PRICE, GAP_MONTH, _ac_07_project, _staffed

COMPETING_PRICE = Decimal("160000.0000")
LATE_PRICE = "170000"


def _patch_price(
    client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID, marker: str, price: str
) -> Any:
    return client.patch(
        commercial_terms_path(project_id, scenario_id),
        json={"updated_at": marker, "agreed_price": price},
        headers=as_caller(IN_SCOPE_USER),
    )


# --- 1. the marker inside the writing statement (kills SC-4-02-M14) ------------------------------


def test_k_05_a_competing_edit_committed_just_before_the_guarded_update_is_the_marker_409(
    committing_client: TestClient, engine: Engine
) -> None:
    """Two editors read the same marker. The second editor's request passes every read it makes;
    immediately before its guarded `UPDATE` runs, the first editor's edit commits on another
    connection (price → 160000, marker rotated by the database — what `update_fixed_price` itself
    writes). The second edit must be the `409` naming the concurrency marker, and the first
    editor's price must stay.

    Mutation killed: the marker compared in Python before the statement and left out of its
    `WHERE` (SC-4-02-M14) — the comparison ran before the competing commit, so the late edit
    overwrote it (a lost update) and answered `200`."""
    state = _committed_project(engine, priced_draft=True)
    project_id, draft_id = state["project_id"], state["draft_id"]
    shared_marker = _marker(committing_client, project_id, draft_id)
    fired: list[str] = []

    def competing_edit_first(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if fired or "update commercial_terms set" not in statement.lower():
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

    event.listen(Engine, "before_cursor_execute", competing_edit_first)
    try:
        late = _patch_price(committing_client, project_id, draft_id, shared_marker, LATE_PRICE)
    finally:
        event.remove(Engine, "before_cursor_execute", competing_edit_first)

    assert fired, "the competing edit never landed inside the window"
    assert late.status_code == 409, late.text
    detail = late.json()["detail"]
    assert "concurrency marker" in detail and "approved" not in detail
    assert _price_of(engine, draft_id)[:2] == (COMPETING_PRICE, "PLN")


def test_k_05_contrast_the_same_edit_without_a_competing_commit_is_applied(
    committing_client: TestClient, engine: Engine
) -> None:
    """The contrast — the same fixture, the same marker, the same late price, and no competing
    commit: `200`, the price written and the marker rotated. What stops the test above from being
    satisfied by an edit path that refuses every edit."""
    state = _committed_project(engine, priced_draft=True)
    project_id, draft_id = state["project_id"], state["draft_id"]
    shared_marker = _marker(committing_client, project_id, draft_id)
    before = _price_of(engine, draft_id)

    edited = _patch_price(committing_client, project_id, draft_id, shared_marker, LATE_PRICE)

    assert edited.status_code == 200, edited.text
    after = _price_of(engine, draft_id)
    assert after[:2] == (Decimal("170000.0000"), "PLN")
    assert after[2] != before[2]


# --- 2. the whole-scenario rule only (kills SC-4-02-M15) -----------------------------------------


def _stored(session: Session, scenario_id: uuid.UUID) -> tuple[Decimal, object]:
    """(agreed price, rule marker) of the scenario's one Fixed Price rule, as stored."""
    session.expire_all()
    rule = session.execute(
        sa.select(CommercialTerms).where(CommercialTerms.scenario_id == scenario_id)
    ).scalar_one()
    price = session.execute(
        sa.select(FixedPriceTerms.agreed_price).where(
            FixedPriceTerms.commercial_terms_id == rule.id
        )
    ).scalar_one()
    return price, rule.updated_at


def test_k_05_the_price_edit_does_not_reach_a_segment_scoped_fixed_price_rule(
    client: TestClient, db_session: Session
) -> None:
    """A scenario whose only Fixed Price rule is segment-scoped (`scope_ref` set — writable only at
    the data layer today, SC-4-05). The client sends that rule's real, current marker, read through
    `GET …/commercial-terms`, so neither the marker nor `approved` can refuse the edit: only the
    `scope_ref IS NULL` predicate of the guarded `UPDATE` does. Expected: the "no Fixed Price terms
    to edit" `404` (not the scope body), price and marker unchanged.

    Contrast, one element changed: the same rule as the whole-scenario rule (`scope_ref` `NULL`) →
    `200` with the new price.

    Mutation killed: `scope_ref IS NULL` dropped from the `UPDATE`'s `WHERE` (SC-4-02-M15) — the
    segment rule's price is rewritten and the edit answers `200`."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    segmented = make_scenario(db_session, project, name="Segmented")
    segment = make_scenario_delivery_segment(db_session, segmented)
    make_fixed_price_terms(db_session, segmented, scope_ref=segment.id)
    whole = make_scenario(db_session, project, name="Whole")
    make_fixed_price_terms(db_session, whole)

    segmented_before = _stored(db_session, segmented.id)
    refused = _patch_price(
        client, project.id, segmented.id, _marker(client, project.id, segmented.id), "175000"
    )
    assert refused.status_code == 404, refused.text
    assert refused.json()["detail"] != COMMERCIAL_TERMS_NOT_FOUND_DETAIL
    assert "Fixed Price" in refused.json()["detail"]
    assert _stored(db_session, segmented.id) == segmented_before

    accepted = _patch_price(
        client, project.id, whole.id, _marker(client, project.id, whole.id), "175000"
    )
    assert accepted.status_code == 200, accepted.text
    assert _stored(db_session, whole.id)[0] == Decimal("175000.0000")


# --- 3. "another rule" means any model, not only Fixed Price (kills SC-4-02-M33) -----------------


def test_r_03_a_segment_rule_of_another_model_also_refuses_the_price_edit_unwritten(
    client: TestClient, db_session: Session
) -> None:
    """R-03 of the review (2026-09-28), with the second rule of a **different** model: a
    whole-scenario Fixed Price rule next to a segment-scoped Time & Material rule. The client sends
    the Fixed Price rule's real, current marker (read from the database — the single-rule `GET`
    cannot answer for a two-rule scenario). Expected: the same `409` as for two Fixed Price rules
    (`test_fixed_price_review_fixes.py::test_r_03_*`), and the price and the marker unchanged.

    Contrast: the same whole-scenario rule without the segment rule is `200`
    (`test_k_05_the_price_edit_does_not_reach_a_segment_scoped_fixed_price_rule`, its second half).

    Mutation killed: the `NOT EXISTS` other-rule predicate narrowed to other *Fixed Price* rules
    (SC-4-02-M33) — the price is written and committed, and the answer's single-rule read then
    raises `MultipleCommercialRulesNotSupported` (a write that landed behind a `500`). The existing
    R-03 test, whose second rule is also Fixed Price, does not tell the two predicates apart."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Fixed price and a T&M segment")
    whole = make_fixed_price_terms(db_session, scenario)
    segment = make_scenario_delivery_segment(db_session, scenario)
    make_commercial_terms(db_session, scenario, scope_ref=segment.id)
    marker = whole.updated_at.isoformat()
    before = _stored_whole(db_session, scenario.id)

    response = _patch_price(client, project.id, scenario.id, marker, "175000")

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "more than one set of commercial terms" in detail
    assert "approved" not in detail and "concurrency marker" not in detail
    assert _stored_whole(db_session, scenario.id) == before


def _stored_whole(session: Session, scenario_id: uuid.UUID) -> tuple[Decimal, object]:
    """(agreed price, rule marker) of the scenario's whole-scenario Fixed Price rule, as stored."""
    session.expire_all()
    return tuple(  # type: ignore[return-value]
        session.execute(
            sa.select(FixedPriceTerms.agreed_price, CommercialTerms.updated_at)
            .join(CommercialTerms, CommercialTerms.id == FixedPriceTerms.commercial_terms_id)
            .where(CommercialTerms.scenario_id == scenario_id, CommercialTerms.scope_ref.is_(None))
        ).one()
    )


# --- 4. the dispatcher entry prices Fixed Price as the answer does (kills SC-4-02-M7/M8) ----------


def test_k_01_the_dispatcher_entry_prices_fixed_price_as_the_answer_does(
    client: TestClient, db_session: Session
) -> None:
    """K-01 on the `REVENUE_BY_MODEL` entry itself (`_fixed_price`, reached through `revenue_of`).

    Since R-01 (2026-09-28) `_view_of` prices Fixed Price with its own call to
    `fixed_price_revenue`, so every API path — `…/commercial-terms`, `…/results`, `…/compare`,
    what-if — bypasses the dispatcher's Fixed Price branch, which is now reached only through
    `revenue_of`/`revenue_by_model_type` (SC-4-05's N-rule path). The round-1 K-01 mutations of that
    branch (a `billable_hours` term, SC-4-02-M7; a gate on priced catalogue months, SC-4-02-M8)
    then survived all 1142 tests. This test prices the same scenario through the dispatcher: a
    staffed month with no selling rate, 150000 PLN agreed → `150000.00` PLN, and the same figure
    the answer states.

    Contrast, one element changed (the rule's model): the T&M twin on the same catalogue and month
    is `no_rate` through the same `revenue_of`."""
    project, dimensions = _ac_07_project(db_session)
    fixed, _ = _staffed(db_session, project, dimensions, name="Fixed price", month=GAP_MONTH)
    make_fixed_price_terms(db_session, fixed, agreed_price=AC_07_PRICE, currency="PLN")
    twin, _ = _staffed(db_session, project, dimensions, name="T&M twin", month=GAP_MONTH)
    make_commercial_terms(db_session, twin)

    [twin_rule] = rules_of_scenario(db_session, twin.id)
    twin_revenue = revenue_of(db_session, twin, twin_rule)
    assert isinstance(twin_revenue, RevenueUnavailable) and twin_revenue.reason == "no_rate"

    [fixed_rule] = rules_of_scenario(db_session, fixed.id)
    dispatched = revenue_of(db_session, fixed, fixed_rule)
    assert isinstance(dispatched, RevenueResult), dispatched
    assert (dispatched.revenue, dispatched.currency) == (Decimal("150000.00"), "PLN")
    assert dispatched.assumptions_used.rate_source == "fixed_price_terms"

    answered = client.get(
        commercial_terms_path(project.id, fixed.id), headers=as_caller(IN_SCOPE_USER)
    ).json()["revenue"]
    assert (answered["amount"], answered["currency"]) == (str(dispatched.revenue), "PLN")
