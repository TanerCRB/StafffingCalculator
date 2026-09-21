"""Reversibility of migration `c1a4f7b92e05` (SC-2-03) — R-01 (reviewer, 2026-09-21).

The repository's first test of an actual `alembic downgrade`. Every other migration's reversibility
is, at most, a comment ("caught by reading `alembic downgrade --sql`", `4f0a9c1b7d62`) — read,
never run. This file runs one, because `c1a4f7b92e05` is the first migration where "restore the
old shape" and "keep the data's meaning" can disagree: dropping `vendor_id` does not delete a
vendor-priced row, it silently reinterprets it as an internal one (K-04) — the concrete result the
reviewer produced empirically against a real PostgreSQL and that a passing `downgrade()` used to
reach without warning.

Real PostgreSQL, real migration (ADR-0001, `tests/conftest.py`): the guard under test lives inside
`downgrade()` and runs a `SELECT` against the actual table, so it cannot be proven against a
stand-in. `command.downgrade`/`command.upgrade` are run directly against `database_url`, on a
connection Alembic opens for itself (`migrations/env.py`) — a second, independent connection to the
same database the session-scoped `engine` fixture already migrated to `head`.

Both tests leave the schema at `head` when they finish, because `engine` is session-scoped and every
other test in the suite depends on the shape it left behind: the refusing test never changes the
schema (that is what "refuses" means), and the succeeding test restores it in a `finally` block that
runs whether the assertions above it pass or not.
"""

import os
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tests.conftest import BACKEND_ROOT, make_dimension_tuple, make_rate, make_vendor

_REVISION = "c1a4f7b92e05"
"""The migration under test. Offline (`--sql`) runs need an explicit `from:to` range — Alembic has
no database to ask where it currently is — so both halves of the R-03 test below name it."""

_PREVIOUS_REVISION = "b6d2f74c3e18"
"""What `c1a4f7b92e05` revises — the target of `command.downgrade(config, _PREVIOUS_REVISION)`.
Spelled as the destination revision rather than as the relative `"-1"`: this suite's `engine`
fixture always starts the test run at `head`, but a relative step is a claim about *how many*
migrations separate `head` from here, which is exactly the kind of fact that silently goes stale the
next time a migration is added on top. Naming the revision id makes that impossible."""


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    """The same `Config` the `engine` fixture builds (`tests/conftest.py`), independent of it.

    A fresh `Config`/connection rather than reusing `engine`: `migrations/env.py` opens its own
    connection for every `command.upgrade`/`command.downgrade` call, so the object under test here
    is a second, real client of the database — not a shortcut through the fixture's connection.
    """
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()


def _vendor_id_column_exists(engine: Engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'catalog_default_rates' AND column_name = 'vendor_id')"
            )
        ).scalar_one()


def _catalog_vendors_table_exists(engine: Engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
                " WHERE table_name = 'catalog_vendors')"
            )
        ).scalar_one()


def _rate_row_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(
            sa.text("SELECT count(*) FROM catalog_default_rates")
        ).scalar_one()


def _delete_committed_catalog_rows(engine: Engine) -> None:
    """Undo a committed write this file made directly against `engine`, outside `db_session`.

    Mirrors `committing_client`'s teardown order (`tests/conftest.py`): rates before the dimension
    entries and the vendor they reference, or the foreign keys with no `ON DELETE` action refuse the
    delete.
    """
    with engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM catalog_default_rates"))
        connection.execute(sa.text("DELETE FROM catalog_vendors"))
        connection.execute(sa.text("DELETE FROM catalog_roles"))
        connection.execute(sa.text("DELETE FROM catalog_seniorities"))
        connection.execute(sa.text("DELETE FROM catalog_locations"))
        connection.execute(sa.text("DELETE FROM catalog_engagement_types"))


def test_downgrade_refuses_when_a_vendor_priced_row_exists(
    engine: Engine, alembic_config: Config
) -> None:
    """R-01: a row priced only by a subcontractor must not survive `downgrade()` renamed internal.

    Committed, not flushed inside `db_session`'s rolled-back transaction: `alembic downgrade` opens
    its own connection (`migrations/env.py`), so the guard it runs has to see the row through a
    second, independent client of the database — a row visible only inside this test's transaction
    would prove nothing about what the guard actually queries.

    This tuple/window collides with nothing (no internal rate and no second vendor rate share it),
    which is exactly the case the reviewer identified as the silent one: the four-column constraint
    restore in the old `downgrade()` had nothing to refuse it on. The guard added for R-01 has to
    catch it anyway, on `vendor_id IS NOT NULL` alone.
    """
    session = Session(bind=engine, expire_on_commit=False, future=True)
    try:
        dimensions = make_dimension_tuple(session, suffix=" (R-01 downgrade guard)")
        vendor = make_vendor(session, name="Vendor R-01 downgrade guard")
        make_rate(
            session,
            dimensions,
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 6, 30),
            vendor_id=vendor.id,
            default_cost_rate=Decimal("100.0000"),
            default_selling_rate=Decimal("150.0000"),
        )
        session.commit()
    finally:
        session.close()

    before = _current_revision(engine)
    try:
        with pytest.raises(RuntimeError, match=r"1 row\(s\)"):
            command.downgrade(alembic_config, _PREVIOUS_REVISION)

        # The refusal happened before any schema change: still at `head`, `vendor_id` and
        # `catalog_vendors` both still there. A downgrade that raised *after* altering the schema
        # would be a partial, half-migrated database — worse than either outcome on its own.
        assert _current_revision(engine) == before
        assert _vendor_id_column_exists(engine)
        assert _catalog_vendors_table_exists(engine)
    finally:
        _delete_committed_catalog_rows(engine)


def test_downgrade_succeeds_when_no_row_has_a_vendor(
    engine: Engine, alembic_config: Config
) -> None:
    """Contrast to the refusal above: an all-internal table downgrades cleanly.

    Proves the guard added for R-01 is a guard, not a blanket refusal — it is conditioned on
    `vendor_id IS NOT NULL` existing, not on the migration itself. With no such row, `downgrade()`
    reaches the same restore the four-column constraint always performed: `vendor_id` and
    `catalog_vendors` are gone and the table is back to `7b3d5c81e40a`'s shape.

    `command.upgrade(alembic_config, "head")` runs in `finally` regardless of the outcome above:
    `engine` is session-scoped, so every later test in the suite depends on the schema this test
    leaves behind, and a failed assertion must not strand the database one migration short of
    `head`.
    """
    assert _vendor_id_column_exists(engine)  # sanity: still at `head` when this test starts
    before = _current_revision(engine)
    assert before != _PREVIOUS_REVISION

    try:
        command.downgrade(alembic_config, _PREVIOUS_REVISION)

        assert _current_revision(engine) == _PREVIOUS_REVISION
        assert not _vendor_id_column_exists(engine)
        assert not _catalog_vendors_table_exists(engine)
    finally:
        command.upgrade(alembic_config, "head")
        assert _current_revision(engine) == before
        assert _vendor_id_column_exists(engine)
        assert _catalog_vendors_table_exists(engine)


_COMPETITOR_LOCK_TIMEOUT = "2s"
"""How long the competing writer below waits for the table lock before giving up.

Every passing run of `test_r_01_...` pays this in full, because the lock it is waiting for is held
until the migration's transaction commits and is therefore never granted — so the number is a
trade between suite time and the margin against a false "it was blocked" on a slow machine. Two
seconds: the unblocked case (the mutation) needs no wait at all, so the only way to read "blocked"
wrongly is a database that takes two seconds to execute one small `INSERT`."""


def _committing_a_vendor_rate_before_the_schema_changes(
    engine: Engine, outcome: list[str], dimensions: Any, vendor_id: uuid.UUID
) -> Any:
    """A hook that tries to commit a vendor-priced rate after the guard's count, before the drop.

    This is the exact window `LOCK TABLE ... IN ACCESS EXCLUSIVE MODE` exists to close (R-01): the
    guard has counted zero vendor rows a moment ago, and the schema change that destroys the
    `vendor_id` column has not run yet. Fires on the first `ALTER TABLE ... DROP CONSTRAINT` of
    `downgrade()`, which is the first statement after the count.

    The competitor's own outcome is what the test reads, so the exception is caught here rather than
    allowed to abort the migration mid-way: it records `"blocked"` when PostgreSQL refuses to grant
    the lock within `_COMPETITOR_LOCK_TIMEOUT`, and `"committed"` when the row went in.
    """

    def interleave(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        normalised = " ".join(statement.lower().split())
        first_schema_change = "alter table catalog_default_rates drop constraint"
        if outcome or not normalised.startswith(first_schema_change):
            return
        outcome.append("fired")
        try:
            with engine.begin() as competitor:
                competitor.execute(
                    sa.text(f"SET LOCAL lock_timeout = '{_COMPETITOR_LOCK_TIMEOUT}'")
                )
                competitor.execute(
                    sa.text(
                        "INSERT INTO catalog_default_rates (id, role_id, seniority_id, location_id,"
                        " engagement_type_id, vendor_id, default_cost_rate, default_selling_rate,"
                        " currency, unit, effective_from, effective_to) VALUES (:id, :role,"
                        " :seniority, :location, :engagement, :vendor, 999.0000, 999.0000, 'EUR',"
                        " 'hour', :start, :end)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "role": dimensions.role_id,
                        "seniority": dimensions.seniority_id,
                        "location": dimensions.location_id,
                        "engagement": dimensions.engagement_type_id,
                        "vendor": vendor_id,
                        "start": date(2026, 1, 1),
                        "end": date(2026, 6, 30),
                    },
                )
        except OperationalError:
            outcome.append("blocked")
        else:
            outcome.append("committed")

    return interleave


def test_r_01_a_vendor_rate_written_after_the_guards_count_cannot_reach_the_column_drop(
    engine: Engine, alembic_config: Config
) -> None:
    """R-01: the guard is check-then-act, and the table lock is what makes the check final.

    Added by QA after a surviving mutation: deleting the `LOCK TABLE catalog_default_rates IN ACCESS
    EXCLUSIVE MODE` line from `downgrade()` left all 305 tests green, because every other test in
    this file runs the migration with nothing else touching the database — and "nothing can be
    written between the count and the drop" is not a claim a single-connection run can make. The
    guard itself is proven twice above; the lock is a separate mechanism and this is its test.

    The interleaving is the repository's existing one (`test_k_05_an_overlapping_window_committed_by
    _a_competitor_mid_write_is_still_refused`): a `before_cursor_execute` hook on a second
    connection, not a thread, so the two statements meet in a fixed order rather than a hoped-for
    one. It fires on the first statement *after* the guard's count — the point at which the count
    has already been taken as final and the `vendor_id` column is still there.

    What each outcome means:

    - **blocked** — the competitor waited for a lock the migration holds until it commits, and gave
      up. Its row never existed, so the `0` the guard counted was still true when the column was
      dropped;
    - **committed** (the mutation) — the row landed after the count, and the `drop_column` two
      statements later turned a subcontractor's price into the organisation's own, indistinguishable
      from an internal rate. This is K-04's forbidden reinterpretation, reached through a downgrade
      that reported success.

    Asserted on the table rather than on the hook alone: after the downgrade,
    `catalog_default_rates` must hold no row at all. A row there is the renamed vendor rate,
    whatever the hook thought happened.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        dimensions = make_dimension_tuple(setup, suffix=" (R-01 lock)")
        vendor = make_vendor(setup, name="Vendor R-01 lock")
        setup.commit()
        vendor_id = vendor.id

    before = _current_revision(engine)
    outcome: list[str] = []
    interleave = _committing_a_vendor_rate_before_the_schema_changes(
        engine, outcome, dimensions, vendor_id
    )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        command.downgrade(alembic_config, _PREVIOUS_REVISION)
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)
        command.upgrade(alembic_config, "head")
        surviving = _rate_row_count(engine)
        _delete_committed_catalog_rows(engine)

    assert "fired" in outcome, "the competing write never ran — nothing below is about a race"
    assert outcome[-1] == "blocked", (
        "a vendor-priced rate committed between the guard's count and the column drop — the "
        "downgrade counted zero vendor rows and then dropped the column out from under one"
    )
    assert _current_revision(engine) == before
    assert surviving == 0, (
        "a rate row survived the downgrade: it is the competitor's vendor-priced row, now "
        "indistinguishable from an internal rate (K-04)"
    )


def test_r_03_an_offline_downgrade_refuses_in_words_instead_of_crashing(
    engine: Engine, alembic_config: Config
) -> None:
    """R-03: `alembic downgrade --sql` says why it cannot run, rather than raising `AttributeError`.

    Added by QA after a surviving mutation: deleting the `context.is_offline_mode()` guard from
    `downgrade()` left all 303 tests green, because no test in this repository had ever run a
    migration offline — the reviewer's evidence for the guard was a manual `--sql` run. What the
    mutation produces instead, confirmed by running it: `AttributeError: 'NoneType' object has no
    attribute 'scalar_one'`, because `op.get_bind()` is `None` when there is no connection. Reading
    a generated `--sql` script is this repository's documented review practice for migrations
    (`4f0a9c1b7d62`), so the person who hits this is a reviewer doing the thing the process asks
    for, and the difference between the two failures is whether they can tell a broken migration
    from one that simply cannot be rendered offline.

    Offline mode executes nothing and opens no connection (`migrations/env.py`,
    `run_migrations_offline`), so neither call here touches the database the `engine` fixture
    migrated — `engine` is taken as a parameter only to assert that afterwards.

    **The contrast is the offline `upgrade` in the same test**: the same migration, the same offline
    mode, the opposite direction, and it renders its SQL without raising. Without it, a guard that
    refused every offline run of this file — or an `env.py` that could not do offline mode at all —
    would satisfy the refusal above just as well.
    """
    before = _current_revision(engine)

    with pytest.raises(RuntimeError, match="offline"):
        command.downgrade(alembic_config, f"{_REVISION}:{_PREVIOUS_REVISION}", sql=True)

    command.upgrade(alembic_config, f"{_PREVIOUS_REVISION}:{_REVISION}", sql=True)

    assert _current_revision(engine) == before, "an offline run must not have touched the database"
    assert _vendor_id_column_exists(engine)
