"""R-06 (design review, 2026-09-22): migration `d5e94a1c6b73` must not queue behind a held lock.

`ALTER TABLE ... ADD COLUMN` takes `ACCESS EXCLUSIVE`, and a lock request queues *ahead* of
everything that arrives after it. So an `ALTER` that waits on one long-running catalogue reader
does not merely wait: every later reader of that table waits behind the `ALTER`. Without a
`lock_timeout` the length of that catalogue-wide stall is whatever the other session feels like,
which is what the review named — a cheap, unbounded, organisation-wide outage waiting for the
first persistent environment.

What is proven here is the only half that can be: **that the bound exists and PostgreSQL enforces
it**. The claim "an unbounded wait would hurt" is an operational argument, not a test.

Real PostgreSQL, real migration, real concurrency (ADR-0001, `tests/conftest.py`): `lock_timeout`
is a server behaviour, and a stand-in that did not implement lock queues would prove nothing about
it. The competing session is a second connection in a thread rather than the `before_cursor_execute`
interleaving used in `test_catalog_migration_reversibility.py` — there the two statements had to
meet in a fixed order *inside* one migration; here the competitor must be holding its lock while the
migration runs, which is a state, not a point in time.

Both tests leave the schema at `head`: `engine` is session-scoped and every other test in the suite
reads the shape it left behind.
"""

import importlib.util
import os
import threading
import time
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from tests.conftest import BACKEND_ROOT

_REVISION = "d5e94a1c6b73"
_PREVIOUS_REVISION = "e2c7b04d9a31"
"""What `d5e94a1c6b73` revises. Spelled as a revision id rather than as `"-1"`, for the reason
`test_catalog_migration_reversibility.py` records: a relative step is a claim about how many
migrations separate `head` from here, and that claim goes stale the next time one is added."""

_BLOCKED_TABLE = "catalog_roles"
"""The table the competitor holds. The *first* one `upgrade()` alters, so the migration meets the
held lock before it has changed anything — the case where a timeout is the difference between a
clean "retry later" and a half-applied transaction waiting to be rolled back."""

_LOCK_TIMEOUT_SQLSTATE = "55P03"
"""`lock_not_available`. Asserted instead of the message text: server messages are localisable
(`lc_messages`), the SQLSTATE is not, and "it failed" alone would also be satisfied by a migration
that fell over for an unrelated reason."""


def _load_migration_module() -> ModuleType:
    """Import the migration by path — `migrations/versions` is not a package and a module name
    starting with a digit is reachable by neither `import` nor `importlib.import_module`. Same
    helper as in `test_catalog_schema_constraints.py`, for the same reason."""
    path = (
        Path(BACKEND_ROOT)
        / "migrations"
        / "versions"
        / "d5e94a1c6b73_add_updated_at_to_the_catalog_tables.py"
    )
    specification = importlib.util.spec_from_file_location("sc_2_04_marker_migration", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _timeout_in_seconds(value: str) -> float:
    """`_LOCK_TIMEOUT` as a number the test can wait against.

    Read from the migration rather than repeated here: a second copy of "3s" in this file would let
    somebody raise the timeout to a minute and still see a green test that waits three seconds.
    Only the two units PostgreSQL accepts in this setting that make sense for a DDL guard are
    understood; anything else fails loudly instead of being guessed at.
    """
    if value.endswith("ms"):
        return float(value[:-2]) / 1000
    if value.endswith("s"):
        return float(value[:-1])
    raise AssertionError(
        f"unrecognised lock_timeout format {value!r}: this test needs a unit ('3s', '3000ms') to "
        "know how long the migration is allowed to wait"
    )


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    """The same `Config` the `engine` fixture builds, independent of it — a second, real client of
    the database, on the connection `migrations/env.py` opens for itself."""
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()


def _marker_column_exists(engine: Engine, table: str) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
                " WHERE table_name = :table AND column_name = 'updated_at')"
            ),
            {"table": table},
        ).scalar_one()


def _hold_access_share(
    engine: Engine, acquired: threading.Event, release: threading.Event, safety_cap: float
) -> None:
    """Hold an `ACCESS SHARE` lock on `_BLOCKED_TABLE` until told to let go.

    `ACCESS SHARE` is what an ordinary `SELECT` takes — this is a long `GET /catalog/rates`, not an
    exotic administrative statement, and it conflicts with the `ACCESS EXCLUSIVE` the `ALTER` needs.

    `safety_cap` is what keeps a *failing* run from hanging the suite: if the migration under test
    lost its `SET LOCAL lock_timeout`, it would block here for as long as this thread held on, so
    the hold has an end of its own. After it the migration completes, the `pytest.raises` below
    fails, and the mutation is reported as a failed assertion rather than as a suite that never
    finishes.
    """
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                sa.text(f"LOCK TABLE {_BLOCKED_TABLE} IN ACCESS SHARE MODE")
            )
            acquired.set()
            release.wait(timeout=safety_cap)


def test_r_06_the_upgrade_gives_up_on_a_held_lock_instead_of_queueing_the_catalogue(
    engine: Engine, alembic_config: Config
) -> None:
    """R-06: with a conflicting lock held, the migration fails inside its bound and changes nothing.

    The two outcomes this distinguishes:

    - **fails with `55P03` after about `_LOCK_TIMEOUT`** — the bound is set and enforced. The
      catalogue was blocked for a known number of seconds, the transaction rolled back whole, and
      the database is still at `e2c7b04d9a31`: a deployment to retry, not an incident;
    - **succeeds** (what deleting the `SET LOCAL` produces) — the `ALTER` sat in the lock queue
      until the competitor let go, which here is a controlled 4 × the timeout and in production is
      unbounded. Every reader of `catalog_roles` that arrived meanwhile sat behind it.

    The elapsed time is asserted from both sides on purpose. The upper bound is the discriminating
    one: it separates "gave up on its own" from "waited for the competitor and then worked". The
    lower bound rules out the opposite mistake — a migration that failed instantly for some reason
    of its own would satisfy a "finished quickly" assertion just as well, and would not be evidence
    of a lock timeout at all.
    """
    migration = _load_migration_module()
    lock_timeout = _timeout_in_seconds(migration._LOCK_TIMEOUT)
    hold_safety_cap = 4 * lock_timeout

    before = _current_revision(engine)
    assert before != _PREVIOUS_REVISION, "the suite starts past this revision, not before it"
    assert _marker_column_exists(engine, _BLOCKED_TABLE)

    command.downgrade(alembic_config, _PREVIOUS_REVISION)
    acquired = threading.Event()
    release = threading.Event()
    holder = threading.Thread(
        target=_hold_access_share,
        args=(engine, acquired, release, hold_safety_cap),
        name="r-06-lock-holder",
        daemon=True,
    )
    holder.start()
    try:
        assert acquired.wait(timeout=30), "the competing session never took its lock"
        assert not _marker_column_exists(engine, _BLOCKED_TABLE)

        started = time.monotonic()
        with pytest.raises(OperationalError) as refusal:
            command.upgrade(alembic_config, _REVISION)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        holder.join(timeout=30)
        command.upgrade(alembic_config, "head")

    assert getattr(refusal.value.orig, "sqlstate", None) == _LOCK_TIMEOUT_SQLSTATE, (
        f"the migration failed for some reason other than the lock timeout: {refusal.value}"
    )
    assert elapsed < hold_safety_cap, (
        f"the migration waited {elapsed:.1f}s for a lock bounded at {migration._LOCK_TIMEOUT} — it "
        "queued until the competitor let go, and every reader of the catalogue queued behind it"
    )
    assert elapsed >= lock_timeout / 2, (
        f"the migration gave up after {elapsed:.1f}s, far sooner than its bound of "
        f"{migration._LOCK_TIMEOUT} — that is not a lock timeout firing"
    )
    assert _current_revision(engine) == before
    assert all(_marker_column_exists(engine, table) for table in migration._CATALOG_TABLES)


def test_r_06_both_directions_carry_the_bound_in_the_sql_they_generate(
    engine: Engine, database_url: str
) -> None:
    """R-06: the statement is in the rendered script — upgrade *and* downgrade — and is scoped.

    The test above proves the upgrade's bound by running it. The downgrade takes the same
    `ACCESS EXCLUSIVE` lock (`DROP COLUMN`) and carries the same statement, but proving it the same
    way would mean a second lock-holding run for a claim about the identical mechanism. Reading the
    generated SQL is what `alembic upgrade --sql` is for and is this repository's documented review
    practice for migrations (`4f0a9c1b7d62`); this test is that reading, automated.

    Three things are asserted about each direction, and the third is the one worth naming: the
    restore to `DEFAULT` after the last `ALTER`. `migrations/env.py` runs every migration of a run
    in one transaction, and `SET LOCAL` lasts to the end of that transaction — so without the
    restore this file would quietly impose its bound on every migration that follows it in the same
    `alembic upgrade`, which is exactly the global change R-06 asked not to make.

    Offline mode executes nothing and opens no connection (`migrations/env.py`), so neither render
    touches the database; `engine` is taken only to assert that afterwards.
    """
    migration = _load_migration_module()
    before = _current_revision(engine)

    rendered: dict[str, str] = {}
    for direction, span in (
        ("upgrade", f"{_PREVIOUS_REVISION}:{_REVISION}"),
        ("downgrade", f"{_REVISION}:{_PREVIOUS_REVISION}"),
    ):
        buffer = StringIO()
        config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"), output_buffer=buffer)
        config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
        config.set_main_option("sqlalchemy.url", database_url)
        getattr(command, direction)(config, span, sql=True)
        rendered[direction] = buffer.getvalue().lower()

    setting = f"set local lock_timeout = '{migration._LOCK_TIMEOUT}'".lower()
    for direction, script in rendered.items():
        assert setting in script, f"the {direction} script sets no lock_timeout"
        assert script.index(setting) < script.index("alter table"), (
            f"the {direction} script sets the bound after its first ALTER TABLE — the lock it is "
            "meant to bound has already been requested by then"
        )
        assert script.rindex("alter table") < script.rindex("set local lock_timeout = default"), (
            f"the {direction} script never restores lock_timeout, so its bound leaks into every "
            "migration running after it in the same transaction (migrations/env.py)"
        )

    assert _current_revision(engine) == before, "an offline render must not have touched the db"
