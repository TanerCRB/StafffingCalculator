"""SC-4-05, K-01..K-04 (Issue #69, F-06.5) — `scope_ref` on `commercial_terms`: the rule scoped to a
segment instead of a whole scenario, the database-level disjointness of scopes that replaces "one
rule per scenario", and the cross-scenario integrity of the pointer.

**What this file does not prove** (named once here, not repeated in every test): revenue-amount
level protection against double billing between a whole-scenario rule and a segment rule — that
needs `staffing_position` to know which segment it belongs to (F-04), which does not exist yet and
is out of scope of SC-4-05 (`docs/PLAN.md`, ADR-0003 addendum 2026-09-25, closing annex). K-01
below proves disjointness of the *aggregation mechanism*
(`app.data.commercial_terms.revenue_by_model_type`) on the one case that mechanism can actually go
wrong today: two rules of the *same* model, which read identical, unscoped allocation data.
"""

import importlib.util
import os
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.commercial_terms import (
    CommercialTermsFrozen,
    MultipleCommercialRulesNotSupported,
    MultipleRulesOfOneModelNotSupported,
    commercial_terms_for_caller,
    copy_commercial_terms,
    create_commercial_terms,
    revenue_by_model_type,
    revenue_of,
    rules_of_scenario,
)
from app.data.project_writes import SCENARIO_CHILD_COPIERS
from app.data.scenario_delivery_segment import copy_scenario_delivery_segments
from app.domain.revenue import RevenueResult
from app.models import (
    CatalogDefaultRate,
    CommercialTerms,
    Scenario,
    ScenarioDeliverySegment,
    ScenarioStatus,
)
from app.models.commercial_terms import (
    SCENARIO_ID_SCOPE_UNIQUE,
    SCENARIO_ID_WHOLE_SCENARIO_UNIQUE,
    SCOPE_REF_FOREIGN_KEY,
    SCOPE_REF_NOT_NULL_EXPRESSION,
    SCOPE_REF_NULL_EXPRESSION,
)
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    commercial_terms_path,
    make_allocation,
    make_commercial_terms,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_scenario_delivery_segment,
    make_staffing_position,
    make_story_points_terms,
)

MAR = date(2026, 3, 1)
SELLING = Decimal("200.0000")


def _scenario(session: Session) -> Scenario:
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    return make_scenario(session, project, name="Baseline")


def _count_rules(session: Session, scenario_id: uuid.UUID) -> int:
    session.expire_all()
    return session.execute(
        sa.select(sa.func.count())
        .select_from(CommercialTerms)
        .where(CommercialTerms.scenario_id == scenario_id)
    ).scalar_one()


def _sqlstate_and_constraint(error: IntegrityError) -> tuple[str | None, str | None]:
    diagnostics = error.orig.diag  # type: ignore[union-attr]
    return diagnostics.sqlstate, diagnostics.constraint_name


def _revenue(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    response = client.get(
        commercial_terms_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    return response.json()["revenue"]


def _priced_plan(session: Session) -> tuple[Scenario, ScenarioDeliverySegment, CatalogDefaultRate]:
    """A scenario with one priced position (100 billable hours at 200 PLN/h = 20000.00 PLN) and one
    delivery segment — the shared fixture K-01 and K-03's snapshot test both need."""
    scenario = _scenario(session)
    dimensions = make_dimension_tuple(session)
    position = make_staffing_position(session, scenario, dimensions, headcount=2, start_date=MAR)
    make_allocation(session, position, period_month=MAR)
    rate = make_rate(
        session,
        dimensions,
        effective_from=date(2026, 1, 1),
        default_selling_rate=SELLING,
        currency="PLN",
    )
    segment = make_scenario_delivery_segment(session, scenario, name="Phase 1")
    return scenario, segment, rate


# --- K-01: revenue counted once per (position, month), not summed per rule row --------------------


def test_k_01_a_whole_scenario_and_a_segment_tm_rule_price_the_shared_allocations_once(
    db_session: Session,
) -> None:
    """K-01 — a whole-scenario T&M rule and a segment T&M rule of one scenario both read the same
    `staffing_position_allocation` rows (no F-04 segment-to-position link exists to tell them
    apart), so the scenario's Time & Material revenue is computed once, not once per rule row.

    Mutation: `revenue_by_model_type`'s `setdefault` (the scope-disjointness predicate) replaced by
    pricing every rule and summing — the answer would then be 40000.00, not 20000.00 (proven not to
    be a vacuous claim by the contrast test right below).
    """
    scenario, segment, _rate = _priced_plan(db_session)
    make_commercial_terms(db_session, scenario)  # scope_ref=None, the whole-scenario rule
    make_commercial_terms(db_session, scenario, scope_ref=segment.id)  # the segment rule
    assert _count_rules(db_session, scenario.id) == 2, "fixture setup: two coexisting T&M rules"

    rules = rules_of_scenario(db_session, scenario.id)
    answers = revenue_by_model_type(db_session, scenario, rules)

    assert set(answers) == {"time_and_material"}
    result = answers["time_and_material"]
    assert isinstance(result, RevenueResult), result
    assert result.revenue == Decimal("20000.00")
    assert result.currency == "PLN"


def test_k_01_contrast_pricing_every_rule_row_independently_really_would_double_it(
    db_session: Session,
) -> None:
    """The contrast that makes the mutation above meaningful: pricing the two rule rows
    independently (the naive, wrong aggregation) really does produce twice the correct figure on
    this fixture — so `revenue_by_model_type`'s dedup by `model_type` is not a no-op here."""
    scenario, segment, _rate = _priced_plan(db_session)
    make_commercial_terms(db_session, scenario)
    make_commercial_terms(db_session, scenario, scope_ref=segment.id)

    rules = rules_of_scenario(db_session, scenario.id)
    naive_total = sum(
        (revenue_of(db_session, scenario, rule).revenue for rule in rules), start=Decimal("0.00")
    )

    assert naive_total == Decimal("40000.00"), (
        "the fixture does not actually double under a naive per-row sum — this pair of tests would "
        "not distinguish the correct aggregation from the mutation it names"
    )


def test_k_01_one_rule_of_each_model_is_not_a_double_count_and_is_not_collapsed_either(
    db_session: Session,
) -> None:
    """K-01's own note: Time & Material and Story Points share no countable unit (hours vs. points),
    so a scenario with one rule of each keeps two independent answers — summing them is not what
    K-01 forbids, and `revenue_by_model_type` must not collapse them into one either."""
    scenario, segment, _rate = _priced_plan(db_session)
    make_commercial_terms(db_session, scenario)  # T&M, whole scenario
    make_story_points_terms(
        db_session, scenario, scope_ref=segment.id, price_per_point=Decimal("1000.0000"),
        accepted_points=25, currency="PLN",
    )

    rules = rules_of_scenario(db_session, scenario.id)
    answers = revenue_by_model_type(db_session, scenario, rules)

    assert set(answers) == {"time_and_material", "story_points"}
    assert answers["time_and_material"].revenue == Decimal("20000.00")
    assert answers["story_points"].revenue == Decimal("25000.00")


def test_k_01_two_story_points_rules_of_one_scenario_refuse_to_guess_instead_of_dropping_one(
    db_session: Session,
) -> None:
    """Reviewer R-01 (gate 2) — unlike Time & Material, `story_points_terms` carries its own
    `price_per_point`/`accepted_points` on the rule's row, so two legally-coexisting Story Points
    rules (K-02/D-3=A: a whole-scenario rule and a segment rule) can genuinely disagree.
    `revenue_by_model_type` must not silently keep one and drop the other's revenue — it refuses.

    Mutation: `_MODEL_TYPES_WITH_SHARED_SCENARIO_REVENUE` widened to include `story_points` (or the
    guard removed) — the call below would then return a `calculated` answer for only one of the two
    rules, with the other's revenue gone and no signal that anything was dropped.
    """
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    make_story_points_terms(
        db_session, scenario, price_per_point=Decimal("1000.0000"), accepted_points=25,
        currency="PLN",
    )
    make_story_points_terms(
        db_session, scenario, scope_ref=segment.id, price_per_point=Decimal("2000.0000"),
        accepted_points=10, currency="PLN",
    )
    assert _count_rules(db_session, scenario.id) == 2, "fixture setup: two coexisting SP rules"

    rules = rules_of_scenario(db_session, scenario.id)

    with pytest.raises(MultipleRulesOfOneModelNotSupported):
        revenue_by_model_type(db_session, scenario, rules)


# --- K-02: `scope_ref` disjointness enforced in the database, replacing UNIQUE (scenario_id) -----


def test_k_02_a_second_whole_scenario_rule_is_refused_by_the_same_named_constraint(
    db_session: Session,
) -> None:
    """K-02 — at most one rule with `scope_ref IS NULL` per scenario, refused by the *same*
    constraint name SC-4-01 shipped (`uq_commercial_terms_scenario_id`, now a partial index) so the
    SC-4-01 tests that already assert this name keep meaning what they said.

    Mutation (`docs/PLAN.md`): the constraint dropped without a replacement — this insert would then
    succeed and `_count_rules` below would read 2.
    """
    scenario = _scenario(db_session)
    make_commercial_terms(db_session, scenario)

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            make_commercial_terms(db_session, scenario)

    assert _sqlstate_and_constraint(refused.value) == (
        "23505",
        SCENARIO_ID_WHOLE_SCENARIO_UNIQUE,
    )
    assert _count_rules(db_session, scenario.id) == 1


def test_k_02_a_second_rule_of_the_same_segment_is_refused(db_session: Session) -> None:
    """K-02's other half — no segment carries two rules, refused by the new partial index."""
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario)
    make_commercial_terms(db_session, scenario, scope_ref=segment.id)

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            make_commercial_terms(db_session, scenario, scope_ref=segment.id)

    assert _sqlstate_and_constraint(refused.value) == ("23505", SCENARIO_ID_SCOPE_UNIQUE)
    assert _count_rules(db_session, scenario.id) == 1


def test_k_02_a_whole_scenario_rule_and_distinct_segment_rules_coexist(db_session: Session) -> None:
    """The contrast proving the pair of constraints is exactly that — not still a blanket "one rule
    per scenario": a whole-scenario rule and two different-segment rules on the same scenario are
    all accepted together (D-3=A's "combined rule" — schema-level, K-01 proves the revenue side for
    the one case within reach today)."""
    scenario = _scenario(db_session)
    segment_a = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    segment_b = make_scenario_delivery_segment(db_session, scenario, name="Phase 2")

    make_commercial_terms(db_session, scenario)
    make_commercial_terms(db_session, scenario, scope_ref=segment_a.id)
    make_commercial_terms(db_session, scenario, scope_ref=segment_b.id)

    assert _count_rules(db_session, scenario.id) == 3


# --- K-03: a segment-scoped rule stays behind the existing guard/snapshot mechanism --------------


def test_k_03_a_segment_scoped_tm_rule_of_an_approved_scenario_reads_its_own_snapshot(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — extends SC-4-01's K-09: a T&M rule scoped to a segment (`scope_ref` set) is frozen at
    approval and stays reproducible exactly like a whole-scenario rule, through the same snapshot
    mechanism.

    Mutation named in `docs/PLAN.md`: `scope_ref` as a column exempting the row from the existing
    guard/snapshot — disproven by reading the same 20000.00 after the catalogue changes underneath
    the approved scenario.
    """
    scenario, segment, rate = _priced_plan(db_session)
    make_commercial_terms(db_session, scenario, scope_ref=segment.id)
    project_id = scenario.project_id

    approved = client.post(
        approve_path(project_id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert approved.status_code == 200, approved.text

    before = _revenue(client, project_id, scenario.id)
    assert (before["state"], before["amount"]) == ("calculated", "20000.00")
    assert before["assumptions_used"]["rate_source"] == "approved_snapshot"

    db_session.execute(
        sa.update(CatalogDefaultRate)
        .where(CatalogDefaultRate.id == rate.id)
        .values(default_selling_rate=Decimal("999.0000"))
    )
    db_session.flush()
    db_session.expire_all()

    after = _revenue(client, project_id, scenario.id)
    assert after == before, (
        "an approved revenue moved with the catalogue although its only rule is segment-scoped"
    )


def test_k_03_a_segment_scoped_story_points_rule_is_refused_by_the_same_write_guard(
    db_session: Session,
) -> None:
    """K-03 — extends SC-4-04's K-04(b): the write guard refuses a Story Points rule carrying a
    non-null `scope_ref` exactly as it refuses a whole-scenario one. `scope_ref` is one more literal
    on the same guarded `INSERT … SELECT` (`create_commercial_terms`), not a second, unguarded write
    path a segment-scoped rule could slip through.
    """
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    approved = make_scenario(db_session, project, name="Approved", status=ScenarioStatus.APPROVED)
    segment = make_scenario_delivery_segment(db_session, approved, name="Phase 1")
    caller = CallerIdentity(user_id=IN_SCOPE_USER)

    with pytest.raises(CommercialTermsFrozen):
        create_commercial_terms(
            db_session,
            caller,
            project.id,
            approved.id,
            model_type="story_points",
            scope_ref=segment.id,
            domain_values={
                "price_per_point": Decimal("1000.0000"),
                "accepted_points": 25,
                "currency": "PLN",
            },
        )

    assert _count_rules(db_session, approved.id) == 0


# --- K-04: cross-scenario integrity is a composite foreign key -----------------------------------


def test_k_04_a_rule_cannot_point_at_a_segment_of_another_scenario(db_session: Session) -> None:
    """K-04 — the composite foreign key refuses a `scope_ref` naming a segment of a *different*
    scenario than the rule's own `scenario_id`.

    Mutation (`docs/PLAN.md`): the foreign key narrowed to `scope_ref` alone (dropping the
    `scenario_id` half) — the insert below would then be accepted.
    """
    scenario = _scenario(db_session)
    other_scenario = make_scenario(db_session, scenario.project, name="Other")
    other_segment = make_scenario_delivery_segment(db_session, other_scenario, name="Phase 1")

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            make_commercial_terms(db_session, scenario, scope_ref=other_segment.id)

    assert _sqlstate_and_constraint(refused.value) == ("23503", SCOPE_REF_FOREIGN_KEY)
    assert _count_rules(db_session, scenario.id) == 0


def test_k_04_contrast_a_rule_can_point_at_a_segment_of_its_own_scenario(
    db_session: Session,
) -> None:
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario)

    rule = make_commercial_terms(db_session, scenario, scope_ref=segment.id)

    assert rule.scope_ref == segment.id


def test_k_04_the_scope_ref_foreign_key_is_composite_in_the_catalog_of_the_database(
    db_session: Session,
) -> None:
    """K-04, structurally — the foreign key really spans both columns, on both sides, the same
    structural proof `test_commercial_terms_schema.py` already runs for
    `TYPE_AGREEMENT_FOREIGN_KEY`.
    """
    row = db_session.execute(
        sa.text(
            "SELECT c.conrelid::regclass::text, c.confrelid::regclass::text,"
            " ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(n, i)"
            "       JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n"
            "       ORDER BY k.i)::text[],"
            " ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(n, i)"
            "       JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.n"
            "       ORDER BY k.i)::text[]"
            " FROM pg_constraint c WHERE c.conname = :name AND c.contype = 'f'"
        ),
        {"name": SCOPE_REF_FOREIGN_KEY},
    ).one()

    assert tuple(row[:2]) == ("commercial_terms", "scenario_delivery_segment")
    assert list(row[2]) == ["scope_ref", "scenario_id"]
    assert list(row[3]) == ["id", "scenario_id"]


# --- D-4: the copying cascade remaps `scope_ref` onto the copy's own segment ---------------------


def _copied_scenario_id(session: Session, response) -> uuid.UUID:
    assert response.status_code == 201, response.text
    session.expire_all()
    return session.execute(
        sa.select(Scenario.id).where(Scenario.project_id == uuid.UUID(response.json()["id"]))
    ).scalar_one()


def test_d4_copying_a_scenario_remaps_a_segment_scoped_rules_scope_ref_to_the_copys_own_segment(
    client: TestClient, db_session: Session
) -> None:
    """D-4=A — the copy's segment-scoped rule points at the *copy's own* segment of the same name,
    never at the source's segment (which the composite foreign key would refuse outright — K-04).
    Proves `copy_scenario_delivery_segments` really runs before `copy_commercial_terms` in
    `SCENARIO_CHILD_COPIERS`, not merely that the registry lists both.
    """
    scenario, segment, _rate = _priced_plan(db_session)
    source_rule = make_commercial_terms(db_session, scenario, scope_ref=segment.id)
    project_id = scenario.project_id

    copy_id = _copied_scenario_id(
        db_session, client.post(f"/projects/{project_id}/copy", headers=as_caller(IN_SCOPE_USER))
    )

    copied_rule = db_session.execute(
        sa.select(CommercialTerms).where(CommercialTerms.scenario_id == copy_id)
    ).scalar_one()
    assert copied_rule.scope_ref is not None
    assert copied_rule.scope_ref != segment.id, (
        "the copy's rule still points at the source's segment"
    )
    copied_segment = db_session.get(ScenarioDeliverySegment, copied_rule.scope_ref)
    assert copied_segment is not None
    assert copied_segment.scenario_id == copy_id
    assert copied_segment.name == "Phase 1"
    assert db_session.get(CommercialTerms, source_rule.id).scope_ref == segment.id, (
        "the source rule was mutated by the copy"
    )

    copy_project_id = db_session.get(Scenario, copy_id).project_id
    response = client.get(
        commercial_terms_path(copy_project_id, copy_id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    read = response.json()
    assert (read["revenue"]["state"], read["revenue"]["amount"]) == ("calculated", "20000.00")


def test_d4_two_differently_named_segments_each_remap_to_the_copy_segment_of_the_same_name(
    client: TestClient, db_session: Session
) -> None:
    """D-4=A, the contrast the single-segment fixture above cannot force: with *two* segments on the
    source, each carrying a rule of a different model (so K-02's one-rule-per-segment index does not
    stand in the way), the copy must remap each rule to *its own* segment's namesake — not to the
    other one, and not to whichever copy segment the join happens to return first. A join predicate
    that matches on anything looser than the segment name (e.g. "any segment of the copy", or "the
    first segment created") passes the single-segment test above but swaps these two.
    """
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    segment_a = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    segment_b = make_scenario_delivery_segment(db_session, scenario, name="Phase 2")
    tm_rule = make_commercial_terms(db_session, scenario, scope_ref=segment_a.id)
    sp_rule = make_story_points_terms(
        db_session, scenario, scope_ref=segment_b.id,
        price_per_point=Decimal("1000.0000"), accepted_points=25, currency="PLN",
    )

    copy_id = _copied_scenario_id(
        db_session, client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    )

    copied_segments_by_name = {
        segment.name: segment.id
        for segment in db_session.execute(
            sa.select(ScenarioDeliverySegment).where(
                ScenarioDeliverySegment.scenario_id == copy_id
            )
        ).scalars()
    }
    assert set(copied_segments_by_name) == {"Phase 1", "Phase 2"}

    copied_tm = db_session.execute(
        sa.select(CommercialTerms).where(
            CommercialTerms.scenario_id == copy_id,
            CommercialTerms.model_type == "time_and_material",
        )
    ).scalar_one()
    copied_sp = db_session.execute(
        sa.select(CommercialTerms).where(
            CommercialTerms.scenario_id == copy_id,
            CommercialTerms.model_type == "story_points",
        )
    ).scalar_one()

    assert copied_tm.scope_ref == copied_segments_by_name["Phase 1"]
    assert copied_sp.scope_ref == copied_segments_by_name["Phase 2"]
    assert copied_tm.scope_ref != copied_sp.scope_ref
    # The source rows are untouched — same check the single-segment test makes.
    assert db_session.get(CommercialTerms, tm_rule.id).scope_ref == segment_a.id
    assert db_session.get(CommercialTerms, sp_rule.id).scope_ref == segment_b.id


def test_d4_the_segment_copier_runs_before_the_commercial_terms_copier_in_the_registry() -> None:
    """D-4=A, structurally — the registry's own order, not only this file's behavioural proof."""
    assert (
        SCENARIO_CHILD_COPIERS.index(copy_scenario_delivery_segments)
        < SCENARIO_CHILD_COPIERS.index(copy_commercial_terms)
    )


# --- hardening the single-row read path against a state the schema now permits -------------------
#
# `commercial_terms_for_caller`/`_view_of`/`_rule_of` still answer "the" rule of a scenario, a
# question `scope_ref` makes ill-posed once a scenario carries more than one row. Unreachable
# through the running API today (no request schema writes `scope_ref`, so the API alone can never
# create a second row) — reachable only by a direct data-layer write, exactly as the fixture below
# does. Guardian/reviewer, gate 2: fail loud with a named exception, not a raw, unhandled
# `sqlalchemy.exc.MultipleResultsFound`.


def test_the_single_row_read_path_names_the_state_instead_of_an_unhandled_sqlalchemy_error(
    db_session: Session,
) -> None:
    """`commercial_terms_for_caller` raises `MultipleCommercialRulesNotSupported` — not the raw
    `MultipleResultsFound` `scalar_one_or_none()` would otherwise let through uncaught — when a
    scenario carries more than one `commercial_terms` row (the exact, legal K-02/D-3=A shape: a
    whole-scenario rule and a segment rule).

    Mutation: `_rule_of` reverted to `scalar_one_or_none()` — this test would then fail with
    `sqlalchemy.exc.MultipleResultsFound` instead of the named exception `pytest.raises` expects.
    """
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    make_commercial_terms(db_session, scenario)
    make_commercial_terms(db_session, scenario, scope_ref=segment.id)
    assert _count_rules(db_session, scenario.id) == 2, "fixture setup: two coexisting rules"

    caller = CallerIdentity(user_id=IN_SCOPE_USER)

    with pytest.raises(MultipleCommercialRulesNotSupported):
        commercial_terms_for_caller(
            db_session, caller, scenario.project_id, scenario.id
        )


# --- drift guard and the migration running both ways -----------------------------------------

_MIGRATION_REVISION = "b7e3f19a6c52"
_PREVIOUS_REVISION = "d2f6a91c4b58"
_MIGRATION_PATH = (
    Path(BACKEND_ROOT)
    / "migrations"
    / "versions"
    / "b7e3f19a6c52_add_scope_ref_to_commercial_terms.py"
)


def _migration() -> ModuleType:
    """Import `b7e3f19a6c52` by path — the same technique
    `test_commercial_terms_schema.py::_migration` uses for `e7b41c9d2a58`."""
    spec = importlib.util.spec_from_file_location("sc_4_05_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_model_and_the_b7e3f19a6c52_migration_agree_on_every_sql_expression() -> None:
    """The migration's copies of the two partial-index predicates are the model's, the same
    drift guard `test_commercial_terms_schema.py` already runs for the discriminator CHECK."""
    migration = _migration()

    assert migration._SCOPE_REF_NULL_EXPRESSION == SCOPE_REF_NULL_EXPRESSION
    assert migration._SCOPE_REF_NOT_NULL_EXPRESSION == SCOPE_REF_NOT_NULL_EXPRESSION


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_the_b7e3f19a6c52_migration_downgrades_and_upgrades_again(
    engine: Engine, alembic_config: Config
) -> None:
    """`downgrade()` of `b7e3f19a6c52` really runs: `scope_ref`, its foreign key and its two partial
    indexes go, and the original non-partial `UNIQUE (scenario_id)` comes back — and all of it
    returns on `upgrade`. The starting revision is **read**, not hard-coded (the lesson recorded in
    `tests/test_absence_budget_schema_constraints.py`); `upgrade(head)` runs in `finally`, because
    `engine` is session-scoped and every other test reads the schema left here.
    """

    def revision() -> str:
        with engine.connect() as connection:
            return connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()

    def state() -> tuple[bool, bool]:
        with engine.connect() as connection:
            has_column = connection.execute(
                sa.text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
                    " WHERE table_name = 'commercial_terms' AND column_name = 'scope_ref')"
                )
            ).scalar_one()
            has_partial_index = connection.execute(
                sa.text(
                    "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname"
                    " = 'uq_commercial_terms_scenario_id_scope_ref')"
                )
            ).scalar_one()
        return has_column, has_partial_index

    before = revision()
    try:
        command.downgrade(alembic_config, _PREVIOUS_REVISION)
        assert revision() == _PREVIOUS_REVISION
        has_column, has_partial_index = state()
        assert has_column is False
        assert has_partial_index is False
    finally:
        command.upgrade(alembic_config, "head")

    assert revision() == before
    has_column, has_partial_index = state()
    assert has_column is True
    assert has_partial_index is True
