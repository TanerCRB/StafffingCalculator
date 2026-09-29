"""SC-5-08, K-07 — migration `c6e1a94d7b35` (`cost_rate_unit` on the catalogue rate and on its
approval snapshot) is expand-safe, on a real PostgreSQL (ADR-0004, addendum 2026-09-29, points 3 and
6; ADR-0001, expand → deploy → contract).

What is proven, and by which test:

- **Backfill and shape.** Rows written in the *previous* schema get `hour` when the migration runs,
  in both tables; the column is `NOT NULL DEFAULT 'hour'`; the `CHECK` on the catalogue admits
  `hour`, `day` and `month` and nothing else.
- **The downgrade guard.** It refuses — with no row value in the message — while a non-`hour` row
  exists in the catalogue **or** in the snapshot table (each alone), leaves the schema untouched,
  succeeds when every row is hourly (so it is a guard, not a blanket refusal), and refuses to run
  offline instead of crashing.
- **The lock bound.** The upgrade gives up on a held lock (`55P03`) instead of queueing the
  catalogue, and the rendered script scopes its `lock_timeout` — the precedent of `d5e94a1c6b73`.

Committed rows, not `db_session`'s rolled-back transaction: `alembic` opens its own connection
(`migrations/env.py`), so the guard has to see rows through a second, independent client. Every test
leaves the schema at `head` and the tables empty — `engine` is session-scoped.
"""

import os
import threading
import time
import uuid
from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models import ApprovedSnapshotCatalogDefaultRate, ScenarioStatus
from tests.conftest import (
    BACKEND_ROOT,
    DimensionTuple,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
)

_REVISION = "c6e1a94d7b35"
_PREVIOUS_REVISION = "b8f2d6a41c93"
"""What `c6e1a94d7b35` revises, spelled as a revision id rather than a relative step (the reason
`test_catalog_migration_reversibility.py` records)."""

_LIVE = "catalog_default_rates"
_SNAPSHOT = "approved_snapshot_catalog_default_rate"
_CHECK = "ck_catalog_default_rates_cost_rate_unit_is_known"
_LOCK_TIMEOUT_SQLSTATE = "55P03"

_ROW_MARKER = Decimal("812.3400")
"""A cost rate no other fixture uses — the refusal message must not contain it (NF-11)."""


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(autouse=True)
def _leave_the_schema_at_head_and_the_tables_empty(engine: Engine, alembic_config: Config):
    yield
    command.upgrade(alembic_config, "head")
    with engine.begin() as connection:
        for table in (_SNAPSHOT, _LIVE, "project_access", "scenarios", "projects",
                      "catalog_roles", "catalog_seniorities", "catalog_locations",
                      "catalog_engagement_types"):
            connection.execute(sa.text(f"DELETE FROM {table}"))


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()


def _column(engine: Engine, table: str):
    with engine.connect() as connection:
        return connection.execute(
            sa.text(
                "SELECT is_nullable, column_default FROM information_schema.columns"
                " WHERE table_name = :table AND column_name = 'cost_rate_unit'"
            ),
            {"table": table},
        ).one_or_none()


def _committed_plan(engine: Engine) -> tuple[DimensionTuple, uuid.UUID]:
    """A dimension tuple and an approved scenario, committed (no rate rows yet)."""
    with Session(bind=engine, expire_on_commit=False, future=True) as session:
        dimensions = make_dimension_tuple(session, suffix=" k07")
        project = make_project(session, name="Migration k07")
        scenario = make_scenario(session, project, name="Approved k07",
                                 status=ScenarioStatus.APPROVED)
        session.commit()
        return dimensions, scenario.id


def _commit_live_rate(engine: Engine, dimensions: DimensionTuple, **values) -> None:
    with Session(bind=engine, expire_on_commit=False, future=True) as session:
        make_rate(session, dimensions, effective_from=date(2026, 1, 1),
                  default_cost_rate=_ROW_MARKER, **values)
        session.commit()


def _commit_frozen_rate(
    engine: Engine, dimensions: DimensionTuple, scenario_id: uuid.UUID, unit: str
) -> None:
    with Session(bind=engine, expire_on_commit=False, future=True) as session:
        session.add(ApprovedSnapshotCatalogDefaultRate(
            id=uuid.uuid4(), scenario_id=scenario_id, source_rate_id=uuid.uuid4(),
            source_role_id=dimensions.role_id, source_seniority_id=dimensions.seniority_id,
            source_location_id=dimensions.location_id,
            source_engagement_type_id=dimensions.engagement_type_id, source_vendor_id=None,
            default_cost_rate=_ROW_MARKER, default_selling_rate=Decimal("200.0000"),
            currency="PLN", unit="hour", effective_from=date(2026, 1, 1), effective_to=None,
            surcharge_percent=Decimal("0"), includes_surcharge=False, cost_rate_unit=unit,
        ))
        session.commit()


# --- the expand: backfill, default, check -------


def test_k_07_existing_rows_get_hour_and_the_column_is_not_null_default_hour_with_a_check(
    engine: Engine, alembic_config: Config
) -> None:
    """K-07 — the schema is taken back to `b8f2d6a41c93`, one catalogue row and one snapshot row are
    written **in that shape** (raw SQL naming no `cost_rate_unit`), and the migration runs: both
    rows read `hour`; both columns are `NOT NULL`, the live one with default `'hour'` and the
    snapshot one with **no**
    default (dropped after the backfill, reviewer R-01); a direct insert of `week`
    is refused by the named CHECK while `day` and `month` are accepted. Mutations: the default
    removed (the old-shape insert would fail before the upgrade), `NOT NULL` removed, the CHECK
    missing or widened."""
    dimensions, scenario_id = _committed_plan(engine)
    command.downgrade(alembic_config, _PREVIOUS_REVISION)
    assert _column(engine, _LIVE) is None and _column(engine, _SNAPSHOT) is None
    common = {
        "role": dimensions.role_id, "seniority": dimensions.seniority_id,
        "location": dimensions.location_id, "engagement": dimensions.engagement_type_id,
        "cost": _ROW_MARKER, "scenario": scenario_id,
    }
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO catalog_default_rates (id, role_id, seniority_id, location_id,"
                " engagement_type_id, default_cost_rate, default_selling_rate, currency, unit,"
                " effective_from, effective_to) VALUES (gen_random_uuid(), :role, :seniority,"
                " :location,"
                " :engagement, :cost, 200, 'PLN', 'hour', DATE '2026-01-01', DATE '2026-12-31')"
            ),
            common,
        )
        connection.execute(
            sa.text(
                "INSERT INTO approved_snapshot_catalog_default_rate (id, scenario_id,"
                " source_rate_id, source_role_id, source_seniority_id, source_location_id,"
                " source_engagement_type_id, default_cost_rate, default_selling_rate, currency,"
                " unit, effective_from, surcharge_percent, includes_surcharge) VALUES"
                " (gen_random_uuid(), :scenario, gen_random_uuid(), :role, :seniority, :location,"
                " :engagement, :cost, 200, 'PLN', 'hour', DATE '2026-01-01', 0, false)"
            ),
            common,
        )

    command.upgrade(alembic_config, "head")

    with engine.connect() as connection:
        for table in (_LIVE, _SNAPSHOT):
            units = connection.execute(sa.text(f"SELECT cost_rate_unit FROM {table}")).scalars()
            assert list(units) == ["hour"], f"the existing row of {table} was not backfilled"
    for table in (_LIVE, _SNAPSHOT):
        nullable, default = _column(engine, table)
        assert nullable == "NO", f"{table}.cost_rate_unit is nullable"
        if table == _LIVE:
            assert default is not None and "'hour'" in default, f"{table}.cost_rate_unit default"
        else:
            assert default is None, "the snapshot column kept its backfill default (R-01)"

    with engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT count(*) FROM pg_constraint WHERE conname = :name"), {"name": _CHECK}
        ).scalar_one() == 1
    for accepted, (start, end) in (("day", ("2027-02-01", "2027-02-28")),
                                   ("month", ("2027-03-01", "2027-03-31"))):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO catalog_default_rates (id, role_id, seniority_id, location_id,"
                    " engagement_type_id, default_cost_rate, default_selling_rate, currency, unit,"
                    " cost_rate_unit, effective_from, effective_to) VALUES (gen_random_uuid(),"
                    " :role, :seniority, :location, :engagement, 1, 1, 'PLN', 'hour', :unit,"
                    " :start, :end)"
                ),
                common | {"unit": accepted, "start": date.fromisoformat(start),
                          "end": date.fromisoformat(end)},
            )
    with pytest.raises(IntegrityError, match=_CHECK):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO catalog_default_rates (id, role_id, seniority_id, location_id,"
                    " engagement_type_id, default_cost_rate, default_selling_rate, currency, unit,"
                    " cost_rate_unit, effective_from, effective_to) VALUES (gen_random_uuid(),"
                    " :role, :seniority, :location, :engagement, 1, 1, 'PLN', 'hour', 'week',"
                    " DATE '2027-04-01', DATE '2027-04-30')"
                ),
                common,
            )


def test_k_07_a_snapshot_insert_that_omits_the_unit_fails_while_a_live_one_defaults_to_hour(
    engine: Engine,
) -> None:
    """K-07 / R-01 — the snapshot column has no default: an `INSERT` that omits it violates `NOT
    NULL` (a copier that forgot the column fails loudly instead of freezing hourly); the live column
    keeps `DEFAULT 'hour'`, so an insert omitting it stores `hour`. Mutation: the `DROP DEFAULT`
    removed from the migration."""
    dimensions, scenario_id = _committed_plan(engine)
    values = {
        "role": dimensions.role_id, "seniority": dimensions.seniority_id,
        "location": dimensions.location_id, "engagement": dimensions.engagement_type_id,
        "scenario": scenario_id,
    }
    with pytest.raises(IntegrityError, match="cost_rate_unit"):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO approved_snapshot_catalog_default_rate (id, scenario_id,"
                    " source_rate_id, source_role_id, source_seniority_id, source_location_id,"
                    " source_engagement_type_id, default_cost_rate, default_selling_rate,"
                    " currency, unit, effective_from, surcharge_percent, includes_surcharge)"
                    " VALUES (gen_random_uuid(), :scenario, gen_random_uuid(), :role, :seniority,"
                    " :location, :engagement, 1, 1, 'PLN', 'hour', DATE '2026-01-01', 0, false)"
                ),
                values,
            )
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO catalog_default_rates (id, role_id, seniority_id, location_id,"
                " engagement_type_id, default_cost_rate, default_selling_rate, currency, unit,"
                " effective_from) VALUES (gen_random_uuid(), :role, :seniority, :location,"
                " :engagement, 1, 1, 'PLN', 'hour', DATE '2026-01-01')"
            ),
            values,
        )
        assert connection.execute(
            sa.text("SELECT cost_rate_unit FROM catalog_default_rates")
        ).scalar_one() == "hour"


# --- the downgrade guard ----------------------------------------------------------------


def _assert_refused_without_values(
    engine: Engine, alembic_config: Config, before: str
) -> None:
    with pytest.raises(RuntimeError, match="Refusing to downgrade") as refusal:
        command.downgrade(alembic_config, _PREVIOUS_REVISION)
    message = str(refusal.value)
    for value in (str(_ROW_MARKER), "812", "'day'", "'month'", "'week'", "PLN"):
        assert value not in message, f"the refusal echoes {value!r} (NF-11)"
    assert not [part for part in message.split() if len(part) == 36 and part.count("-") == 4]
    # QA (SC-5-08): not even a count. The revision id is the only place a digit may appear; a
    # refusal that appended "1 row" survived the value list above.
    assert not any(ch.isdigit() for ch in message.replace(_REVISION, "")), (
        f"the refusal carries a number (NF-11): {message!r}"
    )
    # Refused before any schema change: still at `head`, both columns still there.
    assert _current_revision(engine) == before
    assert _column(engine, _LIVE) is not None and _column(engine, _SNAPSHOT) is not None


def test_k_07_the_downgrade_refuses_while_a_catalogue_row_is_not_hourly(
    engine: Engine, alembic_config: Config
) -> None:
    """K-07 — a committed `day` catalogue row (and nothing non-hourly in the snapshot table): the
    downgrade raises, names no row value, and changes nothing. Mutation: the guard removed (the
    column would drop and the row would silently become an hourly rate)."""
    dimensions, _ = _committed_plan(engine)
    _commit_live_rate(engine, dimensions, cost_rate_unit="day")
    before = _current_revision(engine)

    _assert_refused_without_values(engine, alembic_config, before)


def test_k_07_the_downgrade_refuses_while_a_snapshot_row_is_not_hourly(
    engine: Engine, alembic_config: Config
) -> None:
    """K-07 — the snapshot table alone: an hourly catalogue row and a frozen `month` row. The guard
    reads **both** tables; one that counted only the catalogue would let this through and reprice an
    approved scenario hourly for ever, with no path to repair it."""
    dimensions, scenario_id = _committed_plan(engine)
    _commit_live_rate(engine, dimensions, cost_rate_unit="hour")
    _commit_frozen_rate(engine, dimensions, scenario_id, "month")
    before = _current_revision(engine)

    _assert_refused_without_values(engine, alembic_config, before)


def test_k_07_the_downgrade_succeeds_when_every_row_is_hourly_and_the_upgrade_restores_it(
    engine: Engine, alembic_config: Config
) -> None:
    """K-07, the contrast — hourly rows in both tables: the downgrade runs, both columns are gone
    and the revision is `b8f2d6a41c93`; upgrading again restores the columns with `hour` on every
    row. A guard that refused everything would fail here."""
    dimensions, scenario_id = _committed_plan(engine)
    _commit_live_rate(engine, dimensions, cost_rate_unit="hour")
    _commit_frozen_rate(engine, dimensions, scenario_id, "hour")

    command.downgrade(alembic_config, _PREVIOUS_REVISION)

    assert _current_revision(engine) == _PREVIOUS_REVISION
    assert _column(engine, _LIVE) is None and _column(engine, _SNAPSHOT) is None

    command.upgrade(alembic_config, "head")

    with engine.connect() as connection:
        for table in (_LIVE, _SNAPSHOT):
            assert list(
                connection.execute(sa.text(f"SELECT cost_rate_unit FROM {table}")).scalars()
            ) == ["hour"]


def test_k_07_an_offline_downgrade_refuses_in_words_and_the_offline_upgrade_renders(
    engine: Engine, database_url: str
) -> None:
    """K-07 — `alembic downgrade --sql` says why it cannot run instead of crashing (the guard reads
    the tables live); the offline *upgrade*, the contrast, renders the `ALTER`s and touches
    nothing."""
    before = _current_revision(engine)
    buffer = StringIO()
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"), output_buffer=buffer)
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)

    with pytest.raises(RuntimeError, match="offline"):
        command.downgrade(config, f"{_REVISION}:{_PREVIOUS_REVISION}", sql=True)
    command.upgrade(config, f"{_PREVIOUS_REVISION}:{_REVISION}", sql=True)

    script = buffer.getvalue().lower()
    assert "add column cost_rate_unit" in script
    assert "set local lock_timeout = '3s'" in script
    assert script.index("set local lock_timeout = '3s'") < script.index("alter table")
    assert script.rindex("alter table") < script.rindex("set local lock_timeout = default")
    assert _current_revision(engine) == before


# --- the lock bound (precedent: d5e94a1c6b73) -------


def test_k_07_the_upgrade_gives_up_on_a_held_lock_instead_of_queueing_the_catalogue(
    engine: Engine, alembic_config: Config
) -> None:
    """K-07 — with an `ACCESS SHARE` lock held on the catalogue rate table (a long `GET`), the
    upgrade fails with `55P03` inside its 3-second bound and changes nothing: still at
    `b8f2d6a41c93`, no column. Elapsed time is asserted from both sides (as in
    `test_catalog_migration_lock_timeout.py`): the upper bound separates "gave up" from "waited for
    the competitor"; the lower rules out a failure of some other kind."""
    command.downgrade(alembic_config, _PREVIOUS_REVISION)
    acquired, release = threading.Event(), threading.Event()

    def hold() -> None:
        with engine.connect() as connection, connection.begin():
            connection.execute(sa.text(f"LOCK TABLE {_LIVE} IN ACCESS SHARE MODE"))
            acquired.set()
            release.wait(timeout=12)

    holder = threading.Thread(target=hold, name="k-07-lock-holder", daemon=True)
    holder.start()
    try:
        assert acquired.wait(timeout=30), "the competing session never took its lock"
        started = time.monotonic()
        with pytest.raises(OperationalError) as refusal:
            command.upgrade(alembic_config, _REVISION)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        holder.join(timeout=30)

    assert getattr(refusal.value.orig, "sqlstate", None) == _LOCK_TIMEOUT_SQLSTATE, refusal.value
    assert 1.5 <= elapsed < 12, f"the bound fired after {elapsed:.1f}s"
    assert _current_revision(engine) == _PREVIOUS_REVISION
    assert _column(engine, _LIVE) is None


def _wait_until_a_backend_waits_on_a_lock(engine: Engine, *, timeout: float) -> bool:
    """Poll `pg_stat_activity` until some other backend is waiting on a heavyweight lock."""
    deadline = time.monotonic() + timeout
    # AUTOCOMMIT: pg_stat_activity is cached for the length of a transaction, so a polling
    # transaction would keep seeing the first snapshot.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        while time.monotonic() < deadline:
            if connection.execute(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
                    " AND pid <> pg_backend_pid() AND datname = current_database()"
                )
            ).scalar_one():
                return True
            time.sleep(0.05)
    return False


@pytest.mark.parametrize("table", [_LIVE, _SNAPSHOT])
def test_k_07_a_row_committed_while_the_downgrade_waits_is_seen_by_the_guard(
    engine: Engine, alembic_config: Config, table: str
) -> None:
    """K-07 (QA, SC-5-08) - the guard's `ACCESS EXCLUSIVE` locks are what close the window between
    the count and the drop. A writer inserts a `day` catalogue row and holds its transaction open
    until the downgrade is seen waiting on a lock (polled, within the 3-second bound). With the
    locks taken first, the downgrade queues behind the writer, then counts the committed row and
    refuses. Without them the count runs at once, sees no such row (it is uncommitted), and the drop
    that follows waits for the commit and then silently discards the unit of a row nobody checked.
    Once for each table. Mutations: the two `LOCK TABLE` statements removed, and the snapshot's
    alone removed, survived every earlier test, which had no writer in flight."""
    dimensions, scenario_id = _committed_plan(engine)
    # The revision the schema is at when the downgrade starts — `_REVISION` until SC-5-04 put a
    # newer migration on top of it: the refusal must leave the schema exactly where it was.
    revision_before = _current_revision(engine)
    inserted, failures, waited = threading.Event(), [], []

    def write_slowly() -> None:
        try:
            with Session(bind=engine, expire_on_commit=False, future=True) as session:
                if table == _LIVE:
                    make_rate(session, dimensions, effective_from=date(2026, 1, 1),
                              default_cost_rate=_ROW_MARKER, cost_rate_unit="day")
                else:
                    session.add(ApprovedSnapshotCatalogDefaultRate(
                        id=uuid.uuid4(), scenario_id=scenario_id, source_rate_id=uuid.uuid4(),
                        source_role_id=dimensions.role_id,
                        source_seniority_id=dimensions.seniority_id,
                        source_location_id=dimensions.location_id,
                        source_engagement_type_id=dimensions.engagement_type_id,
                        source_vendor_id=None, default_cost_rate=_ROW_MARKER,
                        default_selling_rate=Decimal("200.0000"), currency="PLN", unit="hour",
                        effective_from=date(2026, 1, 1), effective_to=None,
                        surcharge_percent=Decimal("0"), includes_surcharge=False,
                        cost_rate_unit="day",
                    ))
                    session.flush()
                inserted.set()
                # Commit only once the downgrade is provably queued behind this transaction (a
                # backend waiting on a heavyweight lock), not after a fixed sleep.
                waited.append(_wait_until_a_backend_waits_on_a_lock(engine, timeout=10))
                session.commit()
        except Exception as error:  # noqa: BLE001 - reported by the assertion below
            failures.append(error)
            inserted.set()

    writer = threading.Thread(target=write_slowly, name="k-07-slow-writer", daemon=True)
    writer.start()
    try:
        assert inserted.wait(timeout=30), "the writer never inserted its row"
        with pytest.raises(RuntimeError, match="Refusing to downgrade"):
            command.downgrade(alembic_config, _PREVIOUS_REVISION)
    finally:
        writer.join(timeout=30)

    assert not failures, failures
    assert waited == [True], (
        "the downgrade never queued behind the writer, so the test proved nothing about the "
        "guard's locks"
    )
    assert _current_revision(engine) == revision_before
    assert _column(engine, _LIVE) is not None
