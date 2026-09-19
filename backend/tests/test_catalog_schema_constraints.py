"""SC-2-01, K-05 / K-06 / K-07 — what the database refuses, whoever is writing.

Every test here writes through the ORM or raw SQL, with no request schema anywhere in the path.
That is the point: `CatalogRateCreateRequest` only ever sees HTTP requests, while a fixture, a seed
script, a future import or a second endpoint writes straight to the table. A guarantee that lives
in Pydantic is a guarantee about one caller.

Real PostgreSQL, real migration (ADR-0001, `tests/conftest.py`): an `EXCLUDE USING gist` constraint
and a generated column cannot be proven against a stand-in that does not implement them — and this
is the repository's **first** use of that pattern (ADR-0008), so what is proven here is also the
precedent for `exchange_rates` (ADR-0006) and `commercial_terms` (ADR-0003).

What none of this proves: that the application role on the (not yet chosen) target environment may
run `CREATE EXTENSION btree_gist`. The container these tests run in has a superuser role — ADR-0008,
point 7 names that limit rather than leaving it to be discovered at the first deployment.
"""

import importlib.util
import uuid
from dataclasses import replace
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

from app.core.identity import Permission
from app.data.catalog import (
    DIMENSION_MODELS,
    CatalogWriteFailed,
    CatalogWriteRefused,
    create_rate,
)
from app.data.write_errors import REFUSAL_BY_SQLSTATE
from app.models import CatalogSeniority
from app.models import catalog as catalog_models
from app.models.catalog import (
    NO_OVERLAP_CONSTRAINT,
    RATE_DIMENSION_COLUMNS,
    CatalogDefaultRate,
)
from tests.conftest import (
    IN_SCOPE_USER,
    DimensionTuple,
    as_caller,
    caller_holding,
    count_dimension_entries,
    count_rates,
    make_dimension_tuple,
    make_rate,
    rate_payload,
)

WINDOW = (date(2026, 1, 1), date(2026, 6, 30))
LATER_WINDOW = (date(2026, 7, 1), date(2026, 12, 31))

Listener = Any


# --- K-05: overlapping windows for one tuple, refused by the database ----------------------------


def test_k_05_the_database_refuses_a_second_overlapping_window_for_the_same_tuple(
    db_session: Session,
) -> None:
    """K-05, single connection. Two overlapping windows, one tuple → `IntegrityError`.

    The overlap is partial (the second window starts inside the first and ends after it), not a
    duplicate: an equality-based unique constraint would accept it, and "no two rows with the same
    dates" is a weaker claim than "no two rows whose periods intersect".

    The error is asserted to name the constraint, so the refusal is *this* mechanism and not a
    foreign key, a NOT NULL or a CHECK that happened to fire first."""
    dimensions = make_dimension_tuple(db_session)
    make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1])

    with pytest.raises(IntegrityError) as error:
        make_rate(
            db_session,
            dimensions,
            effective_from=date(2026, 6, 1),
            effective_to=date(2026, 9, 30),
        )

    assert NO_OVERLAP_CONSTRAINT in str(error.value)
    db_session.rollback()


def test_k_05_an_open_ended_window_overlaps_everything_after_its_start(
    db_session: Session,
) -> None:
    """K-05. `effective_to IS NULL` is unbounded, not "ends today" and not a `9999-12-31` sentinel.

    Without this, "open-ended" could be implemented as an empty upper bound that overlaps
    nothing — which is the failure mode of a range whose bounds are wrong: `&&` against an empty
    `daterange` is false for everything, so the constraint would silently stop applying to
    exactly the rows most likely to exist (the current, open-ended rate)."""
    dimensions = make_dimension_tuple(db_session)
    make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=None)

    with pytest.raises(IntegrityError) as error:
        make_rate(db_session, dimensions, effective_from=date(2030, 1, 1), effective_to=None)

    assert NO_OVERLAP_CONSTRAINT in str(error.value)
    db_session.rollback()


def test_k_05_a_disjoint_window_for_the_same_tuple_is_accepted(db_session: Session) -> None:
    """K-05's contrast, one day apart: a constraint refusing everything would pass the tests above.

    The second window starts the day after the first ends, which is also the assertion that the
    inclusive `effective_to` is converted exactly once: with the `+ 1 day` applied twice, these two
    adjacent windows would overlap and this test would fail.
    """
    dimensions = make_dimension_tuple(db_session)
    make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1])

    make_rate(db_session, dimensions, effective_from=LATER_WINDOW[0], effective_to=LATER_WINDOW[1])

    assert count_rates(db_session) == 2


def test_k_05_the_exclude_constraint_and_btree_gist_exist_in_the_migrated_database(
    db_session: Session,
) -> None:
    """Gate-1 decision 10: the constraint exists in the *migrated database*, not only in a file.

    A test that grepped the migration for "EXCLUDE" would pass against a migration that fails to
    apply, and a test that only triggered the refusal could in principle be satisfied by some
    other mechanism with the same name. This asks the catalogue tables PostgreSQL actually has:
    the extension is installed, and the constraint is of type `x` (exclusion) on
    `catalog_default_rates`."""
    extension = db_session.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
    ).scalar_one_or_none()
    constraint_type = db_session.execute(
        sa.text("SELECT contype FROM pg_constraint WHERE conname = :name"),
        {"name": NO_OVERLAP_CONSTRAINT},
    ).scalar_one_or_none()

    assert extension == 1, "btree_gist is not installed — the migration did not create it"
    assert constraint_type == "x", (
        f"{NO_OVERLAP_CONSTRAINT} is not an exclusion constraint (contype={constraint_type!r})"
    )


def test_k_05_valid_period_is_generated_by_the_database_from_the_inclusive_effective_to(
    db_session: Session,
) -> None:
    """K-05's foundation: the window is one generated column, and the `+ 1 day` lives only in it.

    `effective_to = 2026-06-30` is stored as `[2026-01-01, 2026-07-01)`; an open-ended row has
    no upper bound at all. Read back from the database rather than computed in the test, because
    the claim is about what the column generates — and the column is what both the constraint
    and every lookup read (ADR-0008, point 2)."""
    dimensions = make_dimension_tuple(db_session)
    closed = make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1])
    open_ended = make_rate(
        db_session,
        make_dimension_tuple(db_session, suffix=" (open)"),
        effective_from=WINDOW[0],
        effective_to=None,
    )
    db_session.expire_all()

    closed_period = db_session.get(CatalogDefaultRate, closed.id).valid_period
    open_period = db_session.get(CatalogDefaultRate, open_ended.id).valid_period

    assert (closed_period.lower, closed_period.upper) == (date(2026, 1, 1), date(2026, 7, 1))
    assert closed_period.bounds == "[)"
    assert (open_period.lower, open_period.upper) == (date(2026, 1, 1), None)


# --- K-05, the race: the guard has to be in the database, not in Python --------------------------


def _committing_an_overlapping_rate(
    engine: Engine, fired: list[str], dimensions: DimensionTuple
) -> Listener:
    """A hook that commits an overlapping rate on another connection, just before *our* INSERT runs.

    This is the window a Python check-then-act guard would have: it would have asked "does an
    overlapping row exist?" a moment ago, been told no, and would now insert a second one. A
    single-threaded pre-check survives every other test in this file — the mutation log for SC-1-02
    and SC-1-04 records the same shape surviving twice — so this interleaving is the only thing that
    kills it.

    Fires once (`fired` guards re-entry: the competing INSERT goes through the same class-level
    listener). The competitor commits immediately, because the session under test holds no lock on
    anything yet.
    """

    def interleave(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or "insert into catalog_default_rates" not in statement.lstrip().lower():
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(
                sa.text(
                    "INSERT INTO catalog_default_rates (id, role_id, seniority_id, location_id,"
                    " engagement_type_id, default_cost_rate, default_selling_rate, currency, unit,"
                    " effective_from, effective_to) VALUES (:id, :role, :seniority, :location,"
                    " :engagement, 999.0000, 999.0000, 'EUR', 'hour', :start, :end)"
                ),
                {
                    "id": uuid.uuid4(),
                    "role": dimensions.role_id,
                    "seniority": dimensions.seniority_id,
                    "location": dimensions.location_id,
                    "engagement": dimensions.engagement_type_id,
                    # The same window the request below asks for — an overlap, not an adjacency.
                    "start": WINDOW[0],
                    "end": WINDOW[1],
                },
            )

    return interleave


def test_k_05_an_overlapping_window_committed_by_a_competitor_mid_write_is_still_refused(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, two connections. The competing row lands *between* any check and the write.

    The production path holds no Python pre-check at all, so for the code as delivered this is
    simply "the INSERT loses to a committed row and the database says no". The test exists for the
    mutation: with the `EXCLUDE` removed from the migration, or with the guard rewritten as a
    `SELECT` followed by an `INSERT`, the table ends up with two overlapping windows for one tuple
    and every later resolution has to choose between them — which is the situation rule 13 forbids
    outright.

    Asserted on the table, not only on the status code: one row survives, and it is the competitor's
    (recognisable by its rate). A `409` returned while both rows were written would satisfy a
    status-only assertion.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        dimensions = make_dimension_tuple(setup)
        setup.commit()
    fired: list[str] = []
    interleave = _committing_an_overlapping_rate(engine, fired, dimensions)

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        response = committing_client.post(
            "/catalog/rates",
            json=rate_payload(
                dimensions,
                effective_from=WINDOW[0].isoformat(),
                effective_to=WINDOW[1].isoformat(),
            ),
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the competing write never ran — nothing below is about a race"
    assert response.status_code == 409, response.text
    assert NO_OVERLAP_CONSTRAINT in response.json()["detail"], response.text
    with engine.connect() as connection:
        surviving = connection.execute(
            sa.text(
                "SELECT default_selling_rate FROM catalog_default_rates"
                " WHERE role_id = :role ORDER BY default_selling_rate"
            ),
            {"role": dimensions.role_id},
        ).scalars().all()
    assert surviving == [Decimal("999.0000")], (
        "two overlapping windows for one tuple survived the race — the guard is not in the database"
    )


def test_k_05_the_refusal_message_names_the_constraint_and_quotes_no_row_values(
    db_session: Session,
) -> None:
    """ADR-0008, "Konsekwencje": the `EXCLUDE` violation is wrapped like every other write failure.

    PostgreSQL's own message for this constraint carries `DETAIL: Key (…)=(…) conflicts with
    existing key`, i.e. the row — and for this table the row holds `default_cost_rate`, the one
    field the whole response layer exists to remove (NF-11). So the write path must not let the
    driver's exception escape: the message keeps the SQLSTATE and the constraint name and drops
    everything else."""
    from app.data.catalog import CatalogWriteFailed, create_rate

    dimensions = make_dimension_tuple(db_session)
    make_rate(
        db_session,
        dimensions,
        effective_from=WINDOW[0],
        effective_to=WINDOW[1],
        default_cost_rate=Decimal("123.4567"),
    )

    with pytest.raises(CatalogWriteFailed) as error:
        create_rate(
            db_session,
            role_id=dimensions.role_id,
            seniority_id=dimensions.seniority_id,
            location_id=dimensions.location_id,
            engagement_type_id=dimensions.engagement_type_id,
            default_cost_rate=Decimal("999.1111"),
            default_selling_rate=Decimal("999.2222"),
            currency="EUR",
            unit="hour",
            effective_from=WINDOW[0],
            effective_to=WINDOW[1],
        )

    message = str(error.value)
    assert NO_OVERLAP_CONSTRAINT in message
    assert "sqlstate=23P01" in message  # exclusion_violation
    assert "999.1111" not in message
    assert "123.4567" not in message
    assert "conflicts with existing key" not in message


# --- K-06: the key is the full dimension tuple ---------------------------------------------------


@pytest.mark.parametrize("dimension", RATE_DIMENSION_COLUMNS)
def test_k_06_a_row_differing_in_exactly_one_dimension_shares_the_window(
    db_session: Session, dimension: str
) -> None:
    """K-06, four runs — one per dimension, each differing from the base tuple in that one column.

    Four parametrised runs rather than one test with four inserts: the criterion is that *each* of
    the four columns is part of the key, and the mutation it names (removing one column from the
    constraint, four times over) must kill exactly its own run. Four inserts in one test would all
    die together and name none of them.

    A "Senior Backend Engineer in Poland, full-time" and a "Senior Backend Engineer in Germany,
    full-time" are two different things that cost two different amounts at the same time — that
    is the whole content of this criterion."""
    base = make_dimension_tuple(db_session)
    other = make_dimension_tuple(db_session, suffix=" (other)")
    varied = replace(base, **{dimension: getattr(other, dimension)})
    make_rate(db_session, base, effective_from=WINDOW[0], effective_to=WINDOW[1])

    make_rate(db_session, varied, effective_from=WINDOW[0], effective_to=WINDOW[1])

    assert count_rates(db_session) == 2, f"a row differing in {dimension} alone was refused"


def test_k_06_a_row_differing_in_nothing_at_all_is_refused(db_session: Session) -> None:
    """K-06's contrast: identical tuple, identical window → refused by the same constraint.

    Without it, the four runs above would be satisfied by a constraint that exists only on paper: a
    table with no `EXCLUDE` at all accepts every one of them.
    """
    dimensions = make_dimension_tuple(db_session)
    make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1])

    with pytest.raises(IntegrityError) as error:
        make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1])

    assert NO_OVERLAP_CONSTRAINT in str(error.value)
    db_session.rollback()


def test_k_06_the_currency_is_not_part_of_the_key(db_session: Session) -> None:
    """ADR-0008, point 4 / gate-1 decision 9: the same tuple in two currencies is still two rates
    too many, and the database refuses the second one.

    Stated as a test because the alternative is attractive and wrong: allowing one rate per currency
    would mean a lookup has to decide *which* currency applies, i.e. a second unnamed resolution
    mechanism. Conversion goes through `exchange_rates` (ADR-0006), not through parallel rows.
    """
    dimensions = make_dimension_tuple(db_session)
    make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1])

    with pytest.raises(IntegrityError) as error:
        make_rate(
            db_session,
            dimensions,
            effective_from=WINDOW[0],
            effective_to=WINDOW[1],
            currency="USD",
        )

    assert NO_OVERLAP_CONSTRAINT in str(error.value)
    db_session.rollback()


# --- K-07: the unit is unambiguous, enforced below the request schema ----------------------------


@pytest.mark.parametrize("unit", ["day", "month", "Hour", "HOUR", "hours", "", " hour"])
def test_k_07_the_database_refuses_a_unit_other_than_hour(db_session: Session, unit: str) -> None:
    """K-07, first half. A unit written around the request schema is refused, by a named mechanism.

    Written through the ORM, so `CatalogRateCreateRequest` is nowhere in the path — which is the
    criterion: the Pydantic validator defends the status code, the CHECK constraint defends the
    table.

    The case variants matter as much as `"day"`: a rate row reading `"Hour"` would satisfy any
    equality the application later writes against `"hour"` only by accident, and two spellings
    of one unit in one column is the same class of defect as two spellings of one currency."""
    dimensions = make_dimension_tuple(db_session)

    with pytest.raises(IntegrityError) as error:
        make_rate(
            db_session,
            dimensions,
            effective_from=WINDOW[0],
            effective_to=WINDOW[1],
            unit=unit,
        )

    assert "ck_catalog_default_rates_unit_is_hour" in str(error.value)
    db_session.rollback()


def test_k_07_the_unit_hour_is_accepted(db_session: Session) -> None:
    """The contrast, one value apart — a CHECK refusing every unit would pass the test above."""
    dimensions = make_dimension_tuple(db_session)

    rate = make_rate(
        db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1], unit="hour"
    )
    db_session.expire_all()

    assert db_session.get(CatalogDefaultRate, rate.id).unit == "hour"


def test_k_07_the_api_refuses_a_unit_other_than_hour_with_a_422(
    client: TestClient, db_session: Session
) -> None:
    """The boundary's share of K-07: a request naming another unit is a `422`, not a `500`.

    The database constraint stays the guarantee; this is about which answer a client gets, and about
    not writing a row that the constraint would have to reject at the last moment.
    """
    dimensions = make_dimension_tuple(db_session)

    response = client.post(
        "/catalog/rates",
        json=rate_payload(dimensions, unit="day"),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert count_rates(db_session) == 0


@pytest.mark.parametrize("currency", ["EU", "E", ""])
def test_the_database_refuses_a_currency_that_is_not_three_characters(
    db_session: Session, currency: str
) -> None:
    """Added by QA: `ck_catalog_default_rates_currency_iso4217` had no test at all.

    Removing that CHECK from the migration passed all 174 delivered tests — the constraint was
    written, documented in two places (`app.models.catalog`, the migration) and proven nowhere. The
    boundary's `Iso4217Code` pattern only ever sees requests, which is the same argument criterion
    K-07 makes for the unit: a fixture, a seed script or an F-11 import writes straight to the
    table.

    Why a shorter code and not a longer one: `String(3)` already truncates-or-refuses anything
    longer at the type level, so `"EURO"` would prove the column width, not this constraint.
    `"EU"` and `""` fit the column and are exactly what the CHECK exists to stop — a two-letter
    code is a country, and a rate whose currency is a country is a money value with no unit
    (ADR-0006 keeps currencies an open list precisely so the *shape* is the only guarantee).
    """
    dimensions = make_dimension_tuple(db_session)

    with pytest.raises(IntegrityError) as error:
        make_rate(
            db_session,
            dimensions,
            effective_from=WINDOW[0],
            effective_to=WINDOW[1],
            currency=currency,
        )

    assert "ck_catalog_default_rates_currency_iso4217" in str(error.value)
    db_session.rollback()


def test_the_database_refuses_a_lowercase_currency_code(db_session: Session) -> None:
    """`Iso4217Code` refuses `"eur"` at the API boundary; a write bypassing the schema (fixture,
    seed script, F-11 import) must not create a second, lower-case spelling of one currency in the
    same table — the exact class `unit` is already guarded against by `unit_is_hour`."""
    dimensions = make_dimension_tuple(db_session)

    with pytest.raises(IntegrityError) as error:
        make_rate(
            db_session,
            dimensions,
            effective_from=WINDOW[0],
            effective_to=WINDOW[1],
            currency="eur",
        )

    assert "ck_catalog_default_rates_currency_is_upper" in str(error.value)
    db_session.rollback()


def test_a_three_character_currency_is_accepted(db_session: Session) -> None:
    """The contrast, one character apart — a CHECK refusing every currency would pass the runs
    above, and the catalogue would hold no rates at all."""
    dimensions = make_dimension_tuple(db_session)

    rate = make_rate(
        db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1], currency="USD"
    )
    db_session.expire_all()

    assert db_session.get(CatalogDefaultRate, rate.id).currency == "USD"


def test_the_database_refuses_an_inverted_effective_window(db_session: Session) -> None:
    """Not a criterion of its own, and load-bearing for K-05 all the same.

    `effective_to` one day before `effective_from` generates an *empty* `daterange`, and `&&` is
    false against an empty range — so such a row would sit outside the overlap constraint
    entirely, silently exempt from the guarantee every other row has. `effective_period_ordered`
    is what keeps every window non-empty."""
    dimensions = make_dimension_tuple(db_session)

    with pytest.raises(IntegrityError) as error:
        make_rate(
            db_session,
            dimensions,
            effective_from=date(2026, 6, 30),
            effective_to=date(2026, 6, 29),
        )

    assert "ck_catalog_default_rates_effective_period_ordered" in str(error.value)
    db_session.rollback()


def test_the_database_refuses_a_blank_dimension_name(db_session: Session) -> None:
    """The dictionaries get the same non-blank guarantee `projects` has (migration `4f0a9c1b7d62`).

    A dimension entry *is* its name; `'   '` as a role is a row no screen can render and no
    planner can choose, and `NOT NULL` alone admits it."""
    from app.models import CatalogRole

    db_session.add(CatalogRole(id=uuid.uuid4(), name="  \t "))

    with pytest.raises(IntegrityError) as error:
        db_session.flush()

    assert "ck_catalog_roles_name_not_blank" in str(error.value)
    db_session.rollback()


def test_a_duplicate_dimension_name_is_refused_with_a_409(
    client: TestClient, db_session: Session
) -> None:
    """Not a criterion — the refusal branch of `POST /catalog/dimensions/{dimension}`.

    Two roles both called "Backend Engineer" are two choices no screen can tell apart, and they
    would split the rate table into two halves that look like one. `409`, not `500`: the request was
    understood and refused by the state of the catalogue, and the message names the constraint
    rather than guessing at a cause.
    """
    from app.models import CatalogRole

    first = client.post(
        "/catalog/dimensions/roles",
        json={"name": "Backend Engineer"},
        headers=as_caller(IN_SCOPE_USER),
    )
    second = client.post(
        "/catalog/dimensions/roles",
        json={"name": "Backend Engineer"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 409, second.text
    assert "uq_catalog_roles_name_normalized" in second.json()["detail"]
    assert count_dimension_entries(db_session, CatalogRole) == 1


def test_a_rate_pointing_at_a_dimension_that_does_not_exist_is_refused_with_a_409(
    client: TestClient, db_session: Session
) -> None:
    """Not a criterion — the foreign keys, which are what makes the four dimensions references.

    Without them a rate could name a role nobody ever created (or one that was deleted), and the
    resolution lookup would answer with a rate for a tuple that has no meaning. Deleting a dimension
    entry a rate still points at is out of scope for SC-2-01 (it needs a decision about referential
    history, ADR-0004), so the foreign keys carry no `ON DELETE` action: the database refuses, which
    pre-empts neither answer.
    """
    dimensions = make_dimension_tuple(db_session)

    response = client.post(
        "/catalog/rates",
        json=rate_payload(replace(dimensions, role_id=uuid.uuid4())),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 409, response.text
    assert "fk_catalog_default_rates_role_id" in response.json()["detail"]
    assert count_rates(db_session) == 0


# --- R-04: two names that differ only in case or spacing are one name ----------------------------


SAME_NAME_AS_SENIOR = ["senior", "SENIOR", "Senior", " Senior ", "\tSenior\n"]
SAME_NAME_AS_SENIOR_DEV = ["Senior  Dev", "senior dev", "  Senior   Dev  "]


@pytest.mark.parametrize(
    ("existing", "variant"),
    [("Senior", variant) for variant in SAME_NAME_AS_SENIOR]
    + [("Senior Dev", variant) for variant in SAME_NAME_AS_SENIOR_DEV],
)
def test_r_04_a_dimension_name_differing_only_in_case_or_spacing_is_the_same_name(
    db_session: Session, existing: str, variant: str
) -> None:
    """R-04 (reviewer 2026-09-19). Measured before the fix: five "Senior"s were five rows.

    Written through the ORM, with no request schema in the path — same reasoning as the unit CHECK:
    the guarantee has to hold for a fixture, a seed script or an import, not only for HTTP callers.
    A `SELECT`-then-`INSERT` validator would also lose the race between two callers adding "Senior"
    at the same moment, which is the mutation that has already survived delivered tests twice here.

    `" Senior "` and `"\\tSenior\\n"` are in the list although the request schema strips
    whitespace: below the schema nothing does, so trimming has to be part of what the database
    considers the same name."""
    db_session.add(CatalogSeniority(id=uuid.uuid4(), name=existing))
    db_session.flush()
    db_session.add(CatalogSeniority(id=uuid.uuid4(), name=variant))

    with pytest.raises(IntegrityError) as error:
        db_session.flush()

    assert "uq_catalog_seniorities_name_normalized" in str(error.value)
    db_session.rollback()


@pytest.mark.parametrize("second_name", ["Senior Developer", "Seniors", "Sénior", "Mid"])
def test_r_04_a_genuinely_different_dimension_name_is_still_accepted(
    db_session: Session, second_name: str
) -> None:
    """R-04's contrast: the index must refuse near-duplicates, not everything that looks similar.

    `"Sénior"` is in the list deliberately — the normalisation is case and whitespace only, and
    it is not a transliteration: two languages' spellings of a role are two entries until
    somebody decides otherwise. Without this test, a normalisation that collapsed far more than
    it should (stripping accents, or every non-letter) would pass the parametrised refusals
    above."""
    db_session.add(CatalogSeniority(id=uuid.uuid4(), name="Senior"))
    db_session.flush()

    db_session.add(CatalogSeniority(id=uuid.uuid4(), name=second_name))
    db_session.flush()

    assert count_dimension_entries(db_session, CatalogSeniority) == 2


def test_r_04_the_api_refuses_a_name_that_differs_only_in_case_and_keeps_the_original_spelling(
    client: TestClient, db_session: Session
) -> None:
    """R-04 through the endpoint: `409`, one row left, and the stored name is what was *sent*.

    The last assertion is the half that says this is a refusal and not a normalisation:
    `"Senior"` is stored as `"Senior"`, not folded to `"senior"` on the way in. Silently
    rewriting input is what `Iso4217Code` refuses to do for `"eur"` — the index key is computed,
    the column is left alone."""
    first = client.post(
        "/catalog/dimensions/seniorities",
        json={"name": "Senior"},
        headers=as_caller(IN_SCOPE_USER),
    )
    second = client.post(
        "/catalog/dimensions/seniorities",
        json={"name": "SENIOR"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 409, second.text
    assert "uq_catalog_seniorities_name_normalized" in second.json()["detail"]
    assert count_dimension_entries(db_session, CatalogSeniority) == 1
    listed = client.get("/catalog/dimensions/seniorities", headers=as_caller(IN_SCOPE_USER))
    assert [entry["name"] for entry in listed.json()["entries"]] == ["Senior"]


# --- R-01: a failure nobody classified is not a conflict -----------------------------------------


UNCLASSIFIED_FAILURES = {
    # 22003 numeric_field_overflow: 11 integer digits do not fit NUMERIC(14,4).
    "an amount wider than the column": {"default_cost_rate": Decimal("99999999999.0000")},
    # The generated column asks for `9999-12-31 + 1 day`, which the driver cannot carry back. There
    # is no SQLSTATE at all here — the failure happens client-side — which is why the classification
    # asks "is this code one of the four", never "is it absent from a list of fatal ones".
    "a date whose window end cannot be represented": {"effective_to": date(9999, 12, 31)},
}


@pytest.mark.parametrize("case", sorted(UNCLASSIFIED_FAILURES))
def test_r_01_a_write_failure_no_sqlstate_classifies_is_broken_not_refused(
    db_session: Session, case: str
) -> None:
    """R-01 (reviewer 2026-09-19). Measured before the fix: both of these answered `409` "overlaps".

    Both are defects, not conflicts: nothing about the existing rows caused them, and no other row
    the caller could pick would avoid them. Reported as a refusal, they sent whoever hit them
    looking for an overlapping window that did not exist.

    The assertions are about the *type*, because the type is what the endpoint branches on: a
    `CatalogWriteFailed` that is not a `CatalogWriteRefused` cannot become a `409`. The message is
    asserted not to name any of the four refusal reasons, and not to carry the amount (NF-11).
    """
    dimensions = make_dimension_tuple(db_session)
    arguments: dict[str, Any] = {
        "role_id": dimensions.role_id,
        "seniority_id": dimensions.seniority_id,
        "location_id": dimensions.location_id,
        "engagement_type_id": dimensions.engagement_type_id,
        "default_cost_rate": Decimal("100.0000"),
        "default_selling_rate": Decimal("150.0000"),
        "currency": "EUR",
        "unit": "hour",
        "effective_from": date(2026, 1, 1),
        "effective_to": date(2026, 6, 30),
        **UNCLASSIFIED_FAILURES[case],
    }

    with pytest.raises(CatalogWriteFailed) as error:
        create_rate(db_session, **arguments)

    assert not isinstance(error.value, CatalogWriteRefused), (
        f"{case} was classified as a refusal — it would be served as a 409 naming a cause nobody "
        "established"
    )
    message = str(error.value)
    for reason in REFUSAL_BY_SQLSTATE.values():
        assert reason not in message, message
    assert NO_OVERLAP_CONSTRAINT not in message
    assert "99999999999" not in message
    db_session.rollback()


def test_r_01_a_refusal_the_sqlstate_does_classify_still_names_its_reason(
    db_session: Session,
) -> None:
    """R-01's contrast: the four classified SQLSTATEs must still produce a refusal with a reason.

    Without this, the fix could have been "never report a reason", which trades a false answer
    for no answer. The overlap case carries both halves: the constraint name from the driver's
    diagnostics and the wording keyed by `23P01`."""
    dimensions = make_dimension_tuple(db_session)
    make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1])
    db_session.commit()

    with pytest.raises(CatalogWriteRefused) as error:
        create_rate(
            db_session,
            role_id=dimensions.role_id,
            seniority_id=dimensions.seniority_id,
            location_id=dimensions.location_id,
            engagement_type_id=dimensions.engagement_type_id,
            default_cost_rate=Decimal("100.0000"),
            default_selling_rate=Decimal("150.0000"),
            currency="EUR",
            unit="hour",
            effective_from=WINDOW[0],
            effective_to=WINDOW[1],
        )

    message = str(error.value)
    assert "sqlstate=23P01" in message
    assert NO_OVERLAP_CONSTRAINT in message
    assert REFUSAL_BY_SQLSTATE["23P01"] in message


def test_r_01_an_unclassified_failure_reaches_the_client_as_a_500_not_as_a_conflict(
    client_serving_server_errors: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-01 at the endpoint: the `except` clause catches refusals only.

    The failure is injected rather than provoked, because after this round the request schema bounds
    both amounts and both dates to what the column can hold — so no *request* can still reach the
    database and fail this way. That is the better fix, and it leaves the endpoint's branch untested
    unless something stands in for the broken write. What is asserted is exactly the branch: a
    `CatalogWriteFailed` propagates (`500`), and the body says nothing about a conflict.
    """
    dimensions = make_dimension_tuple(db_session)

    def fail_unclassified(*args: object, **kwargs: object) -> None:
        raise CatalogWriteFailed("Writing the catalogue rate failed: DataError, sqlstate=22003")

    monkeypatch.setattr("app.api.catalog.create_rate", fail_unclassified)
    response = client_serving_server_errors.post(
        "/catalog/rates", json=rate_payload(dimensions), headers=as_caller(IN_SCOPE_USER)
    )

    assert response.status_code == 500, response.text
    assert "Refused by the database" not in response.text
    assert "overlap" not in response.text.lower()


# --- R-05 / R-01: the request boundary, bounded by the column it protects -------------------------


@pytest.mark.parametrize("amount", ["123.45678", "0.00001", "100.123456789"])
def test_r_05_an_amount_more_precise_than_the_column_is_refused_not_truncated(
    client: TestClient, db_session: Session, amount: str
) -> None:
    """R-05 (reviewer 2026-09-19). Measured before the fix: `123.45678` was stored as `123.4568`.

    Silent rounding at write time is the one thing ADR-0008 point 6, `create_rate`'s docstring and
    this task's own report all say does not happen — a rate is an input, not the result of a
    rounding step. `422`, and nothing written: the caller is told their value cannot be kept,
    instead of discovering later that the catalogue holds a number they never entered.

    `0.00001` is in the list because it rounds to `0.0000`, i.e. to a *free* hour of work — the case
    where silent truncation stops being a rounding error and becomes a different fact.
    """
    dimensions = make_dimension_tuple(db_session)

    response = client.post(
        "/catalog/rates",
        json=rate_payload(dimensions, default_cost_rate=amount),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert "decimal" in response.text.lower()
    assert count_rates(db_session) == 0


def test_r_05_four_decimal_places_are_accepted_and_kept_exactly(
    client: TestClient, db_session: Session
) -> None:
    """R-05's contrast: the boundary refuses *more* precision than the column, not the column's own.

    Trailing zeros are not precision either — `"100.00000"` names a value scale 4 can hold exactly,
    so it is accepted. Without this half, `decimal_places=0` would pass the refusals above.
    """
    dimensions = make_dimension_tuple(db_session)

    exact = client.post(
        "/catalog/rates",
        json=rate_payload(
            dimensions, default_cost_rate="123.4567", default_selling_rate="100.00000"
        ),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert exact.status_code == 201, exact.text
    assert exact.json()["default_selling_rate"] == "100.0000"
    with caller_holding(Permission.CATALOG_READ, Permission.PERSONNEL_COSTS_READ):
        read_back = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))
    assert read_back.json()["rates"][0]["default_cost_rate"] == "123.4567"


def test_r_01_an_amount_wider_than_the_column_is_a_422_about_the_field(
    client: TestClient, db_session: Session
) -> None:
    """R-01, the half that is better fixed at the boundary than classified after the fact.

    `99999999999.0000` (11 integer digits) cannot fit `NUMERIC(14,4)`. It used to travel all the way
    to the database and come back as a `409` about an overlapping window; now it does not travel at
    all, and the message names the field and the limit.
    """
    dimensions = make_dimension_tuple(db_session)

    response = client.post(
        "/catalog/rates",
        json=rate_payload(dimensions, default_selling_rate="99999999999.0000"),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert "default_selling_rate" in response.text
    assert count_rates(db_session) == 0


@pytest.mark.parametrize(
    ("field", "other_dates"),
    [
        # With `effective_from` in 9999 the window would also be inverted, and the ordering
        # validator answers first — so the end is left open here. The two refusals are separate
        # claims and this test is about the horizon one.
        ("effective_from", {"effective_to": None}),
        ("effective_to", {}),
    ],
)
def test_r_01_a_date_beyond_the_planning_horizon_is_a_422_pointing_at_null_instead(
    client: TestClient, db_session: Session, field: str, other_dates: dict[str, object]
) -> None:
    """R-01's other half: `effective_to = 9999-12-31` used to be a `409` claiming an overlap.

    The generated column adds a day to `effective_to`, so that value asks PostgreSQL for
    `10000-01-01` — a date the driver cannot carry back, which surfaced as a `DataError` with no
    SQLSTATE at all. Refused at the boundary now, with the message naming the representation that
    does exist for "no end": `effective_to: null` (ADR-0008, point 2).
    """
    dimensions = make_dimension_tuple(db_session)

    response = client.post(
        "/catalog/rates",
        json=rate_payload(dimensions, **{field: "9999-12-31"}, **other_dates),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"][0]["msg"]
    assert field in detail, detail
    assert "null" in detail, detail
    assert count_rates(db_session) == 0


def test_r_01_an_open_ended_window_and_a_far_but_sane_date_are_both_accepted(
    client: TestClient, db_session: Session
) -> None:
    """The contrast for the horizon: `null` is how "no end" is said, and the horizon is not a
    restriction on normal planning.

    `2999-12-31` is exactly the boundary, so an off-by-one in the comparison (`>=` instead of `>`)
    fails here rather than silently narrowing what callers may plan for.
    """
    open_ended = client.post(
        "/catalog/rates",
        json=rate_payload(make_dimension_tuple(db_session, suffix=" (open)"), effective_to=None),
        headers=as_caller(IN_SCOPE_USER),
    )
    at_the_horizon = client.post(
        "/catalog/rates",
        json=rate_payload(
            make_dimension_tuple(db_session, suffix=" (horizon)"), effective_to="2999-12-31"
        ),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert open_ended.status_code == 201, open_ended.text
    assert open_ended.json()["effective_to"] is None
    assert at_the_horizon.status_code == 201, at_the_horizon.text
    assert at_the_horizon.json()["effective_to"] == "2999-12-31"


# --- R-02: the model and the migration hold the same SQL twice -----------------------------------


def test_the_model_and_the_migration_agree_on_every_sql_expression() -> None:
    """R-02 (reviewer + invariant-guardian 2026-09-19): nothing used to compare the two copies.

    `app.models.catalog` and migration `7b3d5c81e40a` each spell the generated-column expression
    and the normalised-name key, on purpose — a migration must keep describing the schema it
    produced, so it cannot import a constant the model is free to change. The measured gap:
    `alembic upgrade` ignores the model's `Computed(...)` entirely, so editing one copy alone broke
    no test, and the model would then describe a database nobody has.

    A string comparison, not a full `alembic check`: it catches the realistic edit (somebody changes
    the expression in the file they are reading) and it needs no database. The behavioural proof of
    what the migrated database really generates stays
    `test_k_05_valid_period_is_generated_by_the_database_from_the_inclusive_effective_to`.

    The table list and the `EXCLUDE` key are compared for the same reason: a fifth dictionary or a
    fifth dimension added to the model alone would produce a model that cannot be migrated to.
    """
    migration = _load_migration_module()

    assert migration._VALID_PERIOD_EXPRESSION == catalog_models.VALID_PERIOD_EXPRESSION
    assert (
        migration._DIMENSION_NAME_KEY_EXPRESSION == catalog_models.DIMENSION_NAME_KEY_EXPRESSION
    )
    assert migration._DIMENSION_KEY == catalog_models.RATE_DIMENSION_COLUMNS
    assert tuple(migration._DIMENSION_TABLES) == tuple(
        model.__tablename__ for model in DIMENSION_MODELS.values()
    )


def _load_migration_module() -> ModuleType:
    """Import the migration by path — `migrations/versions` is not a package, and the module name
    starts with a digit, so neither `import` nor `importlib.import_module` can reach it."""
    path = (
        Path(__file__).resolve().parent.parent
        / "migrations"
        / "versions"
        / "7b3d5c81e40a_create_catalog_dimensions_and_default_rates.py"
    )
    specification = importlib.util.spec_from_file_location("sc_2_01_migration", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module
