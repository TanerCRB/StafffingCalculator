"""SC-3-02, K-18, K-19 and K-21 — the approval endpoint itself (ADR-0004, ADR-0005).

The first path in this repository that sets `ScenarioStatus.APPROVED`. Three criteria:

- **K-18** one transaction, snapshot **before** status; a failure leaves the scenario `draft` with
  zero snapshot rows. Read from a separate connection, because "the session says nothing was
  written" and "nothing was committed" are two different claims.
- **K-19** a second approval writes **nothing at all** — asserted on the statements issued and on
  the row counts, not only on the status code.
- **K-21** `404` never `403`, and `404` takes precedence over `409` for a scenario outside the
  caller's scope that happens to be approved already.

**What none of these prove, and it is not a small gap:** who is *allowed* to approve. ADR-0005's
addendum of 2026-09-22 (point 9) accepts that consciously — the placeholder identity carries no role
dimension, so no permission distinguishes "may plan" from "may freeze irreversibly" — and ADR-0004's
addendum of the same date (point 5) names the compounding fact that there is no `audit_log` either.
These tests prove the mechanics and the scope. They are silent about the authorisation, and a reader
must not take them for more.
"""

import threading
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.api.scenarios import SCENARIO_NOT_FOUND_DETAIL
from app.models import ProjectAccess, ScenarioStatus, WorkingCalendarDayKind
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    approve_path,
    as_caller,
    count_snapshot_rows,
    make_absence,
    make_absence_type,
    make_allocation,
    make_calendar_day,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
    wait_until_a_lock_request_is_pending,
)

MARCH = date(2026, 3, 1)
STATUS_UPDATE = "update scenarios set status"
SNAPSHOT_INSERT = "insert into approved_snapshot"

Listener = Any


def _committed_approvable_scenario(engine: Engine, **scenario_kwargs: Any) -> dict[str, Any]:
    """A committed project with a draft scenario that has something to snapshot.

    Committed, because every claim in this file is about what survives a transaction and is read
    back from a *separate* connection.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        calendar = make_working_calendar(
            setup, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
        )
        make_calendar_day(
            setup, calendar, day=date(2026, 12, 25), kind=WorkingCalendarDayKind.NON_WORKING
        )
        project = make_project(
            setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,)
        )
        scenario = make_scenario(setup, project, name="Baseline", **scenario_kwargs)
        position = make_staffing_position(
            setup,
            scenario,
            make_dimension_tuple(setup, calendar=calendar),
            headcount=1,
            start_date=MARCH,
        )
        make_allocation(setup, position, period_month=MARCH)
        make_absence(
            setup,
            position,
            make_absence_type(setup, name="Paid holiday"),
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 6),
        )
        state = {"project_id": project.id, "scenario_id": scenario.id}
        setup.commit()
    return state


def _status_and_snapshot(engine: Engine, scenario_id: uuid.UUID) -> tuple[str, int]:
    """The scenario's status and its snapshot row count, read from a **separate** connection.

    Committed rows only. The transaction under test having "written nothing" is not the claim — the
    claim is that nothing is there afterwards, and only another connection can say so.
    """
    with engine.connect() as connection:
        status = connection.execute(
            sa.text("SELECT status FROM scenarios WHERE id = :id"), {"id": scenario_id}
        ).scalar_one()
        return status, count_snapshot_rows(connection, scenario_id)


# --- K-18: one transaction, snapshot before status ------------------------------------------------


def test_k_18_a_failure_inside_the_approval_leaves_the_scenario_draft_with_no_snapshot_row(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-18 — the approval is one transaction, and a failure inside it leaves **nothing** behind.

    The failure is injected at the one moment that matters: just before the `UPDATE scenarios SET
    status` runs, i.e. **after** every snapshot row has been written. The state afterwards is read
    from a separate connection, and it is `draft` with zero snapshot rows — so the snapshot rows
    that were written a moment earlier were rolled back with the status that never was.

    That is what kills the criterion's first mutation, a `commit()` between the snapshot and the
    status: with it, the snapshot rows survive this failure and the scenario stays `draft`, leaving
    a calculation that carries a frozen copy of values it was never approved with. Nobody would
    notice until the day something read it.

    The contrast is the second half: the same approval without the injected failure gives `approved`
    and a non-zero number of snapshot rows, from the same separate connection. Without it, a broken
    endpoint that never wrote anything would pass.
    """
    state = _committed_approvable_scenario(engine)
    fired: list[str] = []

    def fail_before_the_status_update(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or STATUS_UPDATE not in statement.lower():
            return
        fired.append(statement)
        raise RuntimeError("injected failure, after the snapshot and before the status")

    event.listen(Engine, "before_cursor_execute", fail_before_the_status_update)
    try:
        response = committing_client.post(
            approve_path(state["project_id"], state["scenario_id"]),
            headers=as_caller(IN_SCOPE_USER),
        )
    except RuntimeError as error:  # the TestClient re-raises server exceptions by default
        assert "injected failure" in str(error)
    else:
        assert response.status_code >= 500, response.text
    finally:
        event.remove(Engine, "before_cursor_execute", fail_before_the_status_update)

    assert fired, "the failure was never injected — nothing here is about a partial approval"
    status, snapshot_rows = _status_and_snapshot(engine, state["scenario_id"])
    assert status == "draft", "a failed approval left the scenario approved"
    assert snapshot_rows == 0, (
        "a failed approval left snapshot rows behind — the snapshot was committed separately from "
        "the status, so a scenario now carries a frozen copy of values it was never approved with"
    )

    # The contrast: the same approval, unimpeded.
    ok = committing_client.post(
        approve_path(state["project_id"], state["scenario_id"]),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert ok.status_code == 200, ok.text
    status_after, rows_after = _status_and_snapshot(engine, state["scenario_id"])
    assert status_after == "approved"
    assert rows_after == 3, rows_after  # one calendar, one exceptional day, one absence type


def test_k_18_the_snapshot_rows_are_written_before_the_status_update(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-18's ordering half — asserted on the **order of the statements issued**.

    ADR-0004's addendum of 2026-09-22 (point 4) makes the order mandatory: the child-table guards
    (`status <> 'approved'`) would refuse the snapshot writes themselves if the status went first.
    The reversed order is therefore not "the same thing in a different sequence" — it is an approval
    that produces an empty snapshot, and the second assertion is what shows that the guard is really
    what would stop it.

    Asserted on the sequence rather than only on the outcome because the outcome of the *correct*
    order and the outcome of "status first, snapshot written anyway without a guard" are identical:
    an approved scenario with a full snapshot. Only the order tells them apart.
    """
    state = _committed_approvable_scenario(engine)
    issued: list[str] = []

    def record(connection, cursor, statement, parameters, context, executemany) -> None:
        lowered = statement.lstrip().lower()
        if lowered.startswith(SNAPSHOT_INSERT) or STATUS_UPDATE in lowered:
            issued.append("snapshot" if lowered.startswith(SNAPSHOT_INSERT) else "status")

    event.listen(Engine, "before_cursor_execute", record)
    try:
        response = committing_client.post(
            approve_path(state["project_id"], state["scenario_id"]),
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert response.status_code == 200, response.text
    assert issued == ["snapshot", "snapshot", "snapshot", "status"], issued
    assert issued.index("status") == len(issued) - 1, (
        "the status update was not the last statement of the approval — the snapshot inserts that "
        "follow it would match no unapproved parent and write nothing"
    )


# --- K-19: a second approval writes nothing -------------------------------------------------------


def test_k_19_approving_an_already_approved_scenario_writes_nothing_at_all(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-19 — the second approval issues no write statement and adds no row.

    Two runs in one test, and the **first one is the contrast**: it approves, it issues three
    snapshot inserts and one status update, and it leaves three rows. The second run, against the
    same scenario, issues *none* of those statements and leaves the count where it was.

    Asserting on the statements matters and is not belt and braces. "Nothing was added" is also true
    of an implementation that wrote the snapshot rows again and rolled them back — and that
    implementation burns identifiers, takes locks and, on a table with a unique constraint, would
    fail with a message about the snapshot rather than about the scenario. The claim is that the
    approval **stops** at the locking `SELECT ... WHERE status = 'draft'`, before any write.

    That `AND status = 'draft'` is also the mutation the criterion names: removed, the locking
    select returns the row, the snapshot is written a second time, and the row count doubles.
    """
    state = _committed_approvable_scenario(engine)
    issued: list[str] = []

    def record(connection, cursor, statement, parameters, context, executemany) -> None:
        lowered = statement.lstrip().lower()
        if lowered.startswith(SNAPSHOT_INSERT) or STATUS_UPDATE in lowered:
            issued.append(lowered.split("(")[0][:60])

    first = committing_client.post(
        approve_path(state["project_id"], state["scenario_id"]),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert first.status_code == 200, first.text
    _, after_first = _status_and_snapshot(engine, state["scenario_id"])
    assert after_first == 3, "the contrast is void: the first approval wrote nothing"

    event.listen(Engine, "before_cursor_execute", record)
    try:
        second = committing_client.post(
            approve_path(state["project_id"], state["scenario_id"]),
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert second.status_code == 409, second.text
    assert "already approved" in second.json()["detail"].lower()
    assert issued == [], f"the second approval issued write statements: {issued}"
    status, after_second = _status_and_snapshot(engine, state["scenario_id"])
    assert status == "approved"
    assert after_second == after_first, "the second approval changed the snapshot"


def test_k_19_a_scenario_created_as_approved_is_refused_without_writing_a_snapshot(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-19 from the other direction — an `approved` scenario that never went through this endpoint.

    The fixture writes the status directly, so there is no snapshot at all. A `409` and **still zero
    rows**: the refusal is decided before any write, so it does not matter whether a previous
    approval happened through this code or not.

    Worth its own run because the test above reaches the second approval through the first, and an
    implementation that remembered "I have already approved this one" in process state — a cache, a
    set of ids — would pass it and fail here.
    """
    state = _committed_approvable_scenario(engine, status=ScenarioStatus.APPROVED)

    response = committing_client.post(
        approve_path(state["project_id"], state["scenario_id"]),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 409, response.text
    status, rows = _status_and_snapshot(engine, state["scenario_id"])
    assert status == "approved"
    assert rows == 0, "the refused approval wrote a snapshot anyway"


def test_k_19_two_concurrent_approvals_of_one_draft_leave_one_snapshot_not_two(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-19 with the two approvals overlapping instead of following one another (R-02, reviewer).

    The run above approves twice in sequence, so the second one reads a committed `approved` status
    and the `WHERE status = 'draft'` predicate alone decides it. That says nothing about two
    approvals **in flight at the same time**: under `READ COMMITTED` a predicate is evaluated
    against the snapshot the statement started with, and without a lock both transactions would read
    `draft`, both would write a full snapshot, and the scenario would end up approved once and
    frozen twice — permanently, since snapshot rows are never updated or deleted.

    What closes that window is the `FOR UPDATE` in `app.data.scenario_guard.draft_scenario`, taken
    as the approval's **first** statement and held until it commits. It is the only lock the
    approval takes *by contract*, and **this is the only test in the suite that kills its removal**
    — verified by running that mutation on 2026-09-22: the whole of the rest of the suite stayed
    green and this run failed. What it failed on is worth stating exactly, because the failure is
    milder than the reasoning above suggests: the snapshot inserts' own `LockRows` node happens to
    lock the scenario row anyway under today's plan, so the second approval still writes no rows —
    it ends with its final `UPDATE` matching nothing, which this module deliberately treats as a
    defect rather than a conflict, and the caller gets an unhandled `500` in place of a `409`. The
    data survives on a property of a query plan; the answer does not survive at all.

    The interleaving is forced rather than hoped for: the first approval is paused by a hook just
    before its status update — after its snapshot rows, while it still holds the lock — the second
    is started on another thread, and the test waits until PostgreSQL itself reports the second one
    waiting for a lock before letting the first commit. `blocked` is asserted, so a run in which
    nothing ever waited is a failure and not a quiet pass.

    The postcondition is all three facts together: exactly one `200`, exactly one `409`, and a
    snapshot the size of **one** approval.
    """
    state = _committed_approvable_scenario(engine)
    outcome: dict[str, Any] = {}
    fired: list[str] = []

    def start_the_second_approval_and_wait_for_it_to_block(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or STATUS_UPDATE not in statement.lower():
            return
        fired.append(statement)

        def second_approval() -> None:
            try:
                outcome["second"] = committing_client.post(
                    approve_path(state["project_id"], state["scenario_id"]),
                    headers=as_caller(IN_SCOPE_USER),
                )
            except BaseException as error:  # noqa: BLE001 — reported, never swallowed
                outcome["error"] = error

        thread = threading.Thread(target=second_approval, daemon=True)
        thread.start()
        outcome["thread"] = thread
        outcome["blocked"] = wait_until_a_lock_request_is_pending(engine)

    event.listen(
        Engine, "before_cursor_execute", start_the_second_approval_and_wait_for_it_to_block
    )
    try:
        outcome["first"] = committing_client.post(
            approve_path(state["project_id"], state["scenario_id"]),
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(
            Engine, "before_cursor_execute", start_the_second_approval_and_wait_for_it_to_block
        )

    assert fired, "the first approval never reached its status update"
    thread = outcome.get("thread")
    assert thread is not None
    thread.join(timeout=30)
    assert not thread.is_alive(), "the second approval never finished — it is still holding a lock"
    assert "error" not in outcome, outcome.get("error")
    assert outcome["blocked"], (
        "the second approval never waited for a lock — nothing serialises two approvals of one "
        "draft, and this run says nothing about the race"
    )

    codes = sorted((outcome["first"].status_code, outcome["second"].status_code))
    assert codes == [200, 409], (
        f"two concurrent approvals answered {codes}: exactly one of them must win and exactly one "
        "must be told the scenario is already approved"
    )
    refused = outcome["first"] if outcome["first"].status_code == 409 else outcome["second"]
    assert "already approved" in refused.json()["detail"].lower()

    status, rows = _status_and_snapshot(engine, state["scenario_id"])
    assert status == "approved"
    assert rows == 3, (
        f"{rows} snapshot rows for one scenario: both approvals wrote one. The rows are never "
        "updated or deleted, so the duplicate freeze is permanent."
    )


# --- K-21: 404 never 403, and 404 before 409 -----------------------------------------------------


def test_k_21_approving_a_scenario_outside_the_callers_scope_is_not_found_not_forbidden(
    client: TestClient, db_session: Session
) -> None:
    """K-21 — the scope boundary of the approval endpoint is an absence, not a refusal.

    The scenario belongs to `OUT_OF_SCOPE_USER`. The caller holds `PROJECT_EDIT` (the placeholder
    identity grants it), so a `403` here could only mean "you may not approve *this* one", which
    would confirm that it exists. The answer is `404`, with the same body and the same length as the
    answer for a project id nobody ever created.

    The mutation the criterion names — `session.get` instead of `project_for_caller` — turns the
    first response into a `200` that freezes another user's calculation irreversibly.

    The contrast is the last block: one `project_access` row added for this caller, nothing else
    changed, and the same request answers `200`. Without it a server answering `404` to everything
    would pass.
    """
    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    project = make_project(
        db_session, name="Borealis rollout", accessible_to=(OUT_OF_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, project, name="Baseline")
    position = make_staffing_position(
        db_session,
        scenario,
        make_dimension_tuple(db_session, calendar=calendar),
        start_date=MARCH,
    )
    make_allocation(db_session, position, period_month=MARCH)

    out_of_scope = client.post(
        approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    unknown_project = client.post(
        approve_path(uuid.uuid4(), scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    for response in (out_of_scope, unknown_project):
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == SCENARIO_NOT_FOUND_DETAIL
    assert out_of_scope.text == unknown_project.text
    assert out_of_scope.headers["content-length"] == unknown_project.headers["content-length"]
    db_session.expire_all()
    assert (
        db_session.execute(
            sa.text("SELECT status FROM scenarios WHERE id = :id"), {"id": scenario.id}
        ).scalar_one()
        == "draft"
    ), "a caller outside the scope approved somebody else's scenario"

    db_session.add(ProjectAccess(user_id=IN_SCOPE_USER, project_id=project.id))
    db_session.flush()
    granted = client.post(
        approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert granted.status_code == 200, granted.text


def test_k_21_an_out_of_scope_approved_scenario_is_not_found_rather_than_a_conflict(
    client: TestClient, db_session: Session
) -> None:
    """K-21's precedence half — `404` beats `409`, so the answer confirms neither existence nor
    state.

    The scenario is **already approved** and belongs to somebody else. An implementation that
    diagnosed "already approved" before establishing the scope would answer `409`, and that `409`
    would tell the caller two things they are not entitled to know: that the scenario exists, and
    what state it is in. Both of those are what ADR-0005's addendum of 2026-09-18 (point 3) puts out
    of reach, and the addendum of 2026-09-22 (point 9) repeats for this endpoint specifically.

    The contrast: one `project_access` row for this caller, and the very same request becomes the
    `409` it would have been all along. That is what shows the `404` above was a scope decision
    rather than the endpoint being broken.
    """
    project = make_project(
        db_session, name="Borealis rollout", accessible_to=(OUT_OF_SCOPE_USER,)
    )
    scenario = make_scenario(
        db_session, project, name="Approved v1", status=ScenarioStatus.APPROVED
    )

    refused = client.post(
        approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert refused.status_code == 404, refused.text
    assert refused.json()["detail"] == SCENARIO_NOT_FOUND_DETAIL
    assert "approved" not in refused.text.lower(), (
        "the refusal for a scenario outside the caller's scope named its state — confirming both "
        "that it exists and that it is frozen"
    )

    db_session.add(ProjectAccess(user_id=IN_SCOPE_USER, project_id=project.id))
    db_session.flush()
    conflict = client.post(
        approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert conflict.status_code == 409, conflict.text
    assert "already approved" in conflict.json()["detail"].lower()


def test_the_approval_endpoint_declares_a_permission_and_refuses_a_caller_without_it(
    client: TestClient, db_session: Session
) -> None:
    """Deny by default, and **nothing more than that** — read the assertion with its docstring.

    What this proves: the endpoint declares a permission dependency, so a caller who does not hold
    it is refused before the database is touched, and the scenario stays `draft`. That is the rule
    `app.api.deps.require_permission` exists for and it holds here like everywhere else.

    What this does **not** prove, and must not be read as proving: that the right people can
    approve. `PROJECT_EDIT` is declared because *some* permission must be, and because it is one the
    placeholder identity grants so the endpoint is reachable at all — not because "who may edit a
    project" is the same question as "who may irreversibly freeze a calculation". ADR-0005's
    addendum of 2026-09-22 (point 9) states that gap and accepts it, conditionally on the
    authentication ADR. There is no role dimension to test against, so there is no test here that
    could close it.
    """
    from app.core.identity import Permission
    from tests.conftest import caller_holding

    calendar = make_working_calendar(
        db_session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    make_staffing_position(
        db_session,
        scenario,
        make_dimension_tuple(db_session, calendar=calendar),
        start_date=MARCH,
    )

    with caller_holding(
        Permission.PROJECT_READ,
        Permission.STAFFING_READ,
        Permission.STAFFING_WRITE,
        Permission.CATALOG_READ,
    ):
        denied = client.post(
            approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )

    assert denied.status_code == 403, denied.text
    db_session.expire_all()
    assert (
        db_session.execute(
            sa.text("SELECT status FROM scenarios WHERE id = :id"), {"id": scenario.id}
        ).scalar_one()
        == "draft"
    )
    assert count_snapshot_rows(db_session, scenario.id) == 0


def test_approving_a_scenario_with_no_positions_freezes_it_with_an_empty_snapshot(
    committing_client: TestClient, engine: Engine
) -> None:
    """Not an acceptance criterion — a state the snapshot's scope makes reachable, named here.

    The snapshot freezes the calendars the scenario's *positions* read (`_copy_calendars`), so a
    scenario with no positions freezes nothing and is approved with an empty snapshot. That is the
    honest state of a draft with nothing planned in it, and it is worth a test because the
    alternative reading — "an empty snapshot means the approval failed" — is the one a later reader
    is likely to have.

    It also pins the shape of the answer: `200` with three zero counts, not a `409` and not a `500`.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Empty")
        state = {"project_id": project.id, "scenario_id": scenario.id}
        setup.commit()

    response = committing_client.post(
        approve_path(state["project_id"], state["scenario_id"]), headers=as_caller(IN_SCOPE_USER)
    )

    assert response.status_code == 200, response.text
    assert response.json()["snapshot"] == {
        "working_calendars": 0,
        "working_calendar_days": 0,
        "absence_types": 0,
    }
    assert response.json()["status"] == "Approved"
    status, rows = _status_and_snapshot(engine, state["scenario_id"])
    assert (status, rows) == ("approved", 0)
