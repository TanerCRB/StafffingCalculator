"""SC-4-04, K-04 — the three boundaries SC-4-01 already proved for `tm_terms`, repeated for the new
details table `story_points_terms` (Issue #68).

(a) scope: a Story Points rule of a scenario outside the caller's scope is the same `404` as no
    scenario, for the read and the write (ADR-0005, addendum SC-4-01, point 1 — unchanged by
    SC-4-04, proven here specifically for the new request shape).
(b) the write guard: a Story Points rule cannot land in an `approved` scenario, refused in the same
    guarded statement T&M uses — proven on *this* request shape and *this* details table, so a
    future refactor that branches the write path per model and forgets to route Story Points
    through the guard fails here, not only in a generic `scenario_guard.py` test.
(c) copying: a scenario copy duplicates the rule **and** the `story_points_terms` row as one
    aggregate, with new identifiers — a copy with the rule and without the details row is a
    regression (`incomplete_commercial_terms` on a scenario that was priced).

Plus the database-level type agreement `story_points_terms` needs proven for itself (K-04,
structural half), the same way `test_commercial_terms_schema.py` already proves it for `tm_terms`.
"""

import importlib.util
import os
import threading
import uuid
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.commercial_terms import (
    COMMERCIAL_TERMS_COLUMNS_NOT_COPIED,
    TM_TERMS_COLUMNS_NOT_COPIED,
)
from app.models import CommercialTerms, Scenario, ScenarioStatus, StoryPointsTerms
from app.models.commercial_terms import (
    MODEL_TYPE_KNOWN_EXPRESSION,
    SP_MODEL_TYPE_EXPRESSION,
    STORY_POINTS_TYPE_AGREEMENT_FOREIGN_KEY,
)
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    approve_path,
    as_caller,
    commercial_terms_path,
    make_commercial_terms,
    make_project,
    make_scenario,
    make_story_points_terms,
    wait_until_a_lock_request_is_pending,
)

_MIGRATION_PATH = (
    Path(BACKEND_ROOT)
    / "migrations"
    / "versions"
    / "d2f6a91c4b58_create_story_points_terms_and_widen_the_model_type_check.py"
)


def _migration() -> ModuleType:
    """Import `d2f6a91c4b58` by path — the same technique
    `test_commercial_terms_schema.py::_migration` uses for `e7b41c9d2a58`."""
    spec = importlib.util.spec_from_file_location("sc_4_04_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

SP = {
    "model_type": "story_points",
    "price_per_point": "1000.0000",
    "accepted_points": 25,
    "currency": "PLN",
}


def _sqlstate_and_constraint(error: IntegrityError) -> tuple[str | None, str | None]:
    diagnostics = error.orig.diag  # type: ignore[union-attr]
    return diagnostics.sqlstate, diagnostics.constraint_name


def _count_rules(session: Session) -> int:
    session.expire_all()
    return session.execute(sa.select(sa.func.count()).select_from(CommercialTerms)).scalar_one()


# --- K-04 (a): scope — 404, indistinguishable from absent, read and write -----------------------


def test_k_04a_the_story_points_rule_of_a_scenario_outside_the_callers_scope_is_the_same_404(
    client: TestClient, db_session: Session
) -> None:
    """K-04(a) — repeats K-05 of SC-4-01 for the Story Points request shape specifically.

    Mutation: the endpoint routing a Story Points payload through a scope check of its own instead
    of `scenario_in_scope` — the write below would then leak a `409` (or a `201`) for a scenario
    outside the caller's scope instead of the shared `404`.
    """
    mine_project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    mine = make_scenario(db_session, mine_project, name="Mine")
    theirs_project = make_project(
        db_session, name="Borealis", accessible_to=(OUT_OF_SCOPE_USER,)
    )
    theirs = make_scenario(db_session, theirs_project, name="Theirs")
    make_story_points_terms(db_session, theirs)  # something to leak, if scope were broken

    read = client.get(
        commercial_terms_path(theirs_project.id, theirs.id), headers=as_caller(IN_SCOPE_USER)
    )
    write = client.post(
        commercial_terms_path(theirs_project.id, theirs.id),
        json=SP,
        headers=as_caller(IN_SCOPE_USER),
    )

    for response in (read, write):
        assert response.status_code == 404, response.text
    assert _count_rules(db_session) == 1, "the refused write created a second rule"

    contrast = client.post(
        commercial_terms_path(mine_project.id, mine.id), json=SP, headers=as_caller(IN_SCOPE_USER)
    )
    assert contrast.status_code == 201, contrast.text


# --- K-04 (b): the write guard, on the Story Points request shape specifically ------------------


def test_k_04b_the_story_points_domain_values_are_written_by_the_one_guarded_statement(
    client: TestClient, db_session: Session
) -> None:
    """K-04(b), structural — `price_per_point`/`accepted_points`/`currency` reach
    `story_points_terms` through *the same* guarded `INSERT … SELECT` that creates the rule, never a
    second statement issued after it.

    The race test above (two real connections, a real approval) proves the guard wins whenever the
    write's own guarded statement is still the one contending for the scenario's row lock — but it
    can only observe that contention if the create call still *is* one guarded statement. A
    refactor that let the guarded statement create the rule (and, for Story Points, an initially
    valid details row) and then re-wrote `price_per_point`/`accepted_points`/`currency` a second
    time with a plain, unguarded `UPDATE` would keep every other assertion in this file green: the
    race test's timing hook fires on the approval's own `UPDATE scenarios SET status …`, and by the
    time a concurrent approval wins that race the guarded statement has already refused (zero
    rows), so the race test never even reaches a hypothetical second statement to prove it does not
    run. This test closes that gap directly, by counting statements instead of racing a clock: it
    asserts there is exactly one *write* (`INSERT`/`UPDATE`) touching `story_points_terms` for one
    successful create call — the read-back the dispatcher does afterwards (`_rule_of`'s existence
    check, `_story_points`'s `SELECT`) is expected and excluded, only a second write would be the
    mutation.
    """
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")

    writes_touching_story_points_terms: list[str] = []

    def record(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        lowered = statement.lower()
        if "story_points_terms" in lowered and ("insert into" in lowered or "update " in lowered):
            writes_touching_story_points_terms.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        response = client.post(
            commercial_terms_path(project.id, scenario.id),
            json=SP,
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["commercial_terms"]["model_type"] == "story_points"
    assert len(writes_touching_story_points_terms) == 1, (
        "the Story Points domain values were written by more than one statement — the guard on the "
        f"first does not cover the others: {writes_touching_story_points_terms}"
    )
    assert writes_touching_story_points_terms[0].strip().lower().startswith("with"), (
        "the one write touching story_points_terms is not the guarded INSERT … SELECT chained onto "
        "the commercial_terms CTE"
    )


def test_k_04b_a_story_points_rule_cannot_be_written_into_an_approved_scenario(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-04(b), plain run — `409` naming *approved*, zero rules and zero details rows, with the
    draft of the same project as the contrast (`201`, both rows written).
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Aurora", accessible_to=(IN_SCOPE_USER,))
        draft = make_scenario(setup, project, name="Draft")
        approved = make_scenario(setup, project, name="Approved", status=ScenarioStatus.APPROVED)
        state = {"project_id": project.id, "draft_id": draft.id, "approved_id": approved.id}
        setup.commit()

    refused = committing_client.post(
        commercial_terms_path(state["project_id"], state["approved_id"]),
        json=SP,
        headers=as_caller(IN_SCOPE_USER),
    )
    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"]
    with engine.connect() as connection:
        rules = connection.execute(
            sa.text("SELECT count(*) FROM commercial_terms WHERE scenario_id = :id"),
            {"id": state["approved_id"]},
        ).scalar_one()
        details = connection.execute(
            sa.text(
                "SELECT count(*) FROM story_points_terms s JOIN commercial_terms c"
                " ON c.id = s.commercial_terms_id WHERE c.scenario_id = :id"
            ),
            {"id": state["approved_id"]},
        ).scalar_one()
    assert (rules, details) == (0, 0)

    accepted = committing_client.post(
        commercial_terms_path(state["project_id"], state["draft_id"]),
        json=SP,
        headers=as_caller(IN_SCOPE_USER),
    )
    assert accepted.status_code == 201, accepted.text
    with engine.connect() as connection:
        rules = connection.execute(
            sa.text("SELECT count(*) FROM commercial_terms WHERE scenario_id = :id"),
            {"id": state["draft_id"]},
        ).scalar_one()
        details = connection.execute(
            sa.text(
                "SELECT count(*) FROM story_points_terms s JOIN commercial_terms c"
                " ON c.id = s.commercial_terms_id WHERE c.scenario_id = :id"
            ),
            {"id": state["draft_id"]},
        ).scalar_one()
    assert (rules, details) == (1, 1)


def test_k_04b_an_approval_racing_a_story_points_write_leaves_no_rule_under_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-04(b), the race — two connections, the real approval endpoint, on the Story Points request
    shape. The shape of `test_commercial_terms_guards.py::test_k_06_an_approval_committing_
    concurrently_with_the_rule_write_leaves_no_rule_under_it`, repeated here so a future write path
    that branches per model and forgets to route Story Points through `unapproved_scenario` (and its
    row lock) is caught by *this* file, not only by the T&M one.

    Mutation: a details-table-specific write function for Story Points that skips the lock — the
    write then does not block (`blocked` would be false) and can land under an approved scenario.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Aurora", accessible_to=(IN_SCOPE_USER,))
        draft = make_scenario(setup, project, name="Draft")
        state = {"project_id": project.id, "draft_id": draft.id}
        setup.commit()

    outcome: dict[str, Any] = {}
    fired: list[str] = []

    def start_the_writer(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if fired or "update scenarios set status" not in statement.lower():
            return
        fired.append(statement)

        def writer() -> None:
            try:
                outcome["response"] = committing_client.post(
                    commercial_terms_path(state["project_id"], state["draft_id"]),
                    json=SP,
                    headers=as_caller(IN_SCOPE_USER),
                )
            except BaseException as error:  # noqa: BLE001 — reported, never swallowed
                outcome["error"] = error

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        outcome["thread"] = thread
        outcome["blocked"] = wait_until_a_lock_request_is_pending(engine)

    event.listen(Engine, "before_cursor_execute", start_the_writer)
    try:
        outcome["approval"] = committing_client.post(
            approve_path(state["project_id"], state["draft_id"]), headers=as_caller(IN_SCOPE_USER)
        )
    finally:
        event.remove(Engine, "before_cursor_execute", start_the_writer)

    assert fired, "the approval never reached its status update"
    thread = outcome["thread"]
    thread.join(timeout=30)
    assert not thread.is_alive(), "the Story Points write never finished"
    assert "error" not in outcome, outcome.get("error")
    assert outcome["blocked"], "the Story Points write never waited for the approval's lock"

    with engine.connect() as connection:
        approved = connection.execute(
            sa.text("SELECT status FROM scenarios WHERE id = :id"), {"id": state["draft_id"]}
        ).scalar_one() == "approved"
        rules = connection.execute(
            sa.text("SELECT count(*) FROM commercial_terms WHERE scenario_id = :id"),
            {"id": state["draft_id"]},
        ).scalar_one()
    assert not (approved and rules), (
        f"approved={approved} with {rules} rule(s): a Story Points rule landed in a calculation "
        "that was already being approved"
    )
    assert approved, "the approval itself failed — the pair above is satisfied vacuously"
    assert outcome["response"].status_code == 409, outcome["response"].text
    assert "approved" in outcome["response"].json()["detail"]


# --- K-04 (c): copying the aggregate -------------------------------------------------------------


def _copied_scenario_id(session: Session, response: Any) -> uuid.UUID:
    assert response.status_code == 201, response.text
    session.expire_all()
    return session.execute(
        sa.select(Scenario.id).where(Scenario.project_id == uuid.UUID(response.json()["id"]))
    ).scalar_one()


def test_k_04c_copying_a_scenario_copies_its_story_points_rule_and_details_row(
    client: TestClient, db_session: Session
) -> None:
    """K-04(c) — the copy has its own rule, its own `story_points_terms` row pointing at *that*
    rule, and prices to the same revenue as the source; the source's rows are untouched.

    Mutations this kills: the copier not registered for this model (no rule on the copy — it is
    not, it is the same `copy_commercial_terms` entry T&M uses, generic over
    `DETAIL_TABLE_BY_MODEL`); the details row re-pointed at the source's rule id; a copy with the
    rule and no details row — an *incomplete* Story Points rule, not a copied one.
    """
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    source_rule = make_story_points_terms(
        db_session, scenario, price_per_point=Decimal("1000.0000"), accepted_points=25,
        currency="PLN",
    )

    copy_id = _copied_scenario_id(
        db_session, client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    )

    copied_rule = db_session.execute(
        sa.select(CommercialTerms).where(CommercialTerms.scenario_id == copy_id)
    ).scalar_one()
    assert copied_rule.id != source_rule.id
    assert copied_rule.model_type == source_rule.model_type == "story_points"
    copied_details = db_session.execute(
        sa.select(StoryPointsTerms).where(StoryPointsTerms.commercial_terms_id == copied_rule.id)
    ).scalar_one_or_none()
    assert copied_details is not None, (
        "the copy has a rule and no details row — an incomplete rule, not a copied one"
    )
    assert (
        copied_details.price_per_point, copied_details.accepted_points, copied_details.currency,
    ) == (Decimal("1000.0000"), 25, "PLN")
    source_details = db_session.execute(
        sa.select(StoryPointsTerms).where(StoryPointsTerms.commercial_terms_id == source_rule.id)
    ).scalar_one()
    assert source_details.accepted_points == 25, "the source row was mutated by the copy"

    copy_project_id = db_session.get(Scenario, copy_id).project_id
    read = client.get(
        commercial_terms_path(copy_project_id, copy_id), headers=as_caller(IN_SCOPE_USER)
    ).json()
    assert read["commercial_terms"]["id"] == str(copied_rule.id)
    assert (read["revenue"]["state"], read["revenue"]["amount"]) == ("calculated", "25000.00")


def test_every_story_points_column_is_either_copied_or_explicitly_excluded() -> None:
    """Drift guard, the `story_points_terms` half — a column added later forces the decision instead
    of being silently copied or silently dropped (`app.data.column_copy`); the same guard
    `test_commercial_terms_copy.py` already runs for `tm_terms`."""
    rule_columns = {attribute.key for attribute in sa.inspect(CommercialTerms).column_attrs}
    details_columns = {attribute.key for attribute in sa.inspect(StoryPointsTerms).column_attrs}

    assert rule_columns == {"model_type"} | COMMERCIAL_TERMS_COLUMNS_NOT_COPIED
    assert details_columns == {
        "model_type", "price_per_point", "accepted_points", "currency",
    } | TM_TERMS_COLUMNS_NOT_COPIED


# --- K-04, structural half: type agreement enforced by the database -------------------------------


def test_k_04_a_story_points_details_row_of_a_rule_of_another_model_is_refused_by_the_foreign_key(
    db_session: Session,
) -> None:
    """The mirror of `test_commercial_terms_schema.py`'s K-04 test, for `story_points_terms`.

    The contrast is first, in the same transaction: a Story Points details row of a Story Points
    rule is accepted. Then a T&M rule's row cannot claim to be Story Points details — refused by
    `fk_story_points_terms_commercial_terms_model_type`, named explicitly so the refusal cannot be
    mistaken for the CHECK on `story_points_terms.model_type` (which refuses a different row).
    """
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    accepted = make_story_points_terms(db_session, scenario)
    assert accepted.model_type == "story_points"

    tm_scenario = make_scenario(db_session, project, name="T&M variant")
    tm_rule = make_commercial_terms(db_session, tm_scenario, with_details=False)

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "INSERT INTO story_points_terms"
                    " (commercial_terms_id, model_type, price_per_point, accepted_points, currency)"
                    " VALUES (:id, 'story_points', 1000.0000, 25, 'PLN')"
                ),
                {"id": tm_rule.id},
            )

    assert _sqlstate_and_constraint(refused.value) == (
        "23503", STORY_POINTS_TYPE_AGREEMENT_FOREIGN_KEY,
    )


def test_k_04_a_story_points_details_row_cannot_claim_the_tm_model(db_session: Session) -> None:
    """The child-side CHECK on `story_points_terms.model_type`, by its own constraint name — without
    it a details row could agree with a T&M rule by *claiming* to be Story Points."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    rule = make_commercial_terms(db_session, scenario, with_details=False)

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "INSERT INTO story_points_terms"
                    " (commercial_terms_id, model_type, price_per_point, accepted_points, currency)"
                    " VALUES (:id, 'time_and_material', 1000.0000, 25, 'PLN')"
                ),
                {"id": rule.id},
            )

    assert _sqlstate_and_constraint(refused.value) == (
        "23514", "ck_story_points_terms_model_type_is_sp",
    )


def test_price_per_point_and_accepted_points_are_checked_in_the_database(
    db_session: Session,
) -> None:
    """`price_per_point > 0` and `accepted_points >= 0`, the same reasoning `amount_positive`
    (`app.models.additional_cost`) and `budget_days_not_negative` (`app.models.catalog`) already
    apply — not application validation (NF-01): both are refused directly through the ORM."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario_a = make_scenario(db_session, project, name="A")
    scenario_b = make_scenario(db_session, project, name="B")
    rule_a = make_story_points_terms(db_session, scenario_a, with_details=False)
    rule_b = make_story_points_terms(db_session, scenario_b, with_details=False)

    with pytest.raises(IntegrityError) as zero_price:
        with db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "INSERT INTO story_points_terms"
                    " (commercial_terms_id, model_type, price_per_point, accepted_points, currency)"
                    " VALUES (:id, 'story_points', 0, 10, 'PLN')"
                ),
                {"id": rule_a.id},
            )
    assert _sqlstate_and_constraint(zero_price.value) == (
        "23514", "ck_story_points_terms_price_per_point_positive",
    )

    with pytest.raises(IntegrityError) as negative_points:
        with db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "INSERT INTO story_points_terms"
                    " (commercial_terms_id, model_type, price_per_point, accepted_points, currency)"
                    " VALUES (:id, 'story_points', 1000.0000, -1, 'PLN')"
                ),
                {"id": rule_b.id},
            )
    assert _sqlstate_and_constraint(negative_points.value) == (
        "23514", "ck_story_points_terms_accepted_points_not_negative",
    )


def test_the_model_and_the_d2f6a91c4b58_migration_agree_on_every_sql_expression() -> None:
    """The forward-looking half of `test_commercial_terms_schema.py`'s drift guard, for the
    migration that widens `model_type_known` (SC-4-04).

    `e7b41c9d2a58`'s own copy of `model_type_known` is deliberately compared against a fixed
    historical string there, not against the model's live constant, once a second migration widens
    the CHECK further — see that file's `_MODEL_TYPE_KNOWN_EXPRESSION_AT_E7B41C9D2A58`. *This*
    migration's copy is the one that has to agree with the model going forward, until a third model
    widens the CHECK again and this file's constant becomes the next historical snapshot.
    """
    migration = _migration()

    assert migration._MODEL_TYPE_KNOWN_EXPRESSION == MODEL_TYPE_KNOWN_EXPRESSION
    assert migration._SP_MODEL_TYPE_EXPRESSION == SP_MODEL_TYPE_EXPRESSION


# --- the migration runs both ways -----------------------------------------------------------------

_PREVIOUS_REVISION = "a3d9e6f20c71"


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_the_d2f6a91c4b58_migration_downgrades_and_upgrades_again(
    engine: Engine, alembic_config: Config
) -> None:
    """`downgrade()` of `d2f6a91c4b58` really runs: `story_points_terms` goes and the discriminator
    CHECK narrows back to `time_and_material` alone — and both come back on `upgrade`.

    The starting revision is **read**, not hard-coded (the lesson recorded in
    `tests/test_absence_budget_schema_constraints.py`); `upgrade(head)` runs in `finally`, because
    `engine` is session-scoped and every other test reads the schema left here.
    """

    def revision() -> str:
        with engine.connect() as connection:
            return connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()

    def state() -> tuple[bool, str]:
        with engine.connect() as connection:
            table_exists = connection.execute(
                sa.text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
                    " WHERE table_name = 'story_points_terms')"
                )
            ).scalar_one()
            check_expression = connection.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conname = 'ck_commercial_terms_model_type_known'"
                )
            ).scalar_one()
        return table_exists, check_expression

    before = revision()
    try:
        command.downgrade(alembic_config, _PREVIOUS_REVISION)
        assert revision() == _PREVIOUS_REVISION
        table_exists, check_expression = state()
        assert table_exists is False
        assert "story_points" not in check_expression
    finally:
        command.upgrade(alembic_config, "head")

    assert revision() == before
    table_exists, check_expression = state()
    assert table_exists is True
    assert "story_points" in check_expression and "time_and_material" in check_expression
