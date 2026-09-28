"""SC-4-01, K-04 — type agreement between a rule and its details row is enforced by the database.

Every write here goes through the ORM or raw SQL, never through a request schema: the claim of K-04
is that the **database** refuses a details row of the wrong model, so a path through Pydantic would
prove only that Pydantic refused first.

**How a mismatch is staged.** The discriminator CHECK on `commercial_terms` admits
`time_and_material`, `story_points` (SC-4-04), `outcome_based` (SC-4-03) and `fixed_price` (SC-4-02)
(ADR-0003, point 2), so a rule of another model can exist, and a *T&M* details row must still be
unable to point at it — a T&M details row pointing at a T&M rule is, correctly, accepted. The test
was written when the CHECK admitted `time_and_material` alone, so it still drops the CHECK inside
its own transaction before inserting the Fixed-Price rule (rolled back with the test); with
`fixed_price` now admitted, that step is no longer needed for the insert to succeed. What refuses
the mismatched row is then asserted **by constraint name**, so the refusal cannot be the CHECK on
`tm_terms` (which refuses a different row) or anything else that happened to fail.

Real PostgreSQL, real migration (ADR-0001).
"""

import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.commercial_terms import DETAIL_TABLE_BY_MODEL, REVENUE_BY_MODEL
from app.models.catalog import VALID_PERIOD_EXPRESSION
from app.models.commercial_terms import (
    MODEL_TYPE_KNOWN_EXPRESSION,
    MODEL_TYPES,
    TM_MODEL_TYPE_EXPRESSION,
    TYPE_AGREEMENT_FOREIGN_KEY,
)
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    make_commercial_terms,
    make_project,
    make_scenario,
)

MIGRATION_REVISION = "e7b41c9d2a58"
PREVIOUS_REVISION = "c8e2a4f61d93"
MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "e7b41c9d2a58_create_commercial_terms_tm_terms_and_the_rate_snapshot.py"
)
# The newest migration recreating `ck_commercial_terms_model_type_known` with the full `IN` list
# (ADR-0003, addendum 2026-09-25 SC-4-03, point 10c). The next commercial model repoints this path
# at its own migration; that model's schema test pins the history through its downgrade.
# Repointed by SC-4-02 from `b9e3c7a1f264` (SC-4-03) to `b8f2d6a41c93` (Fixed Price) at the sync of
# 2026-09-28 — the block-4 rule of the human decision of 2026-09-25 on Issue #66; `b9e3c7a1f264`
# stays compared with what it itself created (`tests/test_outcome_terms_schema.py`,
# `MODEL_TYPES_OF_THIS_MIGRATION`).
LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "b8f2d6a41c93_create_fixed_price_terms.py"
)


def _migration(path: Path = MIGRATION_PATH, name: str = "sc_4_01_migration") -> ModuleType:
    """Import the migration by path — `migrations/versions` is not a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sqlstate_and_constraint(error: IntegrityError) -> tuple[str | None, str | None]:
    diagnostics = error.orig.diag  # type: ignore[union-attr]
    return diagnostics.sqlstate, diagnostics.constraint_name


def _scenario(session: Session):
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    return make_scenario(session, project, name="Baseline")


# --- K-04: type agreement in the database --------------------------------------------------------


def test_k_04_a_tm_details_row_of_a_rule_of_another_model_is_refused_by_the_composite_foreign_key(
    db_session: Session,
) -> None:
    """K-04 — the mismatched details row is refused by `fk_tm_terms_commercial_terms_model_type`.

    The contrast is first and in the same transaction: a T&M details row of a T&M rule is accepted,
    so the refusal below is about the *mismatch* and not about `tm_terms` refusing everything.

    Mutation named by the criterion: **the foreign key on `commercial_terms_id` alone** (type
    agreement left to the application). With it, the mismatched row is accepted and this test fails
    on `pytest.raises`. The second mutation — dropping `uq_commercial_terms_id_model_type` — is not
    expressible without the first: PostgreSQL refuses a composite foreign key without it.
    """
    scenario = _scenario(db_session)
    accepted = make_commercial_terms(db_session, scenario)  # the contrast: rule + details, T&M
    assert accepted.model_type == "time_and_material"

    # What the next model's migration will do: widen the discriminator. Rolled back with the test.
    db_session.execute(
        sa.text("ALTER TABLE commercial_terms DROP CONSTRAINT ck_commercial_terms_model_type_known")
    )
    other_project = make_project(db_session, name="Borealis", accessible_to=(IN_SCOPE_USER,))
    other_scenario = make_scenario(db_session, other_project, name="Fixed price variant")
    fixed_price_rule_id = db_session.execute(
        sa.text(
            "INSERT INTO commercial_terms (id, scenario_id, model_type)"
            " VALUES (gen_random_uuid(), :scenario_id, 'fixed_price') RETURNING id"
        ),
        {"scenario_id": other_scenario.id},
    ).scalar_one()

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "INSERT INTO tm_terms (commercial_terms_id, model_type)"
                    " VALUES (:id, 'time_and_material')"
                ),
                {"id": fixed_price_rule_id},
            )

    assert _sqlstate_and_constraint(refused.value) == ("23503", TYPE_AGREEMENT_FOREIGN_KEY)


def test_k_04_the_details_row_cannot_claim_another_model_and_a_rule_cannot_name_an_unknown_one(
    db_session: Session,
) -> None:
    """K-04's two neighbouring refusals, each by its own constraint name.

    - a `tm_terms` row saying `fixed_price` is refused by `ck_tm_terms_model_type_is_tm` — without
      it, a details row could agree with a Fixed-Price rule by *claiming* to be one;
    - a rule naming a model with no details table (`not_a_model`) is refused by
    `ck_commercial_terms_model_type_known`
      (ADR-0003, point 2: a discriminator value without a details table is a rule nothing can
      price).
    """
    scenario = _scenario(db_session)
    rule = make_commercial_terms(db_session, scenario, with_details=False)

    with pytest.raises(IntegrityError) as claims_other_model:
        with db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "INSERT INTO tm_terms (commercial_terms_id, model_type)"
                    " VALUES (:id, 'fixed_price')"
                ),
                {"id": rule.id},
            )
    assert _sqlstate_and_constraint(claims_other_model.value) == (
        "23514",
        "ck_tm_terms_model_type_is_tm",
    )

    # 'not_a_model' — a value that will never be a real model. The placeholder for "a model with no
    # details table" used to be 'fixed_price', but SC-4-02 made it real (it is in the CHECK), and
    # SC-4-03 did the same with 'outcome_based' — so the placeholder cannot be the name of any F-06
    # model. The first half of the test (a `tm_terms` row claiming 'fixed_price') deliberately stays
    # with a real, different model: that is exactly the case the CHECK on `tm_terms` guards against.
    other = make_scenario(db_session, scenario.project, name="Variant")
    with pytest.raises(IntegrityError) as unknown_model:
        with db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "INSERT INTO commercial_terms (id, scenario_id, model_type)"
                    " VALUES (gen_random_uuid(), :scenario_id, 'not_a_model')"
                ),
                {"scenario_id": other.id},
            )
    assert _sqlstate_and_constraint(unknown_model.value) == (
        "23514",
        "ck_commercial_terms_model_type_known",
    )


def test_k_04_the_type_agreement_key_is_composite_in_the_catalog_of_the_database(
    db_session: Session,
) -> None:
    """K-04, structurally — the foreign key really spans both columns, on both sides.

    The behavioural test above needs a staged schema change; this one asks `pg_constraint` about the
    schema as migrated, so "the FK was quietly narrowed to one column" fails here without staging.
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
        {"name": TYPE_AGREEMENT_FOREIGN_KEY},
    ).one()

    assert tuple(row[:2]) == ("tm_terms", "commercial_terms")
    assert list(row[2]) == ["commercial_terms_id", "model_type"]
    assert list(row[3]) == ["id", "model_type"]


def test_one_rule_per_scenario_is_refused_by_the_unique_constraint(db_session: Session) -> None:
    """ADR-0003, point 1 — a second rule for one scenario is refused inside the `INSERT`."""
    scenario = _scenario(db_session)
    make_commercial_terms(db_session, scenario)

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            make_commercial_terms(db_session, scenario)

    assert _sqlstate_and_constraint(refused.value) == ("23505", "uq_commercial_terms_scenario_id")


# --- drift guards: one rule, spelled in two places -----------------------------------------------


_MODEL_TYPE_KNOWN_EXPRESSION_AT_E7B41C9D2A58 = "model_type IN ('time_and_material')"
"""What `model_type_known` read the day `e7b41c9d2a58` created it — a historical fact about *that*
migration, not a live mirror of the model's current CHECK. SC-4-04's `d2f6a91c4b58` widens the CHECK
in the database and in `app.models.commercial_terms.MODEL_TYPE_KNOWN_EXPRESSION` alike (ADR-0003,
point 2: "every later model widens this CHECK in the migration that creates its own details table")
— comparing the *first* migration's copy against the model's *current* constant would fail on
purpose from the day a second model lands, which says nothing about either being wrong. The live
drift guard going forward is the copy in the newest migration recreating the CHECK
(`LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH` — `b9e3c7a1f264` since SC-4-03 was linearised on top of
SC-4-04, `b8f2d6a41c93` since SC-4-02 was re-parented on top of `main` on 2026-09-28), asserted
against the model below."""


def test_the_model_and_the_migration_agree_on_every_sql_expression() -> None:
    """The migration's copies of the CHECKs and of the generated window are the model's.

    `valid_period` on the snapshot is the reason this matters most: the snapshot reader asks the
    frozen rows the live read's question only if both columns are generated by the same expression
    (ADR-0004, addendum 2026-09-23 SC-4-01, point 2e). `model_type_known` is compared against the
    fixed historical expression `e7b41c9d2a58` actually produced (see above), not against the
    model's live constant — SC-4-04 widened the latter without editing this already-applied
    migration (ADR-0001: a migration keeps describing the schema it produced).
    """
    migration = _migration()
    latest_check = _migration(
        LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH, "latest_model_type_check_migration"
    )

    assert latest_check._MODEL_TYPE_KNOWN_EXPRESSION == MODEL_TYPE_KNOWN_EXPRESSION
    assert migration._MODEL_TYPE_KNOWN_EXPRESSION == _MODEL_TYPE_KNOWN_EXPRESSION_AT_E7B41C9D2A58
    assert migration._TM_MODEL_TYPE_EXPRESSION == TM_MODEL_TYPE_EXPRESSION
    assert migration._VALID_PERIOD_EXPRESSION == VALID_PERIOD_EXPRESSION


def test_the_snapshot_window_is_generated_exactly_like_the_catalogue_window(
    db_session: Session,
) -> None:
    """The two generated columns, as PostgreSQL itself renders them, are the same expression."""
    rendered = dict(
        db_session.execute(
            sa.text(
                "SELECT c.relname, pg_get_expr(d.adbin, d.adrelid)"
                " FROM pg_attrdef d"
                " JOIN pg_class c ON c.oid = d.adrelid"
                " JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum"
                " WHERE a.attname = 'valid_period' AND c.relname = ANY(:tables)"
            ),
            {
                "tables": [
                    "catalog_default_rates",
                    "approved_snapshot_catalog_default_rate",
                ]
            },
        ).all()
    )

    assert len(rendered) == 2
    assert rendered["approved_snapshot_catalog_default_rate"] == rendered["catalog_default_rates"]


def test_every_model_the_discriminator_admits_has_a_details_table_and_a_revenue_branch() -> None:
    """ADR-0003, points 2 and 9: a discriminator value, a details table and a dispatcher branch
    arrive together. A model the CHECK admits and the dispatcher does not know is a rule nothing can
    price; a branch without a CHECK value is code no row can reach."""
    assert set(REVENUE_BY_MODEL) == set(MODEL_TYPES) == set(DETAIL_TABLE_BY_MODEL)


# --- the migration runs both ways ----------------------------------------------------------------


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_the_commercial_terms_migration_downgrades_and_upgrades_again(
    engine: Engine, alembic_config: Config
) -> None:
    """`downgrade()` of `e7b41c9d2a58` really runs, and the three tables come back.

    Not an acceptance criterion — the repository's practice since SC-2-03. The revision the database
    starts at is **read**, not hard-coded (the lesson recorded in
    `tests/test_absence_budget_schema_constraints.py`). `command.upgrade(..., "head")` runs in
    `finally`: `engine` is session-scoped and every other test reads the schema this one leaves.
    """

    def revision() -> str:
        with engine.connect() as connection:
            return connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()

    def tables() -> set[str]:
        with engine.connect() as connection:
            return set(
                connection.execute(
                    sa.text(
                        "SELECT table_name FROM information_schema.tables WHERE table_name ="
                        " ANY(ARRAY['commercial_terms', 'tm_terms',"
                        " 'approved_snapshot_catalog_default_rate'])"
                    )
                ).scalars()
            )

    before = revision()
    try:
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        assert revision() == PREVIOUS_REVISION
        assert tables() == set()
    finally:
        command.upgrade(alembic_config, "head")

    assert revision() == before
    assert tables() == {"commercial_terms", "tm_terms", "approved_snapshot_catalog_default_rate"}
