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

import re
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
from app.data.scenario_guard import unapproved_scenario
from app.models import (
    SNAPSHOT_TABLES,
    ApprovedSnapshotAbsenceBudget,
    ApprovedSnapshotAbsenceType,
    ApprovedSnapshotWorkingCalendar,
    ApprovedSnapshotWorkingCalendarDay,
    CatalogLocation,
    ProjectAccess,
    ScenarioStatus,
    StaffingPosition,
    WorkingCalendar,
    WorkingCalendarDayKind,
)
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    STATUTORY_LEAVE_TYPE_NAME,
    approve_path,
    as_caller,
    count_absence_budgets,
    count_snapshot_rows,
    make_absence,
    make_absence_budget,
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
_SNAPSHOT_TARGET = re.compile(r"insert into (approved_snapshot_\w+)")

Listener = Any


def _snapshot_tables_inserted_by(statement: str) -> set[str]:
    """Every `approved_snapshot_*` table one issued statement inserts into — anywhere in it.

    Searched for, not matched as a prefix. Until S-01 (2026-09-23) every snapshot table had its own
    `INSERT` statement and `startswith("insert into approved_snapshot")` recognised them all; since
    the four inserts became data-modifying CTEs of one statement, that statement begins with
    `WITH`, and a prefix test would recognise **nothing** — which in K-19's "the second approval
    issued no snapshot insert" is a pass for the wrong reason. A search recognises both shapes, so
    it is strictly stronger than the prefix it replaced.
    """
    return set(_SNAPSHOT_TARGET.findall(statement.lower()))


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
    issued: list[tuple[str, frozenset[str]]] = []

    def record(connection, cursor, statement, parameters, context, executemany) -> None:
        tables = _snapshot_tables_inserted_by(statement)
        if tables:
            issued.append(("snapshot", frozenset(tables)))
        elif STATUS_UPDATE in statement.lower():
            issued.append(("status", frozenset()))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        response = committing_client.post(
            approve_path(state["project_id"], state["scenario_id"]),
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert response.status_code == 200, response.text
    kinds = [kind for kind, _ in issued]
    # The status update is the last statement and there is exactly one of it; every snapshot insert
    # comes before it. Asserted on kinds rather than on a literal sequence, because *how many*
    # statements carry the snapshot is not this criterion's claim: until S-01 (2026-09-23) it was
    # one per table, since then it is one for all of them (`_snapshot_statement`), and the order is
    # what K-18 is about. That the inserts are one statement is S-01's own claim, proven below —
    # by behaviour in the two race runs and by count in `test_s_01_the_snapshot_is_written_by_one_
    # statement` — rather than here.
    assert kinds.count("status") == 1 and kinds[-1] == "status", (
        "the status update was not the last statement of the approval — the snapshot inserts that "
        f"follow it would match no unapproved parent and write nothing: {kinds}"
    )
    # Every snapshot table is inserted into before the status update. Read off the registry rather
    # than written out, because the number is an artifact of how many snapshot tables exist
    # (SC-3-03 added the fourth — ADR-0004, addendum 2026-09-22 SC-3-03, point 4): a fifth table
    # registered without an insert — the "snapshot that is always empty" failure that registry
    # exists to make visible — fails here.
    inserted_before_status = set().union(*(tables for kind, tables in issued[:-1]))
    assert inserted_before_status == set(SNAPSHOT_TABLES), (
        f"snapshot tables inserted before the status update: {sorted(inserted_before_status)}; "
        f"registered: {sorted(SNAPSHOT_TABLES)}"
    )


# --- K-19: a second approval writes nothing -------------------------------------------------------


def test_k_19_approving_an_already_approved_scenario_writes_nothing_at_all(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-19 — the second approval issues no write statement and adds no row.

    Two runs in one test, and the **first one is the contrast**: it approves, it issues the snapshot
    statement (one statement whose data-modifying CTEs insert into every snapshot table — S-01,
    `_snapshot_statement`) and one status update, and it leaves three rows. The second run, against
    the same scenario, issues *neither* of those statements and leaves the count where it was.

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
        if _snapshot_tables_inserted_by(lowered) or STATUS_UPDATE in lowered:
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


# --- S-01: one snapshot of the catalogue for every snapshot table, taken under the lock ----------
#
# The approval's lock serialises it against the scenario's own children, never against the
# catalogue: nobody editing a calendar, a location or a budget asks for a scenario's lock. Under
# `READ COMMITTED` each statement reads the catalogue afresh, so the snapshot is internally
# consistent only if every snapshot table is written by **one** statement (`_snapshot_statement`).
#
# The first two runs below force a catalogue edit to commit *between* two snapshot tables' inserts:
# the hook fires after the **first** issued statement that inserts into **any** snapshot table and
# commits the edit on a separate connection before the approval issues its next statement. With one
# statement there is no "between" — the hook fires after the whole snapshot, the edit lands after
# it, and the snapshot is the catalogue as it was. With the inserts split into several statements,
# every table not in that first statement reads the edited catalogue.
#
# "The first statement, whichever tables it holds" rather than "the statement holding table X" is
# what makes this catch *every* split, not only the historical one (R-01, 2026-09-23). The three
# pairs that must agree — calendar ↔ days, calendar ↔ budgets, statutory type ↔ budgets — connect
# all four tables, so any first statement that is not all of them separates at least one pair from
# its partner, and the edit then lands between them. A hook keyed to one named table does not: in a
# split whose *last* statement holds that table (types and budgets first, calendars and days
# second), it fired after everything had been read, no race happened, and both runs passed on code
# that had the defect back. Measured on every ordered split of the four copiers; see the report of
# the R-01 round. `test_s_01_the_snapshot_is_written_by_one_statement` below says the same thing
# structurally, so that a split is named as a split and not only as its symptom.
#
# The last run (`…_waits_for_the_lock_is_in_the_snapshot`) is the other half of S-01: the one
# snapshot is taken *after* the lock is granted, so a child row committed while the approval waited
# for the lock is in the copy. It passes on the four-statement code as well — it is not a proof of
# the one-statement change, it is the guard against the change that was measured and rejected in
# its place (`REPEATABLE READ`, variant A), which fixes the snapshot before the lock and would lose
# exactly that row.


def _approval_pausing_after(
    committing_client: TestClient,
    state: dict[str, Any],
    *,
    catalogue_edit: Any,
) -> tuple[Any, list[str]]:
    """Approve, committing `catalogue_edit` on another connection right after the **first**
    statement that inserts into any `approved_snapshot_*` table — once, and before the approval's
    next statement runs.

    Deliberately not "after the statement that inserts into table X": which table a split puts
    first is the one thing a regression is free to choose, and a hook keyed to a table that lands
    in the last statement fires after the whole snapshot has been read (R-01; the section comment
    above). `after_cursor_execute`, so the edit is committed after that statement has read the
    catalogue and before anything later in the approval does. Returns the response and the list the
    hook appends to, which the caller asserts non-empty: a run in which the edit never happened says
    nothing about the race.
    """
    fired: list[str] = []

    def edit_the_catalogue_between_statements(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or not _snapshot_tables_inserted_by(statement):
            return
        fired.append(statement)
        catalogue_edit()

    event.listen(Engine, "after_cursor_execute", edit_the_catalogue_between_statements)
    try:
        response = committing_client.post(
            approve_path(state["project_id"], state["scenario_id"]),
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "after_cursor_execute", edit_the_catalogue_between_statements)
    return response, fired


def _frozen_calendar_ids(engine: Engine, scenario_id: uuid.UUID) -> dict[str, set[uuid.UUID]]:
    """`source_calendar_id` of every frozen calendar, day and budget of one scenario — committed
    rows, read from a separate connection."""
    with engine.connect() as connection:
        return {
            model.__tablename__: set(
                connection.execute(
                    sa.select(model.source_calendar_id).where(model.scenario_id == scenario_id)
                ).scalars()
            )
            for model in (
                ApprovedSnapshotWorkingCalendar,
                ApprovedSnapshotWorkingCalendarDay,
                ApprovedSnapshotAbsenceBudget,
            )
        }


def test_s_01_a_location_re_pointed_during_the_approval_cannot_split_a_calendar_from_its_days_or_budgets(  # noqa: E501 — the name is the claim
    committing_client: TestClient, engine: Engine
) -> None:
    """S-01 — the calendar, its days and its budgets are frozen from **one** catalogue.

    Two calendars, each with exceptional days of its own and a budget of its own for the position's
    engagement type. The position's location points at calendar A. Right after the approval's first
    statement that writes any snapshot row, another connection re-points the location at calendar B
    and commits.

    With the snapshot as one statement: calendar A, the days of A, the budget of A — the catalogue
    as it was when the statement started. With the calendar insert as its own statement (the code
    until 2026-09-23): calendar A, and then the days and the budget of **B**, read by the next
    statements from the edited catalogue — a frozen calendar whose holidays and entitlement belong
    to a different calendar, a state the catalogue was never in, and permanent, because snapshot
    rows are never updated or deleted. Split the other way round (budgets first, calendar later) it
    is budgets of A under calendar B — the same broken pair, caught by the same assertion.

    The assertion is the invariant rather than "A": every frozen day and every frozen budget belongs
    to a frozen calendar. It is not vacuous — the contrast is that both sets are non-empty.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        calendar_a = make_working_calendar(
            setup, name="Poland A", standard_hours_per_day=Decimal("7.50")
        )
        make_calendar_day(
            setup, calendar_a, day=date(2026, 12, 25), kind=WorkingCalendarDayKind.NON_WORKING
        )
        calendar_b = make_working_calendar(
            setup, name="Poland B", standard_hours_per_day=Decimal("7.25")
        )
        make_calendar_day(
            setup, calendar_b, day=date(2026, 11, 11), kind=WorkingCalendarDayKind.NON_WORKING
        )
        make_calendar_day(
            setup, calendar_b, day=date(2026, 5, 3), kind=WorkingCalendarDayKind.NON_WORKING
        )
        dimensions = make_dimension_tuple(setup, calendar=calendar_a)
        for calendar, days in ((calendar_a, Decimal("26.00")), (calendar_b, Decimal("20.00"))):
            make_absence_budget(
                setup,
                calendar,
                dimensions.engagement_type_id,
                budget_days=days,
                effective_from=date(2026, 1, 1),
                effective_to=date(2026, 12, 31),
            )
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Baseline")
        position = make_staffing_position(
            setup, scenario, dimensions, headcount=1, start_date=MARCH
        )
        make_allocation(setup, position, period_month=MARCH)
        state = {"project_id": project.id, "scenario_id": scenario.id}
        location_id, a_id, b_id = dimensions.location_id, calendar_a.id, calendar_b.id
        setup.commit()

    def re_point_the_location_at_calendar_b() -> None:
        with engine.begin() as editor:
            editor.execute(
                sa.update(CatalogLocation)
                .where(CatalogLocation.id == location_id)
                .values(calendar_id=b_id)
            )

    response, fired = _approval_pausing_after(
        committing_client,
        state,
        catalogue_edit=re_point_the_location_at_calendar_b,
    )

    assert fired, "the catalogue edit never ran — this run says nothing about the race"
    assert response.status_code == 200, response.text
    with engine.connect() as observer:
        assert (
            observer.execute(
                sa.select(CatalogLocation.calendar_id).where(CatalogLocation.id == location_id)
            ).scalar_one()
            == b_id
        ), "the edit did not commit — the race never happened"

    frozen = _frozen_calendar_ids(engine, state["scenario_id"])
    calendars = frozen["approved_snapshot_working_calendar"]
    days = frozen["approved_snapshot_working_calendar_day"]
    budgets = frozen["approved_snapshot_absence_budget"]
    assert days and budgets, "the contrast is void: nothing but the calendar was frozen"
    assert days <= calendars, (
        "frozen days belong to a calendar that was not frozen — the calendar and its days were "
        f"read from two different catalogues (days of {days}, calendars {calendars})"
    )
    assert budgets <= calendars, (
        "a frozen budget belongs to a calendar that was not frozen — the calendar and its budgets "
        f"were read from two different catalogues (budgets of {budgets}, calendars {calendars})"
    )
    # Last, so that a split is reported by the invariant it breaks where it breaks one. It still
    # catches the split in which the first statement holds none of calendar, days and budgets
    # (e.g. types alone): those three then agree with each other — on calendar B, the catalogue
    # *after* the edit, which only a statement issued after the first could have read.
    assert calendars == {a_id}, (
        f"frozen calendar {calendars}, expected calendar A {a_id}: the snapshot was read after the "
        "edit committed mid-approval, so it was not one statement"
    )


def test_s_01_a_budget_committed_during_the_approval_cannot_be_frozen_without_the_statutory_type(
    committing_client: TestClient, engine: Engine
) -> None:
    """S-01 — the statutory type and the budgets are frozen from **one** catalogue.

    `ApprovedSnapshotAbsenceType`'s contract: a frozen row with `is_statutory_leave = true` present
    means the frozen budget applies; absent means nobody had named a statutory type and it applies
    to nothing. The type here **is** flagged, from the start. There is no budget yet. Right after
    the approval's first statement that writes any snapshot row, another connection creates the
    budget the scenario's month reads and commits.

    With one statement: no budget and no flagged type — the catalogue before the edit, and a
    consistent answer ("no budget to apply"). With the types inserted as their own statement (the
    code until 2026-09-23): no flagged type, because no budget existed when that statement read the
    catalogue, and then **one** budget, because the next statement read it — a snapshot that says
    "a 26-day entitlement, and no statutory type was ever named", i.e. deduct none of it, although
    the type was named the whole time. Split the other way round (budgets first, types later) it is
    no budget and a flagged type — (0, 1), outside the allowed set as well.

    The assertion is the invariant — a frozen budget implies the frozen flagged type — plus the
    contrast that the budget really was committed during the approval.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        calendar = make_working_calendar(
            setup, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
        )
        make_absence_type(setup, name=STATUTORY_LEAVE_TYPE_NAME, is_statutory_leave=True)
        dimensions = make_dimension_tuple(setup, calendar=calendar)
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Baseline")
        position = make_staffing_position(
            setup, scenario, dimensions, headcount=1, start_date=MARCH
        )
        make_allocation(setup, position, period_month=MARCH)
        state = {"project_id": project.id, "scenario_id": scenario.id}
        setup.commit()

    def create_the_budget_the_scenario_reads() -> None:
        with Session(bind=engine, expire_on_commit=False, future=True) as editor:
            make_absence_budget(
                editor,
                editor.get(WorkingCalendar, calendar.id),
                dimensions.engagement_type_id,
                budget_days=Decimal("26.00"),
                effective_from=date(2026, 1, 1),
                effective_to=date(2026, 12, 31),
            )
            editor.commit()

    response, fired = _approval_pausing_after(
        committing_client,
        state,
        catalogue_edit=create_the_budget_the_scenario_reads,
    )

    assert fired, "the catalogue edit never ran — this run says nothing about the race"
    assert response.status_code == 200, response.text
    with engine.connect() as observer:
        assert count_absence_budgets(observer) == 1, "the budget did not commit — no race happened"
        frozen_budgets = observer.execute(
            sa.select(sa.func.count())
            .select_from(ApprovedSnapshotAbsenceBudget)
            .where(ApprovedSnapshotAbsenceBudget.scenario_id == state["scenario_id"])
        ).scalar_one()
        frozen_statutory = observer.execute(
            sa.select(sa.func.count())
            .select_from(ApprovedSnapshotAbsenceType)
            .where(
                ApprovedSnapshotAbsenceType.scenario_id == state["scenario_id"],
                ApprovedSnapshotAbsenceType.is_statutory_leave.is_(True),
            )
        ).scalar_one()

    assert (frozen_budgets, frozen_statutory) in {(0, 0), (1, 1)}, (
        f"{frozen_budgets} frozen budget(s) and {frozen_statutory} frozen statutory type(s): the "
        "snapshot says a budget applies to no named statutory type although one was flagged the "
        "whole time — the types and the budgets were read from two different catalogues"
    )


def test_s_01_the_snapshot_is_written_by_one_statement(
    committing_client: TestClient, engine: Engine
) -> None:
    """S-01, structurally — exactly one issued statement inserts into snapshot tables, and it
    inserts into every registered one.

    The two runs above prove the *consequence* of a split for the pairs they stage; this names the
    split itself, whatever the grouping, whatever the order, and whatever the fixture data happens
    to exercise. It is the cheaper half of R-01's fix and the one whose failure message says what
    happened ("two statements") rather than what it broke — the behavioural runs stay, because a
    count of statements says nothing about whether one statement really is one snapshot.

    The contrast is built in: the count of statements is compared with 1 and the tables with the
    registry, so an approval that issued no snapshot statement at all fails as surely as one that
    issued two.
    """
    state = _committed_approvable_scenario(engine)
    snapshot_statements: list[frozenset[str]] = []

    def record(connection, cursor, statement, parameters, context, executemany) -> None:
        tables = _snapshot_tables_inserted_by(statement)
        if tables:
            snapshot_statements.append(frozenset(tables))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        response = committing_client.post(
            approve_path(state["project_id"], state["scenario_id"]),
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert response.status_code == 200, response.text
    assert len(snapshot_statements) == 1, (
        f"the snapshot was written by {len(snapshot_statements)} statements "
        f"({[sorted(tables) for tables in snapshot_statements]}); under READ COMMITTED each reads "
        "the catalogue afresh, so the frozen tables can disagree with each other (S-01)"
    )
    assert snapshot_statements[0] == frozenset(SNAPSHOT_TABLES), (
        f"the snapshot statement inserts into {sorted(snapshot_statements[0])}; "
        f"registered: {sorted(SNAPSHOT_TABLES)}"
    )


def test_s_01_a_child_row_committed_while_the_approval_waits_for_the_lock_is_in_the_snapshot(
    committing_client: TestClient, engine: Engine
) -> None:
    """S-01's other half — the one snapshot is taken **under** the lock, not before it.

    Another transaction holds the scenario row the way every guarded child write does
    (`unapproved_scenario`, `FOR UPDATE`). The approval starts on another thread and the test waits
    until PostgreSQL reports it waiting for that lock. Only then does the holder add a month in 2027
    to the position — a month read by a *second* budget window — and commit, releasing the lock.

    That month is a row of the calculation being approved: it was committed before the approval
    could read anything, and the approval then froze the scenario it belongs to. So its budget
    window must be frozen too — two windows, not one.

    **This run is green on the four-statement code as well**, and it says so here rather than
    being presented as the proof of S-01: under `READ COMMITTED` every statement after the lock
    takes a fresh snapshot, so that code saw the month too. It guards against the fix that was
    measured and rejected in its place (variant A, `REPEATABLE READ`), which fixes the snapshot at
    the transaction's first statement — before the lock is granted — and freezes one window here.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        calendar = make_working_calendar(
            setup, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
        )
        dimensions = make_dimension_tuple(setup, calendar=calendar)
        for year in (2026, 2027):
            make_absence_budget(
                setup,
                calendar,
                dimensions.engagement_type_id,
                budget_days=Decimal("26.00") if year == 2026 else Decimal("24.00"),
                effective_from=date(year, 1, 1),
                effective_to=date(year, 12, 31),
            )
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Baseline")
        position = make_staffing_position(
            setup, scenario, dimensions, headcount=1, start_date=MARCH
        )
        make_allocation(setup, position, period_month=MARCH)
        state = {"project_id": project.id, "scenario_id": scenario.id}
        position_id = position.id
        setup.commit()

    outcome: dict[str, Any] = {}

    def approval() -> None:
        try:
            outcome["response"] = committing_client.post(
                approve_path(state["project_id"], state["scenario_id"]),
                headers=as_caller(IN_SCOPE_USER),
            )
        except BaseException as error:  # noqa: BLE001 — reported, never swallowed
            outcome["error"] = error

    with Session(bind=engine, expire_on_commit=False, future=True) as holder:
        assert holder.execute(unapproved_scenario(state["scenario_id"])).one_or_none()
        thread = threading.Thread(target=approval, daemon=True)
        thread.start()
        blocked = wait_until_a_lock_request_is_pending(engine)
        make_allocation(
            holder, holder.get(StaffingPosition, position_id), period_month=date(2027, 1, 1)
        )
        holder.commit()

    thread.join(timeout=30)
    assert not thread.is_alive(), "the approval never finished"
    assert "error" not in outcome, outcome.get("error")
    assert blocked, (
        "the approval never waited for the scenario lock — this run says nothing about what it "
        "reads after the lock is granted"
    )
    assert outcome["response"].status_code == 200, outcome["response"].text

    with engine.connect() as observer:
        windows = sorted(
            observer.execute(
                sa.select(ApprovedSnapshotAbsenceBudget.effective_from).where(
                    ApprovedSnapshotAbsenceBudget.scenario_id == state["scenario_id"]
                )
            ).scalars()
        )
    assert windows == [date(2026, 1, 1), date(2027, 1, 1)], (
        f"frozen windows {windows}: the month committed while the approval waited for the lock is "
        "part of the approved calculation, and the window it reads was not frozen — the snapshot "
        "was taken before the lock"
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

    It also pins the shape of the answer: `200` with four zero counts, not a `409` and not a `500`.
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
        # The fourth counter joined in SC-3-03 (ADR-0004, addendum 2026-09-22 SC-3-03, point 4).
        # This equality is a canary, and it fired deliberately on the day a snapshot table was
        # added: a scenario with no positions freezes no budget either, which is what this zero
        # says. Updated with the change that caused it, not around it.
        "absence_budgets": 0,
        # The fifth counter, SC-1-10 (ADR-0012, point 6). A deliberate canary growth, like the
        # fourth: zero because this fixture configures no organisation defaults row (gate 1, P-D:
        # no migration seeds one). The proof that the row *is* frozen when it exists is K-05, in
        # `tests/test_assumption_approval.py`.
        "organization_defaults": 0,
        # The sixth counter, SC-4-01 (ADR-0004, addendum 2026-09-23 SC-4-01, point 2d): a
        # deliberate canary growth. Zero because this fixture has no catalogue rate; the
        # proof that the windows read *are* frozen is K-08 in `test_commercial_revenue.py`.
        "catalog_default_rates": 0,
    }
    assert response.json()["status"] == "Approved"
    status, rows = _status_and_snapshot(engine, state["scenario_id"])
    assert (status, rows) == ("approved", 0)
