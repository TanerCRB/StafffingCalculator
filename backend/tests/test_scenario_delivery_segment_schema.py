"""SC-1-11, K-01..K-05, K-09 — the shape of a delivery-segment row is enforced by the database
(ADR-0016).

Every write here is raw SQL/ORM against the migrated database, never a request schema — there is no
request schema for this table in this task (ADR-0016, point 8). Every refusal is asserted **by
SQLSTATE and constraint name**, so it cannot be some other constraint that happened to fire, and
every refusal has an accepted near miss beside it, so it cannot be a table refusing everything.

Real PostgreSQL, real migration (ADR-0001).
"""

import importlib.util
import os
import uuid
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ScenarioDeliverySegment
from app.models import scenario_delivery_segment as segment_model
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    make_project,
    make_scenario,
    make_scenario_delivery_segment,
)

PREVIOUS_REVISION = "a3d9e6f20c71"
MIGRATION_PATH = (
    Path(BACKEND_ROOT)
    / "migrations"
    / "versions"
    / "b1f4e8a3c95d_create_scenario_delivery_segment.py"
)


def _migration() -> ModuleType:
    """Import the migration by path — `migrations/versions` is not a package."""
    spec = importlib.util.spec_from_file_location("sc_1_11_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(session: Session) -> dict[str, object]:
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline")
    other = make_scenario(session, project, name="Variant")
    return {"project": project, "scenario": scenario, "other": other}


def _insert(session: Session, **overrides: object) -> None:
    row = {"id": uuid.uuid4(), "scenario_id": None, "name": "Discovery"} | overrides
    with session.begin_nested():
        session.execute(sa.insert(ScenarioDeliverySegment.__table__).values(**row))


def _refusal(session: Session, **overrides: object) -> tuple[str | None, str | None]:
    with pytest.raises(IntegrityError) as refused:
        _insert(session, **overrides)
    diagnostics = refused.value.orig.diag  # type: ignore[union-attr]
    return diagnostics.sqlstate, diagnostics.constraint_name


# --- K-01: exactly one parent, enforced by the database -------------------------------------------


def test_k_01_a_segment_naming_a_non_existent_scenario_is_refused_by_the_database(
    db_session: Session,
) -> None:
    """K-01 — `scenario_id` is a foreign key to `scenarios.id`; a random id is refused, never
    accepted by application code that never ran. Contrast: the same row naming a real scenario."""
    fixture = _fixture(db_session)
    _insert(db_session, scenario_id=fixture["scenario"].id, name="Discovery")

    assert _refusal(db_session, scenario_id=uuid.uuid4(), name="Ghost") == (
        "23503", "fk_scenario_delivery_segment_scenario_id"
    )


def test_k_01_scenario_id_is_not_nullable(db_session: Session) -> None:
    """K-01 — a segment with no parent at all is refused by `NOT NULL`, not by an application
    check that never ran."""
    fixture = _fixture(db_session)

    assert _refusal(db_session, scenario_id=None, name="Orphan")[0] == "23502"
    # Contrast, same transaction shape: a segment naming the fixture's own scenario is accepted.
    _insert(db_session, scenario_id=fixture["scenario"].id, name="Discovery")


# --- K-02: one scenario, many segments — a column, not an association table -----------------------


def test_k_02_one_scenario_has_many_segments_on_its_own_column(db_session: Session) -> None:
    """K-02 — inserting two segments under the same scenario succeeds, and each row carries the
    scenario id directly (no join/association table exists for this relationship: the model has
    exactly one table, `scenario_delivery_segment`, and `scenario_id` lives on it)."""
    fixture = _fixture(db_session)
    make_scenario_delivery_segment(db_session, fixture["scenario"], name="Discovery")
    make_scenario_delivery_segment(db_session, fixture["scenario"], name="Build")

    rows = db_session.execute(
        sa.select(ScenarioDeliverySegment.name)
        .where(ScenarioDeliverySegment.scenario_id == fixture["scenario"].id)
        .order_by(ScenarioDeliverySegment.name)
    ).scalars().all()
    assert rows == ["Build", "Discovery"]

    tables = set(
        db_session.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_name LIKE '%scenario_delivery_segment%'"
            )
        ).scalars()
    )
    assert tables == {"scenario_delivery_segment"}, (
        "a second table would mean the relationship grew an association table"
    )


# --- K-03: UNIQUE (id, scenario_id) exists in the database, structurally --------------------------


def test_k_03_the_id_scenario_id_unique_constraint_exists_in_the_database(
    db_session: Session,
) -> None:
    """K-03 — `uq_scenario_delivery_segment_id_scenario_id`, asked of `pg_constraint` directly, so
    "narrowed to `id` alone (redundant with the primary key)" fails here without staging anything.
    """
    row = db_session.execute(
        sa.text(
            "SELECT ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(n, i)"
            "       JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n"
            "       ORDER BY k.i)::text[]"
            " FROM pg_constraint c WHERE c.conname = :name AND c.contype = 'u'"
            " AND c.conrelid = 'scenario_delivery_segment'::regclass"
        ),
        {"name": segment_model.ID_SCENARIO_UNIQUE},
    ).scalar_one()

    assert list(row) == ["id", "scenario_id"]


def test_k_03_the_unique_constraint_is_distinct_from_the_primary_key(db_session: Session) -> None:
    """K-03 — the primary key is `id` alone; `uq_scenario_delivery_segment_id_scenario_id` is a
    second, separate constraint, not merely another name for the same one."""
    constraints = db_session.execute(
        sa.text(
            "SELECT conname, contype FROM pg_constraint"
            " WHERE conrelid = 'scenario_delivery_segment'::regclass AND contype IN ('p', 'u')"
        )
    ).all()
    by_name = dict(constraints)

    assert by_name.get("pk_scenario_delivery_segment") == "p"
    assert by_name.get(segment_model.ID_SCENARIO_UNIQUE) == "u"
    assert by_name.get(segment_model.SCENARIO_NAME_UNIQUE) == "u"
    assert len(constraints) == 3, constraints


# --- K-04: the model and the migration agree on every constraint of this table ------------------


def test_k_04_the_model_and_the_migration_agree_on_the_check_expression_and_names() -> None:
    """K-04 (drift guard R-02, the pattern of `test_additional_cost_schema.py`) — the migration's
    copies of the CHECK expression and of both unique-constraint names are the model's. A migration
    must keep describing the schema it produced, so it cannot import constants the model is free to
    change; `alembic upgrade` never reads the model, so editing one copy alone would break nothing
    else until this test."""
    migration = _migration()

    assert migration._NAME_NOT_BLANK_EXPRESSION == segment_model.NAME_NOT_BLANK_EXPRESSION
    assert migration._ID_SCENARIO_UNIQUE == segment_model.ID_SCENARIO_UNIQUE
    assert migration._SCENARIO_NAME_UNIQUE == segment_model.SCENARIO_NAME_UNIQUE
    assert migration._TABLE == ScenarioDeliverySegment.__tablename__


def test_k_04_every_constraint_of_the_migrated_table_is_named_in_the_model(
    db_session: Session,
) -> None:
    """K-04, structurally and exhaustively — every constraint `pg_constraint` reports for this
    table (by contype) is one the model's `__table_args__` also names, and vice versa: neither side
    can drift without the other noticing, unlike a test asserting only that the ones the model
    expects are *present* (which says nothing about an extra one appearing only in the migration).
    """
    reported = set(
        db_session.execute(
            sa.text(
                "SELECT conname FROM pg_constraint"
                " WHERE conrelid = 'scenario_delivery_segment'::regclass"
                " AND contype IN ('p', 'u', 'c', 'f')"
            )
        ).scalars()
    )
    expected = {
        "pk_scenario_delivery_segment",
        segment_model.ID_SCENARIO_UNIQUE,
        segment_model.SCENARIO_NAME_UNIQUE,
        "ck_scenario_delivery_segment_name_not_blank",
        "fk_scenario_delivery_segment_scenario_id",
    }
    assert reported == expected


def test_k_04_no_on_delete_action_on_the_scenario_foreign_key(db_session: Session) -> None:
    """ADR-0016, point 2 (ADR-0003, point 1) — `NO ACTION`: a cascade would be a second, unguarded
    way for the rows of an `approved` scenario to disappear."""
    action = db_session.execute(
        sa.text(
            "SELECT confdeltype FROM pg_constraint WHERE conname = :name"
        ),
        {"name": "fk_scenario_delivery_segment_scenario_id"},
    ).scalar_one()
    assert action == "a"


def test_overlapping_segments_are_legal_there_is_no_exclude(db_session: Session) -> None:
    """ADR-0016, point 6 — not a consumer of the effective-range pattern: no `EXCLUDE` on this
    table, and none is needed since it carries no date range at all. Asserted directly against
    `pg_constraint` rather than inferred from K-04's set (which already omits `contype = 'x'`, but
    silently — this names the absence as its own claim)."""
    count = db_session.execute(
        sa.text(
            "SELECT count(*) FROM pg_constraint"
            " WHERE contype = 'x' AND conrelid = 'scenario_delivery_segment'::regclass"
        )
    ).scalar_one()
    assert count == 0


# --- K-05: the column set, by equality -----------------------------------------------------------


def test_k_05_the_column_set_is_exactly_id_scenario_id_name_created_at_updated_at(
    engine: Engine,
) -> None:
    """K-05 — equality, not `not in` (the pattern of
    `test_k_22_the_absence_row_has_no_column_for_a_person_or_a_note`): a negative assertion naming
    one spelling survives every other one (`amount`, `hours`, `currency`, `position_id`, `rate`,
    `budget`), and each is a plausible name for somebody adding "just one more column". Equality
    fails for all of them, including the ones nobody has thought of yet."""
    with engine.connect() as connection:
        columns = set(
            connection.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'scenario_delivery_segment'"
                )
            ).scalars()
        )

    expected = {"id", "scenario_id", "name", "created_at", "updated_at"}
    assert columns == expected
    assert {a.key for a in sa.inspect(ScenarioDeliverySegment).column_attrs} == expected


# --- K-09: UNIQUE (scenario_id, name) ----------------------------------------------------------


def test_k_09_a_second_segment_of_the_same_name_in_the_same_scenario_is_refused(
    db_session: Session,
) -> None:
    """K-09 — the database refuses the second `Discovery` of the same scenario. Contrast: the same
    name under a *different* scenario of the same project is accepted."""
    fixture = _fixture(db_session)
    _insert(db_session, scenario_id=fixture["scenario"].id, name="Discovery")

    assert _refusal(db_session, scenario_id=fixture["scenario"].id, name="Discovery") == (
        "23505", segment_model.SCENARIO_NAME_UNIQUE
    )

    # Contrast: same name, another scenario — accepted.
    _insert(db_session, scenario_id=fixture["other"].id, name="Discovery")


def test_k_09_the_non_blank_name_check_refuses_blank_and_whitespace_only_names(
    db_session: Session,
) -> None:
    fixture = _fixture(db_session)

    for blank in ("", "   "):
        assert _refusal(db_session, scenario_id=fixture["scenario"].id, name=blank) == (
            "23514", "ck_scenario_delivery_segment_name_not_blank"
        )
    _insert(db_session, scenario_id=fixture["scenario"].id, name="Discovery")


# --- the migration runs both ways ----------------------------------------------------------------


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_the_scenario_delivery_segment_migration_downgrades_and_upgrades_again(
    engine: Engine, alembic_config: Config
) -> None:
    """`downgrade()` of `b1f4e8a3c95d` really runs: the table goes, and comes back. The starting
    revision is **read**, not hard-coded; `upgrade(head)` runs in `finally`, because `engine` is
    session-scoped and every other test reads the schema left here."""

    def revision() -> str:
        with engine.connect() as connection:
            return connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()

    def table_exists() -> bool:
        with engine.connect() as connection:
            return connection.execute(
                sa.text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
                    " WHERE table_name = 'scenario_delivery_segment')"
                )
            ).scalar_one()

    before = revision()
    try:
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        assert revision() == PREVIOUS_REVISION
        assert table_exists() is False
    finally:
        command.upgrade(alembic_config, "head")

    assert revision() == before
    assert table_exists() is True
