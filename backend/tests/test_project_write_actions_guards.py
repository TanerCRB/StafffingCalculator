"""Tests added by QA for claims SC-1-02..04 make that the criterion tests did not yet force.

Each test here exists because a mutation of the production code survived the suite as delivered —
the mechanism was right, the proof was missing. What each one pins:

1. ADR-0007's concurrency guard is evaluated **by the database, inside the `UPDATE`**, not against
   the copy of the row the function has just read. A Python comparison passes
   `test_the_concurrency_guard_is_the_update_statement_not_a_python_comparison`, because a token
   nobody ever issued mismatches either way; it only diverges when the row changes *between* the
   read and the write, which is what the interleaved commit below produces.
2. The same for ADR-0004's frozen-field guard: a scenario approved between the check and the write
   must still stop a group-2 edit.
3. Archiving touches no scenario row — asserted at statement level rather than by comparing
   `scenarios.updated_at` before and after. Inside one transaction PostgreSQL's `now()` is the
   transaction's start time, so an `UPDATE` against a scenario row re-stamps `updated_at` with the
   value it already had and a before/after comparison cannot see it.
4. Archiving an already archived project issues no `UPDATE` at all. "Idempotent" in the sense of
   the response body is not enough: a repeated archive that rewrote `status` would move
   `projects.updated_at`, and that column is ADR-0007's concurrency token — every editor's token
   would be invalidated by somebody re-archiving.
5. The browser can actually reach `PATCH /projects/{id}`: the CORS preflight allows the method.

The interleaving in 1 and 2 is produced by a `before_cursor_execute` hook that commits a competing
write on a *separate* connection at the moment the `UPDATE projects` statement is about to run.
That is the only point at which the window exists; a second thread would make the test timing
dependent instead.
"""

import re
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import ProjectStatus
from tests.conftest import IN_SCOPE_USER, as_caller, make_project, make_scenario

Listener = Any


def is_write_statement(statement: str) -> bool:
    """True for statements that change rows — the only ones a "touches nothing" claim is about."""
    return re.match(r"\s*(insert|update|delete)\b", statement, re.IGNORECASE) is not None


def mentions_table(statement: str, table: str) -> bool:
    return re.search(rf"\b{table}\b", statement, re.IGNORECASE) is not None


def recording_statements(recorded: list[str]) -> Listener:
    def record(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        recorded.append(statement)

    return record


def committing_between_read_and_write(
    engine: Engine, fired: list[str], sql: str, params: dict[str, Any]
) -> Listener:
    """A hook that commits `sql` on another connection just before the first `UPDATE projects`.

    Fires once (`fired` guards it, and the competing statement would otherwise re-enter through
    the same class-level listener). The editing session holds no lock on the row at this point —
    it has only `SELECT`ed it — so the competing transaction commits immediately and the statement
    that triggered the hook then runs against the changed row.
    """

    def interleave(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or not statement.lstrip().lower().startswith("update projects"):
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(sa.text(sql), params)

    return interleave


@pytest.fixture
def committed_project(engine: Engine) -> uuid.UUID:
    """One project in `IN_SCOPE_USER`'s scope, committed — visible to other connections."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        project_id = project.id
        setup.commit()
    return project_id


def test_the_concurrency_guard_is_evaluated_by_the_database_not_against_the_row_just_read(
    committing_client: TestClient, engine: Engine, committed_project: uuid.UUID
) -> None:
    """ADR-0007, the part a stale-token test cannot reach: the check-then-act window.

    The competing edit lands *after* `update_project` has read the row and *before* its `UPDATE`
    runs. A guard living in the `UPDATE ... WHERE` sees it (0 rows matched, 409); a guard that
    compares `expected_updated_at` with the value it read a moment ago does not, and overwrites a
    committed change it never saw — the silent lost update NF-05 exists to prevent.
    """
    token = committing_client.get(
        f"/projects/{committed_project}", headers=as_caller(IN_SCOPE_USER)
    ).json()["updated_at"]
    fired: list[str] = []
    interleave = committing_between_read_and_write(
        engine,
        fired,
        "UPDATE projects SET client = :client, updated_at = now() WHERE id = :id",
        {"client": "Northwind Group", "id": committed_project},
    )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        response = committing_client.patch(
            f"/projects/{committed_project}",
            json={"updated_at": token, "client": "Contoso"},
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the competing write never ran — nothing below would be about a race"
    assert response.status_code == 409, response.text
    with engine.connect() as connection:
        survivor = connection.execute(
            sa.text("SELECT client FROM projects WHERE id = :id"), {"id": committed_project}
        ).scalar_one()
    assert survivor == "Northwind Group", (
        "the edit overwrote a change committed after it read the row — the guard is not in the"
        " UPDATE statement"
    )


def test_the_frozen_field_guard_is_evaluated_in_the_same_statement_as_the_write(
    committing_client: TestClient, engine: Engine
) -> None:
    """ADR-0004 group 2, with the approval landing inside the check-then-act window.

    A scenario approved between the check and the write must still freeze `reporting_currency`.
    Read separately and acted upon afterwards, the guard lets exactly this edit through — and the
    project then carries a value the approved calculation was not made with.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        project_id = project.id
        make_scenario(setup, project, name="Baseline")
        setup.commit()
    token = committing_client.get(
        f"/projects/{project_id}", headers=as_caller(IN_SCOPE_USER)
    ).json()["updated_at"]
    fired: list[str] = []
    interleave = committing_between_read_and_write(
        engine,
        fired,
        "UPDATE scenarios SET status = 'approved' WHERE project_id = :id",
        {"id": project_id},
    )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        response = committing_client.patch(
            f"/projects/{project_id}",
            json={"updated_at": token, "reporting_currency": "USD"},
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the approval never landed inside the window — nothing below is about the race"
    assert response.status_code == 409, response.text
    # The *reason* matters: a 409 saying "the project changed since it was read" would be this
    # test passing on the concurrency guard, which is a different mechanism and a different claim.
    assert "approved" in response.json()["detail"], response.text
    with engine.connect() as connection:
        stored = connection.execute(
            sa.text("SELECT reporting_currency FROM projects WHERE id = :id"), {"id": project_id}
        ).scalar_one()
    assert stored == "EUR", (
        "a group-2 field changed although the project's scenario was approved before the write"
    )


def test_archiving_issues_no_write_statement_against_the_scenarios_table(
    client: TestClient, db_session: Session
) -> None:
    """SC-1-04 criterion 2 at statement level — the level at which the claim is actually decidable.

    Comparing scenario rows before and after cannot see a value-preserving `UPDATE`: within one
    transaction `now()` is constant, so `onupdate` re-stamps `updated_at` with the same value.
    What is asserted instead is that no write statement mentions `scenarios` at all — with the
    contrast that the recorder did see the write against `projects`, so it cannot pass by
    recording nothing.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    make_scenario(db_session, project, name="Baseline")
    statements: list[str] = []
    record = recording_statements(statements)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        response = client.post(f"/projects/{project.id}/archive", headers=as_caller(IN_SCOPE_USER))
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert response.status_code == 200, response.text
    writes = [statement for statement in statements if is_write_statement(statement)]
    assert any(mentions_table(statement, "projects") for statement in writes), (
        "the recorder saw no write at all — it would accept an endpoint that did nothing"
    )
    assert [statement for statement in writes if mentions_table(statement, "scenarios")] == [], (
        f"archiving wrote to the scenarios table: {writes}"
    )


def test_archiving_an_already_archived_project_writes_nothing_at_all(
    client: TestClient, db_session: Session
) -> None:
    """Idempotent in the sense of the row, not only of the response body.

    A second archive that rewrote `status` would move `projects.updated_at` — ADR-0007's
    concurrency token — and invalidate the token of everyone mid-edit, for no change. The 200 and
    the `Archived` body are already covered by `test_project_archive_is_one_way_and_idempotent`;
    what this adds is that no statement ran.
    """
    project = make_project(
        db_session,
        name="Aurora migration",
        status=ProjectStatus.ARCHIVED,
        accessible_to=(IN_SCOPE_USER,),
    )
    token_before = client.get(
        f"/projects/{project.id}", headers=as_caller(IN_SCOPE_USER)
    ).json()["updated_at"]
    statements: list[str] = []
    record = recording_statements(statements)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        response = client.post(f"/projects/{project.id}/archive", headers=as_caller(IN_SCOPE_USER))
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "Archived"
    writes = [statement for statement in statements if is_write_statement(statement)]
    assert writes == [], f"re-archiving an archived project wrote to the database: {writes}"
    token_after = client.get(
        f"/projects/{project.id}", headers=as_caller(IN_SCOPE_USER)
    ).json()["updated_at"]
    assert token_after == token_before, "re-archiving moved the concurrency token"


def test_cors_preflight_allows_the_patch_method_the_edit_endpoint_needs(
    client: TestClient,
) -> None:
    """The edit endpoint is reachable from the browser the frontend runs in.

    `PATCH` is not a CORS-simple method: without it in `allow_methods` the preflight is refused and
    `PATCH /projects/{id}` is unreachable from the SPA while every server-side test still passes.
    The `POST` case is asserted alongside so a middleware that allowed everything, or none of it,
    cannot satisfy this on its own.
    """
    allowed_origin = settings.cors_allowed_origins[0]
    preflight = {
        "Origin": allowed_origin,
        "Access-Control-Request-Headers": f"{settings.caller_id_header},Content-Type",
    }

    for method in ("PATCH", "POST"):
        response = client.options(
            f"/projects/{uuid.uuid4()}",
            headers={**preflight, "Access-Control-Request-Method": method},
        )

        assert response.status_code == 200, f"{method}: {response.text}"
        assert method in response.headers.get("access-control-allow-methods", ""), method

    refused = client.options(
        f"/projects/{uuid.uuid4()}",
        headers={**preflight, "Access-Control-Request-Method": "DELETE"},
    )
    assert refused.status_code == 400, (
        "every method is allowed through the preflight — the assertions above prove nothing"
    )
