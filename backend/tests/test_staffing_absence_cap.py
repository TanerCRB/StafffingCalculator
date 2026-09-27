"""SC-3-05 — criterion K-07: a position cannot accumulate more than `MAX_ABSENCES_PER_POSITION`
planned absences (gate-1 decision Q-B).

One criterion test plus its contrast, and one additional run proving the *mechanism* claim the
human's gate-1 decision names explicitly: the cap is a condition of the same guarded `UPDATE` that
rotates the position's token, never a `SELECT` run first in Python — a check-then-act window two
concurrent callers at the limit could both pass. That third test is not itself K-07 (K-07 asks
nothing about concurrency); it is the proof that the *shape* asked for in the impact map is the
shape this file's fixtures actually exercise, run the same way `test_staffing_absence_guards.py`
proves the token/`approved` guards — a competitor commits on a separate connection inside the window
between the caller's read and the guarded statement.
"""

import uuid
from datetime import date
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.data.staffing import MAX_ABSENCES_PER_POSITION
from tests.conftest import (
    IN_SCOPE_USER,
    absences_path,
    as_caller,
    count_absences,
    make_absence,
    make_absence_type,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    staffing_path,
)

MARCH = date(2026, 3, 1)

Listener = Any


def _position_with_n_absences(
    session: Session, count: int
) -> dict[str, uuid.UUID]:
    """A project/scenario/position in `IN_SCOPE_USER`'s scope, with exactly `count` absences already
    booked directly (bypassing the endpoint — the same reason every other absence fixture does)."""
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline")
    position = make_staffing_position(
        session, scenario, make_dimension_tuple(session), start_date=MARCH
    )
    absence_type = make_absence_type(session)
    for index in range(count):
        day = date(2026, 1, 1 + (index % 28))
        make_absence(session, position, absence_type, start_date=day, end_date=day)
    return {
        "project_id": project.id,
        "scenario_id": scenario.id,
        "position_id": position.id,
        "absence_type_id": absence_type.id,
    }


def _token(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    response = client.get(staffing_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    return response.json()["positions"][0]["updated_at"]


def _absence_body(state: dict[str, uuid.UUID], token: str) -> dict[str, str]:
    return {
        "updated_at": token,
        "absence_type_id": str(state["absence_type_id"]),
        "start_date": "2026-06-01",
        "end_date": "2026-06-02",
    }


def test_k_07_an_absence_past_the_limit_is_refused_and_writes_nothing(
    client: TestClient, db_session: Session
) -> None:
    """K-07 — a position already at the cap refuses the next absence, with no row written.

    The refusal names the limit (`MAX_ABSENCES_PER_POSITION`), so the caller has something to act on
    rather than an opaque conflict, and it is asserted against a row count read from the test's own
    transaction — a `409` returned after an insert would satisfy a status-only assertion.
    """
    state = _position_with_n_absences(db_session, MAX_ABSENCES_PER_POSITION)
    token = _token(client, state["project_id"], state["scenario_id"])
    before = count_absences(db_session)
    assert before == MAX_ABSENCES_PER_POSITION

    refused = client.post(
        absences_path(state["project_id"], state["scenario_id"], state["position_id"]),
        json=_absence_body(state, token),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert refused.status_code == 409, refused.text
    assert str(MAX_ABSENCES_PER_POSITION) in refused.json()["detail"], refused.text
    assert count_absences(db_session) == before, "a write landed on a position already at the cap"


def test_k_07_the_contrast_a_position_one_below_the_limit_still_accepts_the_write(
    client: TestClient, db_session: Session
) -> None:
    """K-07's contrast — without it, an implementation refusing every absence would also pass above.

    The position sits at `MAX_ABSENCES_PER_POSITION - 1`; the identical request that was refused
    above is accepted here, and the response carries exactly `MAX_ABSENCES_PER_POSITION` absences —
    proving the accepted write is the one that reaches the cap, not one short of it by accident.
    """
    state = _position_with_n_absences(db_session, MAX_ABSENCES_PER_POSITION - 1)
    token = _token(client, state["project_id"], state["scenario_id"])

    accepted = client.post(
        absences_path(state["project_id"], state["scenario_id"], state["position_id"]),
        json=_absence_body(state, token),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert accepted.status_code == 201, accepted.text
    assert len(accepted.json()["absences"]) == MAX_ABSENCES_PER_POSITION


def test_the_cap_is_checked_inside_the_guarded_statement_not_by_a_python_pre_check(
    committing_client: TestClient, engine: Engine
) -> None:
    """Not K-07 itself — the mechanism claim behind it (gate-1 decision Q-B): the cap is a condition
    of the same guarded `UPDATE` that rotates the token, never a `SELECT` read first in Python.

    The position sits at `MAX_ABSENCES_PER_POSITION - 1` when the caller reads its token — one
    slot free. A competitor inserts the 60th absence directly, on a separate connection, in the
    window the `before_cursor_execute` hook opens just before our own guarded statement runs. A
    Python "count, then decide" implementation would have already read `MAX - 1` *before* this
    window and would proceed to write regardless of what the competitor just committed, landing a
    61st absence. The guard this task implements instead re-reads the count *inside* the same
    statement that would insert, so it sees the competitor's row and refuses.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Borealis rollout", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Baseline")
        position = make_staffing_position(
            setup, scenario, make_dimension_tuple(setup), start_date=MARCH
        )
        absence_type = make_absence_type(setup)
        for index in range(MAX_ABSENCES_PER_POSITION - 1):
            day = date(2026, 1, 1 + (index % 28))
            make_absence(setup, position, absence_type, start_date=day, end_date=day)
        state = {
            "project_id": project.id,
            "scenario_id": scenario.id,
            "position_id": position.id,
            "absence_type_id": absence_type.id,
        }
        setup.commit()

    token = _token(committing_client, state["project_id"], state["scenario_id"])
    fired: list[str] = []

    def interleave(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or "insert into staffing_position_absence" not in statement.lower():
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(
                sa.text(
                    "INSERT INTO staffing_position_absence"
                    " (id, position_id, absence_type_id, start_date, end_date)"
                    " VALUES (:id, :position_id, :absence_type_id, :start_date, :end_date)"
                ),
                {
                    "id": uuid.uuid4(),
                    "position_id": state["position_id"],
                    "absence_type_id": state["absence_type_id"],
                    "start_date": date(2026, 5, 1),
                    "end_date": date(2026, 5, 2),
                },
            )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        response = committing_client.post(
            absences_path(state["project_id"], state["scenario_id"], state["position_id"]),
            json=_absence_body(state, token),
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the competing insert never landed inside the window — nothing here is a race"
    assert response.status_code == 409, response.text
    assert str(MAX_ABSENCES_PER_POSITION) in response.json()["detail"], response.text

    with engine.connect() as connection:
        final_count = connection.execute(
            sa.text(
                "SELECT count(*) FROM staffing_position_absence WHERE position_id = :id"
            ),
            {"id": state["position_id"]},
        ).scalar_one()
    assert final_count == MAX_ABSENCES_PER_POSITION, (
        "more than the named cap ended up committed — a pre-check read a stale count"
    )
