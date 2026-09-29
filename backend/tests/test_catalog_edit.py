"""SC-2-04, the backend half: editing a catalogue row without overwriting somebody else's change.

Until this task the catalogue could only be *added* to, and an `INSERT` cannot lose another
person's work. `PATCH /catalog/dimensions/{dimension}/{id}` and `PATCH /catalog/rates/{id}` can, so
they carry ADR-0007's concurrency marker — and the point of almost every test in this file is
*where* that marker is compared.

**What this file proves, and against which decision.** Criteria K-13..K-22 (Issue #49, analyst,
two rounds) are about the screen and are proven in `frontend/src`; **K-23** is the one addressed
here, because it needs a backend that did not exist before this task. The rest of the tests below
prove the gate-1 decisions this endpoint pair was built from, named as such:

- **K-23 / Q-1** — two edits made from one read: one wins, one is refused, never two successes and
  never a silent overwrite. Proven twice: sequentially, and **in a race between two connections**,
  where the competing edit commits between this request's read and its `UPDATE`. Only the second
  one distinguishes a guard in the `UPDATE ... WHERE` from a Python comparison against the row just
  read — the mutation that survived an entire delivered suite in SC-1-02 before the race test
  existed (`docs/architecture/capabilities.md`, mutation log 2026-09-19).
- **Q-1, the marker itself** — all six catalogue tables carry it, in the migrated database.
- **Q-2** — `PATCH` is partial: an omitted `default_cost_rate` leaves the stored one untouched,
  which is what makes the row editable at all by the caller who cannot read that field.
- **The two `409`s are distinguishable** (Issue #49, ADR-0007 addendum draft): a stale marker and a
  refusal by the state of the data share the status code and must not share the message — one says
  "re-read and try again", the other says "this edit cannot succeed as written". Neither quotes a
  row value (NF-11).
- **`404` before `409`** (ADR-0007, "Konsekwencje"; SC-3-01 R-01).

**Why so much of this file uses `committing_client` rather than `db_session`.** Inside one
transaction PostgreSQL's `now()` is the transaction's start time, so an `UPDATE` re-stamps
`updated_at` with the value it already had: the marker never moves, and a "stale marker" test run
in one transaction would pass while proving nothing (the same trap
`test_project_write_actions_guards.py` records for `scenarios.updated_at`). Real, committed
transactions on a real PostgreSQL are the only place these claims exist.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.data.catalog import DIMENSION_MODELS
from app.data.write_errors import CONCURRENCY_MARKER_CONDITION
from app.models.catalog import NO_OVERLAP_CONSTRAINT
from tests.conftest import (
    IN_SCOPE_USER,
    DimensionTuple,
    as_caller,
    caller_holding,
    make_dimension_tuple,
    make_rate,
)

WINDOW_START = date(2026, 1, 1)
WINDOW_END = date(2026, 6, 30)

CATALOGUE_TABLES = (
    "catalog_roles",
    "catalog_seniorities",
    "catalog_locations",
    "catalog_engagement_types",
    "catalog_vendors",
    "catalog_default_rates",
)
"""The six tables Q-1 puts the marker on. Spelled out here rather than derived from
`DIMENSION_MODELS`, because the claim under test is "all six", and a list derived from the code
under test would move with it."""

Listener = Any


@pytest.fixture
def committed_rate(engine: Engine) -> tuple[uuid.UUID, DimensionTuple]:
    """One rate window and its dimension tuple, committed — visible to other connections."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        dimensions = make_dimension_tuple(setup)
        rate = make_rate(
            setup, dimensions, effective_from=WINDOW_START, effective_to=WINDOW_END
        )
        rate_id = rate.id
        setup.commit()
    return rate_id, dimensions


@pytest.fixture
def committed_role(engine: Engine) -> uuid.UUID:
    """One dictionary entry, committed."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        dimensions = make_dimension_tuple(setup)
        setup.commit()
    return dimensions.role_id


def committing_between_read_and_write(
    engine: Engine, fired: list[str], statement_prefix: str, sql: str, params: dict[str, Any]
) -> Listener:
    """A hook committing `sql` on another connection just before the first matching `UPDATE`.

    The same mechanism as `test_project_write_actions_guards.committing_between_read_and_write`,
    parameterised by the statement it waits for so both catalogue tables can use it. A second
    thread would make the test timing-dependent; this is the one point at which the window exists.

    The editing session holds no lock on the row at this moment — it has only `SELECT`ed it — so the
    competing transaction commits immediately and the statement that triggered the hook then runs
    against the changed row.
    """

    def interleave(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or not statement.lstrip().lower().startswith(statement_prefix):
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(sa.text(sql), params)

    return interleave


def _stored_rate(engine: Engine, rate_id: uuid.UUID) -> sa.Row[Any]:
    with engine.connect() as connection:
        return connection.execute(
            sa.text(
                "SELECT role_id, default_cost_rate, default_selling_rate, currency,"
                " effective_from, effective_to, updated_at FROM catalog_default_rates"
                " WHERE id = :id"
            ),
            {"id": rate_id},
        ).one()


def _stored_role_name(engine: Engine, role_id: uuid.UUID) -> str:
    with engine.connect() as connection:
        return connection.execute(
            sa.text("SELECT name FROM catalog_roles WHERE id = :id"), {"id": role_id}
        ).scalar_one()


def _read_rate(client: TestClient, rate_id: uuid.UUID) -> dict[str, Any]:
    listed = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))
    assert listed.status_code == 200, listed.text
    rows = [row for row in listed.json()["rates"] if row["id"] == str(rate_id)]
    assert len(rows) == 1, listed.text
    return rows[0]


def _read_role(client: TestClient, role_id: uuid.UUID) -> dict[str, Any]:
    listed = client.get("/catalog/dimensions/roles", headers=as_caller(IN_SCOPE_USER))
    assert listed.status_code == 200, listed.text
    rows = [row for row in listed.json()["entries"] if row["id"] == str(role_id)]
    assert len(rows) == 1, listed.text
    return rows[0]


# --- Q-1: the marker exists, and the database moves it -------------------------------------------


def test_q_1_every_catalogue_table_carries_the_concurrency_marker_in_the_migrated_database(
    engine: Engine,
) -> None:
    """Q-1: all six tables, not only the rates (the rejected variant C of the gate-1 decision).

    Read from `information_schema` of the migrated database rather than from the model: the claim is
    about the schema the migration produced, and a model-only assertion would pass on a migration
    that was never written. `NOT NULL` with a default is the half that makes the migration backward
    compatible — an `INSERT` from the code version deployed before it stays valid.
    """
    with engine.connect() as connection:
        columns = {
            row.table_name: row
            for row in connection.execute(
                sa.text(
                    "SELECT table_name, is_nullable, data_type, column_default"
                    " FROM information_schema.columns"
                    " WHERE column_name = 'updated_at' AND table_name = ANY(:tables)"
                ),
                {"tables": list(CATALOGUE_TABLES)},
            )
        }

    assert sorted(columns) == sorted(CATALOGUE_TABLES), (
        "a catalogue table has no concurrency marker — every row a PATCH can reach needs one, "
        "and the table without it is the one where two editors overwrite each other silently"
    )
    for table, column in columns.items():
        assert column.is_nullable == "NO", table
        assert column.data_type == "timestamp with time zone", table
        assert column.column_default is not None, (
            f"{table}.updated_at has no server default — rows written by the code version "
            "deployed before this migration would be refused"
        )


def test_q_1_an_edit_moves_the_marker_and_the_new_one_comes_back_in_the_response(
    committing_client: TestClient, engine: Engine, committed_rate: tuple[uuid.UUID, DimensionTuple]
) -> None:
    """The marker is carried on read, moves on a successful write, and comes back in the answer.

    Without the last part the client would have to re-read the whole catalogue after every edit
    before it could edit again — and the marker in the response is what a second edit of the same
    row is made from (asserted here, not assumed).
    """
    rate_id, _ = committed_rate
    marker = _read_rate(committing_client, rate_id)["updated_at"]

    edited = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": marker, "default_selling_rate": "175.5000"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert edited.status_code == 200, edited.text
    assert edited.json()["updated_at"] != marker, "the marker did not move on a successful edit"
    again = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": edited.json()["updated_at"], "default_selling_rate": "180.0000"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert again.status_code == 200, again.text
    assert _stored_rate(engine, rate_id).default_selling_rate == Decimal("180.0000")


def test_q_1_the_marker_is_required_on_every_edit_request(
    committing_client: TestClient, engine: Engine, committed_rate: tuple[uuid.UUID, DimensionTuple]
) -> None:
    """An edit without a marker is a malformed request, not an edit that skips the check.

    Both endpoints, because "required" that holds on one of them is a protection somebody can walk
    around by editing the other kind of row.
    """
    rate_id, _ = committed_rate

    rate = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"default_selling_rate": "175.5000"},
        headers=as_caller(IN_SCOPE_USER),
    )
    entry = committing_client.patch(
        f"/catalog/dimensions/roles/{uuid.uuid4()}",
        json={"name": "Backend Engineer II"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert rate.status_code == 422, rate.text
    assert entry.status_code == 422, entry.text
    assert _stored_rate(engine, rate_id).default_selling_rate == Decimal("150.0000")


# --- K-23: one read, two edits ------------------------------------------------------------------


def test_k_23_two_edits_of_one_rate_from_one_read_leave_one_winner_and_one_refusal(
    committing_client: TestClient, engine: Engine, committed_rate: tuple[uuid.UUID, DimensionTuple]
) -> None:
    """K-23, the sequential half: never two successes, never a silent overwrite.

    Both requests carry the marker from the same read, which is exactly what two people editing one
    row in two browser tabs produce. The second one must be told — and the value it tried to write
    must not be in the database afterwards.
    """
    rate_id, _ = committed_rate
    marker = _read_rate(committing_client, rate_id)["updated_at"]

    first = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": marker, "default_selling_rate": "175.5000"},
        headers=as_caller(IN_SCOPE_USER),
    )
    second = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": marker, "default_selling_rate": "999.0000"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert CONCURRENCY_MARKER_CONDITION in second.json()["detail"], second.text
    assert _stored_rate(engine, rate_id).default_selling_rate == Decimal("175.5000"), (
        "the second edit overwrote the first one although it was made from a stale read"
    )


def test_k_23_two_renames_of_one_entry_from_one_read_leave_one_winner_and_one_refusal(
    committing_client: TestClient, engine: Engine, committed_role: uuid.UUID
) -> None:
    """K-23, the sequential half, on the five dictionaries — the test the file was missing (QA).

    Everything else about the dictionary marker is proven either against a competitor that stamps
    `updated_at` itself (the race test below writes `SET name = …, updated_at = now()`) or against a
    marker no row ever carried (`2020-01-01`, the stale/duplicate-name test). **Not one of them
    needs the database to move the marker when an ordinary rename succeeds**, so the gate-1 decision
    Q-1 rejected variant C — "the marker on the rates only" — could be half-rebuilt by accident:
    `onupdate=func.now()` dropped from `_CatalogDimension` leaves the column there, `NOT NULL`, with
    its server default, returned on every read and compared in every `WHERE` — and always equal to
    itself, so every marker stays valid forever and two people renaming one entry from one read both
    succeed. Measured: the five dictionaries lose the whole protection and the delivered suite stays
    green (QA mutation run, 2026-09-21).

    The contrast is the third request, and it differs from the second in one thing only — which
    marker it carries. Same entry, same new name, same caller: refused with the marker of the read
    both editors started from, accepted with the marker the winner's answer returned. Without it,
    an endpoint that refused *every* rename after the first would satisfy the two assertions above
    perfectly.
    """
    marker = _read_role(committing_client, committed_role)["updated_at"]

    first = committing_client.patch(
        f"/catalog/dimensions/roles/{committed_role}",
        json={"updated_at": marker, "name": "Platform Engineer"},
        headers=as_caller(IN_SCOPE_USER),
    )
    second = committing_client.patch(
        f"/catalog/dimensions/roles/{committed_role}",
        json={"updated_at": marker, "name": "Data Engineer"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert first.status_code == 200, first.text
    assert first.json()["updated_at"] != marker, (
        "a successful rename did not move the marker — every copy of it stays valid forever, and"
        " the next edit made from the stale read overwrites this one without a word"
    )
    assert second.status_code == 409, second.text
    assert CONCURRENCY_MARKER_CONDITION in second.json()["detail"], second.text
    assert _stored_role_name(engine, committed_role) == "Platform Engineer", (
        "the second rename overwrote the first one although it was made from a stale read"
    )

    retried = committing_client.patch(
        f"/catalog/dimensions/roles/{committed_role}",
        json={"updated_at": first.json()["updated_at"], "name": "Data Engineer"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert retried.status_code == 200, retried.text
    assert _stored_role_name(engine, committed_role) == "Data Engineer"


def test_k_23_a_rate_edit_is_refused_when_a_competitor_commits_between_the_read_and_the_write(
    committing_client: TestClient, engine: Engine, committed_rate: tuple[uuid.UUID, DimensionTuple]
) -> None:
    """K-23 / Q-1, the half a stale-marker test cannot reach: the check-then-act window.

    The competing edit lands *after* `update_rate` has read the row and *before* its `UPDATE` runs.
    A guard living in the `UPDATE ... WHERE` sees it (0 rows matched, `409`); a guard comparing
    `expected_updated_at` in Python against the value it read a moment ago does not — it overwrites
    a committed change it never saw, which is the silent lost update NF-05 exists to prevent and the
    exact mutation that survived a full delivered suite once already in this repository.
    """
    rate_id, _ = committed_rate
    marker = _read_rate(committing_client, rate_id)["updated_at"]
    fired: list[str] = []
    interleave = committing_between_read_and_write(
        engine,
        fired,
        "update catalog_default_rates",
        "UPDATE catalog_default_rates SET default_selling_rate = :rate, updated_at = now()"
        " WHERE id = :id",
        {"rate": Decimal("222.0000"), "id": rate_id},
    )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        response = committing_client.patch(
            f"/catalog/rates/{rate_id}",
            json={"updated_at": marker, "default_selling_rate": "175.5000"},
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the competing write never ran — nothing below would be about a race"
    assert response.status_code == 409, response.text
    assert CONCURRENCY_MARKER_CONDITION in response.json()["detail"], response.text
    assert _stored_rate(engine, rate_id).default_selling_rate == Decimal("222.0000"), (
        "the edit overwrote a change committed after it read the row — the marker is not compared"
        " inside the UPDATE statement"
    )


def test_k_23_a_dictionary_edit_is_refused_when_a_competitor_commits_between_read_and_write(
    committing_client: TestClient, engine: Engine, committed_role: uuid.UUID
) -> None:
    """K-23 / Q-1 for the five dictionaries — the same race, the other table.

    A separate test rather than a parametrised one: the two endpoints reach the guard through
    different functions (`update_dimension_entry`, `update_rate`), so one of them can lose the
    property while the other keeps it, and a single race test would cover whichever one it named.
    """
    marker = _read_role(committing_client, committed_role)["updated_at"]
    fired: list[str] = []
    interleave = committing_between_read_and_write(
        engine,
        fired,
        "update catalog_roles",
        "UPDATE catalog_roles SET name = :name, updated_at = now() WHERE id = :id",
        {"name": "Backend Engineer (competitor)", "id": committed_role},
    )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        response = committing_client.patch(
            f"/catalog/dimensions/roles/{committed_role}",
            json={"updated_at": marker, "name": "Platform Engineer"},
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the competing write never ran — nothing below would be about a race"
    assert response.status_code == 409, response.text
    assert CONCURRENCY_MARKER_CONDITION in response.json()["detail"], response.text
    assert _stored_role_name(engine, committed_role) == "Backend Engineer (competitor)", (
        "the rename overwrote a change committed after it read the row"
    )


# --- the two 409s are different answers ----------------------------------------------------------


def test_the_stale_marker_conflict_and_the_overlap_conflict_are_distinguishable(
    committing_client: TestClient, engine: Engine, committed_rate: tuple[uuid.UUID, DimensionTuple]
) -> None:
    """Two causes, one status code, and they must not read alike (Issue #49, ADR-0007 addendum).

    A client that cannot tell them apart cannot tell "re-read the row and try again" (the marker
    moved) from "this edit will never succeed as written" (the window now overlaps another one for
    the same tuple). The distinction is carried by identifiers the database supplied — the SQLSTATE
    and the constraint name for one, the named condition of the `WHERE` clause for the other — never
    by a cause this code guessed at.

    NF-11 in the same test: neither body quotes a row value, and the cost rate in particular is
    the field PostgreSQL's own `DETAIL: Failing row contains (…)` would have carried.
    """
    rate_id, dimensions = committed_rate
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        make_rate(
            setup,
            dimensions,
            effective_from=date(2026, 7, 1),
            effective_to=date(2026, 12, 31),
            default_cost_rate=Decimal("123.4500"),
        )
        setup.commit()
    marker = _read_rate(committing_client, rate_id)["updated_at"]

    overlap = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": marker, "effective_to": "2026-08-31"},
        headers=as_caller(IN_SCOPE_USER),
    )
    stale = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": "2020-01-01T00:00:00+00:00", "effective_to": "2026-05-31"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert overlap.status_code == 409, overlap.text
    assert stale.status_code == 409, stale.text
    overlap_detail = overlap.json()["detail"]
    stale_detail = stale.json()["detail"]
    assert overlap_detail != stale_detail
    assert NO_OVERLAP_CONSTRAINT in overlap_detail
    assert "23P01" in overlap_detail
    assert CONCURRENCY_MARKER_CONDITION not in overlap_detail
    assert CONCURRENCY_MARKER_CONDITION in stale_detail
    assert NO_OVERLAP_CONSTRAINT not in stale_detail
    assert "sqlstate" not in stale_detail, (
        "the stale-marker refusal claims a SQLSTATE the database never reported"
    )
    for detail in (overlap_detail, stale_detail):
        assert "123.45" not in detail, "a refusal quoted the personnel cost rate (NF-11)"
        assert "150.0000" not in detail, "a refusal quoted a row value (NF-11)"
    assert _stored_rate(engine, rate_id).effective_to == WINDOW_END, "a refused edit was saved"


def test_the_stale_marker_conflict_and_the_duplicate_name_conflict_are_distinguishable(
    committing_client: TestClient, engine: Engine, committed_role: uuid.UUID
) -> None:
    """The same two-causes-one-code problem on the dictionary path, with the other constraint.

    Renaming an entry to a name another entry already carries is refused by
    `uq_catalog_roles_name_normalized` — permanently, until somebody picks a different name. A stale
    marker is refused until the caller re-reads. One message each.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        make_dimension_tuple(setup, suffix=" II")
        setup.commit()
    marker = _read_role(committing_client, committed_role)["updated_at"]

    duplicate = committing_client.patch(
        f"/catalog/dimensions/roles/{committed_role}",
        json={"updated_at": marker, "name": "  backend   engineer II "},
        headers=as_caller(IN_SCOPE_USER),
    )
    stale = committing_client.patch(
        f"/catalog/dimensions/roles/{committed_role}",
        json={"updated_at": "2020-01-01T00:00:00+00:00", "name": "Platform Engineer"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert duplicate.status_code == 409, duplicate.text
    assert stale.status_code == 409, stale.text
    assert duplicate.json()["detail"] != stale.json()["detail"]
    assert "uq_catalog_roles_name_normalized" in duplicate.json()["detail"]
    assert CONCURRENCY_MARKER_CONDITION in stale.json()["detail"]
    assert "Backend Engineer" not in stale.json()["detail"], "a refusal quoted a row value (NF-11)"
    assert _stored_role_name(engine, committed_role) == "Backend Engineer"


# --- 404 before 409 ------------------------------------------------------------------------------


def test_a_rate_that_does_not_exist_answers_404_even_with_a_stale_marker(
    committing_client: TestClient,
) -> None:
    """ADR-0007's precedence, on the row that is not there.

    A `409` here would tell the caller the rate exists — the refusal would be a side channel about
    the catalogue's contents rather than an answer about their request. The marker sent is one no
    row could ever carry, so the `404` is not an accident of sending a fresh one.
    """
    response = committing_client.patch(
        f"/catalog/rates/{uuid.uuid4()}",
        json={"updated_at": "2020-01-01T00:00:00+00:00", "default_selling_rate": "1.0000"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 404, response.text
    assert CONCURRENCY_MARKER_CONDITION not in response.text


def test_a_dictionary_entry_that_does_not_exist_answers_404_even_with_a_stale_marker(
    committing_client: TestClient,
) -> None:
    """The same precedence on the dictionary path, and the unknown *dimension* is a `404` too."""
    unknown_entry = committing_client.patch(
        f"/catalog/dimensions/roles/{uuid.uuid4()}",
        json={"updated_at": "2020-01-01T00:00:00+00:00", "name": "Platform Engineer"},
        headers=as_caller(IN_SCOPE_USER),
    )
    unknown_dimension = committing_client.patch(
        f"/catalog/dimensions/planets/{uuid.uuid4()}",
        json={"updated_at": "2020-01-01T00:00:00+00:00", "name": "Platform Engineer"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert unknown_entry.status_code == 404, unknown_entry.text
    assert CONCURRENCY_MARKER_CONDITION not in unknown_entry.text
    assert unknown_dimension.status_code == 404, unknown_dimension.text
    assert "roles" in unknown_dimension.json()["detail"], "the 404 does not name the known kinds"


# --- Q-2: the partial PATCH and the field the caller cannot see ----------------------------------


def test_q_2_an_omitted_cost_rate_leaves_the_stored_one_untouched(
    committing_client: TestClient, engine: Engine, committed_rate: tuple[uuid.UUID, DimensionTuple]
) -> None:
    """Q-2, the decision this endpoint pair exists to honour.

    The caller here is the placeholder identity — `CATALOG_READ`/`CATALOG_WRITE` and **no**
    `PERSONNEL_COSTS_READ`, i.e. the only caller the running system actually has. They never receive
    `default_cost_rate` on a read, so their edit of the selling rate omits it entirely. The stored
    cost rate must come out of that unchanged: not null, not zero, not a value anybody invented.

    The mutation this kills is the `PUT`-shaped one — building the `UPDATE` from every field of the
    model rather than from `model_fields_set`, which writes `NULL` (a `500` on a `NOT NULL` column)
    or a default over a cost rate nobody meant to touch.
    """
    rate_id, _ = committed_rate
    read_back = _read_rate(committing_client, rate_id)
    assert read_back["default_cost_rate"] is None, "the caller can see the cost rate after all"

    response = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": read_back["updated_at"], "default_selling_rate": "175.5000"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 200, response.text
    stored = _stored_rate(engine, rate_id)
    assert stored.default_cost_rate == Decimal("100.0000"), (
        "an omitted cost rate was written anyway — the edit path is not partial"
    )
    assert stored.default_selling_rate == Decimal("175.5000")


def test_q_2_a_cost_rate_that_is_sent_is_written_at_full_precision(
    committing_client: TestClient, engine: Engine, committed_rate: tuple[uuid.UUID, DimensionTuple]
) -> None:
    """The other half of the partial semantics: present means "write this", at the column's scale.

    Writing the cost rate needs `CATALOG_WRITE` and nothing else — the caller still cannot read it
    back, which is P-2 accepted deliberately at gate 1, not an accident of this test's identity.
    The value is checked in the database, because the response is gated and cannot show it.
    """
    rate_id, _ = committed_rate
    marker = _read_rate(committing_client, rate_id)["updated_at"]

    response = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": marker, "default_cost_rate": "101.2345", "cost_rate_unit": "hour"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 200, response.text
    assert response.json()["default_cost_rate"] is None, (
        "the edit response handed back the cost rate the read path removes — a way around the gate"
    )
    assert _stored_rate(engine, rate_id).default_cost_rate == Decimal("101.2345")


def test_q_2_an_amount_more_precise_than_the_column_is_refused_not_rounded(
    committing_client: TestClient, engine: Engine, committed_rate: tuple[uuid.UUID, DimensionTuple]
) -> None:
    """R-05's rule, on the edit path as on the create path: a request whose precision cannot be kept
    is refused, never silently adjusted. Nothing is written."""
    rate_id, _ = committed_rate
    marker = _read_rate(committing_client, rate_id)["updated_at"]

    response = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": marker, "default_cost_rate": "123.45678"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert "default_cost_rate" in response.text
    assert _stored_rate(engine, rate_id).default_cost_rate == Decimal("100.0000")


def test_the_edit_response_is_gated_exactly_like_a_read(
    committing_client: TestClient, committed_rate: tuple[uuid.UUID, DimensionTuple]
) -> None:
    """The gate is one mechanism, applied to the edit response as to every other representation.

    The contrast is the whole test: the same row, the same request, two callers. Without
    `PERSONNEL_COSTS_READ` the field is absent from the answer; with it, the field is there — so the
    absence above is the gate doing its work rather than the endpoint never carrying the field at
    all.
    """
    rate_id, _ = committed_rate
    marker = _read_rate(committing_client, rate_id)["updated_at"]

    with caller_holding(Permission.CATALOG_READ, Permission.CATALOG_WRITE):
        denied = committing_client.patch(
            f"/catalog/rates/{rate_id}",
            json={"updated_at": marker, "default_cost_rate": "111.0000", "cost_rate_unit": "hour"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert denied.status_code == 200, denied.text

    with caller_holding(
        Permission.CATALOG_READ, Permission.CATALOG_WRITE, Permission.PERSONNEL_COSTS_READ
    ):
        allowed = committing_client.patch(
            f"/catalog/rates/{rate_id}",
            json={
                "updated_at": denied.json()["updated_at"],
                "default_cost_rate": "112.0000",
                "cost_rate_unit": "hour",
            },
            headers=as_caller(IN_SCOPE_USER),
        )

    assert denied.json()["default_cost_rate"] is None
    assert denied.json()["default_selling_rate"] == "150.0000", (
        "the gate removed more than the cost rate"
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["default_cost_rate"] == "112.0000"


def test_an_explicit_null_effective_to_opens_the_window_and_an_omitted_one_changes_nothing(
    committing_client: TestClient, engine: Engine, committed_rate: tuple[uuid.UUID, DimensionTuple]
) -> None:
    """`effective_to: null` is a value ("open-ended"), an absent `effective_to` is not a value.

    The one field where the two spellings differ, and they have to: a window nobody can re-open from
    the API is a window whose first mistake is permanent (the ADR-0008 `EXCLUDE` then refuses every
    later window for that tuple). Both directions are asserted against the database, so a `PATCH`
    that quietly nulled every omitted field would fail the second half.
    """
    rate_id, _ = committed_rate
    marker = _read_rate(committing_client, rate_id)["updated_at"]

    opened = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": marker, "effective_to": None},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["effective_to"] is None
    assert _stored_rate(engine, rate_id).effective_to is None

    closed = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": opened.json()["updated_at"], "effective_to": "2026-09-30"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert closed.status_code == 200, closed.text

    untouched = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": closed.json()["updated_at"], "currency": "PLN"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert untouched.status_code == 200, untouched.text
    stored = _stored_rate(engine, rate_id)
    assert stored.effective_to == date(2026, 9, 30), "an omitted effective_to was written as null"
    assert stored.currency == "PLN"


def test_an_edit_naming_no_field_or_a_field_that_is_not_editable_is_refused(
    committing_client: TestClient, engine: Engine, committed_rate: tuple[uuid.UUID, DimensionTuple]
) -> None:
    """Two `422`s, and the second one is the one that matters.

    A body carrying only the marker would move the marker and invalidate every other client's copy
    of it while changing nothing. A body naming `role_id` is an attempt to re-key an existing window
    onto another dimension tuple — out of scope by decision, and `extra="forbid"` makes it a loud
    refusal rather than a silent no-op that looks like it worked.
    """
    rate_id, dimensions = committed_rate
    marker = _read_rate(committing_client, rate_id)["updated_at"]

    nothing = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": marker},
        headers=as_caller(IN_SCOPE_USER),
    )
    rekeying = committing_client.patch(
        f"/catalog/rates/{rate_id}",
        json={"updated_at": marker, "role_id": str(dimensions.seniority_id)},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert nothing.status_code == 422, nothing.text
    assert rekeying.status_code == 422, rekeying.text
    assert _read_rate(committing_client, rate_id)["updated_at"] == marker, (
        "a refused edit moved the marker and invalidated everybody else's copy of it"
    )
    assert _stored_rate(engine, rate_id).role_id == dimensions.role_id


# --- the guard in front of all of it --------------------------------------------------------------


def test_editing_is_denied_without_catalog_write(
    committing_client: TestClient, engine: Engine, committed_rate: tuple[uuid.UUID, DimensionTuple]
) -> None:
    """Both `PATCH` endpoints declare `CATALOG_WRITE`, and a reader cannot reach either.

    The denied caller holds `CATALOG_READ` — the closest permission there is — so a dependency
    mutated to the read permission fails this test instead of surviving it. Nothing is written,
    asserted from the database rather than inferred from the status code.
    """
    rate_id, dimensions = committed_rate
    marker = _read_rate(committing_client, rate_id)["updated_at"]
    role_marker = _read_role(committing_client, dimensions.role_id)["updated_at"]

    with caller_holding(Permission.CATALOG_READ):
        rate = committing_client.patch(
            f"/catalog/rates/{rate_id}",
            json={"updated_at": marker, "default_selling_rate": "999.0000"},
            headers=as_caller(IN_SCOPE_USER),
        )
        entry = committing_client.patch(
            f"/catalog/dimensions/roles/{dimensions.role_id}",
            json={"updated_at": role_marker, "name": "Platform Engineer"},
            headers=as_caller(IN_SCOPE_USER),
        )

    assert rate.status_code == 403, rate.text
    assert entry.status_code == 403, entry.text
    assert _stored_rate(engine, rate_id).default_selling_rate == Decimal("150.0000")
    assert _stored_role_name(engine, dimensions.role_id) == "Backend Engineer"


def test_every_catalogue_read_carries_the_marker_an_edit_needs(
    client: TestClient, db_session: Session
) -> None:
    """A marker a read does not carry is a marker no client can send — and a row nobody can edit.

    Every read path of the catalogue, checked in one place: the dictionary list (the only way to
    obtain a dictionary entry — there is no single-entry endpoint), the rate page, the
    effective-rate lookup and the create response. The list paths are derived from
    `DIMENSION_MODELS`, so a sixth dictionary is covered on the day it is added.
    """
    dimensions = make_dimension_tuple(db_session)
    make_rate(db_session, dimensions, effective_from=WINDOW_START, effective_to=WINDOW_END)

    for segment in DIMENSION_MODELS:
        listed = client.get(
            f"/catalog/dimensions/{segment}", headers=as_caller(IN_SCOPE_USER)
        )
        assert listed.status_code == 200, listed.text
        for entry in listed.json()["entries"]:
            assert entry["updated_at"], f"{segment}: an entry carries no concurrency marker"

    rates = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))
    effective = client.get(
        "/catalog/rates/effective",
        params={**dimensions.as_query(), "on_date": WINDOW_START.isoformat()},
        headers=as_caller(IN_SCOPE_USER),
    )
    created_entry = client.post(
        "/catalog/dimensions/locations",
        json={"name": "Germany"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert rates.status_code == 200, rates.text
    assert all(row["updated_at"] for row in rates.json()["rates"])
    assert effective.status_code == 200, effective.text
    assert effective.json()["updated_at"]
    assert created_entry.status_code == 201, created_entry.text
    assert created_entry.json()["updated_at"]
