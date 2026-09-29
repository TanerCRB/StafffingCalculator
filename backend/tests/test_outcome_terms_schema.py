"""SC-4-03 — `outcome_terms` rules and the widened discriminator enforced by the database
(ADR-0003, addendum 2026-09-25 SC-4-03; checks O-1 and O-6).

Every write here goes through SQL/ORM, never through the request schema: the claim is that the
**database** refuses — a path through Pydantic would only prove that Pydantic refused first. Every
refusal is an assertion on the constraint's name, so it cannot come from a different mechanism.

- **K-03 (database part)**: a probability sum ≠ 100.00 and an incomplete set refused by
  `ck_outcome_terms_probabilities_sum_to_100`; contrast 33.34/33.33/33.33/0.00 is writable.
- **K-04 (database part)**: `min > max` refused by
  `ck_outcome_terms_revenue_bounds_ordered`.
- O-1: a negative number of units; an `outcome_terms` row pointing at a T&M rule (composite foreign
  key).
- O-6: the full `IN` list in `ck_commercial_terms_model_type_known`; a T&M rule survives this
  migration's downgrade and upgrade.
"""

import importlib.util
import os
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.commercial_terms import (
    OUTCOME_BOUNDS_ORDERED_EXPRESSION,
    OUTCOME_CURRENCY_ISO4217_EXPRESSION,
    OUTCOME_CURRENCY_UPPER_EXPRESSION,
    OUTCOME_MODEL_TYPE_EXPRESSION,
    OUTCOME_PROBABILITIES_EXPRESSION,
    OUTCOME_TYPE_AGREEMENT_FOREIGN_KEY,
    OUTCOME_UNITS_WITH_UNIT_RATE_EXPRESSION,
)
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    make_commercial_terms,
    make_outcome_terms,
    make_project,
    make_scenario,
)

MIGRATION_REVISION = "b9e3c7a1f264"
PREVIOUS_REVISION = "b7e3f19a6c52"
"""SC-4-05 (`scope_ref`) — this migration's `down_revision` after the second linearization (a merge
with `main` on 2026-09-25). `b7e3f19a6c52` does not touch the discriminator's CHECK, so the list
`downgrade` recreates stays the list from `d2f6a91c4b58` (`PREVIOUS_CHECK_MIGRATION_PATH`)."""
MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "b9e3c7a1f264_create_outcome_terms_and_widen_the_model_type_check.py"
)
PREVIOUS_CHECK_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "d2f6a91c4b58_create_story_points_terms_and_widen_the_model_type_check.py"
)


def _load(path: Path, name: str) -> ModuleType:
    """Import a migration by path — `migrations/versions` is not a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sqlstate_and_constraint(error: IntegrityError) -> tuple[str | None, str | None]:
    diagnostics = error.orig.diag  # type: ignore[union-attr]
    return diagnostics.sqlstate, diagnostics.constraint_name


def _scenario(session: Session, name: str = "Outcome schema"):
    project = make_project(session, name=name, accessible_to=(IN_SCOPE_USER,))
    return make_scenario(session, project, name="Baseline")


def _refused_by(session: Session, scenario, **details: object) -> tuple[str | None, str | None]:
    with pytest.raises(IntegrityError) as refused:
        with session.begin_nested():
            make_outcome_terms(session, scenario, **details)
    return _sqlstate_and_constraint(refused.value)


UNKNOWN_MODEL_TYPE = "unknown_model_for_test"
"""A model no migration will add — deliberately not naming a real model (`fixed_price`,
`story_points`…): SC-4-02/SC-4-04 will widen the CHECK with their own values and so cannot break
this test (SC-4-03 verification round 2, R-02)."""

MODEL_TYPES_OF_THIS_MIGRATION = ("time_and_material", "story_points", "outcome_based")
"""The `IN` list frozen in this migration — what the migration **produced**, not the current
`MODEL_TYPES`. The model's equality with the *latest* migration widening the CHECK is guarded by
`tests/test_commercial_terms_schema.py` (`LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH`)."""

PROBABILITY_COLUMNS = (
    "not_achieved_probability",
    "partial_probability",
    "achieved_probability",
    "exceeded_probability",
)


def _probabilities(*values: str | None) -> dict[str, object]:
    return {
        column: None if value is None else Decimal(value)
        for column, value in zip(PROBABILITY_COLUMNS, values, strict=True)
    }


# --- K-03: the probability sum as a database CHECK ---------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        ("33.33", "33.33", "33.33", "0"),
        ("50", "50", None, None),
        ("100", None, None, None),
        ("60", "50", "0", "0"),
    ],
    ids=["sum_99_99", "two_of_four", "one_of_four", "sum_110"],
)
def test_k_03_the_database_refuses_a_probability_set_not_summing_to_100_or_incomplete(
    db_session: Session, values: tuple[str | None, ...]
) -> None:
    """K-03 — an `INSERT` bypassing the API refused by `ck_outcome_terms_probabilities_sum_to_100`
    (O-1). Mutation: the CHECK removed or changed to "sum ≤ 100"/"sum ≈ 100" — the row goes
    through."""
    scenario = _scenario(db_session)

    assert _refused_by(db_session, scenario, **_probabilities(*values)) == (
        "23514",
        "ck_outcome_terms_probabilities_sum_to_100",
    )


def test_k_03_contrast_the_database_accepts_33_34_33_33_33_33_0_and_no_probabilities(
    db_session: Session,
) -> None:
    """K-03, contrast — exactly 100.00 and "all `NULL`" are writable, so the refusals above are
    about the sum/completeness, not about the probability columns as such."""
    make_outcome_terms(
        db_session, _scenario(db_session), **_probabilities("33.34", "33.33", "33.33", "0")
    )
    make_outcome_terms(db_session, _scenario(db_session, "Without probabilities"))


# --- K-04: min <= max as a database CHECK; O-1: the remaining constraints ------------------------


def test_k_04_the_database_refuses_min_greater_than_max(db_session: Session) -> None:
    """K-04 — `revenue_min` 40000 > `revenue_max` 30000 refused by
    `ck_outcome_terms_revenue_bounds_ordered`; contrast: min 22000 / max 30000 writable."""
    scenario = _scenario(db_session)

    assert _refused_by(
        db_session, scenario, revenue_min=Decimal("40000"), revenue_max=Decimal("30000")
    ) == ("23514", "ck_outcome_terms_revenue_bounds_ordered")
    make_outcome_terms(
        db_session, scenario, revenue_min=Decimal("22000"), revenue_max=Decimal("30000")
    )


def test_o_1_negative_units_a_lowercase_currency_and_a_negative_fee_are_refused(
    db_session: Session,
) -> None:
    """O-1 — a negative number of units, a lowercase currency, and a negative fee: each refusal
    from its own constraint."""
    scenario = _scenario(db_session)

    assert _refused_by(db_session, scenario, partial_units=Decimal("-1")) == (
        "23514",
        "ck_outcome_terms_partial_units_not_negative",
    )
    assert _refused_by(db_session, scenario, currency="pln") == (
        "23514",
        "ck_outcome_terms_currency_is_upper",
    )
    assert _refused_by(db_session, scenario, fixed_fee=Decimal("-0.01")) == (
        "23514",
        "ck_outcome_terms_fixed_fee_not_negative",
    )


def test_o_1_an_outcome_details_row_of_a_tm_rule_is_refused_by_the_composite_foreign_key(
    db_session: Session,
) -> None:
    """O-1 — an `outcome_terms` row pointing at a `time_and_material` rule refused by
    `fk_outcome_terms_commercial_terms_model_type` (type agreement in the database, ADR-0003
    point 3 unchanged).
    Contrast: an `outcome_terms` row of an `outcome_based` rule is writable (fixture above)."""
    scenario = _scenario(db_session)
    tm_rule = make_commercial_terms(db_session, scenario, with_details=False)

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "INSERT INTO outcome_terms (commercial_terms_id, model_type, currency,"
                    " fixed_fee, not_achieved_units, partial_units, achieved_units,"
                    " exceeded_units) VALUES (:id, 'outcome_based', 'PLN', 1, 0, 0, 0, 0)"
                ),
                {"id": tm_rule.id},
            )

    assert _sqlstate_and_constraint(refused.value) == ("23503", OUTCOME_TYPE_AGREEMENT_FOREIGN_KEY)


# --- O-6: the full IN list and the migration both ways --------------------------------------------


def test_o_6_the_discriminator_admits_both_models_and_still_refuses_an_unknown_one(
    db_session: Session,
) -> None:
    """O-6 — `ck_commercial_terms_model_type_known` admits `time_and_material` **and**
    `outcome_based` (the full `IN` list, ADR-0003 addendum SC-4-03 point 10c) and still refuses a
    model with no details table. Mutation "the migration carries only its own value": the T&M rule
    refused."""
    make_commercial_terms(db_session, _scenario(db_session, "T&M"))
    make_outcome_terms(db_session, _scenario(db_session, "Outcome"))

    other = _scenario(db_session, "Unknown")
    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "INSERT INTO commercial_terms (id, scenario_id, model_type)"
                    " VALUES (gen_random_uuid(), :id, :model_type)"
                ),
                {"id": other.id, "model_type": UNKNOWN_MODEL_TYPE},
            )
    assert _sqlstate_and_constraint(refused.value) == (
        "23514",
        "ck_commercial_terms_model_type_known",
    )


def test_the_model_and_the_outcome_migration_agree_on_every_sql_expression() -> None:
    """A drift guard: the `outcome_terms` expression copies in migration `b9e3c7a1f264` are the
    model's expressions, and `downgrade` recreates exactly the expression from `d2f6a91c4b58` — the
    last migration to recreate this CHECK before `b9e3c7a1f264` (the pre-migration list, not
    empty). The discriminator's `IN` list is compared against **this migration's frozen list**, not
    the current `MODEL_TYPES` — the next model will widen the model and its own migration, not this
    one (R-02)."""
    migration = _load(MIGRATION_PATH, "sc_4_03_migration")
    previous = _load(PREVIOUS_CHECK_MIGRATION_PATH, "sc_4_04_migration_for_sc_4_03")

    assert migration._MODEL_TYPE_KNOWN_EXPRESSION == (
        "model_type IN ("
        + ", ".join(f"'{model_type}'" for model_type in MODEL_TYPES_OF_THIS_MIGRATION)
        + ")"
    )
    assert migration._PREVIOUS_MODEL_TYPE_KNOWN_EXPRESSION == (
        previous._MODEL_TYPE_KNOWN_EXPRESSION
    )
    assert migration._OUTCOME_MODEL_TYPE_EXPRESSION == OUTCOME_MODEL_TYPE_EXPRESSION
    assert migration._PROBABILITIES_EXPRESSION == OUTCOME_PROBABILITIES_EXPRESSION
    assert migration._BOUNDS_ORDERED_EXPRESSION == OUTCOME_BOUNDS_ORDERED_EXPRESSION
    assert migration._CURRENCY_ISO4217_EXPRESSION == OUTCOME_CURRENCY_ISO4217_EXPRESSION
    assert migration._CURRENCY_UPPER_EXPRESSION == OUTCOME_CURRENCY_UPPER_EXPRESSION
    assert migration._UNITS_WITH_UNIT_RATE_EXPRESSION == OUTCOME_UNITS_WITH_UNIT_RATE_EXPRESSION


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_o_6_a_tm_rule_survives_the_downgrade_and_the_upgrade_of_the_outcome_migration(
    engine: Engine, alembic_config: Config
) -> None:
    """O-6 — a T&M rule committed to the database survives `downgrade` to `b7e3f19a6c52` (the CHECK
    recreated from `time_and_material`, `story_points`) and a subsequent `upgrade` (the CHECK with
    the full list) with no change; `outcome_terms` disappears and comes back. Mutation "downgrade
    recreates the list with `outcome_based` alone": recreating the CHECK refused by the database on
    the existing T&M rule.

    The starting revision is read, not hard-coded; `upgrade head` in `finally`, because `engine` is
    shared across the whole test session.
    """

    def one(sql: str) -> object:
        with engine.connect() as connection:
            return connection.execute(sa.text(sql)).scalar_one()

    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        rule = make_commercial_terms(setup, _scenario(setup, "Downgrade"))
        rule_id, scenario_id = rule.id, rule.scenario_id
        project_id = rule.scenario.project_id
        setup.commit()

    before = one("SELECT version_num FROM alembic_version")
    try:
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        assert one("SELECT version_num FROM alembic_version") == PREVIOUS_REVISION
        assert one("SELECT to_regclass('outcome_terms') IS NULL") is True
        assert one(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'ck_commercial_terms_model_type_known'"
        ) == (
            "CHECK (((model_type)::text = ANY ((ARRAY['time_and_material'::character varying,"
            " 'story_points'::character varying])::text[])))"
        )
        command.upgrade(alembic_config, "head")
        assert one("SELECT version_num FROM alembic_version") == before
        assert one("SELECT to_regclass('outcome_terms') IS NOT NULL") is True
        assert "outcome_based" in str(
            one(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_commercial_terms_model_type_known'"
            )
        )
        assert one(
            f"SELECT model_type FROM commercial_terms WHERE id = '{rule_id}'"
        ) == "time_and_material"
        assert one(f"SELECT count(*) FROM tm_terms WHERE commercial_terms_id = '{rule_id}'") == 1
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM tm_terms WHERE commercial_terms_id = :id"), {"id": rule_id}
            )
            connection.execute(sa.text("DELETE FROM commercial_terms WHERE id = :id"), {"id":
            rule_id})
            connection.execute(
                sa.text("DELETE FROM project_access WHERE project_id = :id"), {"id": project_id}
            )
            connection.execute(sa.text("DELETE FROM scenarios WHERE id = :id"), {"id": scenario_id})
            connection.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": project_id})
