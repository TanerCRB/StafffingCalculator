"""SC-3-03, K-01/K-02/K-04/K-08 — what the budget schema refuses, whoever is writing (F-05).

Every test here writes through the ORM or through raw SQL, with no request schema anywhere in the
path. That is the whole division of labour these criteria rest on: `AbsenceBudgetCreateRequest`
turns a client's mistake into a `422` naming the field, and the constraint in the database is what
actually holds — for a fixture, a seed script, a future import or a second endpoint, none of which
passes through Pydantic.

Real PostgreSQL, real migration (ADR-0001, `tests/conftest.py`): an `EXCLUDE USING gist`, a partial
unique index and the way two connections race for them cannot be proven against a stand-in that
does not implement them.

**The two races are mandatory rather than thorough.** A Python "does an overlapping window exist?"
or "is another type already flagged?" passes in both connections when they ask at the same moment,
and the second write then lands. That mutation has survived delivered tests three times in this
repository (SC-1-02 ×2, SC-2-01), and the single-connection halves of K-01 and K-04 cannot see it —
only the interleaving can.

**One deviation from the criterion text, named here rather than smoothed over.** K-01 asks for the
overlapping contrast to be "one day earlier". On this table that shape does not exist: the same
criteria set requires the window to be aligned to whole months
(`ck_absence_budget_window_aligned_to_whole_months`), so a window starting one day earlier is
refused by *that* constraint and would prove nothing about the `EXCLUDE`. The smallest overlap this
table can express is one month, and that is what the test uses. The other half of the "one day"
contrast — that the inclusive `effective_to` is converted to a half-open range exactly once — is
proven separately by the adjacent windows below: with the `+ 1 day` applied twice they would
overlap, and the insert would be refused.
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

from app.models import AbsenceBudget
from app.models.catalog import (
    ABSENCE_BUDGET_KEY_COLUMNS,
    ABSENCE_BUDGET_NO_OVERLAP_CONSTRAINT,
    BUDGET_WINDOW_MONTH_ALIGNED_EXPRESSION,
    STATUTORY_LEAVE_INDEX_EXPRESSION,
    STATUTORY_LEAVE_UNIQUE_INDEX,
    VALID_PERIOD_EXPRESSION,
)
from tests.conftest import (
    BACKEND_ROOT,
    BUDGET_SOURCE,
    STATUTORY_LEAVE_TYPE_NAME,
    make_absence_budget,
    make_absence_type,
    make_dimension_tuple,
    make_working_calendar,
)


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

YEAR_2026 = (date(2026, 1, 1), date(2026, 12, 31))
YEAR_2027 = (date(2027, 1, 1), date(2027, 12, 31))
FIRST_HALF_2026 = (date(2026, 1, 1), date(2026, 6, 30))
SECOND_HALF_2026 = (date(2026, 7, 1), date(2026, 12, 31))

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "a7c2e5f81b94_create_absence_budget_and_the_statutory_leave_flag.py"
)

Listener = Any


def _profile(session: Session, *, suffix: str = ""):
    """One calendar and one dimension tuple — i.e. one budget key and a second one to contrast it
    with.

    Returns `(calendar, dimensions)`. The calendar's standard day is never 8.00
    (`conftest.FORBIDDEN_FIXTURE_HOURS`), which matters even in this file: a schema test that
    accidentally created an eight-hour calendar would make that value reachable from the capacity
    suite's fixtures through a shared database.
    """
    calendar = make_working_calendar(
        session, name=f"Poland 7.5h{suffix}", standard_hours_per_day=Decimal("7.50")
    )
    return calendar, make_dimension_tuple(session, suffix=suffix, calendar=calendar)


# --- K-01: one pair, one window at a time — and a race decides it --------------------------------


def test_k_01_two_budgets_of_one_profile_for_two_years_coexist_and_an_overlapping_window_is_refused(
    db_session: Session,
) -> None:
    """K-01, single connection: two years side by side, an overlapping window refused by name.

    Three claims in one test, because each of the first two is satisfiable by a schema that gets the
    third wrong:

    1. **Two windows of one pair coexist.** 2026 and 2027 for the same (calendar, engagement type)
       are two rows — an entitlement changes between years, and a `UNIQUE (calendar_id,
       engagement_type_id)` would make that unexpressible.
    2. **An overlapping window is refused, and the refusal names the constraint.** The second window
       starts one month before the first one ends, so it is a partial overlap and not a duplicate:
       an equality-based unique constraint on the dates would accept it. Asserting the constraint
       name is what makes this a statement about the `EXCLUDE` rather than about whichever check
       happened to fire first.
    3. **The same overlapping window for a *different* key is accepted.** Without it, a constraint
       that refused every second row would satisfy the two claims above — and would make one
       calendar unable to carry a budget for more than one engagement type, i.e. the normal case.
    """
    calendar, dimensions = _profile(db_session)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=YEAR_2026[0],
        effective_to=YEAR_2026[1],
    )

    # 1. the next year is a second row, not a replacement
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("27.00"),
        effective_from=YEAR_2027[0],
        effective_to=YEAR_2027[1],
    )
    assert (
        db_session.execute(
            sa.select(sa.func.count())
            .select_from(AbsenceBudget)
            .where(AbsenceBudget.engagement_type_id == dimensions.engagement_type_id)
        ).scalar_one()
        == 2
    )

    # 2. a window overlapping the first by one month — the smallest overlap this table can express
    with pytest.raises(IntegrityError) as error:
        make_absence_budget(
            db_session,
            calendar,
            dimensions.engagement_type_id,
            budget_days=Decimal("30.00"),
            effective_from=date(2026, 12, 1),
            effective_to=date(2027, 11, 30),
        )
    assert ABSENCE_BUDGET_NO_OVERLAP_CONSTRAINT in str(error.value)
    db_session.rollback()

    # 3. the same window, another key: accepted
    calendar, dimensions = _profile(db_session, suffix=" (rebuilt)")
    other = make_dimension_tuple(db_session, suffix=" (contract)", calendar=calendar)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("26.00"),
        effective_from=YEAR_2026[0],
        effective_to=YEAR_2026[1],
    )
    make_absence_budget(
        db_session,
        calendar,
        other.engagement_type_id,
        budget_days=Decimal("20.00"),
        effective_from=YEAR_2026[0],
        effective_to=YEAR_2026[1],
    )
    assert (
        db_session.execute(
            sa.select(sa.func.count())
            .select_from(AbsenceBudget)
            .where(AbsenceBudget.calendar_id == calendar.id)
        ).scalar_one()
        == 2
    ), "one calendar cannot carry a budget for two engagement types over one window"


def test_k_01_two_adjacent_windows_of_one_pair_are_accepted(db_session: Session) -> None:
    """K-01's adjacency contrast — and the proof that the inclusive end is converted exactly once.

    The second window starts the day after the first one ends. With the `+ 1 day` of
    `VALID_PERIOD_EXPRESSION` applied twice — in the generated column and again anywhere else — the
    two ranges would intersect and this insert would be refused. That is the "one day" half of the
    criterion's contrast, in the only form a month-aligned table can carry it.
    """
    calendar, dimensions = _profile(db_session)
    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("13.00"),
        effective_from=FIRST_HALF_2026[0],
        effective_to=FIRST_HALF_2026[1],
    )

    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("13.00"),
        effective_from=SECOND_HALF_2026[0],
        effective_to=SECOND_HALF_2026[1],
    )

    db_session.expire_all()
    windows = db_session.execute(
        sa.select(AbsenceBudget.valid_period)
        .where(AbsenceBudget.engagement_type_id == dimensions.engagement_type_id)
        .order_by(AbsenceBudget.effective_from)
    ).scalars().all()
    assert [(window.lower, window.upper) for window in windows] == [
        (date(2026, 1, 1), date(2026, 7, 1)),
        (date(2026, 7, 1), date(2027, 1, 1)),
    ], "the generated window is not the inclusive one converted exactly once"


def _committing_an_overlapping_budget(
    engine: Engine, fired: list[str], calendar_id: uuid.UUID, engagement_type_id: uuid.UUID
) -> Listener:
    """A hook that commits an overlapping budget on another connection, just before *our* INSERT.

    This is the window a Python check-then-act guard would have: it would have asked "does an
    overlapping row exist?" a moment ago, been told no, and would now insert a second one. The
    competitor commits immediately, because the session under test holds no lock on anything yet.

    Fires once (`fired` guards re-entry: the competing INSERT goes through the same class-level
    listener).
    """

    def interleave(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or "insert into absence_budget" not in statement.lstrip().lower():
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(
                sa.text(
                    "INSERT INTO absence_budget (id, calendar_id, engagement_type_id,"
                    " budget_days, unit, source, effective_from, effective_to)"
                    " VALUES (:id, :calendar, :engagement, 99.00, 'day', :source, :start, :end)"
                ),
                {
                    "id": uuid.uuid4(),
                    "calendar": calendar_id,
                    "engagement": engagement_type_id,
                    "source": "Competing regulation",
                    # The same window the write under test asks for — an overlap, not an adjacency.
                    "start": YEAR_2026[0],
                    "end": YEAR_2026[1],
                },
            )

    return interleave


def test_k_01_an_overlapping_window_committed_by_a_competitor_mid_write_is_still_refused(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-01, two connections. The competing row lands *between* any check and the write.

    The production path holds no Python pre-check at all, so for the code as delivered this is
    simply "the INSERT loses to a committed row and the database says no". The test exists for the
    mutation: with the `EXCLUDE` dropped from the migration and a `SELECT` before the `INSERT` in
    its place, the single-connection halves of K-01 stay green — the pre-check finds the first
    window and refuses — and this one fails, because both connections pass a check taken a moment
    apart and the table ends up with two budgets covering one day for one pair.

    Asserted on the table, not only on the exception: exactly one row survives and it is the
    competitor's, recognisable by its 99 days. A refusal raised while both rows were written would
    satisfy an exception-only assertion.

    `committing_client` is requested although no request is sent through it: the rows here are
    committed and outlive the test, and that fixture's teardown is the one place that empties these
    tables.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        calendar, dimensions = _profile(setup, suffix=" (race)")
        calendar_id, engagement_type_id = calendar.id, dimensions.engagement_type_id
        setup.commit()

    fired: list[str] = []
    interleave = _committing_an_overlapping_budget(
        engine, fired, calendar_id, engagement_type_id
    )
    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        with Session(bind=engine, expire_on_commit=False, future=True) as writer:
            with pytest.raises(IntegrityError) as error:
                writer.execute(
                    sa.text(
                        "INSERT INTO absence_budget (id, calendar_id, engagement_type_id,"
                        " budget_days, unit, source, effective_from, effective_to)"
                        " VALUES (:id, :calendar, :engagement, 26.00, 'day', :source, :start,"
                        " :end)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "calendar": calendar_id,
                        "engagement": engagement_type_id,
                        "source": BUDGET_SOURCE,
                        "start": YEAR_2026[0],
                        "end": YEAR_2026[1],
                    },
                )
                writer.commit()
            writer.rollback()
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the competing write never ran — nothing here is about a race"
    assert ABSENCE_BUDGET_NO_OVERLAP_CONSTRAINT in str(error.value)
    with engine.connect() as connection:
        surviving = connection.execute(
            sa.text(
                "SELECT budget_days FROM absence_budget WHERE calendar_id = :calendar"
                " ORDER BY budget_days"
            ),
            {"calendar": calendar_id},
        ).scalars().all()
    assert surviving == [Decimal("99.00")], (
        "two budgets covering one day for one pair survived the race — the guard is not in the "
        "database"
    )


def test_k_01_the_exclude_constraint_and_btree_gist_exist_in_the_migrated_database(
    db_session: Session,
) -> None:
    """The constraint exists in the *migrated database*, not only in a model file.

    A test that grepped the migration for "EXCLUDE" would pass against a migration that fails to
    apply, and a behavioural refusal could in principle come from some other mechanism with the same
    name. This asks PostgreSQL: the extension is installed (`btree_gist`, which this is now the
    **second** table to depend on — ADR-0008, addendum 2026-09-22 SC-3-03, point 11) and the
    constraint is of type `x` (exclusion) on `absence_budget`.
    """
    extension = db_session.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
    ).scalar_one_or_none()
    constraint_type = db_session.execute(
        sa.text("SELECT contype FROM pg_constraint WHERE conname = :name"),
        {"name": ABSENCE_BUDGET_NO_OVERLAP_CONSTRAINT},
    ).scalar_one_or_none()

    assert extension == 1, "btree_gist is not installed — the rate migration did not create it"
    assert constraint_type == "x", (
        f"{ABSENCE_BUDGET_NO_OVERLAP_CONSTRAINT} is not an exclusion constraint "
        f"(contype={constraint_type!r})"
    )


# --- K-02: a number with no named source is not a row --------------------------------------------


@pytest.mark.parametrize("source", [None, "", "   ", "\t\n"])
def test_k_02_the_database_refuses_a_budget_row_whose_source_is_null_or_blank(
    db_session: Session, source: str | None
) -> None:
    """K-02's other half: the refusal holds on the path that never sees the request schema.

    Four spellings of "no source", because an implementation enforcing only `NOT NULL` passes the
    empty-string cases and one enforcing only "non-empty" passes the `NULL` one. The whitespace
    cases are the ones a well-meaning client produces by sending a blank form field.

    The mutation this kills is K-02's first: "drop the `NOT NULL`/non-blank rule from the database
    and keep the request validation". Written through the ORM, so nothing here is protected by
    Pydantic — which is the point, because a fixture, a seed script or an import is exactly the path
    an organisation's first thousand budget rows arrive by.

    The contrast is the whole rest of this file: every other test writes the same row with a real
    source and it is accepted.
    """
    calendar, dimensions = _profile(db_session)

    with pytest.raises(IntegrityError) as error:
        make_absence_budget(
            db_session,
            calendar,
            dimensions.engagement_type_id,
            budget_days=Decimal("26.00"),
            effective_from=YEAR_2026[0],
            effective_to=YEAR_2026[1],
            source=source,  # type: ignore[arg-type]
        )

    message = str(error.value)
    assert "source" in message, message
    assert ("not-null" in message) or ("source_not_blank" in message), message
    db_session.rollback()


def test_the_database_refuses_a_budget_whose_unit_is_not_days(db_session: Session) -> None:
    """ADR-0008's addendum (SC-3-03, point 5): the unit is named and enforced, never inferred.

    Not an acceptance criterion on its own, and it is here for the reason that addendum gives: "20"
    read as days and "20" read as FTE-days are two different answers from one row, and no test on a
    value from the middle of the range would notice the difference.
    """
    calendar, dimensions = _profile(db_session)

    with pytest.raises(IntegrityError) as error:
        make_absence_budget(
            db_session,
            calendar,
            dimensions.engagement_type_id,
            budget_days=Decimal("26.00"),
            effective_from=YEAR_2026[0],
            effective_to=YEAR_2026[1],
            unit="fte_day",
        )

    assert "unit_is_day" in str(error.value)
    db_session.rollback()


# --- the window: closed, ordered and aligned to whole months -------------------------------------


def test_the_database_refuses_an_open_ended_budget_window(db_session: Session) -> None:
    """ADR-0008's addendum (SC-3-03, point 10b): this table's one narrowing of the shared pattern.

    An open-ended window has no denominator for the monthly proration, so instead of giving the
    arithmetic an exception the table loses the shape that would force one. The other three tables
    of the pattern keep `effective_to IS NULL` as "unbounded" — this refusal is deliberately narrow
    and asserted by the constraint's own name, so a future reader can tell a decision from a bug.
    """
    calendar, dimensions = _profile(db_session)

    with pytest.raises(IntegrityError) as error:
        make_absence_budget(
            db_session,
            calendar,
            dimensions.engagement_type_id,
            budget_days=Decimal("26.00"),
            effective_from=YEAR_2026[0],
            effective_to=None,  # type: ignore[arg-type]
        )

    assert "effective_to_is_closed" in str(error.value)
    db_session.rollback()


@pytest.mark.parametrize(
    ("effective_from", "effective_to"),
    [
        (date(2026, 1, 15), date(2026, 12, 31)),
        (date(2026, 1, 1), date(2026, 12, 15)),
        (date(2026, 1, 15), date(2026, 12, 15)),
    ],
)
def test_the_database_refuses_a_budget_window_that_is_not_whole_months(
    db_session: Session, effective_from: date, effective_to: date
) -> None:
    """ADR-0008's addendum (SC-3-03, point 10a) made structural — and it is load-bearing.

    The proration divides by the number of months of the window, and its required property is that
    the shares of all those months add back up to the budget. A window starting on 15 January makes
    "how many months is that" a question with several defensible answers, each producing a different
    monthly figure and some of them losing a day in the sum. The shape that raises the question is
    refused instead, so the invariant holds for every window the table can hold.

    Three parameters, because a check on one end alone passes two of them.
    """
    calendar, dimensions = _profile(db_session)

    with pytest.raises(IntegrityError) as error:
        make_absence_budget(
            db_session,
            calendar,
            dimensions.engagement_type_id,
            budget_days=Decimal("26.00"),
            effective_from=effective_from,
            effective_to=effective_to,
        )

    assert "window_aligned_to_whole_months" in str(error.value)
    db_session.rollback()


def test_the_database_refuses_an_inverted_budget_window(db_session: Session) -> None:
    """The shared `effective_period_ordered` CHECK — it guards the `EXCLUDE`, not tidiness.

    The case it exists for is the **empty** window: `effective_to` exactly one day before
    `effective_from` makes `daterange(from, to + 1)` an empty range, and `&&` against an empty range
    is false for everything — so such a row would sit outside the overlap guarantee entirely,
    silently exempt from what every other row has. It is also, on a month-aligned table, a shape
    that passes the alignment CHECK (1 February to 31 January), which is why it needs its own test
    rather than being covered by the parameters above.

    A *wider* inversion never reaches the CHECK at all: `daterange` itself refuses it with
    `22000 data_exception` while generating the column. Both are refusals and the mechanisms are
    different, so both are asserted — the second one is why this test would still pass with the
    CHECK deleted if it only used a wide inversion.
    """
    calendar, dimensions = _profile(db_session)

    with pytest.raises(IntegrityError) as error:
        make_absence_budget(
            db_session,
            calendar,
            dimensions.engagement_type_id,
            budget_days=Decimal("26.00"),
            effective_from=date(2026, 2, 1),
            effective_to=date(2026, 1, 31),
        )
    assert "effective_period_ordered" in str(error.value)
    db_session.rollback()

    calendar, dimensions = _profile(db_session, suffix=" (wide inversion)")
    with pytest.raises(sa.exc.DataError) as data_error:
        make_absence_budget(
            db_session,
            calendar,
            dimensions.engagement_type_id,
            budget_days=Decimal("26.00"),
            effective_from=date(2026, 12, 1),
            effective_to=date(2026, 1, 31),
        )
    assert "range lower bound" in str(data_error.value)
    db_session.rollback()


def test_a_budget_of_zero_days_is_a_legal_row(db_session: Session) -> None:
    """ADR-0008's addendum (SC-3-03, point 7): zero is a value, and a negative one is a sign error.

    An engagement type with no leave entitlement is a row saying so, and it has to stay
    distinguishable from *no row at all* — which is the named "no budget" state criterion K-08 is
    about. A CHECK of `budget_days > 0` would make the two the same answer.
    """
    calendar, dimensions = _profile(db_session)

    make_absence_budget(
        db_session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=Decimal("0.00"),
        effective_from=YEAR_2026[0],
        effective_to=YEAR_2026[1],
    )

    with pytest.raises(IntegrityError) as error:
        make_absence_budget(
            db_session,
            calendar,
            make_dimension_tuple(
                db_session, suffix=" (negative)", calendar=calendar
            ).engagement_type_id,
            budget_days=Decimal("-1.00"),
            effective_from=YEAR_2026[0],
            effective_to=YEAR_2026[1],
        )
    assert "budget_days_not_negative" in str(error.value)
    db_session.rollback()


# --- K-04, structurally: at most one absence type carries the flag -------------------------------


def test_k_04_a_second_absence_type_without_the_flag_is_accepted(db_session: Session) -> None:
    """K-04's structural contrast: the index refuses a second *flagged* row, not a second row.

    Without this, a unique index on the column itself — which would allow one `true` and one
    `false` and nothing more — would satisfy the refusal below while making a second ordinary
    absence type impossible, i.e. breaking the dictionary for every organisation that has more than
    two kinds of absence.
    """
    make_absence_type(
        db_session, name=STATUTORY_LEAVE_TYPE_NAME, is_statutory_leave=True
    )

    make_absence_type(db_session, name="Paid holiday")
    make_absence_type(db_session, name="Sick leave", generates_cost=False)
    make_absence_type(db_session, name="Training", generates_revenue=True)

    assert (
        db_session.execute(sa.text("SELECT count(*) FROM absence_type")).scalar_one() == 4
    )


def test_k_04_the_database_refuses_a_second_absence_type_flagged_as_statutory_leave_even_from_two_connections(  # noqa: E501 — the criterion names this test; the name is the contract, not a style choice
    committing_client: TestClient, engine: Engine
) -> None:
    """K-04, two connections: the "at most one flagged type" rule is the database's, not Python's.

    Why it matters at all: the flag decides what every budget row in the organisation settles
    against (ADR-0008, addendum 2026-09-22 SC-3-03, point 8). Two flagged rows are two answers to
    that question, and `app.data.absence_budget.statutory_leave_type` reads it with
    `one_or_none()` — so without the index a second flagged row would turn every budget read into
    a `500`, on data the catalogue had accepted.

    The race is what makes this a claim about the schema. A `SELECT count(*) WHERE
    is_statutory_leave` before the write passes in both connections when they ask at the same
    moment; that mutation survives every single-connection test in this file, and the criterion
    names it for that reason. The competitor commits in the window immediately before our own
    `INSERT`.

    Asserted on the table as well as on the exception: one flagged row survives, and it is the
    competitor's — recognisable by its name.
    """
    fired: list[str] = []
    competitor_name = "Competing statutory leave"

    def interleave(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or "insert into absence_type" not in statement.lstrip().lower():
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(
                sa.text(
                    "INSERT INTO absence_type (id, name, generates_cost, generates_revenue,"
                    " is_statutory_leave) VALUES (:id, :name, true, false, true)"
                ),
                {"id": uuid.uuid4(), "name": competitor_name},
            )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        with Session(bind=engine, expire_on_commit=False, future=True) as writer:
            with pytest.raises(IntegrityError) as error:
                writer.execute(
                    sa.text(
                        "INSERT INTO absence_type (id, name, generates_cost, generates_revenue,"
                        " is_statutory_leave) VALUES (:id, :name, true, false, true)"
                    ),
                    {"id": uuid.uuid4(), "name": STATUTORY_LEAVE_TYPE_NAME},
                )
                writer.commit()
            writer.rollback()
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the competing write never ran — nothing here is about a race"
    assert STATUTORY_LEAVE_UNIQUE_INDEX in str(error.value)
    with engine.connect() as connection:
        flagged = connection.execute(
            sa.text("SELECT name FROM absence_type WHERE is_statutory_leave")
        ).scalars().all()
    assert flagged == [competitor_name], (
        "two absence types carry is_statutory_leave after the race — the guard is not in the "
        "database, and every budget read now has two answers to choose between"
    )


def test_the_statutory_leave_index_is_partial_and_unique_in_the_migrated_database(
    db_session: Session,
) -> None:
    """The index exists, is unique, and is restricted to the flagged rows — asked of PostgreSQL.

    A behavioural refusal alone cannot distinguish "unique among flagged rows" from "unique over the
    whole column": the second one refuses a second unflagged type as well, which the contrast above
    covers, but only this assertion says *why* — the predicate is in the index definition.
    """
    definition = db_session.execute(
        sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
        {"name": STATUTORY_LEAVE_UNIQUE_INDEX},
    ).scalar_one()

    assert "CREATE UNIQUE INDEX" in definition, definition
    assert "WHERE is_statutory_leave" in definition, definition


# --- K-08: the key is two NOT NULL columns, and both are in the EXCLUDE ---------------------------


@pytest.mark.parametrize("column", ABSENCE_BUDGET_KEY_COLUMNS)
def test_k_08_the_budget_key_is_calendar_id_and_engagement_type_id_and_neither_may_be_null(
    db_session: Session, column: str
) -> None:
    """K-08's schema half — one run per key column, and three independent assertions each.

    A nullable column in this key would mean "any", i.e. a second, unnamed resolution mechanism laid
    on top of the date window (ADR-0008, addendum 2026-09-22 SC-3-03, point 3) — and it would break
    the `EXCLUDE` as well, because `NULL = NULL` is not `TRUE` and the overlap guarantee would
    silently stop applying to exactly the rows that carried it.

    1. `information_schema` says the column is `NOT NULL` — the claim about the schema;
    2. an `INSERT` with `NULL` in *that one* column is refused — the claim about behaviour, run
       separately per column so a table that lost one of the two constraints cannot hide behind the
       other;
    3. the column is part of the `EXCLUDE` key, read from `pg_constraint` — dropping one of the two
       from the key is a mutation no behavioural test of a single pair would notice.
    """
    nullable = db_session.execute(
        sa.text(
            "SELECT is_nullable FROM information_schema.columns"
            " WHERE table_name = 'absence_budget' AND column_name = :column"
        ),
        {"column": column},
    ).scalar_one()
    assert nullable == "NO", f"absence_budget.{column} is nullable — the key means 'any'"

    calendar, dimensions = _profile(db_session)
    values = {
        "id": uuid.uuid4(),
        "calendar_id": calendar.id,
        "engagement_type_id": dimensions.engagement_type_id,
        "budget_days": Decimal("26.00"),
        "unit": "day",
        "source": BUDGET_SOURCE,
        "effective_from": YEAR_2026[0],
        "effective_to": YEAR_2026[1],
    }
    values[column] = None
    with pytest.raises(IntegrityError) as error:
        db_session.execute(sa.insert(AbsenceBudget.__table__).values(**values))
    assert column in str(error.value)
    db_session.rollback()

    definition = db_session.execute(
        sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"),
        {"name": ABSENCE_BUDGET_NO_OVERLAP_CONSTRAINT},
    ).scalar_one()
    assert f"{column} WITH =" in definition, (
        f"{column} is not part of {ABSENCE_BUDGET_NO_OVERLAP_CONSTRAINT}: {definition}"
    )
    assert "valid_period WITH &&" in definition, definition


def test_k_08_the_budget_key_columns_are_foreign_keys_that_refuse_to_orphan_a_row(
    db_session: Session,
) -> None:
    """K-08's referential half: both key columns point at real rows, and neither cascades.

    `confdeltype` is PostgreSQL's own record of the rule: `'a'` is `NO ACTION`, `'c'` is `CASCADE`,
    `'n'` is `SET NULL`. A cascade here would delete budgets when a calendar is removed — quietly
    changing the capacity of every scenario reading it, including approved ones whose snapshot was
    taken from rows that no longer exist to explain it.

    The snapshot table is in the same query, because its one foreign key (to `scenarios`) must not
    cascade either: that would be a way for the frozen budget of an `approved` scenario to disappear
    past the write guard, which covers `INSERT`/`DELETE` and not a cascade.
    """
    rules = dict(
        db_session.execute(
            sa.text(
                "SELECT conname, confdeltype FROM pg_constraint"
                " WHERE contype = 'f' AND conrelid IN ("
                " 'absence_budget'::regclass,"
                " 'approved_snapshot_absence_budget'::regclass)"
            )
        ).all()
    )

    assert sorted(rules) == [
        "fk_absence_budget_calendar_id",
        "fk_absence_budget_engagement_type_id",
        "fk_approved_snapshot_absence_budget_scenario_id",
    ], f"unexpected foreign keys: {sorted(rules)}"
    cascading = sorted(name for name, rule in rules.items() if rule != "a")
    assert cascading == [], f"a SC-3-03 foreign key no longer refuses to orphan a row: {cascading}"


@pytest.mark.parametrize("column", ABSENCE_BUDGET_KEY_COLUMNS)
def test_a_budget_may_not_name_a_calendar_or_an_engagement_type_that_does_not_exist(
    db_session: Session, column: str
) -> None:
    """The other direction of the same two foreign keys: a made-up id is not a row.

    Without it, "the budget key is two columns" would be satisfied by two plain `uuid` columns, and
    a budget could name a calendar nobody created — a number attached to nothing, which no screen
    could explain and no calculation could use.

    One run per column rather than a loop inside one test: the rollback each refusal needs also
    discards the fixture rows (they are flushed, not committed), so a second iteration in the same
    transaction would be writing against a calendar that no longer exists and would fail for the
    wrong reason.
    """
    calendar, dimensions = _profile(db_session)
    values = {
        "id": uuid.uuid4(),
        "calendar_id": calendar.id,
        "engagement_type_id": dimensions.engagement_type_id,
        "budget_days": Decimal("26.00"),
        "unit": "day",
        "source": BUDGET_SOURCE,
        "effective_from": YEAR_2026[0],
        "effective_to": YEAR_2026[1],
        column: uuid.uuid4(),
    }

    with pytest.raises(IntegrityError) as error:
        db_session.execute(sa.insert(AbsenceBudget.__table__).values(**values))

    assert f"fk_absence_budget_{column}" in str(error.value)
    db_session.rollback()


# --- the model and the migration must keep describing the same schema ---------------------------


def _migration_module() -> ModuleType:
    """Load the migration file as a module, without going through Alembic's script directory."""
    specification = importlib.util.spec_from_file_location("sc_3_03_migration", MIGRATION_PATH)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_the_model_and_the_migration_agree_on_every_sql_expression_of_this_task(
    db_session: Session,
) -> None:
    """The drift guard R-02, applied to this task's hand-written SQL expressions.

    Each is spelled twice on purpose — a migration must keep describing the schema it produced even
    after the model moves on — and `alembic upgrade` never reads the model, so editing one copy
    alone breaks nothing any other test would notice.

    Then the *migrated database* is asked what it really has, so a migration that was edited but
    never applied fails here too. The `valid_period` comparison is the one that matters most: it is
    the same expression the rate table uses, and a divergence of one day between the two tables
    would be invisible to every test that asks about a date in the middle of a window.
    """
    migration = _migration_module()
    assert migration._VALID_PERIOD_EXPRESSION == VALID_PERIOD_EXPRESSION
    assert (
        migration._BUDGET_WINDOW_MONTH_ALIGNED_EXPRESSION
        == BUDGET_WINDOW_MONTH_ALIGNED_EXPRESSION
    )
    assert migration._STATUTORY_LEAVE_INDEX_EXPRESSION == STATUTORY_LEAVE_INDEX_EXPRESSION

    generated = db_session.execute(
        sa.text(
            "SELECT generation_expression FROM information_schema.columns"
            " WHERE table_name = 'absence_budget' AND column_name = 'valid_period'"
        )
    ).scalar_one()
    assert "daterange" in generated
    assert "effective_from" in generated

    aligned = db_session.execute(
        sa.text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'ck_absence_budget_window_aligned_to_whole_months'"
        )
    ).scalar_one()
    assert "date_trunc" in aligned


# --- this migration is reversible, and the schema comes back -------------------------------------


def test_the_absence_budget_migration_downgrades_and_upgrades_again(
    engine: Engine, alembic_config: Config
) -> None:
    """`downgrade()` of `a7c2e5f81b94` really runs, and the schema it removes comes back.

    Not an acceptance criterion; the repository's practice since SC-2-03, where a `downgrade()` that
    passed review turned out to lose rows. The specific hazard here is the **column on an existing
    table**: `is_statutory_leave` and its partial index have to be removed in an order PostgreSQL
    accepts, and a `downgrade()` that dropped the column while the index still referenced it would
    look clean in review and fail on the first real rollback.

    Both directions are asserted. `command.upgrade(alembic_config, "head")` runs in `finally`
    whatever happens: `engine` is session-scoped and every other test in this suite reads the schema
    this one leaves behind.

    The revision the database starts at is **read**, not hard-coded: a literal would be a claim
    about how many migrations separate `head` from here, and that claim goes stale the next time one
    is added on top (the lesson `tests/test_catalog_migration_reversibility.py` records, and the one
    `tests/test_working_calendar_schema_constraints.py` does not follow).
    """

    def revision() -> str:
        with engine.connect() as connection:
            return connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()

    def exists(statement: str) -> bool:
        with engine.connect() as connection:
            return connection.execute(sa.text(statement)).scalar_one()

    budget_table = (
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
        " WHERE table_name = 'absence_budget')"
    )
    snapshot_table = (
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
        " WHERE table_name = 'approved_snapshot_absence_budget')"
    )
    flag_column = (
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
        " WHERE table_name = 'absence_type' AND column_name = 'is_statutory_leave')"
    )
    flag_index = (
        "SELECT EXISTS (SELECT 1 FROM pg_indexes"
        f" WHERE indexname = '{STATUTORY_LEAVE_UNIQUE_INDEX}')"
    )

    before = revision()
    assert before == "a7c2e5f81b94", "this test must start at this task's migration"
    try:
        command.downgrade(alembic_config, "f3a1d0c58b27")

        assert revision() == "f3a1d0c58b27"
        assert not exists(budget_table)
        assert not exists(snapshot_table)
        assert not exists(flag_column)
        assert not exists(flag_index)
    finally:
        command.upgrade(alembic_config, "head")

    assert revision() == before
    assert exists(budget_table)
    assert exists(snapshot_table)
    assert exists(flag_column)
    assert exists(flag_index)
