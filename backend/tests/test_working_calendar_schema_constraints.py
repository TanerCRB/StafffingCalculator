"""SC-3-02, K-03 — what the calendar schema refuses, whoever is writing (F-05).

Every test here writes through the ORM or through raw SQL, with no request schema anywhere in the
path — and in this task there is not even one to bypass: SC-3-02 ships **no** write endpoint for a
calendar (ADR-0007, addendum 2026-09-22, point 3). A rule that lived in Pydantic would therefore be
a rule nothing in this system enforces at all.

Real PostgreSQL, real migration (ADR-0001, `tests/conftest.py`): a unique constraint and the way two
connections race for it cannot be proven against a stand-in that does not implement them.

The drift guards at the end are the R-02 mechanism, one table over: the SQL expressions are spelled
both in `app.models.catalog` and in the migration, `alembic upgrade` never reads the model, and only
the migrated database can arbitrate between the two copies.
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
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.catalog import (
    DIMENSION_NAME_KEY_EXPRESSION,
    WEEK_PATTERN_EXPRESSION,
    WorkingCalendarDayKind,
)
from tests.conftest import (
    BACKEND_ROOT,
    MONDAY_TO_SATURDAY,
    make_calendar_day,
    make_working_calendar,
)

CHRISTMAS = date(2026, 12, 25)

Listener = Any

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "f3a1d0c58b27_create_working_calendars_absences_and_the_approval_snapshot.py"
)


# --- K-03: one day of one calendar cannot be named twice -----------------------------------------


def test_k_03_one_day_of_one_calendar_cannot_be_named_twice_even_from_two_connections(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-03 — `UNIQUE (calendar_id, day)`, in a single connection **and** in a race between two.

    Why it matters at all: two rows naming one day are two answers to "is this a working day?", and
    nothing downstream could choose between them. `app.data.working_calendar.basis_of` builds a
    mapping keyed by day, so without this constraint it would silently keep whichever row the
    database happened to return last — a capacity that changes between two identical reads.

    The race is what makes this a claim about the *database*. A Python "does a row for this day
    already exist?" pre-check passes in both connections when they ask at the same moment, and the
    second `INSERT` then lands; that mutation has survived delivered tests three times in this
    repository (SC-1-02 ×2, SC-2-01), which is why the interleaving is mandatory rather than
    thorough. The competitor commits on a separate connection in the window immediately before our
    own `INSERT` runs.

    Asserted on the table as well as on the exception: one row survives, and it is the
    competitor's — recognisable because it declares the day `WORKING` while ours declared it
    `NON_WORKING`.

    `committing_client` is requested although no request is sent through it: rows committed here
    outlive the test, and that fixture's teardown is the one place that empties these tables.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        calendar = make_working_calendar(
            setup, name="Poland 2026", standard_hours_per_day=Decimal("7.50")
        )
        calendar_id = calendar.id
        setup.commit()

    fired: list[str] = []

    def interleave(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or "insert into working_calendar_day" not in statement.lstrip().lower():
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(
                sa.text(
                    "INSERT INTO working_calendar_day (id, calendar_id, day, kind)"
                    " VALUES (:id, :calendar_id, :day, 'working')"
                ),
                {"id": uuid.uuid4(), "calendar_id": calendar_id, "day": CHRISTMAS},
            )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        with Session(bind=engine, expire_on_commit=False, future=True) as writer:
            with pytest.raises(IntegrityError) as error:
                writer.execute(
                    sa.text(
                        "INSERT INTO working_calendar_day (id, calendar_id, day, kind)"
                        " VALUES (:id, :calendar_id, :day, 'non_working')"
                    ),
                    {"id": uuid.uuid4(), "calendar_id": calendar_id, "day": CHRISTMAS},
                )
                writer.commit()
            writer.rollback()
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the competing write never ran — nothing here is about a race"
    assert "uq_working_calendar_day_calendar_id_day" in str(error.value)
    with engine.connect() as connection:
        surviving = (
            connection.execute(
                sa.text(
                    "SELECT kind FROM working_calendar_day WHERE calendar_id = :id AND day = :day"
                ),
                {"id": calendar_id, "day": CHRISTMAS},
            )
            .scalars()
            .all()
        )
    assert surviving == ["working"], (
        "two rows for one day of one calendar survived the race — the guard is not in the database"
    )


def test_k_03_the_same_day_in_another_calendar_is_accepted(db_session: Session) -> None:
    """K-03's contrast — the constraint is per calendar, not per day.

    Without it this criterion would be satisfied by a schema that refused *every* second row, which
    would make a second calendar unable to declare the same public holiday as the first — i.e. the
    normal case for any organisation working in more than one country.

    The single-connection refusal is asserted in the same test, so "the same day in another
    calendar" and "the same day in the same calendar" are compared side by side rather than in two
    files nobody reads together.
    """
    first = make_working_calendar(
        db_session, name="Poland 2026", standard_hours_per_day=Decimal("7.50")
    )
    second = make_working_calendar(
        db_session, name="Portugal 2026", standard_hours_per_day=Decimal("7.00")
    )

    make_calendar_day(db_session, first, day=CHRISTMAS, kind=WorkingCalendarDayKind.NON_WORKING)
    make_calendar_day(db_session, second, day=CHRISTMAS, kind=WorkingCalendarDayKind.NON_WORKING)

    assert (
        db_session.execute(
            sa.text("SELECT count(*) FROM working_calendar_day WHERE day = :day"),
            {"day": CHRISTMAS},
        ).scalar_one()
        == 2
    )

    with pytest.raises(IntegrityError) as error:
        make_calendar_day(
            db_session, first, day=CHRISTMAS, kind=WorkingCalendarDayKind.WORKING
        )
    assert "uq_working_calendar_day_calendar_id_day" in str(error.value)
    db_session.rollback()


# --- the rest of what the calendar schema refuses ------------------------------------------------


@pytest.mark.parametrize("pattern", ["111110", "1111102", "monday", ""])
def test_the_database_refuses_a_week_pattern_that_is_not_seven_flags(
    db_session: Session, pattern: str
) -> None:
    """The week pattern is seven characters of `0`/`1` — enforced by the CHECK constraint.

    Not an acceptance criterion on its own: it is what lets `CalendarBasis.is_working_day` index
    into the string without a bounds check, and what stops a six-character pattern from shifting
    every weekday by one as the year goes on. Four spellings, because an implementation checking
    only the length, or only the alphabet, passes a test that uses one: too short, right length with
    a `2` in it, right length and not digits at all, and empty.

    A pattern that is too **long** is refused one layer earlier, by `varchar(7)` — see the test
    below. That is a different mechanism and gets a different test rather than a fifth parameter
    here, because a parameter that passes for another reason is a parameter that proves nothing
    about this constraint.
    """
    with pytest.raises(IntegrityError) as error:
        make_working_calendar(
            db_session,
            name=f"Broken {pattern!r}",
            standard_hours_per_day=Decimal("7.50"),
            week_pattern=pattern,
        )
    assert "week_pattern_is_seven_flags" in str(error.value)
    db_session.rollback()


def test_the_database_refuses_a_week_pattern_longer_than_a_week(db_session: Session) -> None:
    """An eight-day week is refused by the column's own width, before the CHECK ever runs.

    Asserted separately and named for what it is: `varchar(7)` raises `22001`
    (`string_data_right_truncation`), not `23514`. Both refusals are wanted — the point of this
    test is that the over-long case is covered by *something*, and that the something is not
    silently a truncation to seven characters, which would turn `'11111000'` into a valid
    Monday-to-Friday week nobody typed.
    """
    with pytest.raises(sa.exc.DataError) as error:
        make_working_calendar(
            db_session,
            name="Eight-day week",
            standard_hours_per_day=Decimal("7.50"),
            week_pattern="11111000",
        )
    assert "character varying(7)" in str(error.value)
    db_session.rollback()


@pytest.mark.parametrize("hours", [Decimal("0.00"), Decimal("-1.00")])
def test_the_database_refuses_a_calendar_whose_standard_day_is_not_positive(
    db_session: Session, hours: Decimal
) -> None:
    """A zero-hour working day would make every capacity derived from it zero, silently.

    `0.00` is the interesting case: it passes every "is it a number" validation and produces a
    plausible-looking answer for every position in that location — the failure mode criterion K-23
    refuses for a *missing* calendar, arriving through a calendar that exists.
    """
    with pytest.raises(IntegrityError) as error:
        make_working_calendar(
            db_session, name=f"Zero {hours}", standard_hours_per_day=hours
        )
    assert "standard_hours_per_day_positive" in str(error.value)
    db_session.rollback()


def test_two_calendars_whose_names_differ_only_in_case_or_spacing_are_the_same_name(
    db_session: Session,
) -> None:
    """The sixth dictionary inherits the normalised-name uniqueness of the other five (R-04).

    Two "Poland 2026"s would split the capacity of one organisation exactly the way five "Senior"s
    split its rate table: every location points at one of them, no screen can tell them apart, and
    the two drift as holidays are added to one and not the other.

    The contrast is in the same test: a genuinely different name is accepted, so the index is shown
    to refuse a duplicate rather than every second calendar.
    """
    make_working_calendar(db_session, name="Poland 2026", standard_hours_per_day=Decimal("7.50"))

    with pytest.raises(IntegrityError) as error:
        make_working_calendar(
            db_session, name="  poland   2026 ", standard_hours_per_day=Decimal("7.00")
        )
    assert "uq_working_calendar_name_normalized" in str(error.value)
    db_session.rollback()

    make_working_calendar(db_session, name="Poland 2026", standard_hours_per_day=Decimal("7.50"))
    make_working_calendar(db_session, name="Portugal 2026", standard_hours_per_day=Decimal("7.00"))


def test_a_locations_calendar_id_may_be_null_and_may_not_name_a_calendar_that_does_not_exist(
    db_session: Session,
) -> None:
    """The two halves of `catalog_locations.calendar_id` (ADR-0008, addendum 2026-09-22, point 7).

    `NULL` is legal — it is the named "no calendar" state K-23 is about, and it is the state every
    location created before this migration is in. A *made-up* id is not: the foreign key refuses it,
    so "this location follows a calendar nobody created" cannot be a row.

    Both in one test, because either alone is satisfiable by the wrong schema: a `NOT NULL` column
    passes the second, and a column with no foreign key passes the first.
    """
    from app.models import CatalogLocation

    unattached = CatalogLocation(id=uuid.uuid4(), name="Remote", calendar_id=None)
    db_session.add(unattached)
    db_session.flush()
    assert unattached.calendar_id is None

    dangling = CatalogLocation(id=uuid.uuid4(), name="Atlantis", calendar_id=uuid.uuid4())
    db_session.add(dangling)
    with pytest.raises(IntegrityError) as error:
        db_session.flush()
    assert "fk_catalog_locations_calendar_id" in str(error.value)
    db_session.rollback()


def test_no_foreign_key_of_the_calendar_tables_cascades_a_delete(db_session: Session) -> None:
    """Every foreign key of the SC-3-02 tables is `NO ACTION` — the QA check of SC-3-01, extended.

    The sharpest consequence is the same one one table over: `ON DELETE CASCADE` towards `scenarios`
    would be a second, unguarded way for the rows of an `approved` scenario — its absences and its
    snapshot — to disappear, because the write guard covers `INSERT`/`DELETE` and a cascade is
    neither. Nothing deletes a scenario today, so the harm lands in the *next* task that does, and
    by then nothing behavioural would object. Hence a schema assertion.

    `confdeltype` is PostgreSQL's own record of the rule: `'a'` is `NO ACTION`, `'c'` is `CASCADE`,
    `'n'` is `SET NULL`.
    """
    rules = dict(
        db_session.execute(
            sa.text(
                "SELECT conname, confdeltype FROM pg_constraint"
                " WHERE contype = 'f' AND conrelid IN ("
                " 'working_calendar_day'::regclass,"
                " 'staffing_position_absence'::regclass,"
                " 'approved_snapshot_working_calendar'::regclass,"
                " 'approved_snapshot_working_calendar_day'::regclass,"
                " 'approved_snapshot_absence_type'::regclass,"
                " 'catalog_locations'::regclass)"
            )
        ).all()
    )

    assert len(rules) == 7, f"expected seven foreign keys, found: {sorted(rules)}"
    cascading = sorted(name for name, rule in rules.items() if rule != "a")
    assert cascading == [], (
        f"a SC-3-02 foreign key no longer refuses to orphan a row: {cascading}. A cascade towards "
        "the scenario deletes the absences or the snapshot of an approved scenario without passing "
        "the write guard of K-13 (ADR-0004)."
    )


# --- the model and the migration must keep describing the same schema ---------------------------


def _migration_module() -> ModuleType:
    """Load the migration file as a module, without going through Alembic's script directory."""
    specification = importlib.util.spec_from_file_location("sc_3_02_migration", MIGRATION_PATH)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_the_model_and_the_migration_agree_on_every_sql_expression(db_session: Session) -> None:
    """The drift guard R-02, applied to this task's two hand-written SQL expressions.

    Each is spelled twice on purpose — a migration must keep describing the schema it produced even
    after the model moves on — and `alembic upgrade` never reads the model, so editing one copy
    alone breaks nothing any other test would notice.

    Three assertions, because none of them implies the others: the two strings are compared, and
    then the *migrated database* is asked what it really has, so a migration that was edited but
    never applied fails here too.
    """
    migration = _migration_module()
    assert migration._WEEK_PATTERN_EXPRESSION == WEEK_PATTERN_EXPRESSION
    assert migration._DIMENSION_NAME_KEY_EXPRESSION == DIMENSION_NAME_KEY_EXPRESSION

    definition = db_session.execute(
        sa.text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'ck_working_calendar_week_pattern_is_seven_flags'"
        )
    ).scalar_one()
    assert "week_pattern" in definition
    assert "[01]" in definition

    index = db_session.execute(
        sa.text(
            "SELECT indexdef FROM pg_indexes"
            " WHERE indexname = 'uq_working_calendar_name_normalized'"
        )
    ).scalar_one()
    assert "regexp_replace" in index
    assert "btrim" in index


def test_the_week_pattern_column_is_wide_enough_for_a_six_day_week(db_session: Session) -> None:
    """A seven-character column holds a seven-character pattern — checked against the database.

    Trivial-looking, and it is here because the failure it catches is not: `String(7)` in the model
    and `String(length=5)` in the migration would make `'1111110'` a write error nothing else in
    this suite reaches, since `make_working_calendar` defaults to a five-day pattern that is still
    seven characters long. The Monday-to-Saturday pattern is written through, so the assertion is
    behavioural and not only a look at `information_schema`.
    """
    calendar = make_working_calendar(
        db_session,
        name="Six-day",
        standard_hours_per_day=Decimal("7.00"),
        week_pattern=MONDAY_TO_SATURDAY,
    )
    db_session.expire_all()
    stored = db_session.execute(
        sa.text("SELECT week_pattern FROM working_calendar WHERE id = :id"), {"id": calendar.id}
    ).scalar_one()
    assert stored == MONDAY_TO_SATURDAY


# --- the migration is reversible, and the enum type goes with it ---------------------------------


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    """A `Config` of this file's own, mirroring the one `tests/conftest.py`'s `engine` builds.

    A fresh `Config` and a fresh connection rather than a shortcut through the fixture's:
    `migrations/env.py` opens its own connection for every `command.upgrade`/`downgrade`, so what is
    exercised here is a second, real client of the database. The precedent is
    `tests/test_catalog_migration_reversibility.py`, and the harness is deliberately the same shape.
    """
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


_REVISION = "f3a1d0c58b27"
"""The migration under test — the one whose `downgrade()` this file exercises.

**It is not compared with `head`**, and that is the correction SC-3-03 made (decision of a human,
2026-09-22). It used to be, and the assertion was true only for as long as this was the newest
migration in the repository: the first migration added on top of it — `a7c2e5f81b94` — made the test
fail while nothing about *this* migration had changed. The revision the database starts at is now
read from the database (`before` below), exactly as
`tests/test_catalog_migration_reversibility.py` reads it, and every other assertion of the test is
untouched."""

_PREVIOUS_REVISION = "d5e94a1c6b73"
"""What `f3a1d0c58b27` revises — the target of `command.downgrade`. Spelled as a revision id rather
than as `"-1"`, for the reason the reversibility test of SC-2-03 records: a relative step is a claim
about *how many* migrations separate `head` from here, and that claim goes stale the next time one
is added on top."""


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()


def _object_exists(engine: Engine, statement: str) -> bool:
    with engine.connect() as connection:
        return connection.execute(sa.text(statement)).scalar_one()


def test_the_migration_downgrades_and_upgrades_again_including_the_enum_type(
    engine: Engine, alembic_config: Config
) -> None:
    """The `downgrade()` of this migration really runs, and leaves nothing behind.

    Not an acceptance criterion — the repository has one precedent for testing an actual
    `alembic downgrade` (SC-2-03, where a `downgrade()` that passed review turned out to lose rows),
    and this migration has the specific hazard that precedent exists for: it creates a **type**, not
    only tables. `CREATE TYPE` is not undone by `DROP TABLE`, so a `downgrade()` that dropped the
    six tables and forgot the enum would look clean and then fail the *next* `upgrade` with
    "type working_calendar_day_kind already exists" — at which point the database is stuck at the
    previous revision with no obvious reason.

    Both directions are asserted, and the second is the one that catches that: the type is gone
    after the downgrade, and the upgrade that follows succeeds rather than tripping over a leftover.

    `command.upgrade(alembic_config, "head")` runs in `finally` whatever happens: `engine` is
    session-scoped and every other test in this suite reads the schema this one leaves behind.

    The revision the database starts at is **read**, not asserted against a literal (see
    `_REVISION`): what this test is about is that `downgrade()` undoes what `upgrade()` did — the
    tables, the column and the enum type — and not how many migrations have been added since.
    """
    type_exists = (
        "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'working_calendar_day_kind')"
    )
    table_exists = (
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
        " WHERE table_name = 'staffing_position_absence')"
    )
    column_exists = (
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
        " WHERE table_name = 'catalog_locations' AND column_name = 'calendar_id')"
    )

    before = _current_revision(engine)
    assert _object_exists(engine, type_exists)
    assert _object_exists(engine, table_exists)
    assert _object_exists(engine, column_exists)

    try:
        command.downgrade(alembic_config, _PREVIOUS_REVISION)

        assert _current_revision(engine) == _PREVIOUS_REVISION
        assert not _object_exists(engine, table_exists)
        assert not _object_exists(engine, column_exists)
        assert not _object_exists(engine, type_exists), (
            "the downgrade dropped the tables and left the enum type behind — the next upgrade "
            "fails with 'type already exists' and the database is stuck at the previous revision"
        )
    finally:
        command.upgrade(alembic_config, "head")

    assert _current_revision(engine) == before
    assert _object_exists(engine, type_exists)
    assert _object_exists(engine, table_exists)
    assert _object_exists(engine, column_exists)
