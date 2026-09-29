"""SC-2-01 (K-05/K-06/K-07) and SC-2-03 (K-01/K-02/K-07/K-08) — what the database refuses, whoever
is writing.

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
from datetime import date, timedelta
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
    DEFAULT_RATE_LIST_LIMIT,
    DIMENSION_MODELS,
    MAX_RATE_LIST_LIMIT,
    MAX_RATE_LIST_OFFSET,
    CatalogWriteFailed,
    CatalogWriteRefused,
    create_rate,
    rate_page_statement,
)
from app.data.write_errors import REFUSAL_BY_SQLSTATE
from app.models import CatalogSeniority, CatalogVendor
from app.models import catalog as catalog_models
from app.models.catalog import (
    NO_OVERLAP_CONSTRAINT,
    RATE_DIMENSION_COLUMNS,
    RATE_EXCLUDE_KEY,
    RATE_PAGE_INDEX,
    VENDOR_KEY_SENTINEL,
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
    make_vendor,
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


# --- SC-2-03, K-01: the vendor is the fifth element of the key -----------------------------------


def test_k_01_an_internal_rate_and_two_vendors_rates_share_one_tuple_and_one_window(
    db_session: Session,
) -> None:
    """K-01 (SC-2-03). One tuple, one window, three rows: internal, vendor V, vendor W.

    This is the case the four-column key could not express at all, and the reason SC-2-03 rebuilds
    the constraint: "Senior Backend Engineer in Poland, full-time, in March 2026" has an internal
    cost and, at the same time, a price from every subcontractor who can staff it. Before this
    change the second of those rows was refused as an overlap — the catalogue could hold the
    organisation's own price or one vendor's, never both.

    Written through the ORM, so no request schema is in the path: the claim is about the *database*
    key, and it has to hold for a fixture, a seed script or an import too.

    **The contrast is in the same test**, and it is what keeps the three inserts from proving
    "the constraint is gone": a fourth row repeating vendor `V` — same tuple, same window, same
    vendor — is still refused, by name. A table with the `EXCLUDE` dropped altogether passes the
    first half of this test and fails the second.
    """
    dimensions = make_dimension_tuple(db_session)
    first = make_vendor(db_session, name="Contoso")
    second = make_vendor(db_session, name="Fabrikam")

    make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1])
    make_rate(
        db_session,
        dimensions,
        effective_from=WINDOW[0],
        effective_to=WINDOW[1],
        vendor_id=first.id,
    )
    make_rate(
        db_session,
        dimensions,
        effective_from=WINDOW[0],
        effective_to=WINDOW[1],
        vendor_id=second.id,
    )

    assert count_rates(db_session) == 3, (
        "an internal rate and two vendors' rates cannot share one tuple and one window — the "
        "vendor is not part of the key"
    )

    with pytest.raises(IntegrityError) as error:
        make_rate(
            db_session,
            dimensions,
            effective_from=WINDOW[0],
            effective_to=WINDOW[1],
            vendor_id=first.id,
        )

    assert NO_OVERLAP_CONSTRAINT in str(error.value)
    db_session.rollback()


# --- SC-2-03, K-02: a nullable column in the key does not switch the guarantee off ----------------


def test_k_02_two_overlapping_internal_windows_for_one_tuple_are_still_refused(
    db_session: Session,
) -> None:
    """K-02 (SC-2-03). The `NULL = NULL` trap: internal rates keep the protection they had.

    A separate criterion from K-01 and a separate mutation, because the naive implementation passes
    K-01 perfectly: adding `vendor_id` to the key as a plain column lets the three rows of K-01
    coexist *and* silently exempts every internal row from the overlap check, since an `EXCLUDE`
    reports a violation only when every operator yields `TRUE` and `NULL = NULL` yields `NULL`.
    The protection would disappear precisely for the rows that are the organisation's own.

    `COALESCE(vendor_id, VENDOR_KEY_SENTINEL)` is what keeps it (the literal nil UUID, not
    `uuid_nil()` — `uuid-ossp` is not installed on this database). The overlap here is partial, not
    a duplicate — an equality-based unique index would accept it.

    The contrast, in the same test: the same two internal windows one day apart are both accepted,
    so a constraint that refused every internal row would fail here.
    """
    dimensions = make_dimension_tuple(db_session)
    make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1])

    with pytest.raises(IntegrityError) as error:
        make_rate(
            db_session,
            dimensions,
            effective_from=date(2026, 6, 1),
            effective_to=date(2026, 9, 30),
        )

    assert NO_OVERLAP_CONSTRAINT in str(error.value), (
        "two overlapping windows of the *internal* rate were accepted — the nullable vendor column "
        "switched the overlap guarantee off for every row that has no vendor"
    )
    db_session.rollback()

    disjoint = make_dimension_tuple(db_session, suffix=" (disjoint)")
    make_rate(db_session, disjoint, effective_from=WINDOW[0], effective_to=WINDOW[1])
    make_rate(db_session, disjoint, effective_from=LATER_WINDOW[0], effective_to=LATER_WINDOW[1])

    assert count_rates(db_session) == 2, (
        "two adjacent internal windows were refused — the constraint now refuses more than overlaps"
    )


# --- SC-2-03, K-07: the vendor is a reference or nothing -----------------------------------------


def test_k_07_a_rate_naming_a_vendor_that_does_not_exist_is_refused_by_the_database(
    client: TestClient, db_session: Session
) -> None:
    """K-07 (SC-2-03), first half. A vendor id from nowhere is a `409` naming the foreign key.

    Without the foreign key a rate could name a subcontractor nobody ever created — a price
    belonging to no company, which every later screen would render as a blank vendor and every
    later calculation would treat as a real supplier's quote. The refusal is the database's, like
    the four dimensions': no `ON DELETE` action either, so removing a vendor still referenced by a
    rate is refused rather than silently detaching the price (Issue #46, out of scope point 9).

    The contrast is the same request with a vendor that *does* exist: `201`, and the id comes back
    unchanged, so a constraint refusing every vendor would fail here.

    The accepted write runs **first**, and on its own dimension tuple. Both details are about the
    failed write, not about this one: a refused write rolls the session back (that is what
    `create_rate` does with a failure), so a fixture row only flushed would disappear with it and
    the next request would be refused for a missing *role*; and a second rate for the same tuple and
    window would meet the overlap constraint as well as the foreign key, leaving it to PostgreSQL
    which of the two it reports.
    """
    for_the_refusal = make_dimension_tuple(db_session)
    for_the_contrast = make_dimension_tuple(db_session, suffix=" (known vendor)")
    vendor = make_vendor(db_session, name="Contoso")

    known = client.post(
        "/catalog/rates",
        json=rate_payload(for_the_contrast, vendor_id=str(vendor.id)),
        headers=as_caller(IN_SCOPE_USER),
    )
    unknown = client.post(
        "/catalog/rates",
        json=rate_payload(for_the_refusal, vendor_id=str(uuid.uuid4())),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert known.status_code == 201, known.text
    assert known.json()["vendor_id"] == str(vendor.id)
    assert unknown.status_code == 409, unknown.text
    assert "fk_catalog_default_rates_vendor_id" in unknown.json()["detail"], unknown.text
    assert count_rates(db_session) == 1, "the refused rate was written after all"


def test_k_07_a_rate_with_no_vendor_stores_null_not_the_exclude_sentinel(
    client: TestClient, db_session: Session
) -> None:
    """K-07 (SC-2-03), second half. The sentinel lives in the key expression, never in the data.

    `COALESCE(vendor_id, VENDOR_KEY_SENTINEL)` is how the constraint reads a missing vendor (the
    literal nil UUID, not `uuid_nil()` — `uuid-ossp` is not installed on this database); writing
    the nil UUID into the column instead would be the same idea implemented one layer too low, and
    it would be wrong in two ways at once: the foreign key would refuse it (no vendor has that
    id — the CHECK on `catalog_vendors` makes sure of it), and every reader would have to know a
    magic value to tell "internal" from "some vendor".

    Read back from the database rather than from the response, because the claim is about the
    column. The response is asserted too, in the form a client has to be able to rely on: `null`,
    not the sentinel string (criterion K-09 builds the screen's "Internal" state on exactly that).
    """
    dimensions = make_dimension_tuple(db_session)

    created = client.post(
        "/catalog/rates", json=rate_payload(dimensions), headers=as_caller(IN_SCOPE_USER)
    )

    assert created.status_code == 201, created.text
    assert created.json()["vendor_id"] is None
    stored = db_session.execute(
        sa.text("SELECT vendor_id FROM catalog_default_rates WHERE id = :id"),
        {"id": created.json()["id"]},
    ).scalar_one()
    assert stored is None, (
        f"the stored vendor is {stored!r} — a rate with no vendor must hold NULL, not the "
        f"EXCLUDE key's sentinel {VENDOR_KEY_SENTINEL}"
    )


def test_the_database_refuses_a_vendor_whose_id_is_the_exclude_sentinel(
    db_session: Session,
) -> None:
    """Not a criterion of its own — the CHECK that makes the sentinel safe (SC-2-03).

    The nil UUID stands for "no vendor" inside the `EXCLUDE` key. A vendor row carrying that id
    would make "the internal rate" and "this company's rate" the same key to the constraint, so an
    internal rate and that company's rate for one tuple and one window would collide — and the
    only thing keeping such a row out, without this CHECK, is the improbability of `uuid4()`
    producing it. Constructive exclusion is the point of ADR-0008's addendum, point 3.

    The contrast is one value away: any other id is accepted.
    """
    db_session.add(CatalogVendor(id=uuid.UUID(VENDOR_KEY_SENTINEL), name="Nil Inc."))

    with pytest.raises(IntegrityError) as error:
        db_session.flush()

    assert "ck_catalog_vendors_id_is_not_the_exclude_sentinel" in str(error.value)
    db_session.rollback()

    accepted = make_vendor(db_session, name="Contoso")

    assert db_session.get(CatalogVendor, accepted.id) is not None


def test_deleting_a_vendor_a_rate_still_names_is_refused_and_never_makes_the_rate_internal(
    db_session: Session,
) -> None:
    """The `NO ACTION` half of the vendor foreign key (SC-2-03, QA) — asserted, not only documented.

    Three places in this task state it as a fact: `CatalogDefaultRate.vendor_id`'s docstring ("the
    database refuses to delete a vendor a rate still references"), the comment above the four older
    foreign keys, and `test_k_07_a_rate_naming_a_vendor_that_does_not_exist_is_refused_by_the
    _database`'s own docstring. Nothing asserted it. The deletion direction of the key was tested
    nowhere in the suite — only the insertion direction — so `ondelete="SET NULL"` on this one
    foreign key was green across all 307 backend tests (measured, SC-2-03 QA pass).

    **Why that mutation matters more here than on the other four dimensions.** Their columns are
    `NOT NULL`, so `SET NULL` is not even expressible for them and the worst a wrong `ondelete`
    could do is delete rows loudly. `vendor_id` is nullable *and* its `NULL` is a value with a
    meaning: "the organisation's own rate" (ADR-0008, addendum 2026-09-21, point 3). `ON DELETE SET
    NULL` therefore does not lose the price — it **relabels** it. Removing a subcontractor from the
    dictionary would silently turn every one of that company's prices into the organisation's own
    cost, in the catalogue, with no error anywhere. That is precisely the harm K-04 names ("a plan
    quoting a subcontractor's price as the organisation's own cost is wrong in the direction nobody
    checks") arriving through a door K-04 does not watch: K-04 is about the *lookup*, and after this
    mutation the stored row really is internal, so every resolution answers it correctly.

    Issue #46 puts deleting a dictionary entry that is in use out of scope (point 9). Out of scope
    is a decision about the *interface*; it is not a licence for the database to answer the question
    on its own. Refusal is what keeps the decision open.

    **The fixture is chosen so that nothing else can do the catching.** The tuple is priced by this
    vendor and by nobody else: no internal row shares its window, so a `SET NULL` here collides with
    no `EXCLUDE` key and goes through cleanly. Had the fixture also carried an internal rate for the
    same tuple and window — the K-01 shape, the most natural one to reach for — the deletion would
    have been refused by the overlap constraint instead, and this test would have passed while the
    foreign key was wrong. The constraint is asserted **by name** for the same reason.

    `begin_nested`, not `rollback`: a refused statement aborts the transaction, and the rollback
    other tests in this file use would discard the rate row along with it — leaving nothing to ask
    the one question that separates "refused" from "cascaded". The savepoint keeps the fixture.

    The contrast is one fact away: a second vendor, identical in every respect except that no rate
    names it, is deleted without complaint. A foreign key that refused every deletion, or a
    dictionary table nothing can be removed from at all, fails there.
    """
    dimensions = make_dimension_tuple(db_session)
    priced = make_vendor(db_session, name="Contoso Sp. z o.o.")
    unused = make_vendor(db_session, name="Fabrikam Sp. z o.o.")
    rate = make_rate(
        db_session,
        dimensions,
        effective_from=WINDOW[0],
        effective_to=WINDOW[1],
        vendor_id=priced.id,
    )

    with pytest.raises(IntegrityError) as error:
        with db_session.begin_nested():
            db_session.execute(
                sa.text("DELETE FROM catalog_vendors WHERE id = :id"), {"id": priced.id}
            )

    assert "fk_catalog_default_rates_vendor_id" in str(error.value), (
        "the vendor a rate still references was deleted, or refused by some other constraint than "
        "its foreign key"
    )

    still_stored = db_session.execute(
        sa.text("SELECT vendor_id FROM catalog_default_rates WHERE id = :id"), {"id": rate.id}
    ).scalar_one_or_none()
    assert still_stored is not None and str(still_stored) == str(priced.id), (
        f"the rate's vendor is now {still_stored!r} — deleting the subcontractor relabelled its "
        "price as the organisation's own (ON DELETE SET NULL) or deleted the price with the "
        "company (ON DELETE CASCADE)"
    )

    # The contrast: the same deletion, on a vendor no rate names.
    db_session.execute(sa.text("DELETE FROM catalog_vendors WHERE id = :id"), {"id": unused.id})
    db_session.flush()

    surviving = db_session.execute(
        sa.text("SELECT id FROM catalog_vendors WHERE id IN (:priced, :unused)"),
        {"priced": priced.id, "unused": unused.id},
    ).scalars().all()
    assert [str(row) for row in surviving] == [str(priced.id)], (
        "an unreferenced vendor could not be deleted — the refusal above is about the reference, "
        "not about the table"
    )


# --- SC-2-03, K-08: the migrated database holds one constraint, and it is the new one -------------


# `noqa: E501` on the signature only: the name is the one criterion K-08 names in Issue #46, and a
# test whose name differs from the criterion it carries is a criterion nobody can find again.
def test_k_08_the_migrated_database_has_exactly_one_exclusion_constraint_keyed_on_the_vendor_sentinel(  # noqa: E501
    db_session: Session,
) -> None:
    """K-08 (SC-2-03). One exclusion constraint on `catalog_default_rates`, with five keys + period.

    Asserted by **equality**, not by `in` or `>=`, and that is the whole point: the shape this
    task's migration must not leave behind is "the old constraint next to the new one". A
    containment assertion is green in exactly that state, and the table would then refuse the vendor
    rows the new constraint admits — the feature switched off while every other test still passed.

    Read from `pg_constraint`, i.e. from the database the migration produced. A test reading the
    model's metadata would pass against a migration that was never edited (the gap R-02 measured),
    and a test that only provoked a refusal could be satisfied by any mechanism with the same name.
    """
    definitions = db_session.execute(
        sa.text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = 'catalog_default_rates'::regclass AND contype = 'x'"
            " ORDER BY conname"
        )
    ).all()

    expected = (
        "EXCLUDE USING gist ("
        + ", ".join(f"{element} WITH =" for element in RATE_EXCLUDE_KEY)
        + ", valid_period WITH &&)"
    )
    assert [name for name, _ in definitions] == [NO_OVERLAP_CONSTRAINT], (
        "catalog_default_rates does not carry exactly one exclusion constraint — 'the old one "
        "beside the new one' is the state this assertion exists to fail on"
    )
    assert definitions[0][1] == expected


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

    **Extended by SC-2-03** with the two constants that carry the vendor half of the key, and with
    the fifth dictionary's table. The claim is unchanged in strength and wider in reach: the schema
    is now described by *two* migrations, and the five dictionary tables are asserted to be exactly
    the four of the first plus the one of the second — in the order `DIMENSION_MODELS` lists them,
    so neither a missing table nor a sixth one added to the model alone can pass.
    """
    migration = _load_migration_module(
        "7b3d5c81e40a_create_catalog_dimensions_and_default_rates.py", "sc_2_01_migration"
    )
    vendors = _load_migration_module(
        "c1a4f7b92e05_add_catalog_vendors_and_vendor_rates.py", "sc_2_03_migration"
    )

    assert migration._VALID_PERIOD_EXPRESSION == catalog_models.VALID_PERIOD_EXPRESSION
    assert (
        migration._DIMENSION_NAME_KEY_EXPRESSION == catalog_models.DIMENSION_NAME_KEY_EXPRESSION
    )
    assert migration._DIMENSION_KEY == catalog_models.RATE_DIMENSION_COLUMNS
    # The second migration repeats the name key (it creates a dictionary of its own) and holds the
    # vendor half of the `EXCLUDE` key. Both copies are compared, so editing either file alone
    # produces a failure rather than a model describing a database nobody has.
    assert vendors._DIMENSION_NAME_KEY_EXPRESSION == catalog_models.DIMENSION_NAME_KEY_EXPRESSION
    assert vendors._VENDOR_KEY_SENTINEL == catalog_models.VENDOR_KEY_SENTINEL
    assert vendors._VENDOR_KEY_EXPRESSION == catalog_models.VENDOR_KEY_EXPRESSION
    assert vendors._EXCLUDE_KEY == catalog_models.RATE_EXCLUDE_KEY
    assert vendors._NO_OVERLAP_CONSTRAINT == catalog_models.NO_OVERLAP_CONSTRAINT
    # SC-5-05 re-armed this, not loosened it: the cost-category dictionary (ADR-0005, addendum
    # SC-5-05, point 3) comes from a third migration, which repeats the name key as well.
    cost_categories = _load_migration_module(
        "a3d9e6f20c71_create_additional_costs_and_cost_categories.py", "sc_5_05_migration"
    )
    assert (
        cost_categories._DIMENSION_NAME_KEY_EXPRESSION
        == catalog_models.DIMENSION_NAME_KEY_EXPRESSION
    )
    assert (
        *migration._DIMENSION_TABLES,
        vendors._VENDOR_TABLE,
        cost_categories._CATEGORY_TABLE,
    ) == tuple(model.__tablename__ for model in DIMENSION_MODELS.values())

    # The paging index of the gate-2 review (R-01) is the third copied schema fact on this table:
    # `app.models.catalog` declares it so the model keeps describing the database, and migration
    # `e2c7b04d9a31` is what creates it. Named in the model, in the migration and in the test that
    # asserts the plan uses it — a rename in one file alone stops here.
    paging = _load_migration_module(
        "e2c7b04d9a31_index_catalog_default_rates_for_paging.py", "sc_2_03_paging_migration"
    )
    assert paging._PAGE_INDEX == catalog_models.RATE_PAGE_INDEX
    assert paging._RATE_TABLE == CatalogDefaultRate.__tablename__


# --- K-11: GET /catalog/rates is bounded, never without a limit ----------------------------------


def _make_many_rates(
    session: Session, dimensions: DimensionTuple, count: int, *, start: date = date(2020, 1, 1)
) -> list[uuid.UUID]:
    """Bulk-insert `count` one-day, disjoint rate windows for one tuple — K-11's oversized fixture.

    Two days apart, never touching: the smallest disjoint window the `EXCLUDE` constraint accepts
    with room to spare, which is what lets a single dimension tuple carry thousands of rows without
    a matching thousands of dictionary entries. A core `INSERT ... VALUES` list, not `count` calls
    to `make_rate`: one ORM object plus one flush per row would make building the fixture pay the
    same cost the endpoint under test exists to avoid paying on every request.
    """
    ids = [uuid.uuid4() for _ in range(count)]
    rows = [
        {
            "id": row_id,
            "role_id": dimensions.role_id,
            "seniority_id": dimensions.seniority_id,
            "location_id": dimensions.location_id,
            "engagement_type_id": dimensions.engagement_type_id,
            "default_cost_rate": Decimal("100.0000"),
            "default_selling_rate": Decimal("150.0000"),
            "currency": "EUR",
            "unit": "hour",
            "effective_from": start + timedelta(days=2 * index),
            "effective_to": start + timedelta(days=2 * index),
        }
        for index, row_id in enumerate(ids)
    ]
    session.execute(sa.insert(CatalogDefaultRate), rows)
    session.flush()
    return ids


def test_k_11_the_default_page_is_bounded_and_names_the_total(
    client: TestClient, db_session: Session
) -> None:
    """K-11, first half. A small catalogue: the default call still returns everything, and says so.

    This is also the guard against a regression of K-07's SC-2-02 assertions, which read
    `rates.length`/count on catalogues this size: a limit that broke a 3-row catalogue would be a
    limit applied at the wrong end."""
    dimensions = make_dimension_tuple(db_session)
    make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1])
    make_rate(
        db_session,
        make_dimension_tuple(db_session, suffix=" (2)"),
        effective_from=WINDOW[0],
        effective_to=WINDOW[1],
    )
    make_rate(
        db_session,
        make_dimension_tuple(db_session, suffix=" (3)"),
        effective_from=WINDOW[0],
        effective_to=WINDOW[1],
    )

    response = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["rates"]) == 3
    assert body["total"] == 3


def test_k_11_total_counts_the_rows_matching_on_date_not_the_whole_table_and_not_the_page(
    client: TestClient, db_session: Session
) -> None:
    """K-11's `total` is a count *of the filtered set, before the page* — both halves, in one test.

    `CatalogRateList.total` documents exactly that, and neither half was forced by a test: dropping
    the `where(*filters)` from the count in `app.data.catalog.list_rates` left all 302 tests green
    (QA, 2026-09-21), because every other K-11 test calls the endpoint without `on_date`, where the
    filtered count and the whole-table count are the same number.

    Three rows, two of which cover `WINDOW`, and `?limit=1` so the page cannot be the count either:

    - `total == 2` — the rows matching `on_date`. `3` means the filter never reached the count and
      a client is told there is a page of rows it can never reach; `1` means the count came from
      the page and "this is everything" is being asserted about a truncated answer;
    - the contrast, same catalogue, no `on_date`: `total == 3`. One element changed — whether the
      request filters — and the number moves with it, so the `2` above is the filter's doing and not
      an accident of how many rows this fixture inserts.
    """
    dimensions = make_dimension_tuple(db_session)
    make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1])
    make_rate(db_session, dimensions, effective_from=LATER_WINDOW[0], effective_to=LATER_WINDOW[1])
    make_rate(
        db_session,
        make_dimension_tuple(db_session, suffix=" (2)"),
        effective_from=WINDOW[0],
        effective_to=WINDOW[1],
    )

    inside_window = client.get(
        "/catalog/rates",
        params={"on_date": WINDOW[0].isoformat(), "limit": 1},
        headers=as_caller(IN_SCOPE_USER),
    )
    unfiltered = client.get(
        "/catalog/rates", params={"limit": 1}, headers=as_caller(IN_SCOPE_USER)
    )

    assert inside_window.status_code == 200, inside_window.text
    filtered_body = inside_window.json()
    assert len(filtered_body["rates"]) == 1, "the page is still bounded by limit"
    assert filtered_body["total"] == 2, (
        "total must count the rows covering on_date — 3 means the on_date filter never reached "
        "the count, 1 means the count was taken from the page after the limit"
    )

    assert unfiltered.status_code == 200, unfiltered.text
    assert unfiltered.json()["total"] == 3, "with no on_date, total is the whole table"


def test_k_11_a_catalogue_larger_than_the_default_limit_is_truncated_not_silently_returned_whole(
    client: TestClient, db_session: Session
) -> None:
    """K-11, second half. Today's actual defect: `list_rates` had no `LIMIT` at all.

    The catalogue is sized to `DEFAULT_RATE_LIST_LIMIT + 5` — just over the default page — so a
    default call (no query parameters) proves the page is bounded, not merely "smaller than the
    catalogue happened to be". The contrast in the same test, `?limit=<total>`, proves the ceiling
    is the query parameter and not some other hidden cap: the same catalogue answered whole when
    asked for whole."""
    dimensions = make_dimension_tuple(db_session)
    total_rows = DEFAULT_RATE_LIST_LIMIT + 5
    _make_many_rates(db_session, dimensions, total_rows)

    default_page = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))
    whole_catalogue = client.get(
        "/catalog/rates", params={"limit": total_rows}, headers=as_caller(IN_SCOPE_USER)
    )

    assert default_page.status_code == 200, default_page.text
    default_body = default_page.json()
    assert len(default_body["rates"]) == DEFAULT_RATE_LIST_LIMIT
    assert default_body["total"] == total_rows
    assert default_body["total"] > len(default_body["rates"])

    assert whole_catalogue.status_code == 200, whole_catalogue.text
    whole_body = whole_catalogue.json()
    assert len(whole_body["rates"]) == total_rows
    assert whole_body["total"] == total_rows


def test_k_11_limit_above_the_maximum_is_refused(
    client: TestClient, db_session: Session
) -> None:
    """K-11's ceiling: `limit` past `MAX_RATE_LIST_LIMIT` is a `422` naming the field — never a
    `500`, and never a silent clamp to the maximum, which would answer fewer rows than asked for
    with nothing in the response saying so."""
    make_dimension_tuple(db_session)  # the catalogue need not be large for this refusal

    response = client.get(
        "/catalog/rates",
        params={"limit": MAX_RATE_LIST_LIMIT + 1},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert "limit" in response.text


def test_k_11_offset_pages_through_without_gaps_or_duplicates(
    client: TestClient, db_session: Session
) -> None:
    """K-11's paging guarantee: two consecutive pages sum to every row, with no row twice and none
    missing — which needs the deterministic `(effective_from, id)` ordering `list_rates` sorts by,
    since without a tie-breaker the database is free to reorder rows sharing `effective_from`
    between the two calls."""
    dimensions = make_dimension_tuple(db_session)
    page_size = 40
    all_ids = _make_many_rates(db_session, dimensions, 2 * page_size)

    first_page = client.get(
        "/catalog/rates",
        params={"limit": page_size, "offset": 0},
        headers=as_caller(IN_SCOPE_USER),
    )
    second_page = client.get(
        "/catalog/rates",
        params={"limit": page_size, "offset": page_size},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert first_page.status_code == 200, first_page.text
    assert second_page.status_code == 200, second_page.text
    first_ids = [uuid.UUID(rate["id"]) for rate in first_page.json()["rates"]]
    second_ids = [uuid.UUID(rate["id"]) for rate in second_page.json()["rates"]]
    assert len(first_ids) == page_size
    assert len(second_ids) == page_size
    assert set(first_ids).isdisjoint(second_ids), "the two pages overlap — offset skipped no rows"
    assert set(first_ids) | set(second_ids) == set(all_ids), (
        "the two pages together are missing rows, or contain rows that were never inserted"
    )


def test_r_04_a_page_and_its_total_are_read_in_one_statement(
    client: TestClient, db_session: Session
) -> None:
    """R-04: `total` comes from the page's own snapshot, not from a second `SELECT`.

    The claim is about concurrency and cannot be observed single-threaded — restoring the two
    separate statements `list_rates` used before R-04 left all 303 tests green, because with nobody
    else writing, `count(*)` and the page always agree. Under READ COMMITTED they need not: each
    statement takes its own snapshot, so a row committed or deleted between them makes the response
    describe a table that never existed at any instant — a `total` smaller than the page it came
    with, or one that counts a row the page could not see. Folding the count into the page's own
    statement is the mechanism that closes that, and **"one statement" is the whole of it**: that is
    what this test asserts, by counting the `SELECT`s the request issues against
    `catalog_default_rates`.

    Structural on purpose, the way K-08 asserts on `pg_constraint` rather than on an overlap: the
    harmful interleaving needs a second statement to slip between, so the absence of a second
    statement is the property, and an assertion on two response bodies cannot express it. A later
    rewrite into a subquery or a CTE keeps this test green; splitting the query back into two does
    not, which is exactly the change R-04 forbids.

    **Which spelling of the fold is in force is not this test's claim, and it has changed once.**
    R-04 delivered `count(*) OVER ()`; R-01 of the gate-2 review replaced it with a scalar subquery
    beside a self-contained page subquery, because a window function has to consume every filtered
    row before emitting the first and therefore made the `LIMIT` bound the response only
    (`test_r_01_a_page_is_read_as_a_bounded_top_n_not_a_sort_of_the_whole_catalogue`). Both are one
    statement and one snapshot; this assertion held across the change untouched, which is the
    property R-04 asked for.

    The empty page is the documented exception (the count travels on the page's rows, and an empty
    page has none), so this asks about a page that carries rows. What `total` answers on an empty
    one is `test_k_11_an_empty_page_still_names_the_total` below.
    """
    dimensions = make_dimension_tuple(db_session)
    make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1])
    make_rate(db_session, dimensions, effective_from=LATER_WINDOW[0], effective_to=LATER_WINDOW[1])
    reads: list[str] = []

    def record(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        normalised = " ".join(statement.lower().split())
        if normalised.startswith("select") and "from catalog_default_rates" in normalised:
            reads.append(normalised)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        response = client.get(
            "/catalog/rates", params={"limit": 1}, headers=as_caller(IN_SCOPE_USER)
        )
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["rates"]) == 1, "the page must carry a row — the empty page is the exception"
    assert body["total"] == 2, "the total must still describe the whole filtered set"
    assert len(reads) == 1, (
        "a non-empty page took more than one read of catalog_default_rates: total and page no "
        "longer share a snapshot, and a concurrent write between them would go unobserved — "
        f"{reads}"
    )


def test_k_11_an_empty_page_still_names_the_total(
    client: TestClient, db_session: Session
) -> None:
    """K-11 on the one page that carries no rows — the branch R-04 left behind (QA, 2026-09-21).

    `total` and the page come from one statement now (`app.data.catalog.rate_page_statement`), and
    the count travels on the page's own rows — so an empty page has nothing to carry it and
    `list_rates` falls back to a separate `count(*)` exactly there. **Nothing exercised that
    branch**: replacing its whole body
    with `return [], 0` left all 303 tests green, and so did dropping `where(*filters)` from it.
    Both are answers a client acts on — "there is nothing here" is what makes a frontend stop
    paging and report an empty catalogue.

    Three requests over the same two-row catalogue, one element changed at a time:

    - `offset=2` past the end: the page is empty and `total` is still `2`. `0` here is the `return
      [], 0` mutation — a caller that paged one step too far is told the catalogue is empty rather
      than that it has overshot it;
    - the contrast at `offset=0`: the same request minus the overshoot answers both rows and the
      same `total`, so the `2` above is the count's doing and not a number this fixture would
      produce either way;
    - `on_date` outside every window: the page is empty and `total` is `0` — because `total` counts
      the **filtered** set. This is the half that fails if the fallback count forgets `filters` and
      counts the whole table: it would answer `2` for a day on which no rate exists at all.
    """
    dimensions = make_dimension_tuple(db_session)
    make_rate(db_session, dimensions, effective_from=WINDOW[0], effective_to=WINDOW[1])
    make_rate(db_session, dimensions, effective_from=LATER_WINDOW[0], effective_to=LATER_WINDOW[1])
    day_outside_every_window = date(2025, 6, 1)

    past_the_end = client.get(
        "/catalog/rates", params={"limit": 2, "offset": 2}, headers=as_caller(IN_SCOPE_USER)
    )
    first_page = client.get(
        "/catalog/rates", params={"limit": 2, "offset": 0}, headers=as_caller(IN_SCOPE_USER)
    )
    outside_every_window = client.get(
        "/catalog/rates",
        params={"on_date": day_outside_every_window.isoformat()},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert past_the_end.status_code == 200, past_the_end.text
    overshot = past_the_end.json()
    assert overshot["rates"] == [], "offset past the end must answer no rows, not wrap around"
    assert overshot["total"] == 2, (
        "an empty page reported total 0 for a catalogue holding 2 rows — a client that paged one "
        "step too far is told the catalogue is empty"
    )

    assert first_page.status_code == 200, first_page.text
    assert len(first_page.json()["rates"]) == 2, "the contrast page must carry the rows themselves"
    assert first_page.json()["total"] == 2

    assert outside_every_window.status_code == 200, outside_every_window.text
    filtered = outside_every_window.json()
    assert filtered["rates"] == []
    assert filtered["total"] == 0, (
        "total must count the rows covering on_date — 2 means the count on an empty page ignored "
        "the on_date filter and counted the whole table"
    )


def _make_rates_tied_on_effective_from(
    session: Session,
    dimensions: DimensionTuple,
    count: int,
    *,
    window: tuple[date, date] = (date(2021, 3, 1), date(2021, 3, 31)),
) -> list[uuid.UUID]:
    """`count` rows sharing **one** `effective_from`, inserted in ascending id order.

    The fixture `_make_many_rates` cannot express this case and that is the point (QA, mutation on
    the `id` tie-breaker): its windows are two days apart, so every row there has a distinct
    `effective_from` and `ORDER BY effective_from` alone already totally orders them. A tie-breaker
    that is never reached cannot be shown to do anything, and deleting it from `list_rates` left the
    whole suite green.

    Ties are built with one vendor per row rather than one dimension tuple per row: the `EXCLUDE`
    key is `(…four dimensions…, COALESCE(vendor_id, sentinel), valid_period)`, so the same tuple and
    the same window are admitted exactly once per distinct vendor. That keeps `effective_from`
    identical across every row — which is what a tie *is* — while the constraint still holds.

    **Inserted in ascending id order on purpose.** With the tie-breaker removed, PostgreSQL sorts
    on `effective_from` alone, all keys compare equal, and the rows come back in the order the sort
    received them — the reverse of the descending `(effective_from, id)` order `list_rates` claims
    to produce (R-02, reviewer 2026-09-21: newest window first). The returned list is therefore
    ascending; every assertion below compares against `sorted(..., reverse=True)`.
    """
    ids = sorted(uuid.uuid4() for _ in range(count))
    rows = [
        {
            "id": row_id,
            "role_id": dimensions.role_id,
            "seniority_id": dimensions.seniority_id,
            "location_id": dimensions.location_id,
            "engagement_type_id": dimensions.engagement_type_id,
            "vendor_id": make_vendor(session, name=f"Vendor tie-break {index}").id,
            "default_cost_rate": Decimal("100.0000"),
            "default_selling_rate": Decimal("150.0000"),
            "currency": "EUR",
            "unit": "hour",
            "effective_from": window[0],
            "effective_to": window[1],
        }
        for index, row_id in enumerate(ids)
    ]
    session.execute(sa.insert(CatalogDefaultRate), rows)
    session.flush()
    return ids


def test_k_11_rows_sharing_one_effective_from_still_page_in_one_fixed_order(
    client: TestClient, db_session: Session
) -> None:
    """K-11's paging guarantee **on the rows that need it** — the ties.

    `test_k_11_offset_pages_through_without_gaps_or_duplicates` above states the same guarantee but
    cannot prove this half of it: its fixture gives every row a distinct `effective_from`, so
    `ORDER BY effective_from` is already a total order there and the `id` tie-breaker is dead code
    for the duration of that test. Deleting `CatalogDefaultRate.id` from the `order_by` in
    `app.data.catalog.list_rates` kept all 301 tests green (QA, 2026-09-21). This test is the one
    that turns red for it.

    Two pages over twelve rows that all share one `effective_from`:

    - **concatenated, they are the descending `(effective_from, id)` order** — the order
      `list_rates` documents (R-02, reviewer 2026-09-21: newest window first). Without the
      tie-breaker the twelve equal sort keys leave the rows in the order the sort received them, and
      the fixture hands them over ascending, so this is the assertion that fails;
    - **and they are disjoint and complete** — no row on both pages, none missing. This is the harm
      the order exists to prevent, kept as its own assertion because it is the claim a client
      depends on; it is the weaker of the two, since a database is *free* to return ties in a stable
      order and often does.
    """
    dimensions = make_dimension_tuple(db_session)
    page_size = 6
    tied_ids = _make_rates_tied_on_effective_from(db_session, dimensions, 2 * page_size)
    assert tied_ids != sorted(tied_ids, reverse=True), (
        "the fixture must not hand the rows over pre-sorted"
    )

    first_page = client.get(
        "/catalog/rates",
        params={"limit": page_size, "offset": 0},
        headers=as_caller(IN_SCOPE_USER),
    )
    second_page = client.get(
        "/catalog/rates",
        params={"limit": page_size, "offset": page_size},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert first_page.status_code == 200, first_page.text
    assert second_page.status_code == 200, second_page.text
    first_ids = [uuid.UUID(rate["id"]) for rate in first_page.json()["rates"]]
    second_ids = [uuid.UUID(rate["id"]) for rate in second_page.json()["rates"]]

    assert first_ids + second_ids == sorted(tied_ids, reverse=True), (
        "rows sharing one effective_from came back in some other order than descending id — "
        "the tie-breaker is gone, and two pages of this catalogue are no longer a partition of it"
    )
    assert set(first_ids).isdisjoint(second_ids), "a row sharing effective_from is on both pages"
    assert set(first_ids) | set(second_ids) == set(tied_ids), "a row fell between the two pages"


# --- R-01/R-02 (gate-2 review 2026-09-21): the limit bounds the work, not only the response ------


_SUBQUERY_RELATIONSHIPS = ("InitPlan", "SubPlan")
"""How PostgreSQL labels a plan branch that is *not* the page: the `total` subquery hangs off the
top node as an `InitPlan`. It is linear in the catalogue on purpose and forever
(`app.data.catalog.rate_page_statement`) — an exact count of a filtered set cannot be anything
else — so the bounded-work claim is about the rest of the tree, and this is how the rest is
identified."""


def _page_branch_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Every plan node reached from `node` without descending into a subplan branch."""
    nodes = [node]
    for child in node.get("Plans", ()):
        if child.get("Parent Relationship") in _SUBQUERY_RELATIONSHIPS:
            continue
        nodes.extend(_page_branch_nodes(child))
    return nodes


def test_r_01_a_page_is_read_as_a_bounded_top_n_not_a_sort_of_the_whole_catalogue(
    client: TestClient, db_session: Session
) -> None:
    """R-01 (reviewer, gate 2 on Issue #46): the `LIMIT` has to bound the *work*, not the response.

    Every other K-11 test here asserts on row counts, and every one of them passed against the
    shape this replaces: `count(*) OVER ()` next to the page, over a table whose only indexes were
    the primary key and the `EXCLUDE`'s gist index. Both halves of that defeated the `LIMIT` — a
    window function with no partition must consume every filtered row before it emits the first,
    and with no index able to produce `(effective_from DESC, id DESC)` the whole filtered table was
    sorted first either way. A 250k-row catalogue paid for 250k rows to answer with 2000, and no
    assertion about *how many rows came back* can see that. This one asks the planner.

    Two things are asserted about the plan of the statement the endpoint actually runs
    (`rate_page_statement` — re-building the query here would be asking about a query nobody runs):

    - **no `WindowAgg` anywhere.** Planner-independent: a window function is in the plan if and
      only if it is in the SQL. This is the assertion that fails if the shorter `count(*) OVER ()`
      spelling is folded back in;
    - **no node in the page branch reads more than the page.** This is the finding itself. With the
      old shape the scan under the `WindowAgg` reports all 3000 rows; with the index missing, the
      `Sort` does. The same request's answer is asserted above it to be *about* 3000 rows
      (`total`), so the contrast is inside the test: the statement knows the catalogue is 3000 rows
      and touches 25 to say so.

    Plus the mechanism by name — the plan uses `RATE_PAGE_INDEX` — so that dropping the index from
    migration `e2c7b04d9a31` fails here rather than somewhere unrelated.

    **`enable_seqscan`/`enable_sort` are switched off deliberately, and that is a limit of this
    proof.** At fixture size a sequential scan plus a top-N heapsort is a legitimate plan and the
    planner may well prefer it, so a test that let the planner choose would be asserting on the cost
    model rather than on this change. Switched off, a plan that still reads the whole table proves
    that **no** bounded path exists — a disabled node is priced out, not forbidden, so the planner
    still emits one when it has no alternative. What this therefore proves is that the bounded path
    exists and is what the statement's shape asks for; what it does not prove is the planner's
    choice on a production-sized catalogue with production statistics, which no test in this
    repository can prove today (no deployed environment, open decision #5).
    """
    dimensions = make_dimension_tuple(db_session)
    catalogue_size = 3000
    page_size = 25
    _make_many_rates(db_session, dimensions, catalogue_size)

    answer = client.get(
        "/catalog/rates", params={"limit": page_size}, headers=as_caller(IN_SCOPE_USER)
    )
    assert answer.status_code == 200, answer.text
    assert len(answer.json()["rates"]) == page_size
    assert answer.json()["total"] == catalogue_size, "the answer must describe the whole catalogue"

    db_session.execute(sa.text("SET LOCAL enable_seqscan = off"))
    db_session.execute(sa.text("SET LOCAL enable_sort = off"))
    statement = rate_page_statement(on_date=None, limit=page_size, offset=0)
    compiled = statement.compile(
        bind=db_session.get_bind(), compile_kwargs={"literal_binds": True}
    )
    plan = db_session.execute(sa.text(f"EXPLAIN (ANALYZE, FORMAT JSON) {compiled}")).scalar_one()
    page_branch = _page_branch_nodes(plan[0]["Plan"])

    assert not [node for node in page_branch if node["Node Type"] == "WindowAgg"], (
        "the page is computed by a window function again: it has to consume every filtered row "
        f"before the LIMIT can cut, which is the whole of R-01 — {plan}"
    )
    oversized = [node for node in page_branch if node.get("Actual Rows", 0) > page_size]
    assert not oversized, (
        f"a plan node read more than the {page_size} rows the page asked for, over a catalogue of "
        f"{catalogue_size}: the LIMIT is bounding the response and not the work — {oversized}"
    )
    assert [node for node in page_branch if node.get("Index Name") == RATE_PAGE_INDEX], (
        f"the page's order did not come from {RATE_PAGE_INDEX}; without it the rows have to be "
        f"sorted before the LIMIT can cut — {plan}"
    )


def test_r_02_an_offset_above_the_maximum_is_refused_instead_of_reaching_the_database(
    client: TestClient, db_session: Session
) -> None:
    """R-02 (reviewer, gate 2 on Issue #46): `offset` had a floor and no ceiling.

    Three requests over the same catalogue, one element changed at a time:

    - **past `bigint`.** This is the reported defect: the value reached `OFFSET :n`, the driver
      refused it, and with no exception handler anywhere in this application
      (`app.main`) it surfaced as an unhandled server error — the `TestClient` this fixture builds
      re-raises those, so before the ceiling existed this line did not fail the assertion, it
      failed the test with a database error;
    - **one past the maximum.** `422`, naming the field. `200` here is the clamp — the answer
      `limit` already refuses to give (`test_k_11_limit_above_the_maximum_is_refused`), because a
      server that silently answers a different page from the one asked for cannot be told apart
      from one that ran out of rows;
    - **the maximum itself.** `200`. The contrast that keeps the ceiling a ceiling and not a
      blanket refusal: the same request one step lower is answered.

    Why a ceiling at all, beyond the crash: `OFFSET n` walks and discards `n` rows before the first
    row of the page, so the bounded top-N of R-01 is bounded by `limit + offset`. Left unbounded,
    `offset` is the second way to ask the server for unbounded work.
    """
    make_dimension_tuple(db_session)  # the catalogue need not be large for these refusals

    past_bigint = client.get(
        "/catalog/rates", params={"offset": 2**63}, headers=as_caller(IN_SCOPE_USER)
    )
    above_the_maximum = client.get(
        "/catalog/rates",
        params={"offset": MAX_RATE_LIST_OFFSET + 1},
        headers=as_caller(IN_SCOPE_USER),
    )
    at_the_maximum = client.get(
        "/catalog/rates",
        params={"offset": MAX_RATE_LIST_OFFSET},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert past_bigint.status_code == 422, past_bigint.text
    assert "offset" in past_bigint.text
    assert above_the_maximum.status_code == 422, above_the_maximum.text
    assert "offset" in above_the_maximum.text
    assert at_the_maximum.status_code == 200, at_the_maximum.text
    assert at_the_maximum.json()["rates"] == []


def _load_migration_module(filename: str, name: str) -> ModuleType:
    """Import a migration by path — `migrations/versions` is not a package, and a module name
    starting with a digit is reachable by neither `import` nor `importlib.import_module`."""
    path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / filename
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module
