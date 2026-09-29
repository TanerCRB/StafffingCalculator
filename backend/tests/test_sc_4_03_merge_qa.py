"""QA SC-4-03, round 3 (after the merge with SC-4-04 and SC-1-11) — proofs the tree was missing
after the merge.

- **Migration `b9e3c7a1f264` both ways with a saved Story Points rule.** The prior `downgrade`/
  `upgrade` attempts on this migration had only a T&M rule in the database
  (`tests/test_outcome_terms_schema.py`), and the `d2f6a91c4b58` attempt — none at all
  (`tests/test_story_points_terms.py`). A `downgrade` that reconstructs the `IN` list without
  `story_points` therefore passed on the database side (on the T&M rule) and was caught only by
  comparing the `pg_get_constraintdef` text. Here a Story Points rule with details must survive a
  `downgrade` to `b7e3f19a6c52` (the `IN` list from `d2f6a91c4b58`) and a subsequent `upgrade`
  without a value change — the database itself refuses the narrowed list.
- **Contrast: the same attempt with a saved Outcome-based rule is refused and nothing is lost.**
  The `downgrade` docstring promises that a remaining `outcome_based` rule will make the CHECK
  reconstruction be refused — "deliberately: downgrade does not silently delete scenario rules".
  Not checked until now. Here: `downgrade` raises an exception, the revision stays on `head`, the
  rule and its `outcome_terms` row are untouched (PostgreSQL's transactional DDL rolls back the
  `DROP TABLE` too).
- **A project copy with three models at once** (T&M, two Story Points rules with different
  values, Outcome-based): each copied rule has its own model's details, values from **its own**
  source, and the same revenue. The prior copy tests have one rule of one model in the
  transaction, so a copier that looks up the details row without a condition on
  `commercial_terms_id` would find the table's only row — correct by accident.

Real PostgreSQL; migrations on the `engine` shared by the session, `upgrade head` in `finally`.
"""

import os
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.data.commercial_terms import TM_TERMS_COLUMNS_NOT_COPIED
from app.models import CommercialTerms, OutcomeTerms, Scenario, StoryPointsTerms, TmTerms
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    as_caller,
    commercial_terms_path,
    make_commercial_terms,
    make_outcome_terms,
    make_project,
    make_scenario,
    make_story_points_terms,
)
from tests.test_outcome_revenue_copy import FULL_DETAILS

OUTCOME_REVISION = "b9e3c7a1f264"
PREVIOUS_REVISION = "b7e3f19a6c52"
"""The `down_revision` of migration `b9e3c7a1f264` after the second linearization (merge SC-4-05);
`b7e3f19a6c52` does not touch the discriminator CHECK — the list after `downgrade` is still the
list from `d2f6a91c4b58`.

**`OUTCOME_REVISION` stopped being `head` after the third linearization** (merge SC-5-02,
2026-09-25): `9b3f6a1d0c47` was appended onto `b9e3c7a1f264` instead of beside it, so `head` is
now one step further. Both migration tests below examine the behaviour of MIGRATION
`b9e3c7a1f264`, not the definition of `head` — each now starts with an explicit
`downgrade(alembic_config, OUTCOME_REVISION)`, which reproduces exactly the starting point they
had before this linearization. No assertion's content changes."""

DETAILS_BY_MODEL = {
    "time_and_material": TmTerms,
    "story_points": StoryPointsTerms,
    "outcome_based": OutcomeTerms,
}


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _one(engine: Engine, sql: str, **params: object) -> object:
    with engine.connect() as connection:
        return connection.execute(sa.text(sql), params).scalar_one()


def _committed_rule(engine: Engine, make, name: str, **details: object) -> tuple[uuid.UUID, ...]:
    """A rule committed to the database (not in the rolled-back `db_session` transaction):
    `alembic` opens its own connection and does not see uncommitted rows."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name=name, accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Baseline")
        rule = make(setup, scenario, **details)
        ids = (rule.id, scenario.id, project.id)
        setup.commit()
    return ids


def _delete_committed(engine: Engine, rule_id, scenario_id, project_id) -> None:
    with engine.begin() as connection:
        for table in ("tm_terms", "story_points_terms"):
            connection.execute(
                sa.text(f"DELETE FROM {table} WHERE commercial_terms_id = :id"), {"id": rule_id}
            )
        if connection.execute(sa.text("SELECT to_regclass('outcome_terms')")).scalar_one():
            connection.execute(
                sa.text("DELETE FROM outcome_terms WHERE commercial_terms_id = :id"),
                {"id": rule_id},
            )
        connection.execute(sa.text("DELETE FROM commercial_terms WHERE id = :id"), {"id": rule_id})
        connection.execute(
            sa.text("DELETE FROM project_access WHERE project_id = :id"), {"id": project_id}
        )
        connection.execute(sa.text("DELETE FROM scenarios WHERE id = :id"), {"id": scenario_id})
        connection.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": project_id})


# --- migration both ways with a Story Points rule -------------------------------------------------


def test_merge_a_story_points_rule_survives_the_downgrade_and_the_upgrade_of_the_outcome_migration(
    engine: Engine, alembic_config: Config
) -> None:
    """A Story Points rule with details (1000 × 25 PLN) survives a `downgrade` to `b7e3f19a6c52`
    and a subsequent `upgrade` without a value change. Mutation "`downgrade` reconstructs the `IN`
    list without `story_points`" (or `upgrade` with a list without `story_points`): the database
    refuses the CHECK reconstruction on this rule — regardless of whether the migration's constant
    and its drift guard agree."""
    ids = _committed_rule(
        engine, make_story_points_terms, "SP downgrade",
        price_per_point=Decimal("1000.0000"), accepted_points=25, currency="PLN",
    )
    rule_id = ids[0]
    details_sql = (
        "SELECT price_per_point::text || '|' || accepted_points::text || '|' || currency"
        " FROM story_points_terms WHERE commercial_terms_id = :id"
    )
    # `OUTCOME_REVISION` docstring: no longer `head` after the SC-5-02 linearization — an explicit
    # `downgrade` here reproduces the exact starting point this test had before that.
    command.downgrade(alembic_config, OUTCOME_REVISION)
    before = _one(engine, "SELECT version_num FROM alembic_version")
    assert before == OUTCOME_REVISION
    try:
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        assert _one(engine, "SELECT version_num FROM alembic_version") == PREVIOUS_REVISION
        assert _one(engine, "SELECT to_regclass('outcome_terms') IS NULL") is True
        assert _one(
            engine, "SELECT model_type FROM commercial_terms WHERE id = :id", id=rule_id
        ) == "story_points"
        assert _one(engine, details_sql, id=rule_id) == "1000.0000|25|PLN"

        # `OUTCOME_REVISION`, not `"head"`: `head` now points one migration further (SC-5-02) than
        # the point this test is about — going all the way to `head` here would still prove nothing
        # wrong, but `assert … == before` would then compare `OUTCOME_REVISION` against the real
        # `head` and fail for a reason unrelated to what this test checks.
        command.upgrade(alembic_config, OUTCOME_REVISION)
        assert _one(engine, "SELECT version_num FROM alembic_version") == before
        assert _one(
            engine, "SELECT model_type FROM commercial_terms WHERE id = :id", id=rule_id
        ) == "story_points"
        assert _one(engine, details_sql, id=rule_id) == "1000.0000|25|PLN"
    finally:
        command.upgrade(alembic_config, "head")
        _delete_committed(engine, *ids)


def test_merge_contrast_an_outcome_rule_makes_the_downgrade_refuse_and_loses_nothing(
    engine: Engine, alembic_config: Config
) -> None:
    """Contrast to the test above — one change: an Outcome-based rule instead of Story Points.
    `downgrade` is refused (a CHECK reconstruction without `outcome_based` against an existing
    rule), the revision stays on `b9e3c7a1f264`, and the rule and its `outcome_terms` row are
    untouched — `DROP TABLE` rolled back together with the rest of the transaction. Mutation
    "downgrade first deletes `outcome_based` rules to be able to proceed": `downgrade` succeeds
    and the rule silently disappears."""
    ids = _committed_rule(engine, make_outcome_terms, "Outcome downgrade", **FULL_DETAILS)
    rule_id = ids[0]
    # `OUTCOME_REVISION` docstring: no longer `head` after the SC-5-02 linearization — an explicit
    # `downgrade` here reproduces the exact starting point this test had before that.
    command.downgrade(alembic_config, OUTCOME_REVISION)
    before = _one(engine, "SELECT version_num FROM alembic_version")
    assert before == OUTCOME_REVISION
    try:
        with pytest.raises(sa.exc.IntegrityError):
            command.downgrade(alembic_config, PREVIOUS_REVISION)
        assert _one(engine, "SELECT version_num FROM alembic_version") == OUTCOME_REVISION
        assert _one(engine, "SELECT to_regclass('outcome_terms') IS NOT NULL") is True
        assert _one(
            engine, "SELECT model_type FROM commercial_terms WHERE id = :id", id=rule_id
        ) == "outcome_based"
        assert _one(
            engine,
            "SELECT fixed_fee::text FROM outcome_terms WHERE commercial_terms_id = :id",
            id=rule_id,
        ) == "20000.0000"
    finally:
        command.upgrade(alembic_config, "head")
        _delete_committed(engine, *ids)


# --- a project copy with three models' rules at once --------------------------------------------


def _details(session: Session, model_type: str, rule_id: uuid.UUID) -> dict[str, object]:
    table = DETAILS_BY_MODEL[model_type]
    row = session.execute(
        sa.select(table).where(table.commercial_terms_id == rule_id)
    ).scalar_one_or_none()
    assert row is not None, f"the {model_type} rule has no details row — an incomplete rule"
    return {
        attribute.key: getattr(row, attribute.key)
        for attribute in sa.inspect(table).column_attrs
        if attribute.key not in TM_TERMS_COLUMNS_NOT_COPIED
    }


def test_merge_one_project_copy_copies_every_models_rule_with_its_own_sources_details(
    client: TestClient, db_session: Session
) -> None:
    """One project copy with four scenarios: T&M, Story Points 1000 × 25, Story Points
    500 × 10 (EUR) and Outcome-based. Each copied rule: a new identifier, the source's model, a
    details-table row of **its own** model with values from **its own** source, the same revenue.

    Two rules of the same model with different values are deliberate here: a copier that reads
    the details row without a condition on the source rule would, in a single-rule test, hit the
    table's only row; here it hits someone else's or two of them."""
    project = make_project(db_session, name="Mixed copy", accessible_to=(IN_SCOPE_USER,))
    sources = {
        "T&M": make_commercial_terms(db_session, make_scenario(db_session, project, name="T&M")),
        "SP-A": make_story_points_terms(
            db_session, make_scenario(db_session, project, name="SP-A"),
            price_per_point=Decimal("1000.0000"), accepted_points=25, currency="PLN",
        ),
        "SP-B": make_story_points_terms(
            db_session, make_scenario(db_session, project, name="SP-B"),
            price_per_point=Decimal("500.0000"), accepted_points=10, currency="EUR",
        ),
        "Outcome": make_outcome_terms(
            db_session, make_scenario(db_session, project, name="Outcome"), **FULL_DETAILS
        ),
    }
    source_revenue = {
        name: client.get(
            commercial_terms_path(project.id, rule.scenario_id), headers=as_caller(IN_SCOPE_USER)
        ).json()["revenue"]
        for name, rule in sources.items()
    }
    assert source_revenue["SP-A"]["amount"] == "25000.00"
    assert source_revenue["SP-B"]["amount"] == "5000.00"

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    db_session.expire_all()
    copy_project_id = uuid.UUID(response.json()["id"])
    copies = {
        scenario.name: scenario
        for scenario in db_session.execute(
            sa.select(Scenario).where(Scenario.project_id == copy_project_id)
        ).scalars()
    }
    assert set(copies) == set(sources)

    for name, source_rule in sources.items():
        copied_rule = db_session.execute(
            sa.select(CommercialTerms).where(CommercialTerms.scenario_id == copies[name].id)
        ).scalar_one()
        assert copied_rule.id != source_rule.id
        assert copied_rule.model_type == source_rule.model_type
        assert _details(db_session, copied_rule.model_type, copied_rule.id) == _details(
            db_session, source_rule.model_type, source_rule.id
        ), f"{name}: the copy's details are not its own source's"
        copy_read = client.get(
            commercial_terms_path(copy_project_id, copies[name].id),
            headers=as_caller(IN_SCOPE_USER),
        ).json()
        assert copy_read["commercial_terms"]["id"] == str(copied_rule.id)
        assert copy_read["revenue"] == source_revenue[name], name
