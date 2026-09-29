"""SC-5-05, K-03 — the shape of an additional-cost row is enforced by the database (ADR-0014).

Every write here is raw SQL against the migrated database, never a request schema: K-03's claim is
that the **database** refuses the row, so a path through Pydantic would prove only that Pydantic
refused first. Every refusal is asserted **by SQLSTATE and constraint name**, so it cannot be some
other constraint that happened to fail — and every refusal has an accepted near miss beside it, so
it cannot be a table that refuses everything.

Real PostgreSQL, real migration (ADR-0001).
"""

import importlib.util
import os
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AdditionalCost, StaffingPosition
from app.models import additional_cost as cost_model
from app.models.staffing import POSITION_ID_SCENARIO_UNIQUE
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    make_additional_cost,
    make_cost_category,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
)

MIGRATION_REVISION = "a3d9e6f20c71"
PREVIOUS_REVISION = "e7b41c9d2a58"
MIGRATION_PATH = (
    Path(BACKEND_ROOT)
    / "migrations"
    / "versions"
    / "a3d9e6f20c71_create_additional_costs_and_cost_categories.py"
)
MAR = date(2026, 3, 1)
JUN = date(2026, 6, 1)


def _migration() -> ModuleType:
    """Import the migration by path — `migrations/versions` is not a package."""
    spec = importlib.util.spec_from_file_location("sc_5_05_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(session: Session) -> dict[str, Any]:
    """A scenario with one position, a second scenario of the same project with its own position,
    and a category — everything a row needs, and one position of the *wrong* scenario."""
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline")
    other = make_scenario(session, project, name="Variant")
    dimensions = make_dimension_tuple(session)
    return {
        "scenario": scenario,
        "position": make_staffing_position(session, scenario, dimensions, start_date=MAR),
        "other_position": make_staffing_position(session, other, dimensions, start_date=MAR),
        "category": make_cost_category(session),
    }


def _row(fixture: dict[str, Any], **overrides: object) -> dict[str, object]:
    """A valid one-off scenario-level row, as raw column values."""
    return {
        "id": uuid.uuid4(),
        "scenario_id": fixture["scenario"].id,
        "position_id": None,
        "category_id": fixture["category"].id,
        "amount": "100.0000",
        "currency": "EUR",
        "cost_type": "one_off",
        "start_month": MAR,
        "end_month": None,
        "funding_source": "internal",
    } | overrides


def _insert(session: Session, row: dict[str, object]) -> None:
    with session.begin_nested():
        session.execute(sa.insert(AdditionalCost.__table__).values(**row))


def _refusal(session: Session, row: dict[str, object]) -> tuple[str | None, str | None]:
    """Insert, expect a refusal, and return (SQLSTATE, constraint name) from the driver."""
    with pytest.raises(IntegrityError) as refused:
        _insert(session, row)
    diagnostics = refused.value.orig.diag  # type: ignore[union-attr]
    return diagnostics.sqlstate, diagnostics.constraint_name


# --- K-03: the period's shape agrees with the type -----------------------------------------------


def test_k_03_a_recurring_cost_without_an_end_is_refused_by_the_database(
    db_session: Session,
) -> None:
    """K-03 (D-2; ADR-0008, addendum SC-5-05, point 2) — an open-ended recurring cost has no finite
    sum. Contrast: the same row closed (March–June) is accepted."""
    fixture = _fixture(db_session)
    _insert(db_session, _row(fixture, cost_type="recurring", end_month=JUN))

    assert _refusal(db_session, _row(fixture, cost_type="recurring", end_month=None)) == (
        "23514", "ck_additional_cost_recurring_period_closed"
    )


def test_k_03_a_one_off_cost_spanning_more_than_one_month_is_refused_by_the_database(
    db_session: Session,
) -> None:
    """K-03 — a one-off cost is exactly one month; a one-off row carrying an end month (March–June)
    is refused. Contrast: the one-off row with no end month is accepted."""
    fixture = _fixture(db_session)
    _insert(db_session, _row(fixture))

    assert _refusal(db_session, _row(fixture, cost_type="one_off", end_month=JUN)) == (
        "23514", "ck_additional_cost_one_off_single_month"
    )


def test_k_03_an_end_before_the_start_is_refused_by_the_database(db_session: Session) -> None:
    """K-03 — June to March is not a period. Contrast: a range of one month (start = end) is."""
    fixture = _fixture(db_session)
    _insert(db_session, _row(fixture, cost_type="recurring", start_month=MAR, end_month=MAR))

    assert _refusal(
        db_session, _row(fixture, cost_type="recurring", start_month=JUN, end_month=MAR)
    ) == ("23514", "ck_additional_cost_period_ordered")


def test_k_03_months_that_are_not_the_first_of_a_month_are_refused(db_session: Session) -> None:
    """ADR-0014, point 3 (month granularity) — `2026-03-15` is a day, not a month. Both ends."""
    fixture = _fixture(db_session)

    assert _refusal(db_session, _row(fixture, start_month=date(2026, 3, 15))) == (
        "23514", "ck_additional_cost_start_month_is_first_of_month"
    )
    assert _refusal(
        db_session, _row(fixture, cost_type="recurring", end_month=date(2026, 6, 30))
    ) == ("23514", "ck_additional_cost_end_month_is_first_of_month")


# --- K-03 (D-7): a position of another scenario --------------------------------------------------


def test_k_03_a_position_of_another_scenario_is_refused_by_the_composite_foreign_key(
    db_session: Session,
) -> None:
    """K-03 (D-7; ADR-0014, point 1) — the cost's `scenario_id` is Baseline, its `position_id` is a
    position of Variant: refused by `fk_additional_cost_position_same_scenario`.

    Contrast first, in the same transaction: a cost on Baseline's own position is accepted. Mutation
    the criterion names: the foreign key on `position_id` alone (agreement left to the application)
    — the mismatched row is accepted and `pytest.raises` fails.
    """
    fixture = _fixture(db_session)
    _insert(db_session, _row(fixture, position_id=fixture["position"].id))

    assert _refusal(db_session, _row(fixture, position_id=fixture["other_position"].id)) == (
        "23503", cost_model.POSITION_SAME_SCENARIO_FOREIGN_KEY
    )


def test_k_03_the_position_key_is_composite_in_the_catalog_of_the_database(
    db_session: Session,
) -> None:
    """K-03, structurally (the `pg_constraint` assertion of SC-4-01 K-04) — the foreign key really
    spans `(position_id, scenario_id)` → `staffing_position (id, scenario_id)`, so "narrowed to one
    column" fails here without staging anything. The parent side's unique constraint is asserted
    too: PostgreSQL requires it, and it is the one change this task makes to an existing table."""
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
        {"name": cost_model.POSITION_SAME_SCENARIO_FOREIGN_KEY},
    ).one()

    assert tuple(row[:2]) == ("additional_cost", "staffing_position")
    assert list(row[2]) == ["position_id", "scenario_id"]
    assert list(row[3]) == ["id", "scenario_id"]
    assert db_session.execute(
        sa.text("SELECT contype FROM pg_constraint WHERE conname = :name"),
        {"name": POSITION_ID_SCENARIO_UNIQUE},
    ).scalar_one() == "u"


# --- K-03: funding, amount, type ----------------------------------------------------------------


@pytest.mark.parametrize("funding_source", ["vendor", "client", "INTERNAL", ""])
def test_k_03_a_funding_source_outside_the_two_values_is_refused(
    db_session: Session, funding_source: str
) -> None:
    """K-03 (ADR-0014, point 9) — `internal` and `rebilled_to_client` only. `vendor` is the case
    worth naming: the word means a subcontractor in this repository. Contrast: both legal values."""
    fixture = _fixture(db_session)
    _insert(db_session, _row(fixture, funding_source="internal"))
    _insert(db_session, _row(fixture, funding_source="rebilled_to_client"))

    assert _refusal(db_session, _row(fixture, funding_source=funding_source)) == (
        "23514", "ck_additional_cost_funding_source_known"
    )


@pytest.mark.parametrize("amount", ["0", "0.0000", "-1.0000", "-0.0001"])
def test_k_03_an_amount_that_is_not_strictly_positive_is_refused(
    db_session: Session, amount: str
) -> None:
    """K-03 (D-10, G-1 = A) — `CHECK amount > 0`. Contrast: the smallest positive amount the column
    holds, `0.0001`, is accepted — so the refusal is about the sign, not about small amounts.
    Mutation: `>= 0` accepts `0`."""
    fixture = _fixture(db_session)
    _insert(db_session, _row(fixture, amount="0.0001"))

    assert _refusal(db_session, _row(fixture, amount=amount)) == (
        "23514", "ck_additional_cost_amount_positive"
    )


def test_k_03_an_unknown_cost_type_and_a_malformed_currency_are_refused(
    db_session: Session,
) -> None:
    """ADR-0014, point 5 (fixed amount, one-off or recurring) and the currency rules
    `catalog_default_rates` carries."""
    fixture = _fixture(db_session)

    assert _refusal(db_session, _row(fixture, cost_type="per_headcount")) == (
        "23514", "ck_additional_cost_cost_type_known"
    )
    assert _refusal(db_session, _row(fixture, currency="eur")) == (
        "23514", "ck_additional_cost_currency_is_upper"
    )
    assert _refusal(db_session, _row(fixture, currency="EU")) == (
        "23514", "ck_additional_cost_currency_iso4217"
    )


# --- K-03: a category in use cannot be deleted ---------------------------------------------------


def test_k_03_deleting_a_category_any_cost_points_at_is_refused_by_the_database(
    db_session: Session,
) -> None:
    """K-03 (ADR-0014, point 2) — directly against the database: the API has no path that deletes a
    dictionary entry. Contrast: an unreferenced category is deletable, so the refusal is about the
    reference. Mutation: `ON DELETE CASCADE` (the cost would vanish) or `SET NULL` (refused by
    `NOT NULL` with another name) — either fails the constraint-name assertion."""
    fixture = _fixture(db_session)
    make_additional_cost(
        db_session, fixture["scenario"], fixture["category"], amount=Decimal("10.0000"),
        start_month=MAR,
    )
    unused = make_cost_category(db_session, name="Unused")

    with db_session.begin_nested():
        db_session.execute(
            sa.text("DELETE FROM catalog_cost_categories WHERE id = :id"), {"id": unused.id}
        )
    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            db_session.execute(
                sa.text("DELETE FROM catalog_cost_categories WHERE id = :id"),
                {"id": fixture["category"].id},
            )
    diagnostics = refused.value.orig.diag  # type: ignore[union-attr]
    assert (diagnostics.sqlstate, diagnostics.constraint_name) == (
        "23503", cost_model.CATEGORY_FOREIGN_KEY
    )


def test_no_foreign_key_of_the_cost_row_cascades_or_nulls_a_delete(db_session: Session) -> None:
    """ADR-0014, point 1 (ADR-0003, point 1) — every foreign key of `additional_cost` is `NO
    ACTION`: a cascade towards the scenario, the position or the category would be a second,
    unguarded way for the rows of an `approved` scenario to disappear. Asserted on `confdeltype`
    because no test deletes a scenario or a position (there is no path)."""
    rules = dict(
        db_session.execute(
            sa.text(
                "SELECT conname, confdeltype FROM pg_constraint"
                " WHERE contype = 'f' AND conrelid = 'additional_cost'::regclass"
            )
        ).all()
    )

    assert set(rules) == {
        "fk_additional_cost_scenario_id",
        cost_model.POSITION_SAME_SCENARIO_FOREIGN_KEY,
        cost_model.CATEGORY_FOREIGN_KEY,
        # SC-6-08 (ADR-0021, point 7): the declared-risk link. Re-armed, not loosened - the
        # `all(rule == "a")` assertion below still covers it, so the new key is NO ACTION too.
        cost_model.RISK_SAME_SCENARIO_FOREIGN_KEY,
    }
    assert all(rule == "a" for rule in rules.values()), rules


def test_two_overlapping_costs_of_one_category_are_both_legal_there_is_no_exclude(
    db_session: Session,
) -> None:
    """ADR-0008, addendum SC-5-05, point 1 — not a consumer of the effective-range pattern: two
    licences of one category in the same months are both true. The absence of an `EXCLUDE` is the
    decision, asserted both ways: the rows are accepted, and `pg_constraint` holds none."""
    fixture = _fixture(db_session)
    _insert(db_session, _row(fixture, cost_type="recurring", end_month=JUN))
    _insert(db_session, _row(fixture, cost_type="recurring", end_month=JUN))

    assert db_session.execute(
        sa.text(
            "SELECT count(*) FROM pg_constraint"
            " WHERE contype = 'x' AND conrelid = 'additional_cost'::regclass"
        )
    ).scalar_one() == 0


def test_the_migration_seeds_no_cost_category(db_session: Session) -> None:
    """ADR-0014, point 2 (the precedent of ADR-0012, point 3) — the eight categories F-08 lists are
    an organisation's data, not the migration's."""
    assert db_session.execute(
        sa.text("SELECT count(*) FROM catalog_cost_categories")
    ).scalar_one() == 0


# --- drift guards: one rule, spelled in two places -----------------------------------------------


def test_the_model_and_the_migration_agree_on_every_sql_expression() -> None:
    """The migration's copies of the CHECK expressions and of the constraint names are the model's.

    A migration must keep describing the schema it produced, so it cannot import constants the
    model is free to change; `alembic upgrade` never reads the model, so editing one copy alone
    would break nothing else (R-02, SC-2-01)."""
    migration = _migration()

    assert migration._COST_TYPE_KNOWN_EXPRESSION == cost_model.COST_TYPE_KNOWN_EXPRESSION
    assert migration._FUNDING_SOURCE_KNOWN_EXPRESSION == cost_model.FUNDING_SOURCE_KNOWN_EXPRESSION
    assert (
        migration._RECURRING_PERIOD_CLOSED_EXPRESSION
        == cost_model.RECURRING_PERIOD_CLOSED_EXPRESSION
    )
    assert migration._ONE_OFF_SINGLE_MONTH_EXPRESSION == cost_model.ONE_OFF_SINGLE_MONTH_EXPRESSION
    assert migration._PERIOD_ORDERED_EXPRESSION == cost_model.PERIOD_ORDERED_EXPRESSION
    assert migration._START_MONTH_IS_FIRST_EXPRESSION == cost_model.START_MONTH_IS_FIRST_EXPRESSION
    assert migration._END_MONTH_IS_FIRST_EXPRESSION == cost_model.END_MONTH_IS_FIRST_EXPRESSION
    assert (
        migration._POSITION_SAME_SCENARIO_FOREIGN_KEY
        == cost_model.POSITION_SAME_SCENARIO_FOREIGN_KEY
    )
    assert migration._POSITION_UNIQUE == POSITION_ID_SCENARIO_UNIQUE
    assert migration._COST_TABLE == AdditionalCost.__tablename__
    assert set(cost_model.COST_TYPES) == {"one_off", "recurring"}
    assert set(cost_model.FUNDING_SOURCES) == {"internal", "rebilled_to_client"}


def test_the_cost_row_carries_no_free_text_and_no_person_column(engine: Engine) -> None:
    """ADR-0014/ADR-0005, addendum SC-5-05, point 2 — the column set, by equality, read from the
    migrated database. A `description`, a `note` or a `person` column added later would make a cost
    on a `headcount = 1` position *directly* about one person, without the decision that governs
    personal data; this fails on the day it is added."""
    with engine.connect() as connection:
        columns = set(
            connection.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'additional_cost'"
                )
            ).scalars()
        )

    assert columns == {
        "id", "scenario_id", "position_id", "category_id", "amount", "currency", "cost_type",
        "start_month", "end_month", "funding_source", "created_at", "updated_at",
        # SC-6-08 (ADR-0021, point 7): the optional link to a declared risk - a uuid, no text.
        "risk_id",
    }
    assert {attribute.key for attribute in sa.inspect(AdditionalCost).column_attrs} == columns
    assert "scenario_id" in {c.key for c in sa.inspect(StaffingPosition).column_attrs}


# --- the migration runs both ways ----------------------------------------------------------------


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_the_additional_cost_migration_downgrades_and_upgrades_again(
    engine: Engine, alembic_config: Config
) -> None:
    """`downgrade()` of `a3d9e6f20c71` really runs: both tables and the unique constraint on
    `staffing_position` go, and come back. The starting revision is **read**, not hard-coded (the
    lesson recorded in `tests/test_absence_budget_schema_constraints.py`); `upgrade(head)` runs in
    `finally`, because `engine` is session-scoped and every other test reads the schema left
    here."""

    def revision() -> str:
        with engine.connect() as connection:
            return connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()

    def state() -> tuple[set[str], bool]:
        with engine.connect() as connection:
            tables = set(
                connection.execute(
                    sa.text(
                        "SELECT table_name FROM information_schema.tables WHERE table_name ="
                        " ANY(ARRAY['additional_cost', 'catalog_cost_categories'])"
                    )
                ).scalars()
            )
            unique = connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = :name)"),
                {"name": POSITION_ID_SCENARIO_UNIQUE},
            ).scalar_one()
        return tables, unique

    before = revision()
    try:
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        assert revision() == PREVIOUS_REVISION
        assert state() == (set(), False)
    finally:
        command.upgrade(alembic_config, "head")

    assert revision() == before
    assert state() == ({"additional_cost", "catalog_cost_categories"}, True)
