"""SC-5-03, K-06 — a `fixed_amount` basis with no amount or no currency is unwritable, in the
database, whoever is writing (F-07; ADR-0013, addendum 2026-09-25 SC-5-03, point 2; ADR-0014
D-10 the precedent this mirrors).

Every write here is raw SQL against `staffing_position`, never `StaffingPositionCreateRequest`
(mirrors `tests/test_additional_cost_schema.py`): K-06's claim is that the **database** makes the
state nonexistent, so a path through Pydantic would prove only that Pydantic refused first — and a
fixture, a seed script or a future second write path bypasses Pydantic exactly as this file does.
Every refusal is asserted by SQLSTATE and constraint name, with an accepted near miss beside it.

Real PostgreSQL, real migration (ADR-0001, `tests/conftest.py`).
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
    / "a8f18e00172b_add_cost_basis_and_fixed_amount_to_staffing_position.py"
)
MAR = date(2026, 3, 1)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sc_5_03_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(session: Session) -> dict[str, Any]:
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline")
    dimensions = make_dimension_tuple(session)
    return {"scenario": scenario, "dimensions": dimensions}


def _row(fixture: dict[str, Any], **overrides: object) -> dict[str, object]:
    """A valid `worked_time` row, as raw column values."""
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


def _insert(session: Session, row: dict[str, object]) -> None:
    with session.begin_nested():
        session.execute(sa.insert(StaffingPosition.__table__).values(**row))


def _refusal(session: Session, row: dict[str, object]) -> tuple[str | None, str | None]:
    with pytest.raises(IntegrityError) as refused:
        _insert(session, row)
    diagnostics = refused.value.orig.diag  # type: ignore[union-attr]
    return diagnostics.sqlstate, diagnostics.constraint_name


# --- F-4 (K-06): fixed_amount with no amount, no currency, or neither, is unwritable -------------


def test_f_4_fixed_amount_basis_with_no_amount_and_no_currency_is_refused(
    db_session: Session,
) -> None:
    """F-4 — the state ADR-0013's addendum calls "unreachable from the application" from
    construction: neither piece is optional once the basis is `fixed_amount`. Contrast: both
    present is accepted."""
    fixture = _fixture(db_session)

    assert _refusal(
        db_session, _row(fixture, cost_basis="fixed_amount")
    ) == ("23514", "ck_staffing_position_fixed_amount_required_for_its_basis")


def test_f_4_fixed_amount_basis_with_an_amount_but_no_currency_is_refused(
    db_session: Session,
) -> None:
    """F-4, the half the addendum names as its own open question, resolved here: the CHECK requires
    the currency too, not only the amount — a `fixed_amount` with no currency is the same unnamed
    state ADR-0013's "two shapes, never a third" forbids for worked time."""
    fixture = _fixture(db_session)

    assert _refusal(
        db_session,
        _row(fixture, cost_basis="fixed_amount", fixed_amount=Decimal("10.0000")),
    ) == ("23514", "ck_staffing_position_fixed_amount_required_for_its_basis")


def test_f_4_fixed_amount_basis_with_a_currency_but_no_amount_is_refused(
    db_session: Session,
) -> None:
    """F-4, the other half — a currency with no amount is equally the unwritable state."""
    fixture = _fixture(db_session)

    assert _refusal(
        db_session,
        _row(fixture, cost_basis="fixed_amount", fixed_amount_currency="PLN"),
    ) == ("23514", "ck_staffing_position_fixed_amount_required_for_its_basis")


def test_f_4_fixed_amount_basis_with_both_amount_and_currency_is_accepted(
    db_session: Session,
) -> None:
    """The contrast every refusal above needs: the same basis, both fields present, is a legal
    row — the refusals are about the missing field, not about `fixed_amount` in general."""
    fixture = _fixture(db_session)

    _insert(
        db_session,
        _row(
            fixture,
            cost_basis="fixed_amount",
            fixed_amount=Decimal("10.0000"),
            fixed_amount_currency="PLN",
        ),
    )


def test_worked_time_basis_with_a_stray_amount_or_currency_is_not_refused_by_this_check(
    db_session: Session,
) -> None:
    """The CHECK is one-directional by design (ADR-0013's Q2 constrains `fixed_amount`'s own
    requirement, not the reverse): a `worked_time` row carrying a leftover amount/currency — the
    shape the API's edit validator refuses at `422` when switching bases — is not what this
    particular database guarantee is about. Named so a future reader does not mistake this
    permissiveness for an oversight of K-06, which is only about the `fixed_amount` direction."""
    fixture = _fixture(db_session)

    _insert(
        db_session,
        _row(
            fixture,
            cost_basis="worked_time",
            fixed_amount=Decimal("10.0000"),
            fixed_amount_currency="PLN",
        ),
    )


# --- the rest of the schema: known values, positivity, ISO-4217 ----------------------------------


def test_an_unknown_cost_basis_is_refused(db_session: Session) -> None:
    """`cost_basis` is closed to its known values, exactly like `additional_cost.cost_type`.
    (SC-5-04 made `assigned_fte` a known value, so this test's unknown example is now `hourly`; the
    same value is refused by the widened constraint, not by one of the new FTE checks.)"""
    fixture = _fixture(db_session)

    assert _refusal(db_session, _row(fixture, cost_basis="hourly")) == (
        "23514", "ck_staffing_position_cost_basis_known"
    )


@pytest.mark.parametrize("amount", ["0", "0.0000", "-1.0000", "-0.0001"])
def test_a_fixed_amount_that_is_not_strictly_positive_is_refused(
    db_session: Session, amount: str
) -> None:
    """K-06 (G-1 mirror) — `CHECK fixed_amount > 0`. Contrast: the smallest positive amount the
    column holds, `0.0001`, is accepted."""
    fixture = _fixture(db_session)
    _insert(
        db_session,
        _row(
            fixture,
            cost_basis="fixed_amount",
            fixed_amount=Decimal("0.0001"),
            fixed_amount_currency="PLN",
        ),
    )

    assert _refusal(
        db_session,
        _row(
            fixture,
            cost_basis="fixed_amount",
            fixed_amount=Decimal(amount),
            fixed_amount_currency="PLN",
        ),
    ) == ("23514", "ck_staffing_position_fixed_amount_positive")


def test_a_malformed_fixed_amount_currency_is_refused(db_session: Session) -> None:
    """The same two ISO-4217 rules `additional_cost.currency`/`catalog_default_rates.currency`
    already carry: three characters, upper case."""
    fixture = _fixture(db_session)

    assert _refusal(
        db_session,
        _row(
            fixture,
            cost_basis="fixed_amount",
            fixed_amount=Decimal("10.0000"),
            fixed_amount_currency="eur",
        ),
    ) == ("23514", "ck_staffing_position_fixed_amount_currency_is_upper")
    assert _refusal(
        db_session,
        _row(
            fixture,
            cost_basis="fixed_amount",
            fixed_amount=Decimal("10.0000"),
            fixed_amount_currency="EU",
        ),
    ) == ("23514", "ck_staffing_position_fixed_amount_currency_iso4217")


def test_the_model_and_the_migration_agree_on_every_sql_expression() -> None:
    """The migration's copies of the CHECK expressions are the model's — a migration must keep
    describing the schema it produced even after the model moves on (`f3a1d0c58b27`'s rule)."""
    migration = _migration()

    # SC-5-04 widened `cost_basis_known`: the migration that created it keeps the two-value text it
    # produced, and the widening migration (`d4a7e19c2b60`) carries the text the model now has.
    assert migration._COST_BASIS_KNOWN_EXPRESSION == "cost_basis IN ('worked_time', 'fixed_amount')"
    widening = importlib.util.spec_from_file_location(
        "sc_5_04_migration",
        Path(BACKEND_ROOT)
        / "migrations"
        / "versions"
        / "d4a7e19c2b60_add_assigned_fte_to_staffing_position.py",
    )
    assert widening is not None and widening.loader is not None
    widened = importlib.util.module_from_spec(widening)
    widening.loader.exec_module(widened)
    assert widened._COST_BASIS_KNOWN_EXPRESSION == staffing_model.COST_BASIS_KNOWN_EXPRESSION
    assert widened._COST_BASIS_KNOWN_EXPRESSION_BEFORE == migration._COST_BASIS_KNOWN_EXPRESSION
    assert (
        migration._FIXED_AMOUNT_REQUIRES_ITS_OWN_BASIS_EXPRESSION
        == staffing_model.FIXED_AMOUNT_REQUIRES_ITS_OWN_BASIS_EXPRESSION
    )
    assert (
        migration._FIXED_AMOUNT_POSITIVE_EXPRESSION
        == staffing_model.FIXED_AMOUNT_POSITIVE_EXPRESSION
    )
    assert (
        migration._FIXED_AMOUNT_CURRENCY_ISO4217_EXPRESSION
        == staffing_model.FIXED_AMOUNT_CURRENCY_ISO4217_EXPRESSION
    )
    assert (
        migration._FIXED_AMOUNT_CURRENCY_IS_UPPER_EXPRESSION
        == staffing_model.FIXED_AMOUNT_CURRENCY_IS_UPPER_EXPRESSION
    )
    assert set(staffing_model.COST_BASIS_VALUES) == {"worked_time", "fixed_amount", "assigned_fte"}


# --- the migration is reversible ------------------------------------------------------------------

_PREVIOUS_REVISION = "9b3f6a1d0c47"
"""What `a8f18e00172b` revises — the target of `command.downgrade`, spelled as a revision id rather
than `"-1"` for the reason `test_working_calendar_schema_constraints.py`'s reversibility test
records: a relative step goes stale the next time a migration is added on top. Repointed after
merging `origin/main` (SC-5-02's migration chain now sits between `d2f6a91c4b58` and this
migration), the same symmetric fix `dc9c9b4` made for `test_sc_4_03_merge_qa.py`."""


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    """A `Config` of this file's own, mirroring the one `tests/conftest.py`'s `engine` builds — a
    fresh connection, so `command.upgrade`/`downgrade` exercise a second, real client of the
    database rather than the fixture's own."""
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()


def _column_exists(engine: Engine, column: str) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'staffing_position' AND column_name = :column)"
            ),
            {"column": column},
        ).scalar_one()


def test_the_migration_downgrades_and_upgrades_again(
    engine: Engine, alembic_config: Config
) -> None:
    """`downgrade()` really runs and leaves the three columns gone; `upgrade()` afterwards
    restores them. `command.upgrade(alembic_config, "head")` runs in `finally` regardless of
    outcome: `engine` is session-scoped and every other test in this suite reads the schema this
    one leaves behind."""
    before = _current_revision(engine)
    for column in ("cost_basis", "fixed_amount", "fixed_amount_currency"):
        assert _column_exists(engine, column)

    try:
        command.downgrade(alembic_config, _PREVIOUS_REVISION)

        assert _current_revision(engine) == _PREVIOUS_REVISION
        for column in ("cost_basis", "fixed_amount", "fixed_amount_currency"):
            assert not _column_exists(engine, column)
    finally:
        command.upgrade(alembic_config, "head")

    assert _current_revision(engine) == before
    for column in ("cost_basis", "fixed_amount", "fixed_amount_currency"):
        assert _column_exists(engine, column)
