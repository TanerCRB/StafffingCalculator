"""SC-4-03 × SC-4-05 — `scope_ref` (F-06.5, Issue #69) for the Outcome-based rule (F-06.3, Issue
#67).

SC-4-05 proves `scope_ref` on T&M and Story Points rules (`tests/test_commercial_terms_scope.py`);
this file repeats its key cases for the third model, which landed on `main` in parallel: a write in
one guarded statement (the rule, `scope_ref` and the `outcome_terms` row as literals), scope
disjointness in the database (K-02), reading and pricing a segment rule (K-01 — Outcome-based reads
only its own `outcome_terms` row, so two rules of this model are **not** one answer), the
`approved` guard (K-03), the composite foreign key (K-04), and remapping `scope_ref` on copy
(D-4=A) together with the full details row.

**What this file does not prove:** protection against double-counting at the level of the revenue
AMOUNT (requires F-04 — linking staffing positions to a segment, out of scope for SC-4-05); the API
write path for `scope_ref` (no request schema carries it — ADR-0016, point 8).

Real PostgreSQL — the constraints and the guard are claims about the database.
"""

import importlib.util
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.commercial_terms import (
    CommercialTermsFrozen,
    CommercialTermsWriteRefused,
    MultipleRulesOfOneModelNotSupported,
    create_commercial_terms,
    revenue_by_model_type,
    rules_of_scenario,
)
from app.domain.revenue import RevenueResult
from app.models import (
    CommercialTerms,
    OutcomeTerms,
    Scenario,
    ScenarioDeliverySegment,
    ScenarioStatus,
)
from app.models.commercial_terms import SCENARIO_ID_SCOPE_UNIQUE, SCOPE_REF_FOREIGN_KEY
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    commercial_terms_path,
    make_allocation,
    make_commercial_terms,
    make_dimension_tuple,
    make_outcome_terms,
    make_project,
    make_rate,
    make_scenario,
    make_scenario_delivery_segment,
    make_staffing_position,
    make_story_points_terms,
)
from tests.test_outcome_revenue_copy import FULL_DETAILS

MAR = date(2026, 3, 1)
CALLER = CallerIdentity(user_id=IN_SCOPE_USER)

FULL_GUARANTEED = Decimal("22000.00")
"""`FULL_DETAILS`: a fee of 20000 with a zero variable component, raised to `revenue_min`
22000 (ADR-0003, addendum SC-4-03, point 6)."""
FULL_EXPECTED = Decimal("34400.00")
"""0.1·22000 (not achieved, min) + 0.2·25000 (partial, no bonus) + 0.4·38000 (achieved) +
0.3·40000 (exceeded 45000, capped to max)."""
DEFAULT_GUARANTEED = Decimal("20000.00")
"""`make_outcome_terms`'s default details: the fee alone at 20000, no min/max."""


def _scenario(session: Session, name: str = "Baseline") -> Scenario:
    project = make_project(session, name="Aurora outcome", accessible_to=(IN_SCOPE_USER,))
    return make_scenario(session, project, name=name)


def _priced_scenario(session: Session) -> tuple[Scenario, ScenarioDeliverySegment]:
    """A scenario with one priced T&M position (100 h × 200 PLN = 20000.00) and one segment — the
    same setup as SC-4-05's `_priced_plan`, so the approval and the T&M rule have data."""
    scenario = _scenario(session)
    dimensions = make_dimension_tuple(session)
    position = make_staffing_position(session, scenario, dimensions, headcount=2, start_date=MAR)
    make_allocation(session, position, period_month=MAR)
    make_rate(
        session,
        dimensions,
        effective_from=date(2026, 1, 1),
        default_selling_rate=Decimal("200.0000"),
        currency="PLN",
    )
    return scenario, make_scenario_delivery_segment(session, scenario, name="Phase 1")


def _count(session: Session, model, scenario_id: uuid.UUID) -> int:
    session.expire_all()
    query = sa.select(sa.func.count()).select_from(CommercialTerms)
    if model is OutcomeTerms:
        query = query.join(OutcomeTerms, OutcomeTerms.commercial_terms_id == CommercialTerms.id)
    return session.execute(query.where(CommercialTerms.scenario_id == scenario_id)).scalar_one()


def _sqlstate_and_constraint(error: IntegrityError) -> tuple[str | None, str | None]:
    diagnostics = error.orig.diag  # type: ignore[union-attr]
    return diagnostics.sqlstate, diagnostics.constraint_name


def _details_row(session: Session, rule_id: uuid.UUID) -> dict[str, object]:
    row = session.execute(
        sa.select(OutcomeTerms.__table__).where(OutcomeTerms.commercial_terms_id == rule_id)
    ).mappings().one()
    return {column: row[column] for column in FULL_DETAILS}


# --- write: one guarded statement carries `scope_ref` and the details -----------------------------


def test_k_02_outcome_a_segment_scoped_rule_is_written_with_its_details_in_one_guarded_statement(
    db_session: Session,
) -> None:
    """The production path `create_commercial_terms` writes an `outcome_based` rule with the
    segment's `scope_ref` **and** its `outcome_terms` row with every domain column — `scope_ref` is
    just one more literal of the same statement, not a second write alongside the details."""
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")

    view = create_commercial_terms(
        db_session,
        CALLER,
        scenario.project_id,
        scenario.id,
        model_type="outcome_based",
        scope_ref=segment.id,
        domain_values=FULL_DETAILS,
    )

    assert view is not None
    assert view.terms is not None
    assert (view.terms.model_type, view.terms.scope_ref) == ("outcome_based", segment.id)
    assert view.outcome_terms is not None
    assert _details_row(db_session, view.terms.id) == FULL_DETAILS
    assert isinstance(view.revenue, RevenueResult), view.revenue
    assert (view.revenue.revenue, view.revenue.expected_revenue) == (
        FULL_GUARANTEED,
        FULL_EXPECTED,
    )


def test_k_02_outcome_a_second_outcome_rule_of_the_same_segment_is_refused_by_the_database(
    db_session: Session,
) -> None:
    """K-02 for Outcome-based — a second row of the same segment is refused by the partial index
    `uq_commercial_terms_scenario_id_scope_ref`, regardless of the rule's model."""
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario)
    make_outcome_terms(db_session, scenario, scope_ref=segment.id)

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            make_outcome_terms(db_session, scenario, scope_ref=segment.id)

    assert _sqlstate_and_constraint(refused.value) == ("23505", SCENARIO_ID_SCOPE_UNIQUE)
    assert _count(db_session, CommercialTerms, scenario.id) == 1


def test_k_02_outcome_the_guarded_create_path_refuses_a_second_rule_of_a_segment_and_writes_nothing(
    db_session: Session,
) -> None:
    """The same refusal through the production path: a `409` `CommercialTermsWriteRefused`, and
    after the refusal there is neither a second rule nor an orphaned `outcome_terms` row — both
    rows are in one statement, so refusing one rolls back both."""
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario)
    make_outcome_terms(db_session, scenario, scope_ref=segment.id)
    # down to the test's SAVEPOINT: the write path's `rollback` will not undo the setup
    db_session.commit()

    with pytest.raises(CommercialTermsWriteRefused):
        create_commercial_terms(
            db_session,
            CALLER,
            scenario.project_id,
            scenario.id,
            model_type="outcome_based",
            scope_ref=segment.id,
            domain_values=FULL_DETAILS,
        )

    assert _count(db_session, CommercialTerms, scenario.id) == 1
    assert _count(db_session, OutcomeTerms, scenario.id) == 1


def test_k_02_outcome_a_whole_scenario_rule_and_segment_rules_of_three_models_coexist(
    db_session: Session,
) -> None:
    """Contrast: a whole-scenario Outcome-based rule, a segment Outcome-based rule, and rules of
    other models on other segments coexist — the disjointness is about scope, not the model."""
    scenario = _scenario(db_session)
    phase_1 = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    phase_2 = make_scenario_delivery_segment(db_session, scenario, name="Phase 2")
    phase_3 = make_scenario_delivery_segment(db_session, scenario, name="Phase 3")

    make_outcome_terms(db_session, scenario)
    make_outcome_terms(db_session, scenario, scope_ref=phase_1.id)
    make_commercial_terms(db_session, scenario, scope_ref=phase_2.id)
    make_story_points_terms(db_session, scenario, scope_ref=phase_3.id)

    assert _count(db_session, CommercialTerms, scenario.id) == 4
    assert _count(db_session, OutcomeTerms, scenario.id) == 2


# --- read and revenue (K-01) ---------------------------------------------------------------------


def test_k_01_outcome_a_segment_rule_is_priced_from_its_own_row_next_to_a_tm_rule(
    db_session: Session,
) -> None:
    """A whole-scenario T&M rule and a segment Outcome-based rule: two independent answers, one per
    model, each from its own data — T&M from the allocation (20000.00), Outcome-based only from its
    own `outcome_terms` (22000.00 guaranteed, 34400.00 expected)."""
    scenario, segment = _priced_scenario(db_session)
    make_commercial_terms(db_session, scenario)
    make_outcome_terms(db_session, scenario, scope_ref=segment.id, **FULL_DETAILS)

    answers = revenue_by_model_type(
        db_session, scenario, rules_of_scenario(db_session, scenario.id)
    )

    assert set(answers) == {"time_and_material", "outcome_based"}
    assert answers["time_and_material"].revenue == Decimal("20000.00")
    outcome = answers["outcome_based"]
    assert isinstance(outcome, RevenueResult), outcome
    assert (outcome.revenue, outcome.expected_revenue) == (FULL_GUARANTEED, FULL_EXPECTED)
    assert outcome.assumptions_used.rate_source == "not_applicable"


def test_k_01_outcome_two_outcome_rules_of_one_scenario_refuse_to_guess_instead_of_dropping_one(
    db_session: Session,
) -> None:
    """Like Story Points (reviewer R-01 SC-4-05): Outcome-based reads parameters from its **own**
    rule row, so a whole-scenario rule (20000.00) and a segment rule (22000.00) can genuinely
    disagree — `revenue_by_model_type` refuses instead of silently picking one.

    Mutation: `outcome_based` added to `_MODEL_TYPES_WITH_SHARED_SCENARIO_REVENUE` — then the
    result is `calculated` for only one rule, with no trace of the other."""
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    make_outcome_terms(db_session, scenario)
    make_outcome_terms(db_session, scenario, scope_ref=segment.id, **FULL_DETAILS)

    rules = rules_of_scenario(db_session, scenario.id)
    assert [rule.terms.scope_ref for rule in rules] == [None, segment.id]
    assert all(rule.has_details for rule in rules)

    with pytest.raises(MultipleRulesOfOneModelNotSupported):
        revenue_by_model_type(db_session, scenario, rules)


def test_k_01_outcome_the_read_of_a_scenario_whose_only_rule_is_segment_scoped_carries_its_params(
    client: TestClient, db_session: Session
) -> None:
    """`GET` of a scenario's rule whose only Outcome-based rule has a segment `scope_ref`: the
    revenue is calculated from this rule and its parameters appear in the read (R-04) — the scope
    changes nothing in the model's pricing."""
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario)
    make_outcome_terms(db_session, scenario, scope_ref=segment.id, **FULL_DETAILS)

    response = client.get(
        commercial_terms_path(scenario.project_id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["commercial_terms"]["model_type"] == "outcome_based"
    assert body["commercial_terms"]["outcome_terms"]["revenue_min"] == "22000.0000"
    assert (body["revenue"]["state"], body["revenue"]["amount"]) == ("calculated", "22000.00")
    assert body["revenue"]["expected_amount"] == "34400.00"


# --- the `approved` guard (K-03) ---------------------------------------------------------------


def test_k_03_outcome_a_segment_scoped_outcome_rule_is_refused_by_the_same_write_guard(
    db_session: Session,
) -> None:
    """K-03 for Outcome-based (following K-04(b) SC-4-04 and K-03 SC-4-05): a rule with `scope_ref`
    on an approved scenario is refused by the same guard in the statement that writes — neither the
    rule nor the `outcome_terms` row."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    approved = make_scenario(db_session, project, name="Approved", status=ScenarioStatus.APPROVED)
    segment = make_scenario_delivery_segment(db_session, approved, name="Phase 1")

    with pytest.raises(CommercialTermsFrozen):
        create_commercial_terms(
            db_session,
            CALLER,
            project.id,
            approved.id,
            model_type="outcome_based",
            scope_ref=segment.id,
            domain_values=FULL_DETAILS,
        )

    assert _count(db_session, CommercialTerms, approved.id) == 0
    assert _count(db_session, OutcomeTerms, approved.id) == 0


def test_k_03_outcome_an_approved_scenario_with_a_segment_scoped_outcome_rule_reads_its_own_row(
    client: TestClient, db_session: Session
) -> None:
    """Approving a scenario with a segment Outcome-based rule: the read is still `calculated` from
    its own rule row, `rate_source = not_applicable` (no snapshot — ADR-0004, addendum SC-4-03,
    point 4), no `409` from the race guard."""
    scenario, segment = _priced_scenario(db_session)
    make_outcome_terms(db_session, scenario, scope_ref=segment.id, **FULL_DETAILS)

    approval = client.post(
        approve_path(scenario.project_id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert approval.status_code == 200, approval.text

    response = client.get(
        commercial_terms_path(scenario.project_id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    revenue = response.json()["revenue"]
    assert (revenue["state"], revenue["amount"]) == ("calculated", "22000.00")
    assert revenue["assumptions_used"]["rate_source"] == "not_applicable"


# --- the composite foreign key (K-04) ------------------------------------------------------------


def test_k_04_outcome_a_rule_cannot_point_at_a_segment_of_another_scenario(
    db_session: Session,
) -> None:
    """K-04 for Outcome-based — the composite foreign key refuses a segment of another scenario."""
    scenario = _scenario(db_session)
    other = make_scenario(db_session, scenario.project, name="Other")
    other_segment = make_scenario_delivery_segment(db_session, other, name="Phase 1")

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            make_outcome_terms(db_session, scenario, scope_ref=other_segment.id)

    assert _sqlstate_and_constraint(refused.value) == ("23503", SCOPE_REF_FOREIGN_KEY)
    assert _count(db_session, CommercialTerms, scenario.id) == 0


def test_k_04_outcome_the_guarded_create_path_refuses_another_scenarios_segment_and_writes_nothing(
    db_session: Session,
) -> None:
    """The same refusal through the production path: the foreign key refuses the statement that
    carries the rule and the details at once — neither the rule nor the `outcome_terms` row
    remains."""
    scenario = _scenario(db_session)
    other = make_scenario(db_session, scenario.project, name="Other")
    other_segment = make_scenario_delivery_segment(db_session, other, name="Phase 1")
    db_session.commit()

    with pytest.raises(CommercialTermsWriteRefused):
        create_commercial_terms(
            db_session,
            CALLER,
            scenario.project_id,
            scenario.id,
            model_type="outcome_based",
            scope_ref=other_segment.id,
            domain_values=FULL_DETAILS,
        )

    assert _count(db_session, CommercialTerms, scenario.id) == 0
    assert _count(db_session, OutcomeTerms, scenario.id) == 0


# --- copy (D-4=A) ---------------------------------------------------------------------------------


def test_d4_outcome_the_copy_remaps_an_outcome_rules_scope_ref_and_copies_its_details(
    client: TestClient, db_session: Session
) -> None:
    """D-4=A for Outcome-based: three rules of three models — a whole-scenario Story Points rule,
    Outcome-based on "Phase 1", T&M on "Phase 2". The copy points at the copy's own segments of the
    same names (not swapped), the copy's `outcome_terms` row has every domain column of the source,
    and the copy's Outcome-based rule's revenue is the same. The source untouched."""
    project = make_project(db_session, name="Aurora copy", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    phase_1 = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    phase_2 = make_scenario_delivery_segment(db_session, scenario, name="Phase 2")
    make_story_points_terms(db_session, scenario)
    outcome_rule = make_outcome_terms(db_session, scenario, scope_ref=phase_1.id, **FULL_DETAILS)
    tm_rule = make_commercial_terms(db_session, scenario, scope_ref=phase_2.id)

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    db_session.expire_all()
    copy = db_session.execute(
        sa.select(Scenario).where(Scenario.project_id == uuid.UUID(response.json()["id"]))
    ).scalar_one()

    copy_segments = {
        segment.name: segment.id
        for segment in db_session.execute(
            sa.select(ScenarioDeliverySegment).where(ScenarioDeliverySegment.scenario_id == copy.id)
        ).scalars()
    }
    copied = {
        rule.model_type: rule
        for rule in db_session.execute(
            sa.select(CommercialTerms).where(CommercialTerms.scenario_id == copy.id)
        ).scalars()
    }
    assert set(copied) == {"story_points", "outcome_based", "time_and_material"}
    assert copied["story_points"].scope_ref is None
    assert copied["outcome_based"].scope_ref == copy_segments["Phase 1"]
    assert copied["time_and_material"].scope_ref == copy_segments["Phase 2"]
    assert copied["outcome_based"].id != outcome_rule.id
    assert _details_row(db_session, copied["outcome_based"].id) == FULL_DETAILS

    outcome_copy = next(
        rule
        for rule in rules_of_scenario(db_session, copy.id)
        if rule.terms.model_type == "outcome_based"
    )
    answers = revenue_by_model_type(db_session, copy, [outcome_copy])
    assert (answers["outcome_based"].revenue, answers["outcome_based"].expected_revenue) == (
        FULL_GUARANTEED,
        FULL_EXPECTED,
    )

    assert db_session.get(CommercialTerms, outcome_rule.id).scope_ref == phase_1.id
    assert db_session.get(CommercialTerms, tm_rule.id).scope_ref == phase_2.id
    assert _details_row(db_session, outcome_rule.id) == FULL_DETAILS


# --- migration linearization after merging with SC-4-05 ------------------------------------------


_VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def test_merge_the_outcome_migration_follows_the_scope_ref_migration_which_leaves_the_check_alone(
) -> None:
    """`b9e3c7a1f264` sits directly after `b7e3f19a6c52` (one head), and `b7e3f19a6c52` does not
    recreate `ck_commercial_terms_model_type_known` — so the list the outcome `downgrade` recreates
    is still the list from `d2f6a91c4b58` (two values). If `b7e3f19a6c52` ever started touching this
    CHECK, this test forces `PREVIOUS_CHECK_MIGRATION_PATH` to be repointed."""
    spec = importlib.util.spec_from_file_location(
        "sc_4_03_migration_for_scope_ref",
        _VERSIONS / "b9e3c7a1f264_create_outcome_terms_and_widen_the_model_type_check.py",
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.down_revision == "b7e3f19a6c52"
    scope_ref_source = (_VERSIONS / "b7e3f19a6c52_add_scope_ref_to_commercial_terms.py").read_text(
        encoding="utf-8"
    )
    assert "model_type_known" not in scope_ref_source
    assert "model_type IN" not in scope_ref_source
