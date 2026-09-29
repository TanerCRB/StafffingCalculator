"""SC-4-03 × SC-4-05 — QA, round 4 (after the second merge of `origin/main`): gaps in the proof of
`tests/test_outcome_scope_ref.py` found by mutations.

1. **Copy (D-4=A) — the mutation survived the claim, died on a different mechanism.** The mutation
   "the copy of an `outcome_based` rule loses `scope_ref` (writes `NULL`)" reddened
   `test_d4_outcome_the_copy_remaps_…`, but not on the assertion about `scope_ref`: the source also
   carried a whole-scenario Story Points rule, so the copy's second rule with `scope_ref IS NULL`
   ran into `uq_commercial_terms_scenario_id` (a 500 on copy) — the test was measuring the database
   index, not the remapping. Here the source has **only** the segment's Outcome-based rule: a lost
   `scope_ref` collides with nothing, so only the assertion about scope kills it. The contrast (one
   change — a rule without `scope_ref`) shows that `NULL` in the copy is correct if and only if it
   was in the source.
2. **One write statement, `scope_ref` included too.** The name
   `test_k_02_outcome_a_segment_scoped_rule_is_written_with_its_details_in_one_guarded_statement`
   promises one statement, but the test only checks the result. The mutation "the rule with
   `scope_ref` in the guarded statement, the details in a second one" survived the whole suite (902
   green). Here the production path's `INSERT` statements are counted — with `scope_ref` and
   without it (contrast), the same shape.

Real PostgreSQL.
"""

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.commercial_terms import create_commercial_terms
from app.models import CommercialTerms, OutcomeTerms, Scenario, ScenarioDeliverySegment
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    make_outcome_terms,
    make_project,
    make_scenario,
    make_scenario_delivery_segment,
)
from tests.test_outcome_revenue_copy import FULL_DETAILS

CALLER = CallerIdentity(user_id=IN_SCOPE_USER)


def _details_row(session: Session, rule_id: uuid.UUID) -> dict[str, object]:
    row = session.execute(
        sa.select(OutcomeTerms.__table__).where(OutcomeTerms.commercial_terms_id == rule_id)
    ).mappings().one()
    return {column: row[column] for column in FULL_DETAILS}


@pytest.mark.parametrize("scoped", [True, False], ids=["segment_rule", "whole_scenario_rule"])
def test_qa_d4_outcome_the_copy_keeps_the_scope_of_a_lone_outcome_rule(
    client: TestClient, db_session: Session, scoped: bool
) -> None:
    """The source's only rule is Outcome-based — on the "Phase 1" segment (`segment_rule`) or on the
    whole scenario (`whole_scenario_rule`, contrast: one change). The segment exists in both
    variants, so the copy has its own "Phase 1" in both. The copy's rule points at the **copy's**
    segment of the same name if and only if the source pointed at a segment; no `scope_ref` in the
    source — none in the copy. No other rule in the copy can kill the mutation here in place of the
    assertion about scope."""
    project = make_project(db_session, name="Aurora lone", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    phase_1 = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    source_rule = make_outcome_terms(
        db_session, scenario, scope_ref=phase_1.id if scoped else None, **FULL_DETAILS
    )

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    db_session.expire_all()
    copy = db_session.execute(
        sa.select(Scenario).where(Scenario.project_id == uuid.UUID(response.json()["id"]))
    ).scalar_one()
    copy_phase_1 = db_session.execute(
        sa.select(ScenarioDeliverySegment.id).where(
            ScenarioDeliverySegment.scenario_id == copy.id,
            ScenarioDeliverySegment.name == "Phase 1",
        )
    ).scalar_one()
    copied = db_session.execute(
        sa.select(CommercialTerms).where(CommercialTerms.scenario_id == copy.id)
    ).scalar_one()

    assert copied.model_type == "outcome_based"
    if scoped:
        assert copied.scope_ref == copy_phase_1
        assert copied.scope_ref != phase_1.id
    else:
        assert copied.scope_ref is None
    assert _details_row(db_session, copied.id) == FULL_DETAILS
    assert db_session.get(CommercialTerms, source_rule.id).scope_ref == (
        phase_1.id if scoped else None
    )


@pytest.mark.parametrize("scoped", [True, False], ids=["segment_rule", "whole_scenario_rule"])
def test_qa_k_02_outcome_the_rule_scope_ref_and_details_are_one_insert_statement(
    db_session: Session, scoped: bool
) -> None:
    """The production path `create_commercial_terms` for `outcome_based` executes **one** `INSERT`
    statement, and that statement writes both `commercial_terms` (with `scope_ref`) and
    `outcome_terms` — with the segment's `scope_ref` and without it (contrast), the same shape. A
    second statement for the details would be a write outside the `approved` guard built into the
    first (ADR-0003, point 3; ADR-0004, addendum SC-4-03, point 2)."""
    project = make_project(db_session, name="Aurora one statement", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    segment = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    db_session.flush()
    inserts: list[str] = []

    def record(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if "insert into" in statement.lower():
            inserts.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        view = create_commercial_terms(
            db_session,
            CALLER,
            project.id,
            scenario.id,
            model_type="outcome_based",
            scope_ref=segment.id if scoped else None,
            domain_values=FULL_DETAILS,
        )
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert view is not None and view.terms is not None
    assert view.terms.scope_ref == (segment.id if scoped else None)
    assert len(inserts) == 1, (
        "the outcome rule and its details were written by more than one INSERT — the approved "
        f"guard embedded in the first does not cover the others: {inserts}"
    )
    written = inserts[0].lower()
    assert written.strip().startswith("with")
    assert "insert into commercial_terms" in written
    assert "scope_ref" in written
    assert "insert into outcome_terms" in written
