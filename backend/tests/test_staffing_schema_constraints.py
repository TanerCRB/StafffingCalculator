"""SC-3-01, K-04 (and the database half of K-01) — what the schema refuses, whoever is writing.

Every test here writes through the ORM or through raw SQL, with no request schema anywhere in the
path. That is the point: `StaffingPositionCreateRequest` only ever sees HTTP requests, while a
fixture, a seed script, a future import or a second endpoint writes straight to the table. A
guarantee that lives in Pydantic is a guarantee about one caller.

K-04 has three runs, and they are three different mechanisms:

- **(a)** `UNIQUE (position_id, period_month)` — one allocation row per position per month, in a
  single connection *and* in a race between two;
- **(b)** `CHECK period_month = date_trunc('month', period_month)` — without it `2026-03-01` and
  `2026-03-15` are two different `DATE` values, the unique constraint accepts both, and "one
  allocation per calendar month" quietly becomes "one per day somebody typed";
- **(c)** non-negativity of each of the three hour columns, one constraint each.

Their shared contrast is at the end: another month, another position and *zero* hours are accepted —
a month without allocation is data, not an error, and a schema refusing everything would satisfy the
three runs above on its own.

Real PostgreSQL, real migration (ADR-0001, `tests/conftest.py`): a CHECK constraint and a unique
index cannot be proven against a stand-in that does not implement them.
"""

import importlib.util
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.staffing import (
    FIRST_DAY_OF_MONTH_EXPRESSION,
    HOURS_COLUMNS,
    HOURS_NON_NEGATIVE_CONSTRAINTS,
    STAFFING_POSITION_PAGE_INDEX,
    StaffingPosition,
    StaffingPositionAllocation,
)
from tests.conftest import (
    IN_SCOPE_USER,
    count_allocations,
    make_absence,
    make_absence_type,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
)

MARCH = date(2026, 3, 1)
APRIL = date(2026, 4, 1)

Listener = Any

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "b6d2f74c3e18_create_staffing_position_and_allocation.py"
)


def _committed_position(engine: Engine) -> uuid.UUID:
    """One position of one draft scenario, committed — visible to other connections."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Baseline")
        position = make_staffing_position(
            setup, scenario, make_dimension_tuple(setup), start_date=MARCH
        )
        position_id = position.id
        setup.commit()
    return position_id


def _staffing_setup(session: Session) -> StaffingPosition:
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline")
    return make_staffing_position(session, scenario, make_dimension_tuple(session))


# --- K-04 (a): one allocation row per position and month -----------------------------------------


def test_k_04_a_the_database_refuses_two_allocation_rows_for_one_position_and_month(
    db_session: Session,
) -> None:
    """K-04a, single connection. The second row for the same (position, month) is refused.

    The error is asserted to name the constraint, so the refusal is *this* mechanism and not a
    foreign key, a `NOT NULL` or a CHECK that happened to fire first.
    """
    position = _staffing_setup(db_session)
    make_allocation(db_session, position, period_month=MARCH)

    with pytest.raises(IntegrityError) as error:
        make_allocation(
            db_session,
            position,
            period_month=MARCH,
            availability_hours=Decimal("1.00"),
            planned_allocation_hours=Decimal("2.00"),
            billable_hours=Decimal("3.00"),
        )

    assert "uq_staffing_position_allocation_position_id_period_month" in str(error.value)
    db_session.rollback()


def _committing_a_competing_allocation(
    engine: Engine, fired: list[str], position_id: uuid.UUID
) -> Listener:
    """A hook that commits an allocation row for the same (position, month) on another connection,
    just before *our* `INSERT` runs.

    This is the window a Python "does a row for this month already exist?" pre-check would have: it
    would have asked a moment ago, been told no, and would now insert the second one. A
    single-threaded pre-check survives every other test in this file — the mutation log records the
    same shape surviving three times in this repository (SC-1-02 twice, SC-2-01) — so this
    interleaving is the only thing that kills it.

    Fires once (`fired` guards re-entry: the competing `INSERT` goes through the same class-level
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
        if fired or "insert into staffing_position_allocation" not in statement.lstrip().lower():
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(
                sa.text(
                    "INSERT INTO staffing_position_allocation (id, position_id, period_month,"
                    " availability_hours, planned_allocation_hours, billable_hours)"
                    " VALUES (:id, :position_id, :month, 999.00, 888.00, 777.00)"
                ),
                {"id": uuid.uuid4(), "position_id": position_id, "month": MARCH},
            )

    return interleave


def test_k_04_a_a_competing_allocation_for_the_same_month_committed_mid_write_is_still_refused(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-04a, two connections. The competing row lands *between* any check and the write.

    For the code as delivered there is no Python pre-check at all, so this is simply "the `INSERT`
    loses to a committed row and the database says no". The test exists for the mutation: with the
    unique constraint dropped from the migration, or with the rule rewritten as a `SELECT` followed
    by an `INSERT`, the table ends up with two rows for one calendar month of one position, and
    every later sum over that month double-counts it.

    Asserted on the table, not only on the exception: one row survives, and it is the competitor's
    (recognisable by its hours).

    `committing_client` is requested although no request is sent through it: rows committed here
    outlive the test, and that fixture's teardown is the one place that empties the tables
    afterwards. Cleaning up by hand here would be a second, divergent copy of it.
    """
    position_id = _committed_position(engine)
    fired: list[str] = []
    interleave = _committing_a_competing_allocation(engine, fired, position_id)

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        with Session(bind=engine, expire_on_commit=False, future=True) as writer:
            with pytest.raises(IntegrityError) as error:
                writer.execute(
                    sa.insert(StaffingPositionAllocation).values(
                        id=uuid.uuid4(),
                        position_id=position_id,
                        period_month=MARCH,
                        availability_hours=Decimal("160.00"),
                        planned_allocation_hours=Decimal("120.00"),
                        billable_hours=Decimal("100.00"),
                    )
                )
                writer.commit()
            writer.rollback()
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the competing write never ran — nothing here is about a race"
    assert "uq_staffing_position_allocation_position_id_period_month" in str(error.value)
    with engine.connect() as connection:
        surviving = connection.execute(
            sa.text(
                "SELECT availability_hours FROM staffing_position_allocation"
                " WHERE position_id = :id"
            ),
            {"id": position_id},
        ).scalars().all()
    assert surviving == [Decimal("999.00")], (
        "two rows for one calendar month of one position survived the race — the guard is not in "
        "the database"
    )


# --- K-04 (b): the month is a month, not a day ---------------------------------------------------


@pytest.mark.parametrize("day_of_month", [15, 2, 31])
def test_k_04_b_a_period_month_that_is_not_the_first_day_of_a_month_is_refused(
    db_session: Session, day_of_month: int
) -> None:
    """K-04b — the CHECK constraint, on the path that never sees the request schema.

    Load-bearing for K-04a and not merely tidy: `2026-03-15` is a different `DATE` from
    `2026-03-01`, so without this constraint the unique index accepts both and one calendar month of
    one position has two allocation rows — the very state K-04a exists to forbid. Three different
    days of the month, because an implementation comparing against a single value (say, "not the
    15th") would pass a one-case test.
    """
    position = _staffing_setup(db_session)

    with pytest.raises(IntegrityError) as error:
        make_allocation(db_session, position, period_month=date(2026, 3, day_of_month))

    assert "period_month_is_first_of_month" in str(error.value)
    db_session.rollback()


# --- K-04 (c): hours are never negative ---------------------------------------------------------


@pytest.mark.parametrize("column", HOURS_COLUMNS)
def test_k_04_c_a_negative_hours_figure_is_refused_for_each_of_the_three_columns(
    db_session: Session, column: str
) -> None:
    """K-04c — one run per column, so removing one of the three constraints kills exactly one test.

    A single conjunction constraint over the three would be indistinguishable from three constraints
    in a test that only ever made one figure negative — and the refusal would then not name which
    figure was wrong (`describe_without_values` keeps the constraint name, NF-11).
    """
    position = _staffing_setup(db_session)

    with pytest.raises(IntegrityError) as error:
        make_allocation(db_session, position, period_month=MARCH, **{column: Decimal("-0.25")})

    assert HOURS_NON_NEGATIVE_CONSTRAINTS[column] in str(error.value)
    db_session.rollback()


def test_all_three_non_negativity_constraints_exist_in_the_migrated_database(
    db_session: Session,
) -> None:
    """The three CHECK constraints are in the database under the names the tests assert on.

    Two things this catches that the refusal tests above cannot. First, truncation: PostgreSQL cuts
    an identifier at 63 characters, and the names in `HOURS_NON_NEGATIVE_CONSTRAINTS` are short for
    that reason — a longer name would come back truncated and a test asserting on the name it
    expected would fail as if the constraint were missing. Second, a constraint present in the model
    and absent from the migration: `alembic upgrade` never reads the model, so the two are two
    statements about one schema and only the database can arbitrate between them.
    """
    present = set(
        db_session.execute(
            sa.text(
                "SELECT conname FROM pg_constraint WHERE conrelid ="
                " 'staffing_position_allocation'::regclass AND contype = 'c'"
            )
        ).scalars()
    )

    expected = {
        f"ck_staffing_position_allocation_{name}"
        for name in HOURS_NON_NEGATIVE_CONSTRAINTS.values()
    }
    assert expected <= present, f"missing: {sorted(expected - present)}"
    assert all(len(name) <= 63 for name in expected), "a constraint name would be truncated"


def test_k_04_the_shared_contrast_another_month_another_position_and_zero_hours_are_accepted(
    db_session: Session,
) -> None:
    """K-04's contrast — the three refusals above do not come from a schema that refuses everything.

    Three acceptances in one test, because each of them is the *near miss* of one refusal: a second
    row for the same position in a different month (K-04a), the same month under a different
    position (K-04a again, from the other side), and a row whose three figures are all zero (K-04c).
    The third is a statement about the domain, not only about the constraint: a month in which
    nobody works is part of the plan, so `0` must not be refused together with `-1`.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session)
    first = make_staffing_position(db_session, scenario, dimensions, start_date=MARCH)
    second = make_staffing_position(db_session, scenario, dimensions, start_date=APRIL)

    make_allocation(db_session, first, period_month=MARCH)
    make_allocation(db_session, first, period_month=APRIL)
    make_allocation(db_session, second, period_month=MARCH)
    make_allocation(
        db_session,
        second,
        period_month=APRIL,
        availability_hours=Decimal("0.00"),
        planned_allocation_hours=Decimal("0.00"),
        billable_hours=Decimal("0.00"),
    )

    assert count_allocations(db_session) == 4


def test_two_positions_for_the_same_role_over_the_same_months_are_legal(
    db_session: Session,
) -> None:
    """ADR-0008's pattern is deliberately **not** applied here (ADR-0004, addendum 2026-09-19).

    Not an acceptance criterion — the contrast that keeps the decision from drifting into its
    opposite. A PM plans two people on one role in the same months, so two positions with the same
    dimension tuple and overlapping periods are one plan, not a conflict. An `EXCLUDE USING gist`
    copied over from `catalog_default_rates` "for symmetry" would refuse this row, and the failure
    would look like a data problem rather than a schema decision.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session)

    first = make_staffing_position(
        db_session, scenario, dimensions, start_date=MARCH, end_date=date(2026, 12, 31)
    )
    second = make_staffing_position(
        db_session, scenario, dimensions, start_date=MARCH, end_date=date(2026, 12, 31)
    )

    assert first.id != second.id
    assert (
        db_session.execute(
            sa.select(sa.func.count())
            .select_from(StaffingPosition)
            .where(StaffingPosition.scenario_id == scenario.id)
        ).scalar_one()
        == 2
    )


# --- the database half of K-01: headcount and the ordering of the period -------------------------


@pytest.mark.parametrize("headcount", [0, -1])
def test_k_01_the_database_refuses_a_position_without_a_positive_headcount(
    db_session: Session, headcount: int
) -> None:
    """K-01 — "with a headcount" is a claim about the row, not about the request schema.

    `headcount = 0` is the interesting case: it passes every "is it a number" validation and means
    a position that plans for nobody, which is a row no calculation can use and no screen can show.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session)

    with pytest.raises(IntegrityError) as error:
        make_staffing_position(db_session, scenario, dimensions, headcount=headcount)

    assert "headcount_positive" in str(error.value)
    db_session.rollback()


def test_k_01_the_database_refuses_a_position_whose_period_ends_before_it_starts(
    db_session: Session,
) -> None:
    """K-01 — the period is ordered by a CHECK, as on a scenario and on a rate window.

    The contrast comes **first**, and that order is forced rather than chosen: rolling back after
    the refusal discards the savepoint the fixture rows live in, so anything written after it fails
    for want of a scenario rather than for the reason under test. The contrast is an *open-ended*
    position (`end_date IS NULL`), so the constraint is shown to refuse an inverted period rather
    than a missing end date.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session)

    open_ended = make_staffing_position(
        db_session, scenario, dimensions, start_date=APRIL, end_date=None
    )
    assert open_ended.end_date is None

    with pytest.raises(IntegrityError) as error:
        make_staffing_position(
            db_session, scenario, dimensions, start_date=APRIL, end_date=MARCH
        )

    assert "position_period_ordered" in str(error.value)
    db_session.rollback()


# --- the model and the migration must keep describing the same schema ---------------------------


def _migration_module() -> ModuleType:
    """Load the migration file as a module, without going through Alembic's script directory."""
    specification = importlib.util.spec_from_file_location("sc_3_01_migration", MIGRATION_PATH)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_the_model_and_the_migration_agree_on_the_month_check(db_session: Session) -> None:
    """The drift guard R-02 asked for, applied to this task's one hand-written SQL expression.

    The expression is spelled twice on purpose — a migration has to keep describing the schema it
    produced even after the model moves on — and `alembic upgrade` never reads the model, so editing
    one copy alone breaks nothing that any other test would notice. Two assertions, because either
    one alone is satisfiable without the other: the strings are compared, and then the *migrated
    database* is asked what constraint it actually has, so a migration that was edited but never
    applied fails here too.
    """
    assert _migration_module()._FIRST_DAY_OF_MONTH_EXPRESSION == FIRST_DAY_OF_MONTH_EXPRESSION

    definition = db_session.execute(
        sa.text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'ck_staffing_position_allocation_period_month_is_first_of_month'"
        )
    ).scalar_one()
    assert "date_trunc" in definition
    assert "period_month" in definition


_PAGING_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "f1a2c4b6d8e0_index_staffing_position_for_paging.py"
)


def _paging_migration_module() -> ModuleType:
    """Load the SC-3-05 gate-2 (R-02) paging migration as a module — the same construction
    `_migration_module()` uses for `b6d2f74c3e18`, kept separate because the two migrations spell
    two different schema facts and a test comparing the wrong pair would pass by accident."""
    specification = importlib.util.spec_from_file_location(
        "sc_3_05_paging_migration", _PAGING_MIGRATION_PATH
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_the_model_and_the_migration_agree_on_the_staffing_position_page_index(
    db_session: Session,
) -> None:
    """SC-3-05 gate-2, reviewer finding R-02 — the same drift guard `RATE_PAGE_INDEX` already has
    for `GET /catalog/rates`, applied to this task's own paging index.

    Three assertions, because any one alone is satisfiable without the others: the index's *name*
    is compared between `app.models.staffing.STAFFING_POSITION_PAGE_INDEX` and the migration's own
    copy, the *column order* is compared the same way (a name that matches with columns reordered
    would still sort wrong), and then the *migrated database* is asked what index it actually has —
    so a migration that was edited but never applied fails here too, not only a model that drifted
    from a migration nobody ran.
    """
    migration = _paging_migration_module()

    assert migration._PAGE_INDEX == STAFFING_POSITION_PAGE_INDEX
    assert migration._POSITION_TABLE == StaffingPosition.__tablename__
    assert migration._PAGE_INDEX_COLUMNS == ("scenario_id", "start_date", "id")

    indexed_columns = db_session.execute(
        sa.text(
            "SELECT a.attname"
            " FROM pg_index i"
            " JOIN pg_class c ON c.oid = i.indexrelid"
            " JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
            " WHERE c.relname = :index_name"
            " ORDER BY array_position(i.indkey, a.attnum)"
        ),
        {"index_name": STAFFING_POSITION_PAGE_INDEX},
    ).scalars().all()
    assert indexed_columns == ["scenario_id", "start_date", "id"], (
        f"the migrated database's own {STAFFING_POSITION_PAGE_INDEX} does not lead on "
        f"(scenario_id, start_date, id): found {indexed_columns}"
    )


def test_no_foreign_key_of_either_staffing_table_cascades_a_delete(db_session: Session) -> None:
    """Every foreign key of both staffing tables is `NO ACTION` — added by QA, 2026-09-19.

    A *negative* schema decision, and the one with the sharpest consequence in this task: both
    `app.models.staffing` and the migration argue that `ON DELETE CASCADE` towards `scenarios` would
    be "a second, unguarded way for the rows of an `approved` scenario to disappear", because the
    write guard of K-06/K-07 covers `INSERT` and `UPDATE` and a cascade is neither. Nothing was
    holding that argument: adding `ondelete="CASCADE"` to the scenario foreign key in the migration
    left all 249 tests green (measured). The `conftest` teardown deletes the children first, so it
    never notices — and no test deletes a scenario, because deletion is out of scope for SC-3-01.
    That is why this has to be a schema assertion rather than a behavioural one: the harm lands in
    the *next* task that deletes a scenario, and by then nothing would object.

    `confdeltype` is PostgreSQL's own record of the rule — `'a'` is `NO ACTION`, `'c'` is `CASCADE`,
    `'n'` is `SET NULL`. Asserted for the position's five foreign keys and the allocation row's one
    at once, so a cascade added to any of them, not only to the scenario one, fails here.

    Re-armed in SC-2-06, not loosened (Issue #31, human decision 2026-09-28, K-10): the position's
    sixth foreign key, `fk_staffing_position_person_id` (ADR-0019, point 7 — `NO ACTION`, so a
    person assigned to a position cannot be physically deleted), joins the count; the no-cascade
    assertion below is unchanged and covers it too.
    """
    delete_rules = dict(
        db_session.execute(
            sa.text(
                "SELECT conname, confdeltype FROM pg_constraint"
                " WHERE contype = 'f' AND conrelid IN ("
                " 'staffing_position'::regclass,"
                " 'staffing_position_allocation'::regclass)"
            )
        ).all()
    )

    # Re-armed in SC-2-06, not loosened: 6 -> 7 for `fk_staffing_position_person_id`.
    assert len(delete_rules) == 7, f"expected seven foreign keys, found: {sorted(delete_rules)}"
    cascading = sorted(name for name, rule in delete_rules.items() if rule != "a")
    assert cascading == [], (
        f"a staffing foreign key no longer refuses to orphan a row: {cascading}. A cascade towards "
        "the scenario deletes the staffing of an approved scenario without passing the write guard "
        "of K-06/K-07 (ADR-0004)."
    )


# --- SC-3-02: the shape of the absence row -------------------------------------------------------


ABSENCE_COLUMNS = {
    "id",
    "position_id",
    "absence_type_id",
    "start_date",
    "end_date",
    "created_at",
}
"""Every column `staffing_position_absence` is allowed to have — the whole row, spelled out.

Written here, in the test, and deliberately **not** imported from the model: importing it would mean
that adding a column to the model also updates the expectation, and the assertion would pass for
every schema. This set is the claim; the table is what is measured against it."""


def test_k_22_the_absence_row_has_no_column_for_a_person_or_a_note(db_session: Session) -> None:
    """K-22 — the personal-data boundary, asserted as an **equality** of the column set.

    ADR-0005's addendum of 2026-09-22 (point 6) draws this boundary at the moment the table is
    created rather than after an incident: an absence hangs on an *anonymous* staffing position,
    its kind comes only from the dictionary, and there is no column for a note or a justification.
    Without that, a table recording who is away and why is, in some fraction of its rows, a register
    of health data — and it would be one without the decision that governs one.

    **Why equality and not `"person_name" not in columns`.** A negative assertion naming one
    spelling survives every other spelling: `employee`, `comment`, `justification`, `reason`,
    `notes`, `replacement`. Each of them is exactly as much personal data as `person_name`, and each
    of them is a plausible name for somebody to add "just as an optional field". An equality fails
    for all of them, including the ones nobody has thought of yet.

    The expected set is non-empty and is asserted to be so, because `set() == set()` is the way this
    test passes against a table that does not exist.
    """
    assert ABSENCE_COLUMNS, "the expected column set is empty — this test would prove nothing"

    columns = set(
        db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'staffing_position_absence'"
            )
        ).scalars()
    )

    assert columns == ABSENCE_COLUMNS, (
        "the absence row's columns changed. ADR-0005's addendum of 2026-09-22 (point 6) forbids a "
        "column for a person, a note, a justification or a comment on this table — an absence "
        "hangs on an anonymous position and names its kind only through the dictionary. "
        f"Unexpected: {sorted(columns - ABSENCE_COLUMNS)}; missing: "
        f"{sorted(ABSENCE_COLUMNS - columns)}."
    )


def test_the_absence_row_has_no_concurrency_token_of_its_own(db_session: Session) -> None:
    """K-12's structural half: the token is the position's, and only the position's.

    ADR-0007's addendum of 2026-09-22 (point 1) decides it, and this asserts it against the migrated
    database because it is a *negative* decision: an `updated_at` added to the absence row later
    would make one request carry N tokens and would need a rule for partial refusal that ADR-0007
    does not have. The position's own token is checked in the same breath, so the assertion cannot
    pass by there being no token anywhere.

    It is also what keeps K-12 honest. Every behavioural test in
    `tests/test_staffing_absence_guards.py` sends the *position's* token; with a token on the
    absence row as well, those tests would still pass while a second, unchecked concurrency
    granularity existed beside the one ADR-0007 decided.
    """
    assert "updated_at" not in ABSENCE_COLUMNS
    absence_columns = set(
        db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'staffing_position_absence'"
            )
        ).scalars()
    )
    position_columns = set(
        db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'staffing_position'"
            )
        ).scalars()
    )

    assert "updated_at" not in absence_columns
    assert "updated_at" in position_columns


def test_the_database_refuses_an_absence_whose_period_ends_before_it_starts(
    db_session: Session,
) -> None:
    """An inverted range is refused by a CHECK, as on every other period in this schema.

    Load-bearing rather than tidy: an inverted range intersects no calendar day at all
    (`app.domain.capacity.absence_day_equivalents_in_month` counts the intersection), so without the
    refusal it would be an absence that silently consumes nothing — a planner's day off that the
    capacity figure quietly ignores.

    The contrast comes **first**, and that order is forced rather than chosen: rolling back after
    the refusal discards the savepoint the fixture rows live in. A one-day absence (`start == end`)
    is the contrast, so the constraint is shown to refuse an inverted period rather than a
    zero-length one.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    position = make_staffing_position(db_session, scenario, make_dimension_tuple(db_session))
    absence_type = make_absence_type(db_session)

    one_day = make_absence(
        db_session, position, absence_type, start_date=MARCH, end_date=MARCH
    )
    assert one_day.start_date == one_day.end_date

    with pytest.raises(IntegrityError) as error:
        make_absence(
            db_session, position, absence_type, start_date=APRIL, end_date=MARCH
        )
    assert "absence_period_ordered" in str(error.value)
    db_session.rollback()


def test_two_absences_of_one_position_over_the_same_days_are_legal(db_session: Session) -> None:
    """ADR-0008's `EXCLUDE` pattern is deliberately **not** applied here (criterion K-06).

    Not an acceptance criterion — the contrast that keeps the decision from drifting into its
    opposite. A position with `headcount = 3` is three people, and two of them may be away over the
    same days; two absences with the same range are one plan, not a conflict. An `EXCLUDE USING
    gist` copied over from `catalog_default_rates` "for symmetry" would refuse the row that makes
    K-06's arithmetic meaningful, and the failure would look like a data problem rather than a
    schema decision.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    position = make_staffing_position(
        db_session, scenario, make_dimension_tuple(db_session), headcount=3
    )
    absence_type = make_absence_type(db_session)

    first = make_absence(
        db_session, position, absence_type, start_date=MARCH, end_date=APRIL
    )
    second = make_absence(
        db_session, position, absence_type, start_date=MARCH, end_date=APRIL
    )

    assert first.id != second.id
    assert (
        db_session.execute(
            sa.text(
                "SELECT count(*) FROM staffing_position_absence WHERE position_id = :id"
            ),
            {"id": position.id},
        ).scalar_one()
        == 2
    )


def test_the_allocation_row_has_no_concurrency_token_of_its_own(db_session: Session) -> None:
    """ADR-0007, addendum 2026-09-19: the token is the position's, and only the position's.

    Asserted on the columns the migrated database really has, because this is a *negative* decision:
    an `updated_at` added to the allocation row later would make one request carry N tokens and
    would need a rule for partial refusal that ADR-0007 does not have. The position's own token is
    checked in the same breath, so the assertion cannot pass by there being no token anywhere.
    """
    allocation_columns = set(
        db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'staffing_position_allocation'"
            )
        ).scalars()
    )
    position_columns = set(
        db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'staffing_position'"
            )
        ).scalars()
    )

    assert "updated_at" not in allocation_columns
    assert "updated_at" in position_columns
