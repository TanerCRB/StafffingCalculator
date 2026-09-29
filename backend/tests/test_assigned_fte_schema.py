"""SC-5-04, FA-1 and FA-13 — the stored FTE is guarded by the database, and the migration that adds
it is expand-only, spelled once, and refuses a destructive downgrade (F-07; Issue #79; ADR-0013,
addendum 2026-09-29 SC-5-04, points 2 and 13 of its control table).

Every write in the first half is raw SQL against `staffing_position` (the pattern of
`test_staffing_cost_basis_schema.py`): FA-1's claim is that the **database** makes the state
nonexistent, so a path through Pydantic would prove only that Pydantic refused first — and a
fixture, a seed script or a future import bypasses Pydantic exactly as this file does. Every
refusal is asserted by SQLSTATE **and constraint name**, and each row below breaks exactly one
constraint, with an accepted near miss beside it, so a dropped constraint fails its own test and
no other.

Real PostgreSQL, real migrations (ADR-0001, `tests/conftest.py`).
"""

import importlib.util
import os
import uuid
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import staffing as staffing_model
from app.models.staffing import StaffingPosition
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    make_dimension_tuple,
    make_project,
    make_scenario,
)

MIGRATION_PATH = (
    Path(BACKEND_ROOT)
    / "migrations"
    / "versions"
    / "d4a7e19c2b60_add_assigned_fte_to_staffing_position.py"
)
_REVISION = "d4a7e19c2b60"
_PREVIOUS_REVISION = "c6e1a94d7b35"
"""What `d4a7e19c2b60` revises, spelled as a revision id rather than a relative step (the reason
`test_catalog_migration_reversibility.py` records)."""

MAR = date(2026, 3, 1)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sc_5_04_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(session: Session) -> dict[str, Any]:
    project = make_project(session, name="Aurora FTE schema", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline")
    return {"scenario": scenario, "dimensions": make_dimension_tuple(session)}


def _row(fixture: dict[str, Any], **overrides: object) -> dict[str, object]:
    """A valid `worked_time` row, as raw column values, `assigned_fte` not named at all."""
    dimensions = fixture["dimensions"]
    return {
        "id": uuid.uuid4(),
        "scenario_id": fixture["scenario"].id,
        "role_id": dimensions.role_id,
        "seniority_id": dimensions.seniority_id,
        "location_id": dimensions.location_id,
        "engagement_type_id": dimensions.engagement_type_id,
        "headcount": 1,
        "start_date": MAR,
        "end_date": None,
        "cost_basis": "worked_time",
        "fixed_amount": None,
        "fixed_amount_currency": None,
    } | overrides


def _fte_row(fixture: dict[str, Any], fte: object, **overrides: object) -> dict[str, object]:
    return _row(fixture, cost_basis="assigned_fte", assigned_fte=fte, **overrides)


def _insert(session: Session, row: dict[str, object]) -> None:
    with session.begin_nested():
        session.execute(sa.insert(StaffingPosition.__table__).values(**row))


def _refusal(session: Session, row: dict[str, object]) -> tuple[str | None, str | None]:
    with pytest.raises(IntegrityError) as refused:
        _insert(session, row)
    diagnostics = refused.value.orig.diag  # type: ignore[union-attr]
    return diagnostics.sqlstate, diagnostics.constraint_name


def _check(name: str) -> tuple[str, str]:
    return "23514", f"ck_staffing_position_{name}"


# --- FA-1: what the database refuses, and what it accepts next to it -----------------------------


@pytest.mark.parametrize("fte", ["0", "0.0000", "-1.0000", "-0.0001"])
def test_fa_1_an_fte_that_is_not_strictly_positive_is_refused(
    db_session: Session, fte: str
) -> None:
    """FA-1 — `assigned_fte > 0` when set. Contrast: `0.0001`, the smallest four-place value, is
    accepted. Mutation: the constraint dropped or written as `>= 0`."""
    fixture = _fixture(db_session)

    assert _refusal(db_session, _fte_row(fixture, Decimal(fte))) == _check("assigned_fte_positive")
    _insert(db_session, _fte_row(fixture, Decimal("0.0001")))


def test_fa_1_the_fte_basis_with_no_fte_is_refused(db_session: Session) -> None:
    """FA-1 — `cost_basis = 'assigned_fte'` and `assigned_fte IS NULL` is the unnamed state "two
    shapes, never a third" forbids: it cannot exist. Contrast: the same row with a value."""
    fixture = _fixture(db_session)

    assert _refusal(db_session, _fte_row(fixture, None)) == _check(
        "assigned_fte_required_for_its_basis"
    )
    _insert(db_session, _fte_row(fixture, Decimal("0.5000")))


def test_fa_1_a_stray_fte_on_another_basis_is_refused(db_session: Session) -> None:
    """FA-1 — `assigned_fte` is `NULL` on every other basis. A stray value on a `worked_time` row is
    an input nothing reads and somebody may believe. Contrast: the same value on its own basis."""
    fixture = _fixture(db_session)

    stray = _row(fixture, cost_basis="worked_time", assigned_fte=Decimal("1.0000"))
    assert _refusal(db_session, stray) == _check("assigned_fte_only_on_its_basis")
    _insert(db_session, _fte_row(fixture, Decimal("1.0000")))


def test_fa_1_a_stray_fte_on_the_fixed_amount_basis_is_refused(db_session: Session) -> None:
    """FA-1 — the same rule for `fixed_amount`, which also breaks the "not together" rule, so the
    database names one of the two (which one is PostgreSQL's evaluation order, not a promise)."""
    fixture = _fixture(db_session)
    row = _row(
        fixture,
        cost_basis="fixed_amount",
        fixed_amount=Decimal("100.0000"),
        fixed_amount_currency="PLN",
        assigned_fte=Decimal("1.0000"),
    )

    sqlstate, constraint = _refusal(db_session, row)

    assert sqlstate == "23514"
    assert constraint in {
        "ck_staffing_position_assigned_fte_only_on_its_basis",
        "ck_staffing_position_assigned_fte_not_with_fixed_amount",
    }


@pytest.mark.parametrize(
    "stray",
    [
        {"fixed_amount": Decimal("100.0000"), "fixed_amount_currency": "PLN"},
        {"fixed_amount": Decimal("100.0000")},
        {"fixed_amount_currency": "PLN"},
    ],
    ids=["amount-and-currency", "amount-only", "currency-only"],
)
def test_fa_1_an_fte_together_with_a_stated_amount_is_refused(
    db_session: Session, stray: dict[str, object]
) -> None:
    """FA-1 — two stated costs on one row are two answers. On the FTE basis (so the only broken rule
    is this one) a `fixed_amount`, a currency, or both are refused. Contrast: the clean FTE row."""
    fixture = _fixture(db_session)

    assert _refusal(db_session, _fte_row(fixture, Decimal("1.0000"), **stray)) == _check(
        "assigned_fte_not_with_fixed_amount"
    )
    _insert(db_session, _fte_row(fixture, Decimal("1.0000")))


def test_fa_1_a_value_above_the_headcount_is_accepted(db_session: Session) -> None:
    """FA-1/point 2 — no CHECK compares the FTE with `headcount`: 5 FTE on a position for one person
    is stored (accepted without bound, surfaced nowhere - ADR-0013 SC-5-04 point 2). Contrast (the
    mutation): a CHECK `assigned_fte <= headcount` would refuse this row."""
    fixture = _fixture(db_session)

    _insert(db_session, _fte_row(fixture, Decimal("5.0000"), headcount=1))
    stored = db_session.execute(
        sa.select(StaffingPosition.assigned_fte, StaffingPosition.headcount).where(
            StaffingPosition.scenario_id == fixture["scenario"].id
        )
    ).one()
    assert (stored.assigned_fte, stored.headcount) == (Decimal("5.0000"), 1)


def test_fa_1_the_fte_is_stored_exactly_to_four_places_in_a_numeric_10_4_column(
    db_session: Session,
) -> None:
    """FA-1 — `NUMERIC(10,4)`, nullable: 0.3333 comes back as 0.3333 (never a float's 0.33329999…),
    the largest value 999999.9999 is accepted, and the next integer overflows in the database
    (SQLSTATE 22003) — which is why the request schema bounds the same digits and answers 422."""
    fixture = _fixture(db_session)
    column = db_session.execute(
        sa.text(
            "SELECT numeric_precision, numeric_scale, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'staffing_position' AND column_name = 'assigned_fte'"
        )
    ).one()
    assert tuple(column) == (10, 4, "YES")

    _insert(db_session, _fte_row(fixture, Decimal("0.3333")))
    _insert(db_session, _fte_row(fixture, Decimal("999999.9999")))
    values = set(
        db_session.execute(
            sa.select(StaffingPosition.assigned_fte).where(
                StaffingPosition.scenario_id == fixture["scenario"].id
            )
        ).scalars()
    )
    assert values == {Decimal("0.3333"), Decimal("999999.9999")}

    with pytest.raises(sa.exc.DBAPIError) as overflow:
        _insert(db_session, _fte_row(fixture, Decimal("1000000.0000")))
    assert overflow.value.orig.diag.sqlstate == "22003"  # type: ignore[union-attr]


def test_fa_1_the_constraints_exist_in_the_migrated_database_under_their_names(
    db_session: Session,
) -> None:
    """FA-1 — asked of the catalogue, not assumed from the model (the pattern of ADR-0014 D-10): the
    four new CHECKs and the widened one are in `pg_constraint`, and the widened one admits the new
    basis."""
    definitions = {
        name: definition
        for name, definition in db_session.execute(
            sa.text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conrelid = 'staffing_position'::regclass AND contype = 'c'"
            )
        ).all()
    }

    for name in (
        "assigned_fte_positive",
        "assigned_fte_required_for_its_basis",
        "assigned_fte_only_on_its_basis",
        "assigned_fte_not_with_fixed_amount",
    ):
        assert f"ck_staffing_position_{name}" in definitions, name
    known = definitions["ck_staffing_position_cost_basis_known"]
    assert "assigned_fte" in known and "fixed_amount" in known and "worked_time" in known


# --- FA-13: one migration, spelled once, drift-checked --------------------------------------------


def test_fa_13_the_model_and_the_migration_agree_on_every_new_sql_expression() -> None:
    """FA-13 — the migration's copies of the CHECK expressions are the model's (a migration keeps
    describing the schema it produced even after the model moves on, `f3a1d0c58b27`'s rule), the
    constraint names match, and the closed set of bases is the one the widened CHECK admits.
    Mutation: an expression edited in one place only."""
    migration = _migration()

    pairs = {
        "_COST_BASIS_KNOWN_EXPRESSION": staffing_model.COST_BASIS_KNOWN_EXPRESSION,
        "_ASSIGNED_FTE_POSITIVE_EXPRESSION": staffing_model.ASSIGNED_FTE_POSITIVE_EXPRESSION,
        "_ASSIGNED_FTE_REQUIRED_FOR_ITS_BASIS_EXPRESSION": (
            staffing_model.ASSIGNED_FTE_REQUIRED_FOR_ITS_BASIS_EXPRESSION
        ),
        "_ASSIGNED_FTE_ONLY_ON_ITS_BASIS_EXPRESSION": (
            staffing_model.ASSIGNED_FTE_ONLY_ON_ITS_BASIS_EXPRESSION
        ),
        "_ASSIGNED_FTE_NOT_WITH_FIXED_AMOUNT_EXPRESSION": (
            staffing_model.ASSIGNED_FTE_NOT_WITH_FIXED_AMOUNT_EXPRESSION
        ),
    }
    for attribute, model_text in pairs.items():
        assert getattr(migration, attribute) == model_text, attribute

    model_checks = {
        constraint.name
        for constraint in StaffingPosition.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert {f"ck_staffing_position_{name}" for name, _ in migration._NEW_CHECKS} <= model_checks
    for basis in staffing_model.COST_BASIS_VALUES:
        assert f"'{basis}'" in migration._COST_BASIS_KNOWN_EXPRESSION, basis
    assert migration.down_revision == _PREVIOUS_REVISION
    assert (staffing_model.ASSIGNED_FTE_PRECISION, staffing_model.ASSIGNED_FTE_SCALE) == (10, 4)


def test_fa_13_the_migration_before_this_one_is_untouched() -> None:
    """FA-13 — `a8f18e00172b` keeps describing the schema it produced: its known-basis expression
    still names exactly two values (this task widened the constraint in a *new* revision)."""
    old = Path(BACKEND_ROOT) / "migrations" / "versions"
    source = next(old.glob("a8f18e00172b_*.py")).read_text(encoding="utf-8")

    assert "cost_basis IN ('worked_time', 'fixed_amount')" in source
    assert "assigned_fte" not in source


def test_fa_13_this_is_one_revision_on_top_of_the_previous_one_and_the_chain_has_one_head(
    alembic_config: Config,
) -> None:
    """FA-13 — one revision on top of `c6e1a94d7b35`, and Alembic still has a single head (a second
    branch would make `upgrade head` ambiguous)."""
    script = ScriptDirectory.from_config(alembic_config)

    assert script.get_revision(_REVISION).down_revision == _PREVIOUS_REVISION
    assert len(script.get_heads()) == 1


# --- FA-13: expand-safe, lock-bounded, downgrade refuses by name ----------------------------------


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture
def migration_sandbox(engine: Engine, alembic_config: Config):
    """Leaves the schema at head and the tables this file commits into empty, whatever happened."""
    yield
    command.upgrade(alembic_config, "head")
    with engine.begin() as connection:
        for table in (
            "staffing_position_allocation", "staffing_position", "project_access", "scenarios",
            "projects", "catalog_roles", "catalog_seniorities", "catalog_locations",
            "catalog_engagement_types",
        ):
            connection.execute(sa.text(f"DELETE FROM {table}"))


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()


def _column_exists(engine: Engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'staffing_position' AND column_name = 'assigned_fte')"
            )
        ).scalar_one()


def _commit_position(engine: Engine, **overrides: object) -> uuid.UUID:
    with Session(bind=engine, expire_on_commit=False, future=True) as session:
        fixture = _fixture(session)
        row = _row(fixture, **overrides)
        session.execute(sa.insert(StaffingPosition.__table__).values(**row))
        session.commit()
        return row["id"]  # type: ignore[return-value]


def test_fa_13_the_downgrade_refuses_while_a_position_is_costed_on_the_fte_basis(
    engine: Engine, alembic_config: Config, migration_sandbox: None
) -> None:
    """FA-13 — a committed `assigned_fte` position: the downgrade raises **by name**, echoes no
    value, id or count (NF-11), and changes nothing (still at head, column still there). Contrast,
    in the same test: once that position is switched to `worked_time` the same downgrade runs — the
    guard reads the rows, it is not an unconditional refusal — and the upgrade brings everything
    back. Mutation: the guard removed (the column would drop and take the stored FTE with it)."""
    position_id = _commit_position(
        engine, cost_basis="assigned_fte", assigned_fte=Decimal("0.4321")
    )
    before = _current_revision(engine)

    with pytest.raises(RuntimeError, match="Refusing to downgrade") as refusal:
        command.downgrade(alembic_config, _PREVIOUS_REVISION)
    message = str(refusal.value)
    assert "assigned_fte" in message
    for value in ("0.4321", "4321", str(position_id)):
        assert value not in message, f"the refusal echoes {value!r} (NF-11)"
    assert not any(ch.isdigit() for ch in message.replace(_REVISION, "")), message
    assert _current_revision(engine) == before == _REVISION
    assert _column_exists(engine)

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE staffing_position SET cost_basis = 'worked_time', assigned_fte = NULL"
                " WHERE id = :id"
            ),
            {"id": position_id},
        )
    command.downgrade(alembic_config, _PREVIOUS_REVISION)
    assert _current_revision(engine) == _PREVIOUS_REVISION
    assert not _column_exists(engine)
    command.upgrade(alembic_config, "head")
    assert _current_revision(engine) == _REVISION
    assert _column_exists(engine)


def test_fa_13_the_downgrade_restores_the_narrower_constraint_and_the_upgrade_widens_it_again(
    engine: Engine, alembic_config: Config, migration_sandbox: None
) -> None:
    """FA-13 — reversibility with a witness: at the previous revision `'assigned_fte'` is an unknown
    basis again (the old two-value CHECK), and after the upgrade it is known. Mutation: the
    downgrade leaving the widened constraint behind."""
    command.downgrade(alembic_config, _PREVIOUS_REVISION)
    with engine.connect() as connection:
        narrow = connection.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_staffing_position_cost_basis_known'"
            )
        ).scalar_one()
    assert "assigned_fte" not in narrow and "fixed_amount" in narrow

    command.upgrade(alembic_config, "head")
    with engine.connect() as connection:
        wide = connection.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_staffing_position_cost_basis_known'"
            )
        ).scalar_one()
    assert "assigned_fte" in wide


def test_fa_13_an_offline_downgrade_refuses_in_words_and_the_offline_upgrade_renders_the_sql(
    engine: Engine, database_url: str
) -> None:
    """FA-13 — `alembic downgrade --sql` says why it cannot run (the guard reads the table live);
    the offline *upgrade* — the SQL a reviewer reads — bounds the lock before the first `ALTER`,
    adds a nullable `NUMERIC(10, 4)` column with no default, widens `cost_basis_known` and adds the
    four checks, and touches nothing. Mutation: the `lock_timeout` removed, or a `DEFAULT` added to
    the column."""
    before = _current_revision(engine)
    buffer = StringIO()
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"), output_buffer=buffer)
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)

    with pytest.raises(RuntimeError, match="offline"):
        command.downgrade(config, f"{_REVISION}:{_PREVIOUS_REVISION}", sql=True)
    command.upgrade(config, f"{_PREVIOUS_REVISION}:{_REVISION}", sql=True)

    script = buffer.getvalue().lower()
    assert "set local lock_timeout = '3s'" in script
    assert script.index("set local lock_timeout = '3s'") < script.index("alter table")
    assert script.rindex("alter table") < script.rindex("set local lock_timeout = default")
    assert "add column assigned_fte numeric(10, 4)" in script
    assert "default" not in script.split("add column assigned_fte", 1)[1].split(";", 1)[0]
    assert "drop constraint ck_staffing_position_cost_basis_known" in script
    for name in (
        "assigned_fte_positive", "assigned_fte_required_for_its_basis",
        "assigned_fte_only_on_its_basis", "assigned_fte_not_with_fixed_amount",
    ):
        assert f"add constraint ck_staffing_position_{name} check" in script, name
    assert "drop column" not in script  # expand only: nothing is dropped by the upgrade
    assert _current_revision(engine) == before


# --- QA contrast: the downgrade's table lock ------------------------------------------------------


def test_qa_the_downgrade_lock_makes_a_row_committed_during_the_guard_count_as_in_use(
    engine: Engine, alembic_config: Config, migration_sandbox: None
) -> None:
    """QA (SC-5-04, FA-13) — the guard "locks the table before counting". Nothing else proves it:
    with the lock removed, every quiet-table test gives the same outcome.

    Scenario: another session has inserted an `assigned_fte` row and has **not committed**; the
    downgrade starts; the other session commits a moment later (inside the 3 s lock bound). With
    `LOCK TABLE` the downgrade waits for that transaction and its count then sees the row, so it
    refuses **by name**. Without it the count runs at once, sees nothing, and the failure comes
    later from a `CHECK` violation of the narrowed constraint — an error of the database, not the
    refusal in words. Contrast, same test: with nothing pending the downgrade goes through.
    Mutation: `LOCK TABLE ...` removed from `downgrade()`."""
    import threading
    import time

    with Session(bind=engine, expire_on_commit=False, future=True) as seed:
        fixture = _fixture(seed)
        seed.commit()
    pending = Session(bind=engine, expire_on_commit=False, future=True)
    pending.execute(
        sa.insert(StaffingPosition.__table__).values(**_fte_row(fixture, Decimal("0.5000")))
    )  # holds ROW EXCLUSIVE on staffing_position, uncommitted
    committer = threading.Timer(1.0, pending.commit)
    committer.start()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="Refusing to downgrade"):
            command.downgrade(alembic_config, _PREVIOUS_REVISION)
    finally:
        committer.join()
        pending.close()
    assert time.monotonic() - started >= 0.9, "the guard did not wait for the pending transaction"
    assert _current_revision(engine) == _REVISION
    assert _column_exists(engine)

    with engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM staffing_position"))
    command.downgrade(alembic_config, _PREVIOUS_REVISION)
    assert _current_revision(engine) == _PREVIOUS_REVISION
