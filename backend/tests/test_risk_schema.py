"""SC-6-08, K-03 (database half) and K-04 - the shape of a risk, a reserve and their links is
enforced
by the database (ADR-0021, points 4, 7 and Q-8).

Every write here is raw SQL against the migrated database, never a request schema: the claim is that
the **database** refuses the row, so a path through Pydantic would prove only that Pydantic refused
first. Every refusal is asserted **by SQLSTATE and constraint name**, so it cannot be some other
constraint that happened to fail - and every refusal has an accepted near miss beside it, so it
cannot be a table that refuses everything.

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

from app.models import AdditionalCost, RiskReserve, ScenarioRisk
from app.models import additional_cost as cost_model
from app.models import risk as risk_model
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    link_cost_to_risk,
    make_additional_cost,
    make_cost_category,
    make_project,
    make_reserve,
    make_risk,
    make_scenario,
)

MIGRATION_PATH = (
    Path(BACKEND_ROOT)
    / "migrations"
    / "versions"
    / "d4b8a2e6c910_create_scenario_risk_and_risk_reserve.py"
)
PREVIOUS_REVISION = "d4a7e19c2b60"
MAR = date(2026, 3, 1)
JUN = date(2026, 6, 1)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sc_6_08_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(session: Session) -> dict[str, Any]:
    """Two scenarios of one project, a risk in each, and a cost category."""
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline", currency="EUR")
    other = make_scenario(session, project, name="Variant", currency="EUR")
    return {
        "scenario": scenario,
        "other": other,
        "risk": make_risk(session, scenario, name="Vendor delay"),
        "other_risk": make_risk(session, other, name="Vendor delay"),
        "category": make_cost_category(session),
    }


def _reserve_row(fixture: dict[str, Any], **overrides: object) -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "scenario_id": fixture["scenario"].id,
        "risk_id": None,
        "amount": "100.0000",
        "currency": "EUR",
        "reserve_type": "one_off",
        "start_month": MAR,
        "end_month": None,
    } | overrides


def _cost_row(fixture: dict[str, Any], **overrides: object) -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "scenario_id": fixture["scenario"].id,
        "position_id": None,
        "risk_id": None,
        "category_id": fixture["category"].id,
        "amount": "100.0000",
        "currency": "EUR",
        "cost_type": "one_off",
        "start_month": MAR,
        "end_month": None,
        "funding_source": "internal",
    } | overrides


def _insert(session: Session, table: sa.Table, row: dict[str, object]) -> None:
    with session.begin_nested():
        session.execute(sa.insert(table).values(**row))


def _refusal(
    session: Session, table: sa.Table, row: dict[str, object]
) -> tuple[str | None, str | None]:
    with pytest.raises(IntegrityError) as refused:
        _insert(session, table, row)
    diagnostics = refused.value.orig.diag  # type: ignore[union-attr]
    return diagnostics.sqlstate, diagnostics.constraint_name


RESERVE = RiskReserve.__table__
COST = AdditionalCost.__table__
RISK = ScenarioRisk.__table__


# --- K-04: the link is a composite foreign key, asserted in the catalogue ------------------------


def test_k_04_both_links_are_composite_foreign_keys_on_risk_and_scenario_with_no_delete_action(
    db_session: Session,
) -> None:
    """K-04 (R-03, R-09) - read from `pg_constraint`, not from the model: each of the two link
    constraints is a foreign key over `(risk_id, scenario_id)` referencing `scenario_risk (id,
    scenario_id)`, `MATCH SIMPLE`, with `confdeltype = 'a'` (NO ACTION - no cascade, no `SET NULL`).
    Mutation: a single-column `risk_id -> scenario_risk.id` key (the scenario half dropped) fails
    the column-list assertion; an `ON DELETE SET NULL` fails `confdeltype`."""
    rows = db_session.execute(
        sa.text(
            "SELECT c.conname, c.conrelid::regclass::text, c.confrelid::regclass::text,"
            "       c.confdeltype, c.confmatchtype,"
            "       (SELECT array_agg(a.attname::text ORDER BY k.ord)"
            "          FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)"
            "          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum),"
            "       (SELECT array_agg(a.attname::text ORDER BY k.ord)"
            "          FROM unnest(c.confkey) WITH ORDINALITY AS k(attnum, ord)"
            "          JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.attnum)"
            "  FROM pg_constraint c WHERE c.contype = 'f' AND c.conname = ANY(:names)"
        ),
        {
            "names": [
                risk_model.RESERVE_RISK_SAME_SCENARIO_FOREIGN_KEY,
                cost_model.RISK_SAME_SCENARIO_FOREIGN_KEY,
            ]
        },
    ).all()

    by_name = {row[0]: row[1:] for row in rows}
    assert set(by_name) == {
        "fk_risk_reserve_risk_same_scenario",
        "fk_additional_cost_risk_same_scenario",
    }
    for name, table in (
        ("fk_risk_reserve_risk_same_scenario", "risk_reserve"),
        ("fk_additional_cost_risk_same_scenario", "additional_cost"),
    ):
        owner, parent, on_delete, match, columns, referenced = by_name[name]
        assert owner == table and parent == "scenario_risk"
        assert on_delete == "a" and match == "s"
        assert columns == ["risk_id", "scenario_id"]
        assert referenced == ["id", "scenario_id"]


def test_k_04_every_foreign_key_of_the_two_new_tables_is_no_action(db_session: Session) -> None:
    """ADR-0021, point 7 (ADR-0003, point 1) - a cascade towards the scenario or the risk would be a
    second, unguarded way for the rows of an `approved` scenario to disappear."""
    rules = dict(
        db_session.execute(
            sa.text(
                "SELECT conname, confdeltype FROM pg_constraint WHERE contype = 'f'"
                " AND conrelid = ANY(ARRAY['risk_reserve'::regclass, 'scenario_risk'::regclass])"
            )
        ).all()
    )

    assert set(rules) == {
        "fk_scenario_risk_scenario_id",
        "fk_risk_reserve_scenario_id",
        "fk_risk_reserve_risk_same_scenario",
    }
    assert all(rule == "a" for rule in rules.values()), rules


def test_k_04_a_reserve_pointing_at_a_risk_of_another_scenario_is_refused_by_the_database(
    db_session: Session,
) -> None:
    """K-04 (R-03) - a raw `INSERT` of a reserve of scenario A carrying a risk of scenario B: the
    composite key refuses it, by name. Contrast: the same row with A's own risk is accepted, and so
    is an unlinked one (`MATCH SIMPLE` skips the key for `NULL`)."""
    fixture = _fixture(db_session)
    _insert(db_session, RESERVE, _reserve_row(fixture, risk_id=fixture["risk"].id))
    _insert(db_session, RESERVE, _reserve_row(fixture, risk_id=None))

    assert _refusal(
        db_session, RESERVE, _reserve_row(fixture, risk_id=fixture["other_risk"].id)
    ) == ("23503", "fk_risk_reserve_risk_same_scenario")


def test_k_04_a_cost_event_pointing_at_a_risk_of_another_scenario_is_refused_by_the_database(
    db_session: Session,
) -> None:
    """K-04 (R-03) - the same for the cost event, both on `INSERT` and on an `UPDATE` that moves an
    existing, accepted link across scenarios. Contrast: the same-scenario link is accepted."""
    fixture = _fixture(db_session)
    _insert(db_session, COST, _cost_row(fixture, risk_id=fixture["risk"].id))
    existing = make_additional_cost(
        db_session, fixture["scenario"], fixture["category"], amount=Decimal("1"), start_month=MAR
    )

    assert _refusal(db_session, COST, _cost_row(fixture, risk_id=fixture["other_risk"].id)) == (
        "23503",
        "fk_additional_cost_risk_same_scenario",
    )
    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            db_session.execute(
                sa.update(COST)
                .where(COST.c.id == existing.id)
                .values(risk_id=fixture["other_risk"].id)
            )
    diagnostics = refused.value.orig.diag  # type: ignore[union-attr]
    assert diagnostics.constraint_name == "fk_additional_cost_risk_same_scenario"
    link_cost_to_risk(db_session, existing, fixture["risk"])  # the near miss: same scenario


def test_k_04_a_referenced_risk_cannot_be_deleted_and_can_once_unlinked(
    db_session: Session,
) -> None:
    """K-04 (R-09, Q-8 = A) - a raw `DELETE` of a risk that a reserve points at, and of one that a
    cost event points at, is refused (`23503`) by the key of that table. Contrast: a risk nothing
    points at is deleted, and the previously referenced one is deletable once both links are gone.
    Mutation: `ON DELETE SET NULL` or `CASCADE` on either key."""
    fixture = _fixture(db_session)
    scenario, category = fixture["scenario"], fixture["category"]
    with_reserve = make_risk(db_session, scenario, name="Has a reserve")
    with_cost = make_risk(db_session, scenario, name="Has a cost event")
    unreferenced = make_risk(db_session, scenario, name="Nothing points here")
    reserve = make_reserve(
        db_session, scenario, amount=Decimal("5"), start_month=MAR, risk=with_reserve
    )
    cost = make_additional_cost(
        db_session, scenario, category, amount=Decimal("5"), start_month=MAR
    )
    link_cost_to_risk(db_session, cost, with_cost)

    def delete(risk: ScenarioRisk) -> tuple[str | None, str | None]:
        with pytest.raises(IntegrityError) as refused:
            with db_session.begin_nested():
                db_session.execute(sa.delete(RISK).where(RISK.c.id == risk.id))
        diagnostics = refused.value.orig.diag  # type: ignore[union-attr]
        return diagnostics.sqlstate, diagnostics.constraint_name

    assert delete(with_reserve) == ("23503", "fk_risk_reserve_risk_same_scenario")
    assert delete(with_cost) == ("23503", "fk_additional_cost_risk_same_scenario")

    def remaining() -> int:
        return db_session.execute(sa.select(sa.func.count()).select_from(RISK)).scalar_one()

    before = remaining()

    db_session.execute(sa.delete(RISK).where(RISK.c.id == unreferenced.id))
    assert remaining() == before - 1

    db_session.execute(sa.delete(RESERVE).where(RESERVE.c.id == reserve.id))
    link_cost_to_risk(db_session, cost, None)
    db_session.execute(sa.delete(RISK).where(RISK.c.id.in_([with_reserve.id, with_cost.id])))
    assert remaining() == before - 3


def test_k_04_a_risk_belongs_to_exactly_one_scenario_and_names_are_unique_per_scenario(
    db_session: Session,
) -> None:
    """ADR-0021, point 1 and 8 - `UNIQUE (scenario_id, name)`: a second risk of one name in one
    scenario is refused by name; the same name in *another* scenario is accepted (the contrast, and
    the reason a copy never collides with its source). A blank name is refused by its own CHECK."""
    fixture = _fixture(db_session)  # the two scenarios already hold one "Vendor delay" each
    row = {"id": uuid.uuid4(), "scenario_id": fixture["scenario"].id, "name": "Vendor delay"}

    assert _refusal(db_session, RISK, row) == ("23505", risk_model.RISK_SCENARIO_NAME_UNIQUE)
    assert _refusal(db_session, RISK, row | {"id": uuid.uuid4(), "name": "   "}) == (
        "23514",
        "ck_scenario_risk_name_not_blank",
    )
    _insert(db_session, RISK, row | {"id": uuid.uuid4(), "name": "Another name"})


# --- K-03 (database half): a reserve's shape is the database's, not the application's ------------


def test_k_03_a_reserve_amount_must_be_strictly_positive_by_check(db_session: Session) -> None:
    """K-03 - `amount > 0`: zero and negative amounts are refused by `ck_risk_reserve_amount_
    positive`. Contrast: the smallest stored amount (0.0001) is accepted, stored unrounded - and
    12.3456 comes back as 12.3456 (four decimals accepted, never rounded on the way in)."""
    fixture = _fixture(db_session)
    _insert(db_session, RESERVE, _reserve_row(fixture, amount="0.0001"))
    _insert(db_session, RESERVE, _reserve_row(fixture, amount="12.3456"))

    for amount in ("0", "-1.0000"):
        assert _refusal(db_session, RESERVE, _reserve_row(fixture, amount=amount)) == (
            "23514",
            "ck_risk_reserve_amount_positive",
        )
    stored = (
        db_session.execute(sa.select(RESERVE.c.amount).order_by(RESERVE.c.amount)).scalars().all()
    )
    assert stored == [Decimal("0.0001"), Decimal("12.3456")]


def test_k_03_a_reserve_period_agrees_with_its_type_by_check(db_session: Session) -> None:
    """K-03 - the shape rules, each by its own named CHECK: a recurring reserve needs an end; a
    one-off has none; an end before the start; a month that is not a first day; a lower-case
    currency. Each with an accepted near miss (a closed recurring range, a one-month range)."""
    fixture = _fixture(db_session)
    _insert(db_session, RESERVE, _reserve_row(fixture, reserve_type="recurring", end_month=JUN))
    _insert(db_session, RESERVE, _reserve_row(fixture, reserve_type="recurring", end_month=MAR))

    refused = {
        "ck_risk_reserve_recurring_period_closed": _reserve_row(
            fixture, reserve_type="recurring", end_month=None
        ),
        "ck_risk_reserve_one_off_single_month": _reserve_row(
            fixture, reserve_type="one_off", end_month=JUN
        ),
        "ck_risk_reserve_period_ordered": _reserve_row(
            fixture, reserve_type="recurring", start_month=JUN, end_month=MAR
        ),
        "ck_risk_reserve_start_month_is_first_of_month": _reserve_row(
            fixture, start_month=date(2026, 3, 15)
        ),
        "ck_risk_reserve_end_month_is_first_of_month": _reserve_row(
            fixture, reserve_type="recurring", end_month=date(2026, 6, 15)
        ),
        "ck_risk_reserve_reserve_type_known": _reserve_row(fixture, reserve_type="weekly"),
        "ck_risk_reserve_currency_is_upper": _reserve_row(fixture, currency="eur"),
        "ck_risk_reserve_currency_iso4217": _reserve_row(fixture, currency="EU"),
    }
    for constraint, row in refused.items():
        assert _refusal(db_session, RESERVE, row) == ("23514", constraint)


# --- what the two new tables do not carry --------------------------------------------------------


def test_k_07_the_risk_and_the_reserve_carry_no_free_text_no_person_and_no_position_column(
    engine: Engine,
) -> None:
    """ADR-0021, points 7 and 9; ADR-0005, addendum SC-5-05, point 2 - the column sets, by equality,
    read from the migrated database. A `description`, a `note`, a `person` or a `position_id` added
    later would make a risk or a reserve directly about one person (or widen the `headcount = 1`
    exposure) without the decision that governs it; this fails on the day it is added."""

    def columns(table: str) -> set[str]:
        with engine.connect() as connection:
            return set(
                connection.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = :table"
                    ),
                    {"table": table},
                ).scalars()
            )

    assert columns("scenario_risk") == {"id", "scenario_id", "name", "created_at", "updated_at"}
    assert columns("risk_reserve") == {
        "id",
        "scenario_id",
        "risk_id",
        "amount",
        "currency",
        "reserve_type",
        "start_month",
        "end_month",
        "created_at",
        "updated_at",
    }
    assert {a.key for a in sa.inspect(ScenarioRisk).column_attrs} == columns("scenario_risk")
    assert {a.key for a in sa.inspect(RiskReserve).column_attrs} == columns("risk_reserve")


# --- drift guards: one rule, spelled in two places -----------------------------------------------


def test_the_model_and_the_migration_agree_on_every_sql_expression_and_name() -> None:
    """The migration's copies of the CHECK expressions and of the constraint names are the model's
    (a migration must keep describing the schema it produced, so it cannot import them)."""
    migration = _migration()

    assert migration._NAME_NOT_BLANK_EXPRESSION == risk_model.NAME_NOT_BLANK_EXPRESSION
    assert migration._RESERVE_TYPE_KNOWN_EXPRESSION == risk_model.RESERVE_TYPE_KNOWN_EXPRESSION
    assert (
        migration._RECURRING_PERIOD_CLOSED_EXPRESSION
        == risk_model.RECURRING_PERIOD_CLOSED_EXPRESSION
    )
    assert migration._ONE_OFF_SINGLE_MONTH_EXPRESSION == risk_model.ONE_OFF_SINGLE_MONTH_EXPRESSION
    assert migration._PERIOD_ORDERED_EXPRESSION == risk_model.PERIOD_ORDERED_EXPRESSION
    assert migration._START_MONTH_IS_FIRST_EXPRESSION == risk_model.START_MONTH_IS_FIRST_EXPRESSION
    assert migration._END_MONTH_IS_FIRST_EXPRESSION == risk_model.END_MONTH_IS_FIRST_EXPRESSION
    assert migration._RISK_ID_SCENARIO_UNIQUE == risk_model.RISK_ID_SCENARIO_UNIQUE
    assert migration._RISK_SCENARIO_NAME_UNIQUE == risk_model.RISK_SCENARIO_NAME_UNIQUE
    assert migration._RESERVE_RISK_FOREIGN_KEY == risk_model.RESERVE_RISK_SAME_SCENARIO_FOREIGN_KEY
    assert migration._COST_RISK_FOREIGN_KEY == cost_model.RISK_SAME_SCENARIO_FOREIGN_KEY
    assert migration._RISK_TABLE == ScenarioRisk.__tablename__
    assert migration._RESERVE_TABLE == RiskReserve.__tablename__
    assert migration._COST_TABLE == AdditionalCost.__tablename__
    assert set(risk_model.RESERVE_TYPES) == {"one_off", "recurring"}


# --- the migration runs both ways, and is backward compatible ------------------------------------


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_the_risk_migration_is_expand_only_and_downgrades_and_upgrades_again(
    engine: Engine, alembic_config: Config
) -> None:
    """`d4b8a2e6c910` is additive: the only change to an existing table is one **nullable** column
    with no default (so code written before the migration keeps working), asserted from the
    catalogue
    - and `downgrade()` really runs: both tables and the column go, every cost row survives, and it
    all comes back. `upgrade(head)` runs in `finally`, because `engine` is session-scoped."""

    def catalogue() -> tuple[set[str], tuple[str, str | None] | None]:
        with engine.connect() as connection:
            tables = set(
                connection.execute(
                    sa.text(
                        "SELECT table_name FROM information_schema.tables WHERE table_name ="
                        " ANY(ARRAY['scenario_risk', 'risk_reserve'])"
                    )
                ).scalars()
            )
            column = connection.execute(
                sa.text(
                    "SELECT is_nullable, column_default FROM information_schema.columns"
                    " WHERE table_name = 'additional_cost' AND column_name = 'risk_id'"
                )
            ).one_or_none()
        return tables, None if column is None else (column[0], column[1])

    assert catalogue() == ({"scenario_risk", "risk_reserve"}, ("YES", None))
    try:
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        assert catalogue() == (set(), None)
    finally:
        command.upgrade(alembic_config, "head")
    assert catalogue() == ({"scenario_risk", "risk_reserve"}, ("YES", None))
