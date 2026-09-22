"""SC-3-02, K-12 and K-13 — the two guards on every absence write (ADR-0007, ADR-0004).

Two criteria, two mechanisms, four write paths' worth of runs — and the reason they are separate is
that each mutation leaves the other criterion's tests green:

- **K-12** the concurrency token is `staffing_position.updated_at`, compared **by the database**
  inside the statement that writes. Mutation: the comparison moved out of the `WHERE` and into
  Python.
- **K-13** a write under an `approved` scenario is refused in the same statement. Mutation: the
  `status <> 'approved'` predicate moved into Python.

Each criterion is run for **both** absence write paths — the insert and the delete — because they
are two statements. A guard implemented on one and forgotten on the other passes a criterion tested
on one path only, and "the delete is the one that was forgotten" is the likelier half: it is the
first `DELETE` this API has.

Every run asserts on the **rows**, read from a separate connection, and not only on the status code:
a `409` returned after the row was written would satisfy a status-only assertion perfectly.

The interleavings are produced by a `before_cursor_execute` hook that commits the competing change
on a *separate* connection at the moment the guarded statement is about to run. That is the only
point at which the window exists; a second thread would make the test timing-dependent instead. The
competitor never blocks: our transaction holds no lock on the scenario row until the guarded
statement itself runs, which is precisely the property `app.data.scenario_guard` is built on.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.models import ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    absence_path,
    absences_path,
    as_caller,
    make_absence,
    make_absence_type,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
    staffing_path,
)

MARCH = date(2026, 3, 1)
ABSENCE_INSERT = "insert into staffing_position_absence"
ABSENCE_DELETE = "delete from staffing_position_absence"

Listener = Any


def _committing_the_approval_before(
    engine: Engine, fired: list[str], scenario_id: uuid.UUID, *, fragment: str
) -> Listener:
    """Approve the scenario on another connection, just before the statement naming `fragment`.

    `fragment` is matched anywhere in the statement, not only at its start: both absence paths begin
    with the same `WITH guarded_position …` CTE, and what tells them apart is the `INSERT` or the
    `DELETE` that follows it. Aiming a hook at a prefix the two share would fire on whichever ran
    first and prove nothing about the other.
    """

    def interleave(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or fragment not in statement.lower():
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(
                sa.text("UPDATE scenarios SET status = 'approved' WHERE id = :id"),
                {"id": scenario_id},
            )

    return interleave


def _committing_a_competing_edit_before(
    engine: Engine, fired: list[str], position_id: uuid.UUID, *, fragment: str
) -> Listener:
    """Rotate the position's token on another connection, just before the guarded statement.

    Exactly what a second planner's write does: it touches the aggregate and moves
    `staffing_position.updated_at`. It commits in the window between "the caller read the token" and
    "the write happens" — the only interval in which a token compared in Python differs from one
    compared inside the `UPDATE`.
    """

    def interleave(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or fragment not in statement.lower():
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(
                sa.text("UPDATE staffing_position SET updated_at = now() WHERE id = :id"),
                {"id": position_id},
            )

    return interleave


def _committed_fixture(engine: Engine) -> dict[str, Any]:
    """A project in scope with a draft and an approved scenario, each with a position and an
    absence — committed, so a separate connection can read it.

    The `approved` scenario is written straight to the database. SC-3-02 *does* ship a real approval
    endpoint, but using it here would make every K-13 run depend on that endpoint being correct as
    well; the criteria for the endpoint itself are K-18..K-21, in their own files. The fixture keeps
    this file about the guards.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        calendar = make_working_calendar(
            setup, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
        )
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        draft = make_scenario(setup, project, name="Baseline")
        approved = make_scenario(
            setup, project, name="Approved v1", status=ScenarioStatus.APPROVED
        )
        absence_type = make_absence_type(setup)
        state: dict[str, Any] = {
            "project_id": project.id,
            "draft_id": draft.id,
            "approved_id": approved.id,
            "absence_type_id": absence_type.id,
        }
        for label, scenario in (("draft", draft), ("approved", approved)):
            position = make_staffing_position(
                setup,
                scenario,
                make_dimension_tuple(setup, suffix=f" ({label})", calendar=calendar),
                start_date=MARCH,
            )
            absence = make_absence(
                setup,
                position,
                absence_type,
                start_date=date(2026, 3, 2),
                end_date=date(2026, 3, 6),
            )
            state[f"{label}_position_id"] = position.id
            state[f"{label}_absence_id"] = absence.id
        setup.commit()
    return state


def _token_of(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    """The position's `updated_at` as the caller reads it — the aggregate's token (ADR-0007)."""
    response = client.get(staffing_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    return response.json()["positions"][0]["updated_at"]


def _absence_ids(engine: Engine, position_id: uuid.UUID) -> list[uuid.UUID]:
    """The absences of one position, read from a **separate** connection: committed rows only."""
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT id FROM staffing_position_absence WHERE position_id = :id"
                    " ORDER BY start_date, id"
                ),
                {"id": position_id},
            ).scalars()
        )


def _insert_body(state: dict[str, Any], token: str) -> dict[str, str]:
    return {
        "updated_at": token,
        "absence_type_id": str(state["absence_type_id"]),
        "start_date": "2026-03-16",
        "end_date": "2026-03-20",
    }


# --- K-12: the concurrency token belongs to the position and is compared by the database ---------


def test_k_12_an_absence_insert_is_refused_when_a_competitor_commits_between_the_read_and_the_write(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-12, the insert path — the token is compared **inside** the statement, by the database.

    The caller's token is **fresh when the request arrives** and goes stale inside the request: a
    competing writer rotates `staffing_position.updated_at` on another connection in the window
    between the read and the write. A Python comparison against the value read a moment earlier sees
    the old token, concludes "no conflict", and writes the absence — which is the lost update NF-05
    and ADR-0007 exist to prevent, and which every *sequential* stale-token test would still pass.
    (Measured one table over in SC-3-01: moving `updated_at = :expected` out of the `WHERE` and into
    an `if` left the whole suite green until QA added the two-connection run.)

    The assertion that carries the claim is the **row count read from a separate connection**, not
    the status code: a `409` returned after the insert would satisfy a status-only test.

    The refusal must also not say "approved" — the scenario is a draft, and a message naming the
    wrong mechanism sends the caller to copy a scenario they can still edit.
    """
    state = _committed_fixture(engine)
    token = _token_of(committing_client, state["project_id"], state["draft_id"])
    before = _absence_ids(engine, state["draft_position_id"])
    fired: list[str] = []
    interleave = _committing_a_competing_edit_before(
        engine, fired, state["draft_position_id"], fragment=ABSENCE_INSERT
    )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        response = committing_client.post(
            absences_path(
                state["project_id"], state["draft_id"], state["draft_position_id"]
            ),
            json=_insert_body(state, token),
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the competing edit never landed inside the window — nothing here is about a race"
    assert response.status_code == 409, response.text
    assert "changed since it was read" in response.json()["detail"], response.text
    assert "approved" not in response.json()["detail"], response.text
    assert _absence_ids(engine, state["draft_position_id"]) == before, (
        "an absence was inserted although the position's token had moved — the token is not "
        "compared inside the statement"
    )


def test_k_12_an_absence_delete_is_refused_when_a_competitor_commits_between_the_read_and_the_write(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-12, the delete path — the same claim, the other statement.

    Run separately because it *is* a separate statement: a token comparison implemented in the
    insert and forgotten in the delete passes the test above and loses a row here. And the loss is
    worse on this path — an insert that should not have happened leaves a visible extra row, while a
    delete that should not have happened leaves nothing at all.
    """
    state = _committed_fixture(engine)
    token = _token_of(committing_client, state["project_id"], state["draft_id"])
    before = _absence_ids(engine, state["draft_position_id"])
    assert before, "the fixture wrote no absence — the delete below would prove nothing"
    fired: list[str] = []
    interleave = _committing_a_competing_edit_before(
        engine, fired, state["draft_position_id"], fragment=ABSENCE_DELETE
    )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        response = committing_client.request(
            "DELETE",
            absence_path(
                state["project_id"],
                state["draft_id"],
                state["draft_position_id"],
                state["draft_absence_id"],
            ),
            json={"updated_at": token},
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the competing edit never landed inside the window — nothing here is about a race"
    assert response.status_code == 409, response.text
    assert "changed since it was read" in response.json()["detail"], response.text
    assert _absence_ids(engine, state["draft_position_id"]) == before, (
        "an absence was deleted although the position's token had moved — the token is not "
        "compared inside the statement"
    )


def test_k_12_the_same_writes_succeed_with_a_fresh_token_and_rotate_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-12's contrast — the two writes above are refused because of the token, not by default.

    Without this run, an implementation refusing every absence write would satisfy both tests above.
    It also pins the other half of ADR-0007's addendum of 2026-09-22 (point 2): an absence write
    **rotates the position's token**, so the editor of the month grid of the same position is
    invalidated. That is the false collision the addendum accepts by name, and asserting it here is
    what keeps it a decision rather than an accident.
    """
    state = _committed_fixture(engine)
    token = _token_of(committing_client, state["project_id"], state["draft_id"])

    created = committing_client.post(
        absences_path(state["project_id"], state["draft_id"], state["draft_position_id"]),
        json=_insert_body(state, token),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert created.status_code == 201, created.text
    assert created.json()["updated_at"] != token, "the absence insert did not rotate the token"
    assert len(created.json()["absences"]) == 2

    # The token from the *previous* response is the fresh one; the original is now stale.
    stale = committing_client.request(
        "DELETE",
        absence_path(
            state["project_id"],
            state["draft_id"],
            state["draft_position_id"],
            state["draft_absence_id"],
        ),
        json={"updated_at": token},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert stale.status_code == 409, stale.text
    assert "changed since it was read" in stale.json()["detail"]

    deleted = committing_client.request(
        "DELETE",
        absence_path(
            state["project_id"],
            state["draft_id"],
            state["draft_position_id"],
            state["draft_absence_id"],
        ),
        json={"updated_at": created.json()["updated_at"]},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert deleted.status_code == 200, deleted.text
    assert [row["id"] for row in deleted.json()["absences"]] != [str(state["draft_absence_id"])]
    assert len(_absence_ids(engine, state["draft_position_id"])) == 1


def test_a_delete_of_an_absence_that_does_not_exist_rotates_no_token(
    committing_client: TestClient, engine: Engine
) -> None:
    """Not an acceptance criterion — the side effect a token rotation would otherwise have.

    ADR-0004's addendum of 2026-09-19 (point 1) recorded exactly this shape for archiving: a
    statement that writes `updated_at` invalidates every editor's token even when nothing they cared
    about changed. Here it would be worse, because *nothing at all* changes — the absence is not
    there. The existence of the row is therefore a condition of the guarded `UPDATE`, and this test
    is what holds it there.
    """
    state = _committed_fixture(engine)
    token = _token_of(committing_client, state["project_id"], state["draft_id"])

    response = committing_client.request(
        "DELETE",
        absence_path(
            state["project_id"], state["draft_id"], state["draft_position_id"], uuid.uuid4()
        ),
        json={"updated_at": token},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 404, response.text
    assert _token_of(committing_client, state["project_id"], state["draft_id"]) == token, (
        "deleting an absence that does not exist rotated the position's concurrency token"
    )


# --- K-13: no absence write reaches an approved scenario -----------------------------------------


def test_k_13_an_absence_insert_into_an_approved_scenario_is_refused_and_writes_nothing(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-13, the insert path — refused, with the reason named, and no row written.

    The token carried is the *current* one, read a moment earlier, so concurrency cannot be what
    refuses this write: the only reason available is the state of the scenario. That is why the
    assertion on the message matters — the two `409`s share a status code and are told apart by what
    they say, and "approved" is advice the caller can act on (copy the scenario) while "changed
    since it was read" is advice that would never work here.

    The rows are counted from a separate connection. The contrast is the same request against the
    `draft` scenario of the same project, with the same body, so the refusal cannot be an accident
    of the payload, the permissions or the scope.
    """
    state = _committed_fixture(engine)
    token = _token_of(committing_client, state["project_id"], state["approved_id"])
    before = _absence_ids(engine, state["approved_position_id"])

    refused = committing_client.post(
        absences_path(
            state["project_id"], state["approved_id"], state["approved_position_id"]
        ),
        json=_insert_body(state, token),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"], refused.text
    assert _absence_ids(engine, state["approved_position_id"]) == before, (
        "an absence was written into an approved scenario"
    )

    draft_token = _token_of(committing_client, state["project_id"], state["draft_id"])
    accepted = committing_client.post(
        absences_path(state["project_id"], state["draft_id"], state["draft_position_id"]),
        json=_insert_body(state, draft_token),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert accepted.status_code == 201, accepted.text
    assert len(_absence_ids(engine, state["draft_position_id"])) == 2


def test_k_13_an_absence_delete_under_an_approved_scenario_is_refused_and_deletes_nothing(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-13, the delete path — the guard covers removing a row, not only adding one.

    ADR-0004 does not distinguish "changing" an approved calculation from "removing" a row of it,
    and a guard implemented on the insert alone would let a caller empty an approved scenario's
    absences one at a time — an AC-10 regression that leaves no trace, because the rows it removes
    are the evidence.

    The contrast is the same delete against the draft scenario, which succeeds.
    """
    state = _committed_fixture(engine)
    token = _token_of(committing_client, state["project_id"], state["approved_id"])
    before = _absence_ids(engine, state["approved_position_id"])
    assert before, "the fixture wrote no absence under the approved scenario"

    refused = committing_client.request(
        "DELETE",
        absence_path(
            state["project_id"],
            state["approved_id"],
            state["approved_position_id"],
            state["approved_absence_id"],
        ),
        json={"updated_at": token},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"], refused.text
    assert _absence_ids(engine, state["approved_position_id"]) == before, (
        "an absence of an approved scenario was deleted"
    )

    draft_token = _token_of(committing_client, state["project_id"], state["draft_id"])
    accepted = committing_client.request(
        "DELETE",
        absence_path(
            state["project_id"],
            state["draft_id"],
            state["draft_position_id"],
            state["draft_absence_id"],
        ),
        json={"updated_at": draft_token},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert accepted.status_code == 200, accepted.text
    assert _absence_ids(engine, state["draft_position_id"]) == []


def test_k_13_an_approval_committed_just_before_the_absence_insert_still_refuses_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-13, the insert path, in a race — the predicate is in the statement, not in Python.

    The approval lands after `create_absence` has resolved the scenario through the scope-filtered
    read path and after the token was read, and before the guarded statement runs. A predicate
    inside that statement sees it (no parent matches, zero rows affected, `409`); an
    `if scenario.status == APPROVED` does not, because it read the status before the competitor
    committed — and the absence then lands in an approved calculation, which is the AC-10 regression
    ADR-0004's addendum warns about for behaviourally protected tables.
    """
    state = _committed_fixture(engine)
    token = _token_of(committing_client, state["project_id"], state["draft_id"])
    before = _absence_ids(engine, state["draft_position_id"])
    fired: list[str] = []
    interleave = _committing_the_approval_before(
        engine, fired, state["draft_id"], fragment=ABSENCE_INSERT
    )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        response = committing_client.post(
            absences_path(state["project_id"], state["draft_id"], state["draft_position_id"]),
            json=_insert_body(state, token),
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the approval never landed inside the window — nothing here is about the race"
    assert response.status_code == 409, response.text
    assert "approved" in response.json()["detail"], response.text
    assert _absence_ids(engine, state["draft_position_id"]) == before, (
        "an absence was inserted although the scenario was approved before the INSERT ran — the "
        "guard is not in the statement"
    )


def test_k_13_an_approval_committed_just_before_the_absence_delete_still_refuses_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-13, the delete path, in a race — the same claim, the other statement.

    Separate from the insert run for the same reason as everywhere in this file: they are two
    statements, and the mutation "the predicate moved into Python" can be applied to one of them
    alone. This is also the run that would catch a delete path guarded only by the *existence* of
    the row — that condition is satisfied here, and the scenario's status is the only thing standing
    between the caller and a removed row of an approved calculation.
    """
    state = _committed_fixture(engine)
    token = _token_of(committing_client, state["project_id"], state["draft_id"])
    before = _absence_ids(engine, state["draft_position_id"])
    fired: list[str] = []
    interleave = _committing_the_approval_before(
        engine, fired, state["draft_id"], fragment=ABSENCE_DELETE
    )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        response = committing_client.request(
            "DELETE",
            absence_path(
                state["project_id"],
                state["draft_id"],
                state["draft_position_id"],
                state["draft_absence_id"],
            ),
            json={"updated_at": token},
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the approval never landed inside the window — nothing here is about the race"
    assert response.status_code == 409, response.text
    assert "approved" in response.json()["detail"], response.text
    assert _absence_ids(engine, state["draft_position_id"]) == before, (
        "an absence was deleted although the scenario was approved before the DELETE ran — the "
        "guard is not in the statement"
    )


def test_k_13_deleting_an_absence_that_does_not_exist_under_an_approved_scenario_is_a_404(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-13's second contrast — `404` takes precedence over `409` (the R-01 correction).

    Two refusals compete here, and the wrong order is the one SC-3-01 had to correct one table over:
    asking about `approved` first answers a delete of an absence that has **no row** with
    `409 "this scenario is approved — copy it and change the copy"`. That advice cannot work:
    the copy is a `draft`, so the `approved` reason disappears and the same request against it is a
    `404` for an absence that never existed. A refusal must not name a reason it has not
    established.

    **Both halves in one test on purpose.** The `404` alone is satisfiable by answering `404` to
    everything, which would quietly destroy K-13; the contrast is the same `DELETE` against the
    absence that *does* exist under the *same* approved scenario, which must stay a `409` naming
    `approved`.
    """
    state = _committed_fixture(engine)
    token = _token_of(committing_client, state["project_id"], state["approved_id"])

    missing = committing_client.request(
        "DELETE",
        absence_path(
            state["project_id"],
            state["approved_id"],
            state["approved_position_id"],
            uuid.uuid4(),
        ),
        json={"updated_at": token},
        headers=as_caller(IN_SCOPE_USER),
    )
    existing = committing_client.request(
        "DELETE",
        absence_path(
            state["project_id"],
            state["approved_id"],
            state["approved_position_id"],
            state["approved_absence_id"],
        ),
        json={"updated_at": token},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert missing.status_code == 404, missing.text
    assert "approved" not in missing.text.lower(), (
        "an absence that has no row was refused with the frozen-scenario reason — advice the "
        "caller cannot act on, because the same request against the copy is a 404"
    )
    assert existing.status_code == 409, existing.text
    assert "approved" in existing.json()["detail"], existing.text
    assert len(_absence_ids(engine, state["approved_position_id"])) == 1
