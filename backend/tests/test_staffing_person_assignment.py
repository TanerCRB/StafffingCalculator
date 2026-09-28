"""SC-2-06 (Issue #31; ADR-0019; ADR-0004/0005 aneksy 2026-09-27) — a named person on a staffing
position: K-03, K-04, K-05, K-06, K-07.

Every person here is fictitious (ADR-0019, point 8). Every approved scenario reached without the
approval endpoint is made by a direct database write, the limit `tests/conftest.py` already names
for SC-3-01; the race tests go through the real approval endpoint.

The positive branch of `PEOPLE_READ` is reached through `caller_holding` only — the placeholder
identity does not hold it (ADR-0005, aneks 2026-09-27, point 4).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.staffing import STAFFING_NOT_FOUND_DETAIL
from app.core.identity import Permission
from app.models import Person, Scenario, ScenarioStatus, StaffingPosition
from app.models import staffing as staffing_model
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    assign_person_directly,
    caller_holding,
    count_people,
    count_positions,
    make_absence_type,
    make_allocation,
    make_dimension_tuple,
    make_person,
    make_project,
    make_scenario,
    make_staffing_position,
    staffing_path,
    staffing_position_payload,
)
from tests.test_scenario_duplication import duplicate_path
from tests.test_scenario_results import _ensure_statutory_bypass, _full_scenario, results_path
from tests.test_staffing_approved_guards import (
    _committing_the_approval_before,
    _k20_state,
    _race_a_child_write_against_the_approval,
    _scenario_is_approved,
)
from tests.test_staffing_positions_cost_basis_hidden import POSITION_FIELDS

MARCH = date(2026, 3, 1)
MARKER = "person_assignment_updated_at"
OLD_MARKER = datetime(2020, 1, 1, tzinfo=UTC)
"""The assignment's own concurrency marker — request field and response key alike (P-1, P-4;
ADR-0007 aneks 2026-09-28)."""
EVERYTHING = frozenset(Permission)
WITHOUT_PEOPLE_READ = EVERYTHING - {Permission.PEOPLE_READ}
ASSIGNER = (Permission.STAFFING_READ, Permission.STAFFING_WRITE, Permission.PEOPLE_READ)
"""The smallest caller that may assign *and* read the assignment back (STAFFING_WRITE ∧ PEOPLE_READ
for the write, STAFFING_READ ∧ PEOPLE_READ for the field)."""


def _person_path(project_id: uuid.UUID, scenario_id: uuid.UUID, position_id: uuid.UUID) -> str:
    return f"{staffing_path(project_id, scenario_id)}/{position_id}/person"


def _marker(session: Session, position_id: uuid.UUID) -> str:
    """The position's `person_assignment_updated_at` as stored — the marker `PATCH …/person` needs
    (ADR-0007 aneks 2026-09-28)."""
    session.expire_all()
    return session.execute(
        sa.select(StaffingPosition.person_assignment_updated_at).where(
            StaffingPosition.id == position_id
        )
    ).scalar_one().isoformat()


def _person_id_in_db(session: Session, position_id: uuid.UUID) -> uuid.UUID | None:
    session.expire_all()
    return session.execute(
        sa.select(StaffingPosition.person_id).where(StaffingPosition.id == position_id)
    ).scalar_one()


def _grid(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict[str, Any]:
    """The grid by position id, read by a caller holding everything — for tokens."""
    with caller_holding(*EVERYTHING):
        response = client.get(staffing_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return {row["id"]: row for row in response.json()["positions"]}


def _without_identity(position: dict[str, Any]) -> dict[str, Any]:
    """A position body minus what must differ between two *different* rows even when they are
    equal in everything else: row ids and the ADR-0007 token (two rows, two tokens). Since D-4 = B
    the assignment no longer moves the token — the comparison of *one* row before and after an
    assignment, `updated_at` included, is `test_pd_k7_…` below."""
    body = {key: value for key, value in position.items() if key not in {"id", "updated_at"}}
    body["allocations"] = [
        {key: value for key, value in row.items() if key != "id"} for row in body["allocations"]
    ]
    body["absences"] = [
        {key: value for key, value in row.items() if key != "id"} for row in body["absences"]
    ]
    return body


def _twin_positions(session: Session, *, cost_visible: bool = True) -> dict[str, Any]:
    """A scenario with two positions equal in every input — one assigned to a person, one
    anonymous — plus an absence type. Both `headcount = 1` (Q-7 = a)."""
    project = make_project(
        session,
        name="Aurora migration",
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,) if cost_visible else (),
    )
    scenario = make_scenario(session, project, name="Baseline")
    dimensions = make_dimension_tuple(session)
    assigned = make_staffing_position(session, scenario, dimensions, headcount=1, start_date=MARCH)
    anonymous = make_staffing_position(session, scenario, dimensions, headcount=1, start_date=MARCH)
    for position in (assigned, anonymous):
        make_allocation(session, position, period_month=MARCH)
    person = make_person(session)
    assign_person_directly(session, assigned, person)
    return {
        "project": project,
        "scenario": scenario,
        "dimensions": dimensions,
        "assigned": assigned,
        "anonymous": anonymous,
        "person": person,
        "absence_type": make_absence_type(session),
    }


# --- K-03: without PEOPLE_READ, an assigned position is indistinguishable from an anonymous one ---


def _assert_hidden(response: Any, person: Person) -> None:
    """Neither person key (A5-31-2, A5-31-8), no person id, no name."""
    assert "person_id" not in response.text, response.text
    assert MARKER not in response.text, response.text
    assert str(person.id) not in response.text
    assert person.full_name not in response.text


def test_k_03_an_assigned_position_is_indistinguishable_from_an_anonymous_one_without_people_read(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — on **every** path returning a position, for a caller holding every permission but
    `PEOPLE_READ` (so no other refusal can be what hides it): the list, the create, the allocation
    edit, the cost-basis edit, the absence add, the absence delete, and the list of a duplicated
    scenario. On each path the same write is made to both twins and the two answers are compared
    **whole** (minus row ids and the token): same field set, same values. No `person_id` key — not
    even `null` — no person id and no name anywhere in any body.

    Mutations this kills: "the person field is always in the response" (the key appears — also
    caught by `test_staffing_positions_cost_basis_hidden.py`'s unchanged field set), and "the gate
    is applied on the list only" (every write response and the duplicate's list are checked too).
    """
    state = _twin_positions(db_session)
    project, scenario, person = state["project"], state["scenario"], state["person"]
    assigned, anonymous = str(state["assigned"].id), str(state["anonymous"].id)
    base = staffing_path(project.id, scenario.id)

    def compare(first: Any, second: Any) -> None:
        for response in (first, second):
            assert response.status_code in (200, 201), response.text
            _assert_hidden(response, person)
        assert set(first.json()) == POSITION_FIELDS
        assert _without_identity(first.json()) == _without_identity(second.json())

    with caller_holding(*WITHOUT_PEOPLE_READ):
        listed = client.get(base)
        _assert_hidden(listed, person)
        rows = {row["id"]: row for row in listed.json()["positions"]}
        assert set(rows[assigned]) == POSITION_FIELDS
        assert _without_identity(rows[assigned]) == _without_identity(rows[anonymous])

        payload = staffing_position_payload(state["dimensions"], headcount=1)
        created = client.post(base, json=payload)
        assert created.status_code == 201, created.text
        _assert_hidden(created, person)
        assert set(created.json()) == POSITION_FIELDS

        tokens = {key: row["updated_at"] for key, row in rows.items()}

        def both(method: str, suffix: str, body: dict[str, Any]) -> tuple[Any, Any]:
            answers = []
            for position_id in (assigned, anonymous):
                answer = client.request(
                    method,
                    f"{base}/{position_id}{suffix}",
                    json={"updated_at": tokens[position_id], **body},
                )
                if answer.status_code in (200, 201):
                    tokens[position_id] = answer.json()["updated_at"]
                answers.append(answer)
            return answers[0], answers[1]

        compare(*both("PATCH", f"/allocations/{MARCH.isoformat()}",
                      {"planned_allocation_hours": "90.00"}))
        compare(*both("PATCH", "", {"cost_basis": "fixed_amount", "fixed_amount": "10.0000",
                                    "fixed_amount_currency": "PLN"}))
        added = both("POST", "/absences", {"absence_type_id": str(state["absence_type"].id),
                                           "start_date": "2026-03-02", "end_date": "2026-03-03"})
        compare(*added)
        absence_ids = [answer.json()["absences"][0]["id"] for answer in added]
        removed = []
        for position_id, absence_id in zip((assigned, anonymous), absence_ids, strict=True):
            answer = client.request(
                "DELETE",
                f"{base}/{position_id}/absences/{absence_id}",
                json={"updated_at": tokens[position_id]},
            )
            removed.append(answer)
        compare(*removed)

        duplicated = client.post(duplicate_path(project.id, scenario.id))
        assert duplicated.status_code == 201, duplicated.text
        copy_id = uuid.UUID(duplicated.json()["id"])
        copy_list = client.get(staffing_path(project.id, copy_id))
        _assert_hidden(copy_list, person)
        copy_rows = [row for row in copy_list.json()["positions"] if row["headcount"] == 1]

    copied_people = set(
        db_session.execute(
            sa.select(StaffingPosition.person_id).where(StaffingPosition.scenario_id == copy_id)
        ).scalars()
    )
    assert person.id in copied_people, "the duplicate lost the assignment — the check was vacuous"
    assert len(copy_rows) == 3  # the two twins and the position created above
    assert all(set(row) == POSITION_FIELDS for row in copy_rows)
    twins = [
        row for row in copy_rows
        if row["allocations"][0]["planned_allocation_hours"] == "90.00"
    ]
    assert len(twins) == 2
    assert _without_identity(twins[0]) == _without_identity(twins[1])
    assert _person_id_in_db(db_session, state["assigned"].id) == person.id


def test_k_03_the_contrast_with_people_read_the_assignment_is_visible_on_every_path(
    client: TestClient, db_session: Session
) -> None:
    """The contrast that keeps K-03 from passing by hiding everything: with `STAFFING_READ` ∧
    `PEOPLE_READ` the assigned position carries `person_id` (the id only — no name, decision 4 =
    a1) and the anonymous one carries `person_id: null`, on the list and on a write response; the
    create answers with an explicit `null` too."""
    state = _twin_positions(db_session)
    project, scenario, person = state["project"], state["scenario"], state["person"]
    base = staffing_path(project.id, scenario.id)

    with caller_holding(*EVERYTHING):
        rows = {row["id"]: row for row in client.get(base).json()["positions"]}
        edited = client.patch(
            f"{base}/{state['assigned'].id}/allocations/{MARCH.isoformat()}",
            json={"updated_at": rows[str(state["assigned"].id)]["updated_at"],
                  "billable_hours": "80.00"},
        )
        payload = staffing_position_payload(state["dimensions"], headcount=1)
        created = client.post(base, json=payload)

    assert rows[str(state["assigned"].id)]["person_id"] == str(person.id)
    assert rows[str(state["anonymous"].id)]["person_id"] is None
    # A5-31-8: the marker key is present together with `person_id`, and only then.
    assert set(rows[str(state["assigned"].id)]) == POSITION_FIELDS | {"person_id", MARKER}
    assert set(rows[str(state["anonymous"].id)]) == POSITION_FIELDS | {"person_id", MARKER}
    assert set(edited.json()) == POSITION_FIELDS | {"person_id", MARKER}
    assert edited.json()["person_id"] == str(person.id)
    assert created.json()["person_id"] is None
    assert person.full_name not in edited.text


def test_k_03_people_read_without_staffing_read_does_not_see_the_assignment_on_a_write_response(
    client: TestClient, db_session: Session
) -> None:
    """The gate is `STAFFING_READ` ∧ `PEOPLE_READ` (ADR-0019, point 4), not `PEOPLE_READ` alone: a
    caller who may write staffing and read the register, but not read staffing, gets no
    `person_id` back from a write — the write response is not a way around the read permission."""
    state = _twin_positions(db_session)
    project, scenario, person = state["project"], state["scenario"], state["person"]
    token = _grid(client, project.id, scenario.id)[str(state["assigned"].id)]["updated_at"]

    with caller_holding(Permission.STAFFING_WRITE, Permission.PEOPLE_READ):
        edited = client.patch(
            f"{staffing_path(project.id, scenario.id)}/{state['assigned'].id}"
            f"/allocations/{MARCH.isoformat()}",
            json={"updated_at": token, "billable_hours": "80.00"},
        )
    assert edited.status_code == 200, edited.text
    _assert_hidden(edited, person)


# --- K-04: assigning a person changes no calculation --------------------------------------------


def _figures(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict[str, Any]:
    """Every calculated answer about the scenario, read by a caller with both cost gates open:
    the whole-scenario result (revenue, personnel cost, additional cost, profit, margin, markup),
    the personnel-cost endpoint, and the grid's derived capacity — minus the two markers and the
    person field, which are not figures (reviewer R-01, round 2: comparing the assignment's marker
    here passed only because `now()` is frozen inside the `db_session` transaction; K-04 compares
    numbers, not markers)."""
    with caller_holding(*EVERYTHING):
        results = client.get(results_path(project_id, scenario_id))
        cost = client.get(
            f"/projects/{project_id}/scenarios/{scenario_id}/personnel-cost"
        )
        grid = client.get(staffing_path(project_id, scenario_id))
    for response in (results, cost, grid):
        assert response.status_code == 200, response.text
    positions = [
        {
            key: value
            for key, value in row.items()
            if key not in {"updated_at", "person_id", MARKER}
        }
        for row in grid.json()["positions"]
    ]
    return {"results": results.json(), "cost": cost.json(), "grid": positions}


def test_k_04_assigning_a_person_changes_neither_cost_nor_revenue_nor_capacity(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — the same scenario before and after a person is assigned through the real endpoint
    (and after the assignment is removed again): every figure identical, down to the last field.

    Contrast in the same test: changing the planned hours of the same position *does* change the
    figures — so the equality is not a comparison of two answers that could never differ. The
    mutation "a position with a person is skipped in the aggregation" makes the first comparison
    fail (the cost and the profit move)."""
    _ensure_statutory_bypass(db_session)
    project, scenario, position = _full_scenario(db_session, name="Aurora")
    position.headcount = 1  # Q-7 = a: a person only at headcount 1; set before "before".
    db_session.flush()
    person = make_person(db_session)

    before = _figures(client, project.id, scenario.id)
    assert before["results"]["personnel_cost"]["amount"] == "12000.00"
    assert before["results"]["revenue"]["amount"] == "20000.00"

    token = _grid(client, project.id, scenario.id)[str(position.id)][MARKER]
    with caller_holding(*ASSIGNER):
        assigned = client.patch(
            _person_path(project.id, scenario.id, position.id),
            json={MARKER: token, "person_id": str(person.id)},
        )
    assert assigned.status_code == 200, assigned.text
    assert _person_id_in_db(db_session, position.id) == person.id
    assert _figures(client, project.id, scenario.id) == before

    with caller_holding(*ASSIGNER):
        removed = client.patch(
            _person_path(project.id, scenario.id, position.id),
            json={MARKER: assigned.json()[MARKER], "person_id": None},
        )
    assert removed.status_code == 200, removed.text
    assert _figures(client, project.id, scenario.id) == before

    # Contrast: an input that *is* a figure moves the figures.
    db_session.execute(
        sa.text(
            "UPDATE staffing_position_allocation SET planned_allocation_hours = 50.00"
            " WHERE position_id = :id"
        ),
        {"id": position.id},
    )
    db_session.expire_all()
    assert _figures(client, project.id, scenario.id)["results"] != before["results"]


def test_k_04_a_position_without_a_person_is_created_exactly_as_before(
    client: TestClient, db_session: Session
) -> None:
    """K-04's second sentence — creating a position is unchanged: the same request as before
    SC-2-06, the same `201`, the same field set, and the new row has no person."""
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    created = client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(make_dimension_tuple(db_session)),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert created.status_code == 201, created.text
    assert set(created.json()) == POSITION_FIELDS
    assert _person_id_in_db(db_session, uuid.UUID(created.json()["id"])) is None


# --- K-05: scope, a person that does not exist, headcount ---------------------------------------


def test_k_05a_assigning_in_a_scenario_outside_the_callers_scope_is_404_and_writes_nothing(
    client: TestClient, db_session: Session
) -> None:
    """K-05a — a position of another user's project is a `404` with the same body as every other
    absence on the staffing path, never a `403`/`409`/`422`, and the position stays anonymous —
    for an existing person *and* for one that does not exist (the scope decides first, gate 1
    decision 3). Contrast: the identical request against the caller's own project succeeds.

    Mutation killed: `assign_person` without `scenario_in_scope` (the foreign position would be
    written, or answered with a `409`)."""
    theirs = make_project(db_session, name="Borealis", accessible_to=(OUT_OF_SCOPE_USER,))
    their_scenario = make_scenario(db_session, theirs, name="Baseline")
    dimensions = make_dimension_tuple(db_session)
    their_position = make_staffing_position(db_session, their_scenario, dimensions, headcount=1)
    person = make_person(db_session)
    token = _marker(db_session, their_position.id)

    with caller_holding(*EVERYTHING):
        for person_id in (str(person.id), str(uuid.uuid4()), None):
            refused = client.patch(
                _person_path(theirs.id, their_scenario.id, their_position.id),
                json={MARKER: token, "person_id": person_id},
            )
            assert refused.status_code == 404, refused.text
            assert refused.json() == {"detail": STAFFING_NOT_FOUND_DETAIL}
    assert _person_id_in_db(db_session, their_position.id) is None

    mine = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    my_scenario = make_scenario(db_session, mine, name="Baseline")
    my_position = make_staffing_position(db_session, my_scenario, dimensions, headcount=1)
    with caller_holding(*EVERYTHING):
        accepted = client.patch(
            _person_path(mine.id, my_scenario.id, my_position.id),
            json={MARKER: _marker(db_session, my_position.id), "person_id": str(person.id)},
        )
        # A position of *another* scenario, addressed through mine, is the same 404.
        foreign = client.patch(
            _person_path(mine.id, my_scenario.id, their_position.id),
            json={MARKER: token, "person_id": str(person.id)},
        )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["person_id"] == str(person.id)
    assert foreign.status_code == 404, foreign.text
    assert _person_id_in_db(db_session, their_position.id) is None


def test_k_05b_assigning_a_person_that_does_not_exist_is_refused_without_a_write(
    client: TestClient, db_session: Session
) -> None:
    """K-05b — on a `draft` scenario, an id that names nobody in the register is a `409` naming the
    condition, quoting neither the id nor any name, and the position stays as it was. It wins over
    `approved` (gate 1, decision 3: 404 → no such person → 409 approved). Contrast: an existing
    person is accepted."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    draft = make_scenario(db_session, project, name="Baseline")
    approved = make_scenario(db_session, project, name="Approved", status=ScenarioStatus.APPROVED)
    dimensions = make_dimension_tuple(db_session)
    position = make_staffing_position(db_session, draft, dimensions, headcount=1)
    frozen = make_staffing_position(db_session, approved, dimensions, headcount=1)
    nobody = uuid.uuid4()

    with caller_holding(*ASSIGNER):
        refused = client.patch(
            _person_path(project.id, draft.id, position.id),
            json={MARKER: _marker(db_session, position.id), "person_id": str(nobody)},
        )
        on_approved = client.patch(
            _person_path(project.id, approved.id, frozen.id),
            json={MARKER: _marker(db_session, frozen.id), "person_id": str(nobody)},
        )
    assert refused.status_code == 409, refused.text
    assert "No such person" in refused.json()["detail"]
    assert str(nobody) not in refused.text
    assert _person_id_in_db(db_session, position.id) is None
    assert on_approved.status_code == 409, on_approved.text
    assert "No such person" in on_approved.json()["detail"]

    person = make_person(db_session)
    with caller_holding(*ASSIGNER):
        accepted = client.patch(
            _person_path(project.id, draft.id, position.id),
            json={MARKER: _marker(db_session, position.id), "person_id": str(person.id)},
        )
    assert accepted.status_code == 200, accepted.text


def test_k_05b_the_database_refuses_a_person_that_does_not_exist_from_any_writer(
    db_session: Session,
) -> None:
    """K-05b, below the API — a fixture, a seed script or an import writing a dangling `person_id`
    is refused by `fk_staffing_position_person_id`. Mutation killed: "no constraint, only the
    check in `assign_person`"."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    position = make_staffing_position(
        db_session, make_scenario(db_session, project, name="Baseline"),
        make_dimension_tuple(db_session), headcount=1,
    )
    try:
        db_session.execute(
            sa.update(StaffingPosition)
            .where(StaffingPosition.id == position.id)
            .values(person_id=uuid.uuid4())
        )
        raise AssertionError("a dangling person_id was accepted by the database")
    except IntegrityError as error:
        assert "fk_staffing_position_person_id" in str(error.orig)
    finally:
        db_session.rollback()


def test_k_05c_a_person_only_on_a_position_with_headcount_one(
    client: TestClient, db_session: Session
) -> None:
    """K-05c (Q-7 = a) — assigning to a `headcount = 2` position is refused by the database's CHECK
    (named in the `409`), and the position stays anonymous. Contrast: `headcount = 1` is accepted.
    Mutation killed: the CHECK removed (the `headcount = 2` assignment would be written)."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session)
    two = make_staffing_position(db_session, scenario, dimensions, headcount=2)
    one = make_staffing_position(db_session, scenario, dimensions, headcount=1)
    person = make_person(db_session)
    db_session.commit()  # the write path's rollback after a refusal must not take the fixtures

    with caller_holding(*ASSIGNER):
        refused = client.patch(
            _person_path(project.id, scenario.id, two.id),
            json={MARKER: _marker(db_session, two.id), "person_id": str(person.id)},
        )
        accepted = client.patch(
            _person_path(project.id, scenario.id, one.id),
            json={MARKER: _marker(db_session, one.id), "person_id": str(person.id)},
        )
    assert refused.status_code == 409, refused.text
    assert staffing_model.PERSON_REQUIRES_SINGLE_HEADCOUNT_CONSTRAINT in refused.json()["detail"]
    assert person.full_name not in refused.text
    assert _person_id_in_db(db_session, two.id) is None
    assert accepted.status_code == 200, accepted.text


def test_k_05c_raising_the_headcount_of_a_position_with_a_person_is_refused_by_the_database(
    db_session: Session,
) -> None:
    """K-05c, the half with no HTTP path (there is no headcount edit endpoint — L-3): an `UPDATE`
    raising `headcount` to 2 on a position that has a person is refused by the CHECK. Contrast: the
    same `UPDATE` on the same position once the person is removed is accepted."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    position = make_staffing_position(
        db_session, make_scenario(db_session, project, name="Baseline"),
        make_dimension_tuple(db_session), headcount=1,
    )
    assign_person_directly(db_session, position, make_person(db_session))
    db_session.commit()

    try:
        db_session.execute(sa.text("UPDATE staffing_position SET headcount = 2 WHERE id = :id"),
                           {"id": position.id})
        raise AssertionError("a person stayed on a position whose headcount became 2")
    except IntegrityError as error:
        assert staffing_model.PERSON_REQUIRES_SINGLE_HEADCOUNT_CONSTRAINT in str(error.orig)
    finally:
        db_session.rollback()

    db_session.execute(
        sa.text("UPDATE staffing_position SET person_id = NULL, headcount = 2 WHERE id = :id"),
        {"id": position.id},
    )
    db_session.expire_all()
    assert db_session.get(StaffingPosition, position.id).headcount == 2


def test_a5_31_7_the_assignment_needs_all_three_permissions_and_answers_the_same_whatever_exists(
    client: TestClient, db_session: Session
) -> None:
    """A5-31-7 (ADR-0005 aneks 2026-09-28, D-1 = A) — a caller missing exactly one of
    `STAFFING_READ`, `STAFFING_WRITE`, `PEOPLE_READ` (and holding every other permission), or the
    running system's placeholder, is refused `403` with **the same body** whether the position and
    the person exist or not (no existence oracle), and nothing is written. Contrast: with all three
    the same request succeeds.

    Mutation killed: `STAFFING_READ` dropped from the endpoint's dependency (the first variant
    would answer `200`)."""
    state = _twin_positions(db_session)
    project, scenario = state["project"], state["scenario"]
    position = state["anonymous"]
    person = state["person"]
    token = _marker(db_session, position.id)

    real = _person_path(project.id, scenario.id, position.id)
    fake = _person_path(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    for missing in (Permission.STAFFING_READ, Permission.STAFFING_WRITE, Permission.PEOPLE_READ):
        with caller_holding(*(EVERYTHING - {missing})):
            answers = [
                client.patch(path, json={MARKER: token, "person_id": person_id})
                for path in (real, fake)
                for person_id in (str(person.id), str(uuid.uuid4()))
            ]
        assert {answer.status_code for answer in answers} == {403}, missing
        assert len({answer.text for answer in answers}) == 1, missing
        assert missing.value in answers[0].text
    placeholder = client.patch(
        real, json={MARKER: token, "person_id": str(person.id)},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert placeholder.status_code == 403, placeholder.text
    assert _person_id_in_db(db_session, position.id) is None

    with caller_holding(*ASSIGNER):
        accepted = client.patch(real, json={MARKER: token, "person_id": str(person.id)})
    assert accepted.status_code == 200, accepted.text
    assert _person_id_in_db(db_session, position.id) == person.id


def test_other_writes_by_a_caller_without_people_read_never_remove_the_assignment(
    client: TestClient, db_session: Session
) -> None:
    """ADR-0005, aneks 2026-09-27, point 6 (A5-31-5) — the allocation edit, the cost-basis edit and
    the absence add/delete, made by a caller who cannot see the assignment, leave it in place; and a
    `person_id` smuggled into the position create is a `422`, never an assignment."""
    state = _twin_positions(db_session)
    project, scenario, person = state["project"], state["scenario"], state["person"]
    base = f"{staffing_path(project.id, scenario.id)}/{state['assigned'].id}"
    token = _grid(client, project.id, scenario.id)[str(state["assigned"].id)]["updated_at"]

    with caller_holding(*WITHOUT_PEOPLE_READ):
        edited = client.patch(f"{base}/allocations/{MARCH.isoformat()}",
                              json={"updated_at": token, "billable_hours": "1.00"})
        token = edited.json()["updated_at"]
        token = client.patch(base, json={"updated_at": token, "cost_basis": "fixed_amount",
                                         "fixed_amount": "5.0000",
                                         "fixed_amount_currency": "PLN"}).json()["updated_at"]
        added = client.post(f"{base}/absences", json={
            "updated_at": token, "absence_type_id": str(state["absence_type"].id),
            "start_date": "2026-03-02", "end_date": "2026-03-02"})
        assert added.status_code == 201, added.text
        removed = client.request("DELETE", f"{base}/absences/{added.json()['absences'][0]['id']}",
                                 json={"updated_at": added.json()["updated_at"]})
        assert removed.status_code == 200, removed.text
        smuggled = client.post(
            staffing_path(project.id, scenario.id),
            json=staffing_position_payload(state["dimensions"], headcount=1,
                                           person_id=str(person.id)),
        )
    assert smuggled.status_code == 422, smuggled.text
    assert _person_id_in_db(db_session, state["assigned"].id) == person.id


# --- K-06: approved — refused in the statement that writes, per write path ----------------------


def _committed_assignment_state(engine: Engine) -> dict[str, Any]:
    """Committed: a draft and an approved scenario, one `headcount = 1` position in each — the
    approved one already assigned — and a second person. The approved scenario is a direct write
    (the limit named in `tests/conftest.py`)."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        draft = make_scenario(setup, project, name="Baseline")
        approved = make_scenario(setup, project, name="Approved v1", status=ScenarioStatus.APPROVED)
        dimensions = make_dimension_tuple(setup)
        person = make_person(setup)
        other = make_person(setup, full_name="Inna Osoba-Fikcyjna")
        draft_position = make_staffing_position(setup, draft, dimensions, headcount=1)
        approved_position = make_staffing_position(setup, approved, dimensions, headcount=1)
        assign_person_directly(setup, approved_position, person)
        state = {
            "project_id": project.id,
            "draft_id": draft.id,
            "approved_id": approved.id,
            "draft_position_id": draft_position.id,
            "approved_position_id": approved_position.id,
            "person_id": person.id,
            "other_id": other.id,
        }
        setup.commit()
    return state


def _committed_person_id(engine: Engine, position_id: uuid.UUID) -> uuid.UUID | None:
    with engine.connect() as connection:
        return connection.execute(
            sa.text("SELECT person_id FROM staffing_position WHERE id = :id"), {"id": position_id}
        ).scalar_one()


def _committed_marker(
    client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID, key: str = "p"
) -> str:
    """The (single) position's marker as a caller holding everything reads it — the assignment's
    own marker by default (visible only behind the person gate), or `updated_at` with
    `key="updated_at"`."""
    with caller_holding(*EVERYTHING):
        response = client.get(staffing_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return response.json()["positions"][0][MARKER if key == "p" else key]


def test_k_06_assigning_and_removing_on_an_approved_scenario_is_refused_and_changes_nothing(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06, first run, for both uses of the one write path — the assignment of another person and
    the removal of the assignment: `409` naming `approved`, and the committed row still points at
    the original person. Contrast: the same two requests on the draft scenario succeed."""
    state = _committed_assignment_state(engine)
    token = _committed_marker(committing_client, state["project_id"], state["approved_id"])
    path = _person_path(state["project_id"], state["approved_id"], state["approved_position_id"])

    with caller_holding(*ASSIGNER):
        for person_id in (str(state["other_id"]), None):
            refused = committing_client.patch(
                path, json={MARKER: token, "person_id": person_id}
            )
            assert refused.status_code == 409, refused.text
            assert "approved" in refused.json()["detail"]
    assert _committed_person_id(engine, state["approved_position_id"]) == state["person_id"]

    draft_path = _person_path(state["project_id"], state["draft_id"], state["draft_position_id"])
    draft_token = _committed_marker(committing_client, state["project_id"], state["draft_id"])
    grid_token = _committed_marker(
        committing_client, state["project_id"], state["draft_id"], key="updated_at"
    )
    with caller_holding(*ASSIGNER):
        assigned = committing_client.patch(
            draft_path, json={MARKER: draft_token, "person_id": str(state["other_id"])}
        )
        assert assigned.status_code == 200, assigned.text
        # D-4 = B (ADR-0007 aneks 2026-09-28): the assignment's own marker rotates, the
        # position's `updated_at` does not.
        assert assigned.json()[MARKER] != draft_token
        assert assigned.json()["updated_at"] == grid_token
        assert _committed_person_id(engine, state["draft_position_id"]) == state["other_id"]
        removed = committing_client.patch(
            draft_path, json={MARKER: assigned.json()[MARKER], "person_id": None}
        )
    assert removed.status_code == 200, removed.text
    assert _committed_person_id(engine, state["draft_position_id"]) is None


def test_k_06_an_approval_committed_just_before_the_assignment_still_refuses_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06, second run — the approval commits on another connection immediately before the
    guarded `UPDATE staffing_position SET …` runs, i.e. after any Python status check would have
    passed. The assignment is refused and nothing is written. Mutation killed: check-then-act
    (`if scenario.status == APPROVED` in Python) — it would answer `200` here."""
    state = _committed_assignment_state(engine)
    token = _committed_marker(committing_client, state["project_id"], state["draft_id"])
    fired: list[str] = []
    interleave = _committing_the_approval_before(
        engine, fired, state["draft_id"], statement_prefix="update staffing_position set"
    )
    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        with caller_holding(*ASSIGNER):
            response = committing_client.patch(
                _person_path(state["project_id"], state["draft_id"], state["draft_position_id"]),
                json={MARKER: token, "person_id": str(state["person_id"])},
            )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the approval never landed inside the window — nothing here is about the race"
    assert response.status_code == 409, response.text
    assert "approved" in response.json()["detail"]
    assert _committed_person_id(engine, state["draft_position_id"]) is None


def test_k_06_an_approval_committed_just_before_the_removal_still_refuses_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06, second run for the removal: the same window, the assignment stays."""
    state = _committed_assignment_state(engine)
    with Session(bind=engine) as setup:
        setup.execute(sa.update(StaffingPosition)
                      .where(StaffingPosition.id == state["draft_position_id"])
                      .values(person_id=state["person_id"]))
        setup.commit()
    token = _committed_marker(committing_client, state["project_id"], state["draft_id"])
    fired: list[str] = []
    interleave = _committing_the_approval_before(
        engine, fired, state["draft_id"], statement_prefix="update staffing_position set"
    )
    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        with caller_holding(*ASSIGNER):
            response = committing_client.patch(
                _person_path(state["project_id"], state["draft_id"], state["draft_position_id"]),
                json={MARKER: token, "person_id": None},
            )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired
    assert response.status_code == 409, response.text
    assert _committed_person_id(engine, state["draft_position_id"]) == state["person_id"]


def test_k_06_an_approval_committing_concurrently_with_an_assignment_changes_nothing(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06, third run — two **real** connections, through the real approval endpoint: the approval
    holds the scenario row's lock (`app.data.scenario_guard`), the assignment runs on a background
    thread and provably blocks on that lock (`wait_until_a_lock_request_is_pending`), and after the
    approval commits the assignment is refused. Never both: an approved scenario with a person
    assigned after its approval began. Mutation killed: no `FOR UPDATE` in the guard."""
    state = _k20_state(engine)
    with Session(bind=engine, expire_on_commit=False) as setup:
        person = make_person(setup)
        setup.commit()
    token = _committed_marker(committing_client, state["project_id"], state["draft_id"])

    def assign() -> Any:
        with caller_holding(*ASSIGNER):
            return committing_client.patch(
                _person_path(state["project_id"], state["draft_id"], state["position_id"]),
                json={MARKER: token, "person_id": str(person.id)},
            )

    outcome = _race_a_child_write_against_the_approval(committing_client, engine, state, assign)

    assert outcome["approval"].status_code == 200, outcome["approval"].text
    assert outcome["blocked"], "the assignment never waited for a lock"
    assert _scenario_is_approved(engine, state["draft_id"])
    assert _committed_person_id(engine, state["position_id"]) is None, (
        "a person was assigned under a scenario that was already being approved"
    )
    assert outcome["response"].status_code == 409, outcome["response"].text


# --- K-07: copies point at the same person ------------------------------------------------------


def test_k_07_a_duplicated_scenario_keeps_the_assignment_to_the_same_person(
    client: TestClient, db_session: Session
) -> None:
    """K-07 — `POST …/duplicate` (and, below, a project copy): the copied positions carry the
    **same** `person_id` (a reference, not a copy of the person), the register does not grow, and
    the positions do (contrast: something *was* copied). Removing the assignment on the copy
    leaves the source's in place.

    Mutations killed: `person_id` moved to `POSITION_COLUMNS_NOT_COPIED` (the copy would be
    anonymous), and a copier duplicating the person (the register would grow and the ids differ).
    """
    state = _twin_positions(db_session)
    project, scenario, person = state["project"], state["scenario"], state["person"]
    people_before = count_people(db_session)
    positions_before = count_positions(db_session)
    # Everything in this test shares one transaction, so every `now()` is equal: the source's
    # marker is moved to a distinct past value, so "the copy took the source's marker" is visible.
    db_session.execute(
        sa.update(StaffingPosition)
        .where(StaffingPosition.scenario_id == scenario.id)
        .values(person_assignment_updated_at=OLD_MARKER)
    )

    duplicated = client.post(duplicate_path(project.id, scenario.id),
                             headers=as_caller(IN_SCOPE_USER))
    assert duplicated.status_code == 201, duplicated.text
    copy_id = uuid.UUID(duplicated.json()["id"])

    copied = db_session.execute(
        sa.select(StaffingPosition).where(StaffingPosition.scenario_id == copy_id)
    ).scalars().all()
    assert sorted(str(row.person_id) for row in copied) == sorted([str(person.id), "None"])
    assert count_people(db_session) == people_before
    assert count_positions(db_session) == positions_before + 2

    # A7-31-4: the copy's marker is its own — the database's `now()` at the copy, i.e. its
    # `created_at` — never the source's (which was moved back to 2020 above to tell them apart).
    for row in copied:
        assert row.person_assignment_updated_at == row.created_at
        assert row.person_assignment_updated_at != OLD_MARKER
    copy_assigned = next(row for row in copied if row.person_id is not None)
    token = _grid(client, project.id, copy_id)[str(copy_assigned.id)][MARKER]
    with caller_holding(*ASSIGNER):
        removed = client.patch(_person_path(project.id, copy_id, copy_assigned.id),
                               json={MARKER: token, "person_id": None})
    assert removed.status_code == 200, removed.text
    assert _person_id_in_db(db_session, copy_assigned.id) is None
    assert _person_id_in_db(db_session, state["assigned"].id) == person.id


def test_k_07_a_copied_project_keeps_the_assignment_to_the_same_person(
    client: TestClient, db_session: Session
) -> None:
    """K-07 for the other entry point of the one copy mechanism — `POST /projects/{id}/copy` — from
    an **approved** source (a copy is the legal way to continue an approved calculation): the copy's
    position points at the same person, the register does not grow."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    approved = make_scenario(db_session, project, name="Approved", status=ScenarioStatus.APPROVED)
    position = make_staffing_position(db_session, approved, make_dimension_tuple(db_session),
                                      headcount=1)
    person = make_person(db_session)
    assign_person_directly(db_session, position, person)
    people_before = count_people(db_session)

    copied = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert copied.status_code == 201, copied.text
    copy_project_id = uuid.UUID(copied.json()["id"])

    copy_people = db_session.execute(
        sa.select(StaffingPosition.person_id)
        .join(Scenario, Scenario.id == StaffingPosition.scenario_id)
        .where(Scenario.project_id == copy_project_id)
    ).scalars().all()
    assert copy_people == [person.id]
    assert count_people(db_session) == people_before


# --- D-4 = B: the assignment has its own marker (ADR-0007 aneks 2026-09-28) ----------------------


def _updated_at_in_db(session: Session, position_id: uuid.UUID) -> datetime:
    session.expire_all()
    return session.execute(
        sa.select(StaffingPosition.updated_at).where(StaffingPosition.id == position_id)
    ).scalar_one()


def _committed_twins(engine: Engine) -> dict[str, Any]:
    """`_twin_positions`, committed — for the tests whose claim depends on each request running in a
    transaction of its own (a different `now()` per write)."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        state = _twin_positions(setup)
        setup.commit()
    return state


def _committed_updated_at(engine: Engine, position_id: uuid.UUID) -> tuple[Any, Any]:
    """`(updated_at, person_assignment_updated_at)` as committed."""
    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT updated_at, person_assignment_updated_at FROM staffing_position"
                " WHERE id = :id"
            ),
            {"id": position_id},
        ).one()
    return row[0], row[1]


def test_a7_31_1_assigning_and_removing_leave_the_positions_updated_at_as_it_was(
    committing_client: TestClient, engine: Engine
) -> None:
    """A7-31-1 — read from the database, not from a response: the position's `updated_at` is equal
    before and after an assignment and after its removal; the assignment's marker moves each time.

    Through `committing_client`, so every request is its own transaction with its own `now()`:
    inside one shared test transaction every `now()` is equal, and the mutation this test exists to
    kill — the explicit `updated_at = updated_at` removed from `assign_person`, letting the model's
    `onupdate=func.now()` fire — would write the same value and survive."""
    state = _committed_twins(engine)
    project, scenario, person = state["project"], state["scenario"], state["person"]
    position = state["anonymous"]
    path = _person_path(project.id, scenario.id, position.id)
    before, marker = _committed_updated_at(engine, position.id)

    with caller_holding(*ASSIGNER):
        assigned = committing_client.patch(
            path, json={MARKER: marker.isoformat(), "person_id": str(person.id)}
        )
    assert assigned.status_code == 200, assigned.text
    after, new_marker = _committed_updated_at(engine, position.id)
    assert after == before, "the assignment moved the position's updated_at (D-4 = B unmet)"
    assert new_marker != marker

    with caller_holding(*ASSIGNER):
        removed = committing_client.patch(
            path, json={MARKER: new_marker.isoformat(), "person_id": None}
        )
    assert removed.status_code == 200, removed.text
    after, last_marker = _committed_updated_at(engine, position.id)
    assert after == before
    assert last_marker != new_marker


def test_pd_k7_without_people_read_the_position_is_identical_in_whole_before_and_after(
    committing_client: TestClient, engine: Engine
) -> None:
    """PD-K7 / A5-31-9 — the same position, read by a caller without `PEOPLE_READ` before an
    assignment, after it and after its removal (both made by another caller, each in its own
    transaction): the three answers are identical **in whole**, `updated_at` included. And the
    `updated_at` read before the assignment is still accepted afterwards by every other write path
    of the position — the allocation edit, the cost-basis edit, the absence add — so no `409`
    betrays the assignment either.

    Mutation killed: the assignment moving `updated_at` (the answers differ, the old token is
    refused)."""
    state = _committed_twins(engine)
    project, scenario, person = state["project"], state["scenario"], state["person"]
    position = state["anonymous"]
    base = staffing_path(project.id, scenario.id)
    path = _person_path(project.id, scenario.id, position.id)

    def read() -> dict[str, Any]:
        with caller_holding(*WITHOUT_PEOPLE_READ):
            response = committing_client.get(base)
        _assert_hidden(response, person)
        return {row["id"]: row for row in response.json()["positions"]}[str(position.id)]

    before = read()
    with caller_holding(*ASSIGNER):
        assigned = committing_client.patch(
            path,
            json={MARKER: _committed_updated_at(engine, position.id)[1].isoformat(),
                  "person_id": str(person.id)},
        )
    assert assigned.status_code == 200, assigned.text
    assert read() == before

    with caller_holding(*ASSIGNER):
        removed = committing_client.patch(
            path,
            json={MARKER: _committed_updated_at(engine, position.id)[1].isoformat(),
                  "person_id": None},
        )
    assert removed.status_code == 200, removed.text
    assert read() == before

    # Assign again, then use the token read before any of it on every other write path.
    with caller_holding(*ASSIGNER):
        again = committing_client.patch(
            path,
            json={MARKER: _committed_updated_at(engine, position.id)[1].isoformat(),
                  "person_id": str(person.id)},
        )
    assert again.status_code == 200, again.text
    with caller_holding(*WITHOUT_PEOPLE_READ):
        edited = committing_client.patch(
            f"{base}/{position.id}/allocations/{MARCH.isoformat()}",
            json={"updated_at": before["updated_at"], "billable_hours": "7.00"},
        )
        assert edited.status_code == 200, edited.text
        rebased = committing_client.patch(
            f"{base}/{position.id}",
            json={"updated_at": edited.json()["updated_at"], "cost_basis": "fixed_amount",
                  "fixed_amount": "5.0000", "fixed_amount_currency": "PLN"},
        )
        assert rebased.status_code == 200, rebased.text
        added = committing_client.post(f"{base}/{position.id}/absences", json={
            "updated_at": rebased.json()["updated_at"],
            "absence_type_id": str(state["absence_type"].id),
            "start_date": "2026-03-02", "end_date": "2026-03-02"})
        assert added.status_code == 201, added.text
    assert _committed_person_id(engine, position.id) == person.id


def test_a7_31_2_a_stale_assignment_marker_is_refused_and_writes_nothing(
    client: TestClient, db_session: Session
) -> None:
    """A7-31-2, first run — a marker that is not the stored one is a `409` naming the *assignment*
    (distinct from the grid's stale-token message), quoting no value, and nothing is written.
    Contrast: the stored marker is accepted."""
    state = _twin_positions(db_session)
    project, scenario, person = state["project"], state["scenario"], state["person"]
    position = state["anonymous"]
    path = _person_path(project.id, scenario.id, position.id)

    with caller_holding(*ASSIGNER):
        refused = client.patch(path, json={MARKER: "2000-01-01T00:00:00+00:00",
                                           "person_id": str(person.id)})
    assert refused.status_code == 409, refused.text
    assert "person assignment" in refused.json()["detail"]
    assert str(person.id) not in refused.text
    assert _person_id_in_db(db_session, position.id) is None

    with caller_holding(*ASSIGNER):
        accepted = client.patch(path, json={MARKER: _marker(db_session, position.id),
                                            "person_id": str(person.id)})
    assert accepted.status_code == 200, accepted.text


def test_a7_31_2_a_competing_assignment_committed_in_the_window_refuses_the_second(
    committing_client: TestClient, engine: Engine
) -> None:
    """A7-31-2, second run — two connections: another caller's assignment commits between this
    caller's read of the marker and its guarded `UPDATE`. This write is refused (`409`, the
    assignment message) and the competitor's person stays. Mutation killed: the marker compared in
    Python against a value read earlier, or not compared at all (this write would overwrite)."""
    state = _committed_assignment_state(engine)
    token = _committed_marker(committing_client, state["project_id"], state["draft_id"])
    fired: list[str] = []

    def interleave(connection: Any, cursor: Any, statement: str, parameters: Any,
                   context: Any, executemany: bool) -> None:
        if fired or not statement.lstrip().lower().startswith("update staffing_position set"):
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(
                sa.text(
                    "UPDATE staffing_position SET person_id = :person,"
                    " person_assignment_updated_at = clock_timestamp() WHERE id = :id"
                ),
                {"person": state["other_id"], "id": state["draft_position_id"]},
            )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        with caller_holding(*ASSIGNER):
            response = committing_client.patch(
                _person_path(state["project_id"], state["draft_id"], state["draft_position_id"]),
                json={MARKER: token, "person_id": str(state["person_id"])},
            )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the competitor never committed inside the window"
    assert response.status_code == 409, response.text
    assert "person assignment" in response.json()["detail"]
    assert _committed_person_id(engine, state["draft_position_id"]) == state["other_id"]


def test_a7_31_3_other_writes_leave_the_assignment_and_its_marker_and_a_stale_grid_token_still_works(  # noqa: E501
    committing_client: TestClient, engine: Engine
) -> None:
    """A7-31-3 — the allocation edit, the cost-basis edit and the absence add/delete change neither
    `person_id` nor the assignment's marker (disjoint columns, ADR-0007 aneks 2026-09-28 point 2).
    And a grid edit holding an `updated_at` read *before* somebody's assignment succeeds after it,
    with both writes in the database afterwards.

    Committed requests, each its own transaction — so a write path that touched the marker (`now()`)
    would leave a different value, not the same one by coincidence."""
    state = _committed_twins(engine)
    project, scenario, person = state["project"], state["scenario"], state["person"]
    assigned = state["assigned"]
    base = f"{staffing_path(project.id, scenario.id)}/{assigned.id}"
    _, marker = _committed_updated_at(engine, assigned.id)
    token = _committed_updated_at(engine, assigned.id)[0].isoformat()

    with caller_holding(*EVERYTHING):
        edited = committing_client.patch(f"{base}/allocations/{MARCH.isoformat()}",
                                         json={"updated_at": token, "billable_hours": "3.00"})
        assert edited.status_code == 200, edited.text
        rebased = committing_client.patch(base, json={
            "updated_at": edited.json()["updated_at"], "cost_basis": "fixed_amount",
            "fixed_amount": "5.0000", "fixed_amount_currency": "PLN"})
        assert rebased.status_code == 200, rebased.text
        added = committing_client.post(f"{base}/absences", json={
            "updated_at": rebased.json()["updated_at"],
            "absence_type_id": str(state["absence_type"].id),
            "start_date": "2026-03-02", "end_date": "2026-03-02"})
        assert added.status_code == 201, added.text
        removed = committing_client.request(
            "DELETE", f"{base}/absences/{added.json()['absences'][0]['id']}",
            json={"updated_at": added.json()["updated_at"]})
        assert removed.status_code == 200, removed.text
    assert _committed_updated_at(engine, assigned.id)[1] == marker
    assert _committed_person_id(engine, assigned.id) == person.id

    # A grid token read before someone else's assignment still works after it — both writes land.
    anonymous = state["anonymous"]
    grid_token, anonymous_marker = _committed_updated_at(engine, anonymous.id)
    with caller_holding(*ASSIGNER):
        assigned_now = committing_client.patch(
            _person_path(project.id, scenario.id, anonymous.id),
            json={MARKER: anonymous_marker.isoformat(), "person_id": str(person.id)},
        )
    assert assigned_now.status_code == 200, assigned_now.text
    with caller_holding(*WITHOUT_PEOPLE_READ):
        late = committing_client.patch(
            f"{staffing_path(project.id, scenario.id)}/{anonymous.id}"
            f"/allocations/{MARCH.isoformat()}",
            json={"updated_at": grid_token.isoformat(), "billable_hours": "4.00"},
        )
    assert late.status_code == 200, late.text
    assert _committed_person_id(engine, anonymous.id) == person.id
    with engine.connect() as connection:
        assert connection.execute(
            sa.text(
                "SELECT billable_hours FROM staffing_position_allocation WHERE position_id = :id"
            ),
            {"id": anonymous.id},
        ).scalar_one() == Decimal("4.00")
