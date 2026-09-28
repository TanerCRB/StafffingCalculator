"""SC-2-06 (Issue #31; ADR-0019) — the register of named persons: K-01, K-02, K-08, K-09.

Every person in this file is fictitious (ADR-0019, point 8). All of it runs against the real
PostgreSQL of `tests/conftest.py`: a column set, a CHECK constraint and the text PostgreSQL puts
into `DETAIL: Failing row contains (…)` are the things under test, so a stand-in would prove
nothing.

The positive branch of every permission here is reached through `caller_holding`
(`dependency_overrides`) only. The placeholder identity holds neither `PEOPLE_READ` nor
`PEOPLE_WRITE` (ADR-0005, aneks 2026-09-27, point 4), so in the running system every request to
`/people` is refused. That is a limit of the proof, and it is stated here as one.
"""

import importlib.util
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api.deps import PLACEHOLDER_PERMISSIONS
from app.core.identity import Permission
from app.data.people import (
    PersonWriteFailed,
    PersonWriteRefused,
    correct_person_name,
    create_person,
)
from app.models import Person
from app.models import person as person_model
from app.models import staffing as staffing_model
from tests.conftest import (
    BACKEND_ROOT,
    FICTITIOUS_PERSON_NAME,
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    assign_person_directly,
    caller_holding,
    count_people,
    make_person,
    staffing_path,
)
from tests.test_scenario_approval_snapshot import _approvable_scenario

EVERYTHING = frozenset(Permission)

APPROVED_PERSON_COLUMNS = {"id", "full_name", "created_at", "updated_at"}
"""ADR-0019, point 3 — the column set approved at gate 1. Written out by hand, not reflected from
the model: comparing the model with itself would pass whatever the model says."""


def _new_person(full_name: str, person_id: uuid.UUID | None = None) -> dict[str, str]:
    """A `POST /people` body: the client-chosen id (D-2 = B, a fresh v4 unless given) and a name."""
    return {"id": str(person_id or uuid.uuid4()), "full_name": full_name}


def _migration_module() -> Any:
    path = Path(BACKEND_ROOT) / "migrations" / "versions" / (
        "c4d7e2a9b1f6_create_person_register_and_person_on_staffing_position.py"
    )
    spec = importlib.util.spec_from_file_location("sc_2_06_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- K-01: the column set of a person row, by equality ------------------------------------------


def test_k_01_the_person_row_has_exactly_the_approved_column_set(engine: Engine) -> None:
    """K-01 — the columns the migrated database really has equal the set ADR-0019 approved.

    **Equality, not `"email" not in columns`.** A negative assertion naming one spelling lets every
    other spelling through: `note`, `phone`, `comment`, `rate`, `employee_number`. Each one is as
    much personal data as the next, and equality fails on all of them, including ones nobody has
    thought of yet. The mapped model is held to the same set, so a column added to the model
    without a migration (or the reverse) fails here as well.

    The expected set is asserted non-empty, because `set() == set()` is how this test would pass
    against a table that does not exist.
    """
    assert APPROVED_PERSON_COLUMNS
    with engine.connect() as connection:
        columns = set(
            connection.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = 'public' AND table_name = 'person'"
                )
            ).scalars()
        )

    assert columns == APPROVED_PERSON_COLUMNS, (
        "the person row's columns changed. ADR-0019 (point 3) approves exactly id, full_name, "
        "created_at and updated_at; any other column needs a dated annex to ADR-0019 first. "
        f"Unexpected: {sorted(columns - APPROVED_PERSON_COLUMNS)}; missing: "
        f"{sorted(APPROVED_PERSON_COLUMNS - columns)}."
    )
    assert {attribute.key for attribute in sa.inspect(Person).column_attrs} == columns


def test_k_01_the_name_column_is_a_bounded_not_null_varchar_with_no_uniqueness(
    engine: Engine,
) -> None:
    """K-01's types half: `full_name VARCHAR(200) NOT NULL`, and **no** unique constraint on it
    (two people may share a name — ADR-0019, point 3). The canonical-form CHECK is present under
    the name the other tests assert on."""
    with engine.connect() as connection:
        name_column = connection.execute(
            sa.text(
                "SELECT data_type, character_maximum_length, is_nullable"
                " FROM information_schema.columns"
                " WHERE table_name = 'person' AND column_name = 'full_name'"
            )
        ).one()
        constraints = dict(
            connection.execute(
                sa.text(
                    "SELECT conname, contype FROM pg_constraint"
                    " WHERE conrelid = 'person'::regclass"
                )
            ).all()
        )
        unique_indexes = connection.execute(
            sa.text(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'person'"
                " AND indexdef ILIKE '%unique%' AND indexname <> 'pk_person'"
            )
        ).scalars().all()

    assert tuple(name_column) == ("character varying", 200, "NO")
    assert constraints == {"pk_person": "p", person_model.FULL_NAME_CANONICAL_CONSTRAINT: "c"}
    assert unique_indexes == []


def test_the_model_and_the_migration_agree_on_both_check_expressions() -> None:
    """Schema-drift guard, the same pattern `test_staffing_schema_constraints.py` uses for the month
    CHECK: the migration spells each expression out rather than importing it, so the two copies are
    compared here."""
    migration = _migration_module()
    assert (
        migration._FULL_NAME_CANONICAL_EXPRESSION == person_model.FULL_NAME_CANONICAL_EXPRESSION
    )
    assert (
        migration._PERSON_REQUIRES_SINGLE_HEADCOUNT_EXPRESSION
        == staffing_model.PERSON_REQUIRES_SINGLE_HEADCOUNT_EXPRESSION
    )


# --- K-02: the register refuses by default ------------------------------------------------------


def test_k_02_the_person_register_denies_a_caller_holding_every_other_permission(
    client: TestClient, db_session: Session
) -> None:
    """K-02 (read) — `GET /people` without `PEOPLE_READ` is a `403` whose body names no person, even
    for a caller holding **every other** permission, `PEOPLE_WRITE` included. The contrast is the
    same request with `PEOPLE_READ` alone: `200`, and the name is there.

    The mutation "the endpoint declares `STAFFING_READ` (or `CATALOG_READ`) instead" fails the first
    half: this caller holds both."""
    person = make_person(db_session)

    with caller_holding(*(EVERYTHING - {Permission.PEOPLE_READ})):
        denied = client.get("/people")
    assert denied.status_code == 403, denied.text
    assert FICTITIOUS_PERSON_NAME not in denied.text
    assert str(person.id) not in denied.text

    with caller_holding(Permission.PEOPLE_READ):
        allowed = client.get("/people")
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert body["total"] == 1
    assert body["people"] == [
        {
            "id": str(person.id),
            "full_name": FICTITIOUS_PERSON_NAME,
            "updated_at": body["people"][0]["updated_at"],
        }
    ]


def test_k_02_a_refused_write_creates_no_row_and_corrects_nothing(
    client: TestClient, db_session: Session
) -> None:
    """K-02 (write) — without `PEOPLE_WRITE` both the create and the correction are refused
    (`403`) and **nothing is written**, the row count and the stored name being what is asserted,
    not only the status. The caller holds every other permission, `PEOPLE_READ` included. The
    contrast: the identical requests with `PEOPLE_WRITE` succeed."""
    person = make_person(db_session)
    token = person.updated_at.isoformat()
    before = count_people(db_session)

    with caller_holding(*(EVERYTHING - {Permission.PEOPLE_WRITE})):
        created = client.post("/people", json=_new_person("Druga Osoba-Fikcyjna"))
        corrected = client.patch(
            f"/people/{person.id}",
            json={"updated_at": token, "full_name": "Poprawiona Osoba-Fikcyjna"},
        )
    assert created.status_code == 403, created.text
    assert corrected.status_code == 403, corrected.text
    assert "Druga Osoba-Fikcyjna" not in created.text
    assert count_people(db_session) == before
    db_session.expire_all()
    assert db_session.get(Person, person.id).full_name == FICTITIOUS_PERSON_NAME

    with caller_holding(Permission.PEOPLE_WRITE):
        created = client.post("/people", json=_new_person("Druga Osoba-Fikcyjna"))
        corrected = client.patch(
            f"/people/{person.id}",
            json={"updated_at": token, "full_name": "Poprawiona Osoba-Fikcyjna"},
        )
    assert created.status_code == 201, created.text
    assert corrected.status_code == 200, corrected.text
    assert count_people(db_session) == before + 1
    db_session.expire_all()
    assert db_session.get(Person, person.id).full_name == "Poprawiona Osoba-Fikcyjna"


def test_k_02_the_running_systems_placeholder_is_refused_and_a_caller_with_no_identity_is_401(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — the caller the running system actually has (the header placeholder, **no**
    `dependency_overrides`) is refused on every verb, and a request with no identity at all is a
    `401`. The canary on the placeholder set is repeated here (PD-K6): the day `PEOPLE_READ` joins
    it, this file says so as well as `test_access_control.py`."""
    person = make_person(db_session)
    before = count_people(db_session)

    assert Permission.PEOPLE_READ not in PLACEHOLDER_PERMISSIONS
    assert Permission.PEOPLE_WRITE not in PLACEHOLDER_PERMISSIONS

    headers = as_caller(IN_SCOPE_USER)
    listed = client.get("/people", headers=headers)
    created = client.post("/people", json=_new_person("Druga Osoba-Fikcyjna"), headers=headers)
    corrected = client.patch(
        f"/people/{person.id}",
        json={"updated_at": person.updated_at.isoformat(), "full_name": "X Fikcyjny"},
        headers=headers,
    )
    for response in (listed, created, corrected):
        assert response.status_code == 403, response.text
        assert FICTITIOUS_PERSON_NAME not in response.text
    assert count_people(db_session) == before

    assert client.get("/people").status_code == 401


def test_the_register_pages_by_name_then_id_and_counts_before_the_page(
    client: TestClient, db_session: Session
) -> None:
    """ADR-0017 on the register: `limit`/`offset`/`total`, a total order (name, then id — two
    people may share a name), `total` counted before the page, an offset past the end answered as
    an empty page, and an out-of-range `limit` refused (`422`), never clamped."""
    twins = sorted([make_person(db_session, full_name="Bliźniak Fikcyjny") for _ in range(2)],
                   key=lambda p: str(p.id))
    first = make_person(db_session, full_name="Adam Fikcyjny")
    make_person(db_session, full_name="Zenon Fikcyjny")

    with caller_holding(Permission.PEOPLE_READ):
        page = client.get("/people", params={"limit": 2, "offset": 1})
        past_the_end = client.get("/people", params={"limit": 2, "offset": 10})
        too_large = client.get("/people", params={"limit": 1001})
        zero = client.get("/people", params={"limit": 0})

    assert page.status_code == 200, page.text
    assert page.json()["total"] == 4
    assert [row["id"] for row in page.json()["people"]] == [str(twins[0].id), str(twins[1].id)]
    assert str(first.id) not in page.text
    assert past_the_end.json() == {"people": [], "total": 4}
    assert too_large.status_code == 422
    assert zero.status_code == 422


# --- K-08: correcting a name, on an approved scenario, without touching its snapshot ------------


def _snapshot_state(session: Session) -> dict[str, list[Any]]:
    """Every row of every `approved_snapshot_*` table the migrated database holds, as text — read
    from `information_schema`, not from the production registry, so a snapshot table nobody
    registered is counted too (the construction `test_scenario_approval_snapshot.py` uses)."""
    tables = sorted(
        session.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'public' AND table_name LIKE 'approved\\_snapshot\\_%'"
            )
        ).scalars()
    )
    assert tables, "no approved_snapshot_* table exists — this would compare nothing"
    return {
        table: sorted(
            str(tuple(row))
            for row in session.execute(sa.text(f"SELECT * FROM {table}"))  # noqa: S608
        )
        for table in tables
    }


def test_k_08_correcting_a_persons_name_is_visible_on_an_approved_scenario_without_touching_its_snapshot(  # noqa: E501 — the criterion names this test
    client: TestClient, db_session: Session
) -> None:
    """K-08 (as reworded at gate 1, decision 4 = a1) — a person assigned to a position of a
    scenario approved through the **real** approval endpoint:

    - the correction (`PEOPLE_WRITE`) succeeds — no `approved` guard on the person row (ADR-0004,
      aneks 2026-09-27, point 3);
    - the register (`PEOPLE_READ`) shows the corrected name;
    - the approved position still points at the **same** `person_id`;
    - every `approved_snapshot_*` table — the set of them and every row in them — is identical
      before and after, and none holds the name, before or after (ADR-0019, point 7).

    Contrast in the same test: changing the *assignment* on the same approved scenario is refused
    (`409`) — so the correction does not succeed merely because nothing on this scenario is
    guarded. The mutations this kills: an `approved` guard spread onto the person row (the
    correction would be `409`), and the name frozen into a snapshot (the name would be found in a
    snapshot row, and the rows would differ after the correction).
    """
    project, scenario, _, position = _approvable_scenario(db_session)
    person = make_person(db_session)
    assign_person_directly(db_session, position, person)

    approved = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
    assert approved.status_code == 200, approved.text

    snapshot_before = _snapshot_state(db_session)
    assert any(snapshot_before.values()), "the approval froze nothing — nothing to compare"
    assert FICTITIOUS_PERSON_NAME not in str(snapshot_before)

    db_session.expire_all()
    token = db_session.get(Person, person.id).updated_at.isoformat()
    with caller_holding(Permission.PEOPLE_WRITE):
        corrected = client.patch(
            f"/people/{person.id}",
            json={"updated_at": token, "full_name": "Poprawiona Osoba-Fikcyjna"},
        )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["full_name"] == "Poprawiona Osoba-Fikcyjna"

    with caller_holding(Permission.PEOPLE_READ):
        register = client.get("/people")
    assert [row["full_name"] for row in register.json()["people"]] == ["Poprawiona Osoba-Fikcyjna"]

    with caller_holding(Permission.STAFFING_READ, Permission.PEOPLE_READ):
        grid = client.get(staffing_path(project.id, scenario.id))
    assert grid.status_code == 200, grid.text
    assert [row["person_id"] for row in grid.json()["positions"]] == [str(person.id)]

    snapshot_after = _snapshot_state(db_session)
    assert snapshot_after == snapshot_before
    assert "Poprawiona Osoba-Fikcyjna" not in str(snapshot_after)

    # Contrast: the assignment itself is the scenario's own data, and it *is* frozen.
    other = make_person(db_session, full_name="Inna Osoba-Fikcyjna")
    marker = grid.json()["positions"][0]["person_assignment_updated_at"]
    with caller_holding(
        Permission.STAFFING_READ, Permission.STAFFING_WRITE, Permission.PEOPLE_READ
    ):
        refused = client.patch(
            f"{staffing_path(project.id, scenario.id)}/{position.id}/person",
            json={"person_assignment_updated_at": marker, "person_id": str(other.id)},
        )
    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"]
    db_session.expire_all()
    assert db_session.get(type(position), position.id).person_id == person.id


def test_k_08_a_stale_marker_refuses_the_correction_and_a_missing_person_is_404(
    client: TestClient, db_session: Session
) -> None:
    """The correction's own refusals (ADR-0007): a marker that moved is a `409` naming the condition
    and quoting no name, the stored name unchanged; an unknown id is a `404`, never a `409`."""
    person = make_person(db_session)
    stale = "2000-01-01T00:00:00+00:00"

    with caller_holding(Permission.PEOPLE_WRITE):
        refused = client.patch(
            f"/people/{person.id}",
            json={"updated_at": stale, "full_name": "Poprawiona Osoba-Fikcyjna"},
        )
        missing = client.patch(
            f"/people/{uuid.uuid4()}",
            json={"updated_at": stale, "full_name": "Poprawiona Osoba-Fikcyjna"},
        )

    assert refused.status_code == 409, refused.text
    assert "condition=updated_at_marker" in refused.json()["detail"]
    assert FICTITIOUS_PERSON_NAME not in refused.text
    assert "Poprawiona" not in refused.text
    db_session.expire_all()
    assert db_session.get(Person, person.id).full_name == FICTITIOUS_PERSON_NAME
    assert missing.status_code == 404, missing.text


# --- K-09: NF-11 — a failed save of a person does not carry the name into a log ------------------

LEAKY_NAME = " Leaky Fikcyjny-Nazwisko"
"""A fictitious name with a leading space: the one shape the database refuses on a row carrying a
real name (`ck_person_full_name_canonical`), and therefore the one refusal whose `DETAIL: Failing
row contains (…)` would quote it."""


def _raw_insert_message(engine: Engine) -> str:
    """The same refused `INSERT`, straight through the driver with the application's engine flag
    (`hide_parameters=True`) — the contrast: what the message looks like *without* the write path's
    sanitising wrapper."""
    with pytest.raises(sa.exc.StatementError) as error:
        with engine.begin() as connection:
            connection.execute(
                sa.text("INSERT INTO person (id, full_name) VALUES (:id, :name)"),
                {"id": uuid.uuid4(), "name": LEAKY_NAME},
            )
    return str(error.value)


def test_k_09_the_contrast_the_driver_error_itself_quotes_the_name(database_url: str) -> None:
    """The contrast that keeps K-09 from being empty: with the engine flag on, PostgreSQL still
    relays the whole failing row, name included. If this ever stops leaking, the tests below have
    stopped proving anything — better a failing test than a silent pass."""
    engine = sa.create_engine(database_url, hide_parameters=True)
    try:
        message = _raw_insert_message(engine)
    finally:
        engine.dispose()
    assert "Failing row contains" in message
    assert LEAKY_NAME.strip() in message
    assert "[parameters: " not in message


def test_k_09_a_failed_person_save_does_not_log_the_person_name(db_session: Session) -> None:
    """K-09 (create) — the write path, end to end, against the real CHECK: the whole formatted
    traceback of the raised exception carries neither the name nor PostgreSQL's failing-row line,
    and still names what refused (SQLSTATE and constraint). `traceback.format_exception`, not
    `str(error)`: a chained cause leaks through the traceback even when the top message is clean.

    Called on the data layer directly, bypassing the request schema (which trims the name and would
    never let this reach the database) — the writer this guards is exactly the one that does not
    pass through the schema."""
    before = count_people(db_session)
    with pytest.raises(PersonWriteRefused) as error:
        create_person(db_session, person_id=uuid.uuid4(), full_name=LEAKY_NAME)

    rendered = "".join(traceback.format_exception(error.value))
    assert LEAKY_NAME.strip() not in rendered
    assert "Leaky" not in rendered
    assert "Failing row contains" not in rendered
    assert "sqlstate=23514" in str(error.value)
    assert person_model.FULL_NAME_CANONICAL_CONSTRAINT in str(error.value)
    assert count_people(db_session) == before


def test_k_09_a_failed_name_correction_does_not_log_the_old_or_the_new_name(
    db_session: Session,
) -> None:
    """K-09 (correction) — the same claim on the second write path, where the failing row carries
    the *new* name and the row still holds the *old* one: neither appears anywhere in the
    traceback, and the stored name is untouched.

    The fixture row is committed first (into the test's outer transaction, still rolled back at the
    end): the write path's own rollback after the refusal would otherwise discard the savepoint the
    person lives in, and "the name is untouched" would be unaskable."""
    person = make_person(db_session)
    db_session.commit()
    with pytest.raises(PersonWriteFailed) as error:
        correct_person_name(
            db_session,
            person.id,
            expected_updated_at=person.updated_at,
            full_name=LEAKY_NAME,
        )

    rendered = "".join(traceback.format_exception(error.value))
    assert "Leaky" not in rendered
    assert FICTITIOUS_PERSON_NAME not in rendered
    assert "Failing row contains" not in rendered
    assert person_model.FULL_NAME_CANONICAL_CONSTRAINT in str(error.value)
    db_session.expire_all()
    assert db_session.get(Person, person.id).full_name == FICTITIOUS_PERSON_NAME


def test_the_request_schema_trims_the_name_so_the_api_stores_the_canonical_form(
    client: TestClient, db_session: Session
) -> None:
    """Why K-09 has to be proven below the API: over HTTP a name with spaces at its ends is trimmed
    by the schema and stored in canonical form (`201`), so the database refusal is unreachable from
    a request. The CHECK is the guarantee for every other writer; the schema is only the status
    code."""
    with caller_holding(Permission.PEOPLE_WRITE):
        created = client.post("/people", json=_new_person("  Przycięta Osoba-Fikcyjna  "))
    assert created.status_code == 201, created.text
    assert created.json()["full_name"] == "Przycięta Osoba-Fikcyjna"


def test_a_person_body_carrying_any_field_beyond_the_id_and_the_name_is_a_422_and_writes_nothing(  # noqa: E501
    client: TestClient, db_session: Session
) -> None:
    """ADR-0019, point 3, on the way in: an e-mail, a phone or a note is not "ignored", it is a
    `422` — the request schema says what the table refuses to have."""
    before = count_people(db_session)
    with caller_holding(Permission.PEOPLE_WRITE):
        for extra in ({"email": "x@example.invalid"}, {"phone": "000"}, {"note": "n"}):
            response = client.post("/people", json={**_new_person("Osoba Fikcyjna"), **extra})
            assert response.status_code == 422, response.text
    assert count_people(db_session) == before


def test_no_person_value_is_ever_a_url_segment_or_query_parameter() -> None:
    """ADR-0019, point 6 — the name never travels in a URL (access logs record URLs). Asserted on
    the routes themselves: no path or query parameter of any route is named like a name."""
    from app.main import app

    names: list[str] = []
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        names += [param.name for param in dependant.path_params + dependant.query_params]
    assert not [name for name in names if "name" in name or "person" == name], names


def test_the_register_answer_names_no_position_scenario_or_project(
    client: TestClient, db_session: Session
) -> None:
    """ADR-0019, "Decyzja" pt 3 — the register returns persons only, never their assignments."""
    project, scenario, _, position = _approvable_scenario(db_session)
    person = make_person(db_session)
    assign_person_directly(db_session, position, person)

    with caller_holding(*EVERYTHING):
        listed = client.get("/people")
    assert set(listed.json()["people"][0]) == {"id", "full_name", "updated_at"}
    for identifier in (project.id, scenario.id, position.id):
        assert str(identifier) not in listed.text


# --- PD-K8 (D-2 = B): the id is the client's; a retry is a 409 that says nothing about the row ---


def test_pd_k8_a_retried_create_is_a_409_with_one_body_whatever_the_name_and_changes_nothing(
    client: TestClient, db_session: Session
) -> None:
    """PD-K8 — `POST /people` without `id` is a `422` and writes nothing. With an `id`: the first
    request is `201`; a retry under the same `id` is a `409` **with an identical body** whether it
    carries the same name or a different one (no comparison of names — that would be a read of the
    register for a caller without `PEOPLE_READ`), the body names the primary key and neither name,
    and the stored row — name and marker — is unchanged. Contrast: a new `id` is `201`.

    Mutations killed: an idempotent "same body → 201/200" branch (the two bodies would differ), an
    optional `id` (the first assertion), and a retry that overwrites (the stored name would move).
    """
    before = count_people(db_session)
    with caller_holding(Permission.PEOPLE_WRITE):
        missing = client.post("/people", json={"full_name": "Osoba Bez-Id Fikcyjna"})
        person_id = uuid.uuid4()
        first = client.post("/people", json=_new_person("Pierwsza Osoba-Fikcyjna", person_id))
        same = client.post("/people", json=_new_person("Pierwsza Osoba-Fikcyjna", person_id))
        other = client.post("/people", json=_new_person("Inna Osoba-Fikcyjna", person_id))
        fresh = client.post("/people", json=_new_person("Nowa Osoba-Fikcyjna"))

    assert missing.status_code == 422, missing.text
    assert first.status_code == 201, first.text
    for retry in (same, other):
        assert retry.status_code == 409, retry.text
        assert "pk_person" in retry.json()["detail"]
        assert "sqlstate=23505" in retry.json()["detail"]
        assert "Pierwsza" not in retry.text and "Inna" not in retry.text
        assert first.json()["updated_at"] not in retry.text
    assert same.text == other.text
    assert fresh.status_code == 201, fresh.text
    assert count_people(db_session) == before + 2

    db_session.expire_all()
    stored = db_session.get(Person, person_id)
    assert stored.full_name == "Pierwsza Osoba-Fikcyjna"
    assert stored.updated_at == datetime.fromisoformat(first.json()["updated_at"])


def test_pd_k8_the_retry_refusal_is_distinguishable_from_the_canonical_form_refusal(
    db_session: Session,
) -> None:
    """The primary-key refusal and the canonical-form refusal are two different messages, each
    naming its own constraint, neither quoting a value — so a client can tell "already created"
    from "fix the name"."""
    person = make_person(db_session)
    db_session.commit()
    with pytest.raises(PersonWriteRefused) as duplicate:
        create_person(db_session, person_id=person.id, full_name="Jakakolwiek Osoba-Fikcyjna")
    with pytest.raises(PersonWriteRefused) as not_canonical:
        create_person(db_session, person_id=uuid.uuid4(), full_name=LEAKY_NAME)

    assert "constraint=pk_person" in str(duplicate.value)
    assert person_model.FULL_NAME_CANONICAL_CONSTRAINT in str(not_canonical.value)
    assert str(duplicate.value) != str(not_canonical.value)
    for message in (str(duplicate.value), str(not_canonical.value)):
        assert "Jakakolwiek" not in message and "Leaky" not in message
        assert str(person.id) not in message


@pytest.mark.parametrize(
    "person_id",
    ["00000000-0000-0000-0000-000000000000", str(uuid.uuid1()), "not-a-uuid"],
)
def test_pd_k8_an_id_that_is_not_a_version_4_uuid_is_a_422_and_writes_nothing(
    client: TestClient, db_session: Session, person_id: str
) -> None:
    """ADR-0019 aneks 2026-09-28, D-2 point 4 — the nil UUID, a version-1 UUID and garbage are
    refused by the request schema (`UUID4`). Hygiene, not a proof of randomness."""
    before = count_people(db_session)
    with caller_holding(Permission.PEOPLE_WRITE):
        response = client.post(
            "/people", json={"id": person_id, "full_name": "Osoba Fikcyjna"}
        )
    assert response.status_code == 422, response.text
    assert count_people(db_session) == before


# --- PD-K9 (D-3 = A): any ASCII whitespace at either end is refused by the database -------------

ASCII_WHITESPACE = [" ", "\t", "\n", "\r", "\v", "\f"]
"""The guarantee of `ck_person_full_name_canonical` (`[[:space:]]`); non-ASCII coverage depends on
the database's ctype — a named limit, deliberately not pinned here."""


def _insert_bypassing_the_api(session: Session, full_name: str) -> str | None:
    """Insert one person straight into the table inside a savepoint; the violated constraint's
    name, or `None` if the row was accepted (and then kept only until the test's rollback)."""
    try:
        with session.begin_nested():
            session.execute(
                sa.text("INSERT INTO person (id, full_name) VALUES (:id, :name)"),
                {"id": uuid.uuid4(), "name": full_name},
            )
    except sa.exc.IntegrityError as error:
        return error.orig.diag.constraint_name
    return None


@pytest.mark.parametrize("whitespace", ASCII_WHITESPACE, ids=repr)
def test_pd_k9_a_name_with_ascii_whitespace_at_either_end_is_refused_by_the_database(
    db_session: Session, whitespace: str
) -> None:
    """PD-K9 — the writer that bypasses the API (a fixture, a seed, an import): leading and trailing
    space, `\t`, `\n`, `\r`, `\v`, `\f` each refused by `ck_person_full_name_canonical`. The
    contrast: the same character *inside* the name is accepted. Mutation killed: the pre-D-3
    `btrim` rule (it would accept every one of them but the space)."""
    refused = person_model.FULL_NAME_CANONICAL_CONSTRAINT
    assert _insert_bypassing_the_api(db_session, f"{whitespace}Jan Fikcyjny") == refused
    assert _insert_bypassing_the_api(db_session, f"Jan Fikcyjny{whitespace}") == refused
    assert _insert_bypassing_the_api(db_session, f"Jan{whitespace}Fikcyjny") is None


def test_pd_k9_an_empty_name_is_refused_by_the_database(db_session: Session) -> None:
    """The third conjunct of the rule — `char_length(full_name) > 0`."""
    assert (
        _insert_bypassing_the_api(db_session, "")
        == person_model.FULL_NAME_CANONICAL_CONSTRAINT
    )
