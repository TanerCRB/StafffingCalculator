"""SC-3-02, K-10 — an absence is project data, and its scope boundary answers `404`, never `403`.

The other side of criterion K-09. Calendars and absence *types* belong to the organisation and have
no scope; an absence **instance** belongs to a project through two levels of indirection —
`staffing_position_absence.position_id → staffing_position.scenario_id → scenarios.project_id` — and
ADR-0005's addendum of 2026-09-22 (point 4) forbids stretching the catalogue's exemption onto it.

What "`404` never `403`" has to mean to be worth asserting: a caller outside the scope must not be
able to tell an absence that exists from one that never did. So the assertions below compare the
out-of-scope answer with the answer for an id nobody ever created — status, body **and**
content-length — rather than merely checking that the status is not `403`. A `404` whose body said
"this belongs to another project" would pass the first check and fail the others, and it would be
the leak.

The mutation the criterion names — `project_for_caller` replaced by an unfiltered `session.get` —
turns the first response into a `200` carrying the victim's row, and fails immediately.
"""

import uuid
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.staffing import STAFFING_NOT_FOUND_DETAIL
from app.models import ProjectAccess
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
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


def _project_of_another_user(session: Session):
    """A project, a scenario, a position and an absence — all belonging to somebody else.

    `OUT_OF_SCOPE_USER` holds the only `project_access` row, so `IN_SCOPE_USER` cannot see any of
    it. The rows are real and complete, which is what makes the `404` below a statement about the
    filter rather than about an empty database.
    """
    calendar = make_working_calendar(
        session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    project = make_project(
        session, name="Borealis rollout", accessible_to=(OUT_OF_SCOPE_USER,)
    )
    scenario = make_scenario(session, project, name="Baseline")
    position = make_staffing_position(
        session, scenario, make_dimension_tuple(session, calendar=calendar), start_date=MARCH
    )
    absence = make_absence(
        session,
        position,
        make_absence_type(session),
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 6),
    )
    return project, scenario, position, absence


def test_k_10_an_absence_under_a_project_outside_the_callers_scope_is_not_found_not_forbidden(
    client: TestClient, db_session: Session
) -> None:
    """K-10 — the out-of-scope answer is **identical** to the answer for an id nobody created.

    Three responses are compared:

    1. the absences of a real position, in a real scenario, of a project the caller has no
       `project_access` row for;
    2. the same request against a position id that does not exist anywhere;
    3. the same request against a project id that does not exist anywhere.

    All three are `404` with the same body and the same `content-length`. Asserting on the length
    as well as on the text is not belt and braces: it is what catches a body that differs by
    whitespace, by a trailing identifier, or by a longer message for one of the cases — any of which
    is enough to tell an existing project from a missing one by timing or by size.

    The contrast is the last block: **one** `project_access` row added for this caller, nothing else
    changed, and the same request answers `200` with the absence in it. Without it a server that
    answered `404` to everything would pass.
    """
    project, scenario, position, absence = _project_of_another_user(db_session)
    path = absences_path(project.id, scenario.id, position.id)

    out_of_scope = client.get(path, headers=as_caller(IN_SCOPE_USER))
    unknown_position = client.get(
        absences_path(project.id, scenario.id, uuid.uuid4()), headers=as_caller(IN_SCOPE_USER)
    )
    unknown_project = client.get(
        absences_path(uuid.uuid4(), scenario.id, position.id), headers=as_caller(IN_SCOPE_USER)
    )

    for response in (out_of_scope, unknown_position, unknown_project):
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == STAFFING_NOT_FOUND_DETAIL
    assert (
        out_of_scope.text == unknown_position.text == unknown_project.text
    ), "the three refusals differ in their bodies"
    assert (
        out_of_scope.headers["content-length"]
        == unknown_position.headers["content-length"]
        == unknown_project.headers["content-length"]
    ), "the three refusals differ in length — an out-of-scope project is distinguishable"
    assert str(absence.id) not in out_of_scope.text

    # The contrast: one grant, nothing else.
    db_session.add(ProjectAccess(user_id=IN_SCOPE_USER, project_id=project.id))
    db_session.flush()
    granted = client.get(path, headers=as_caller(IN_SCOPE_USER))

    assert granted.status_code == 200, granted.text
    assert [row["id"] for row in granted.json()["absences"]] == [str(absence.id)]


def test_k_10_the_write_paths_answer_the_same_absence_for_a_project_outside_the_scope(
    client: TestClient, db_session: Session
) -> None:
    """K-10 on the **write** paths — ADR-0005's addendum applies to writing as much as to reading.

    A `POST` and a `DELETE` against another user's position. Both `404`, both with the staffing
    body, and — the assertion that carries the claim — **the victim's rows are unchanged**: the
    absence they had is still there, and no new one was written.

    A refusal that answered `409` ("the position changed since it was read", or "this scenario is
    approved") would be a side channel confirming that the position exists, which is exactly what
    point 4 of the addendum forbids for write-specific codes. The token sent below is a plausible
    one, so nothing about its shape can be the reason for the refusal.
    """
    project, scenario, position, absence = _project_of_another_user(db_session)
    absence_type = make_absence_type(db_session, name="Sick leave")
    token = "2026-03-01T00:00:00+00:00"

    created = client.post(
        absences_path(project.id, scenario.id, position.id),
        json={
            "updated_at": token,
            "absence_type_id": str(absence_type.id),
            "start_date": "2026-03-09",
            "end_date": "2026-03-13",
        },
        headers=as_caller(IN_SCOPE_USER),
    )
    deleted = client.request(
        "DELETE",
        f"{absences_path(project.id, scenario.id, position.id)}/{absence.id}",
        json={"updated_at": token},
        headers=as_caller(IN_SCOPE_USER),
    )

    for response in (created, deleted):
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == STAFFING_NOT_FOUND_DETAIL
        assert "approved" not in response.text.lower()
        assert "changed since" not in response.text

    db_session.expire_all()
    surviving = client.get(
        absences_path(project.id, scenario.id, position.id), headers=as_caller(OUT_OF_SCOPE_USER)
    )
    assert [row["id"] for row in surviving.json()["absences"]] == [str(absence.id)], (
        "a caller outside the scope changed the victim's absences"
    )


def test_a_position_with_no_absences_is_an_empty_list_and_not_a_404(
    client: TestClient, db_session: Session
) -> None:
    """Not an acceptance criterion — the distinction K-10 would otherwise be able to erase.

    "Nothing planned here" and "no such position" must be two different answers, or a caller who
    mistypes an id sees an empty plan and believes it. This is also the assertion that stops K-10
    from being satisfiable by an endpoint that answers `404` to everything, one layer below the
    contrast K-10 already carries.
    """
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    position = make_staffing_position(
        db_session,
        scenario,
        make_dimension_tuple(db_session, calendar=calendar),
        start_date=MARCH,
    )

    empty = client.get(
        absences_path(project.id, scenario.id, position.id), headers=as_caller(IN_SCOPE_USER)
    )
    missing = client.get(
        absences_path(project.id, scenario.id, uuid.uuid4()), headers=as_caller(IN_SCOPE_USER)
    )

    assert empty.status_code == 200, empty.text
    assert empty.json() == {"absences": []}
    assert missing.status_code == 404, missing.text


def test_an_absence_is_carried_on_the_position_payload_as_well_as_on_its_own_endpoint(
    client: TestClient, db_session: Session
) -> None:
    """Not a criterion — the two read paths must agree about one row.

    The grid endpoint carries the absences of every position (that is what makes a single request
    enough to render a plan), and the per-position endpoint carries the same rows. Two read paths
    for one table is how a field ends up present on one and missing on the other; this is the
    assertion that keeps them together.
    """
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    position = make_staffing_position(
        db_session,
        scenario,
        make_dimension_tuple(db_session, calendar=calendar),
        start_date=MARCH,
    )
    absence = make_absence(
        db_session,
        position,
        make_absence_type(db_session),
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 6),
    )

    grid = client.get(staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
    own = client.get(
        absences_path(project.id, scenario.id, position.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert grid.json()["positions"][0]["absences"] == own.json()["absences"]
    assert own.json()["absences"] == [
        {
            "id": str(absence.id),
            "absence_type_id": str(absence.absence_type_id),
            "start_date": "2026-03-02",
            "end_date": "2026-03-06",
        }
    ], "the absence payload carries a field beyond the four this table is allowed to have"
