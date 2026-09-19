"""SC-3-01, K-06 and K-07 — the refusal of a write to the staffing of an `approved` scenario.

**Two criteria, not one, and the reason is in the mechanism.** The `INSERT` of a position reads the
scenario's status *directly* (`INSERT ... SELECT ... FROM scenarios WHERE status <> 'approved'`);
the `UPDATE` of a month row reaches the status *through the position* (`… WHERE NOT EXISTS (…
scenarios … )`, in the same statement that also checks the ADR-0007 token). They are two statements,
two predicates and two mutations: moving either one into Python leaves the other working, so a
single criterion covering both would be satisfied by half an implementation.

Each criterion has two runs. The first is the plain refusal — the response, and the *rows*, because
a `409` returned while the row was written would satisfy a status-only assertion. The second is the
race: a competing transaction commits the approval in the window a Python check-then-act guard would
leave open, i.e. immediately before the guarded statement runs. That mutation has survived delivered
tests three times in this repository (SC-1-02 twice, SC-2-01), which is why the race run is
mandatory rather than thorough.

**What these tests do not close, named here and not only in the report** (ADR-0004, addendum
2026-09-19, point 2): the window in which an approval commits *concurrently with* the guarded
statement rather than before it. Under `READ COMMITTED` the statement's read of the parent takes no
lock on it, so the approval and the write can both succeed. It is not provokable today — nothing in
the running system sets `approved`, which is also why every approved scenario here is made by a
direct database write in a fixture — and the first real approval path has to close it for every
child table of `scenarios` at once.

The interleaving is produced by a `before_cursor_execute` hook that commits the approval on a
*separate* connection at the moment the guarded statement is about to run. That is the only point at
which the window exists; a second thread would make the test timing-dependent instead.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.api.staffing import STAFFING_NOT_FOUND_DETAIL
from app.models import ScenarioStatus, StaffingPosition
from tests.conftest import (
    IN_SCOPE_USER,
    allocation_path,
    as_caller,
    count_positions,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    staffing_path,
    staffing_position_payload,
)

MARCH = date(2026, 3, 1)

Listener = Any


def _committing_the_approval_before(
    engine: Engine, fired: list[str], scenario_id: uuid.UUID, *, statement_prefix: str
) -> Listener:
    """A hook that approves the scenario on another connection, just before the guarded statement.

    `statement_prefix` is matched against the start of the statement about to run, so the two
    criteria can each aim at their own statement (`insert into staffing_position` for K-06, the
    `WITH guarded_position …` edit for K-07) instead of sharing one hook that might fire on the
    wrong one.

    Fires once (`fired` guards re-entry — the competing `UPDATE` goes through the same class-level
    listener). The session under test holds no lock on the scenario row at this point, so the
    competitor commits immediately and the statement that triggered the hook then runs against a
    scenario that is already `approved`.
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
            competitor.execute(
                sa.text("UPDATE scenarios SET status = 'approved' WHERE id = :id"),
                {"id": scenario_id},
            )

    return interleave


def _committing_a_competing_edit_before(
    engine: Engine, fired: list[str], position_id: uuid.UUID, *, statement_prefix: str
) -> Listener:
    """A hook that performs *another editor's* allocation edit, committed on another connection,
    just before the guarded statement of the edit under test runs.

    The competitor does exactly what a second `PATCH` of the same position does: it writes a value
    into the month row and rotates the position's `updated_at` (ADR-0007's token for the whole
    grid). It commits in the window between "the token was read" and "the write happens" — the
    interval the token exists to cover (NF-05), and the only interval in which a token compared in
    Python differs from a token compared inside the `UPDATE`.

    Fires once (`fired` guards re-entry: the competitor's own statements go through the same
    class-level listener).
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
            competitor.execute(
                sa.text(
                    "UPDATE staffing_position_allocation SET billable_hours = 555.00"
                    " WHERE position_id = :id AND period_month = :month"
                ),
                {"id": position_id, "month": MARCH},
            )
            competitor.execute(
                sa.text("UPDATE staffing_position SET updated_at = now() WHERE id = :id"),
                {"id": position_id},
            )

    return interleave


def _committed_project_with_two_scenarios(engine: Engine) -> dict[str, Any]:
    """A project in scope, one `draft` and one `approved` scenario, a dimension tuple — committed.

    The `approved` scenario is written straight to the database: no production path sets that status
    (the approval transition is out of scope for SC-3-01 and no endpoint creates a scenario at all),
    so the fixture is the only way to reach the state these two criteria are about. That is the
    limit of the proof, and it is the one the plan entry names as "fundament nieudowodniony".
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        draft = make_scenario(setup, project, name="Baseline")
        approved = make_scenario(
            setup, project, name="Approved v1", status=ScenarioStatus.APPROVED
        )
        dimensions = make_dimension_tuple(setup)
        position = make_staffing_position(setup, approved, dimensions, start_date=MARCH)
        make_allocation(setup, position, period_month=MARCH)
        draft_position = make_staffing_position(setup, draft, dimensions, start_date=MARCH)
        make_allocation(setup, draft_position, period_month=MARCH)
        state = {
            "project_id": project.id,
            "draft_id": draft.id,
            "approved_id": approved.id,
            "dimensions": dimensions,
            "approved_position_id": position.id,
            "draft_position_id": draft_position.id,
        }
        setup.commit()
    return state


def _hours_of(engine: Engine, position_id: uuid.UUID) -> Decimal:
    with engine.connect() as connection:
        return connection.execute(
            sa.text(
                "SELECT planned_allocation_hours FROM staffing_position_allocation"
                " WHERE position_id = :id"
            ),
            {"id": position_id},
        ).scalar_one()


def _token_of(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    """The position's `updated_at` as the caller reads it — ADR-0007's token for the whole grid."""
    response = client.get(staffing_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    return response.json()["positions"][0]["updated_at"]


# --- K-06: inserting a position into an approved scenario ----------------------------------------


def test_k_06_inserting_a_position_into_an_approved_scenario_is_refused_and_writes_nothing(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06, first run — the refusal, the reason, and zero rows.

    Counted from a separate connection, which sees committed rows only: the claim is not "the
    endpoint said no" but "no position and no month row exists". The refusal has to name *approved*
    as well — a `409` reading "the scenario changed since it was read" would be this test passing on
    the other refusal, which is a different mechanism and a different claim.

    The contrast is the same request against the `draft` scenario of the same project, so the
    refusal cannot be an accident of the payload, the permissions or the scope.
    """
    state = _committed_project_with_two_scenarios(engine)
    payload = staffing_position_payload(state["dimensions"], start_date="2026-07-01", end_date=None)

    refused = committing_client.post(
        staffing_path(state["project_id"], state["approved_id"]),
        json=payload,
        headers=as_caller(IN_SCOPE_USER),
    )

    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"], refused.text
    with engine.connect() as connection:
        positions = connection.execute(
            sa.text(
                "SELECT count(*) FROM staffing_position"
                " WHERE scenario_id = :id AND start_date = '2026-07-01'"
            ),
            {"id": state["approved_id"]},
        ).scalar_one()
        months = connection.execute(
            sa.text(
                "SELECT count(*) FROM staffing_position_allocation a"
                " JOIN staffing_position p ON p.id = a.position_id"
                " WHERE p.scenario_id = :id AND p.start_date = '2026-07-01'"
            ),
            {"id": state["approved_id"]},
        ).scalar_one()
    assert positions == 0, "a position was written into an approved scenario"
    assert months == 0, (
        "the month rows of a refused position were written — they inherit the guard through the "
        "position id that was never created"
    )

    accepted = committing_client.post(
        staffing_path(state["project_id"], state["draft_id"]),
        json=payload,
        headers=as_caller(IN_SCOPE_USER),
    )

    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["allocations"] != []


def test_k_06_an_approval_committed_just_before_the_insert_still_refuses_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-06, second run — the guard is the statement, not a status read followed by an insert.

    The approval lands after `create_position` has resolved the scenario through the scope-filtered
    read path and before its `INSERT` runs. A predicate inside the `SELECT` the inserted row comes
    from sees it (no parent row matches, zero rows affected, `409`); a Python `if scenario.status ==
    APPROVED` does not, because it read the status before the competitor committed — and the
    position then lands in an approved calculation, which is the AC-10 regression ADR-0004's
    addendum warns about for behaviourally-protected tables.
    """
    state = _committed_project_with_two_scenarios(engine)
    fired: list[str] = []
    interleave = _committing_the_approval_before(
        engine, fired, state["draft_id"], statement_prefix="insert into staffing_position"
    )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        response = committing_client.post(
            staffing_path(state["project_id"], state["draft_id"]),
            json=staffing_position_payload(
                state["dimensions"], start_date="2026-08-01", end_date=None
            ),
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the approval never landed inside the window — nothing here is about the race"
    assert response.status_code == 409, response.text
    assert "approved" in response.json()["detail"], response.text
    with engine.connect() as connection:
        written = connection.execute(
            sa.text(
                "SELECT count(*) FROM staffing_position WHERE start_date = '2026-08-01'"
            )
        ).scalar_one()
    assert written == 0, (
        "a position was inserted although the scenario was approved before the INSERT ran — the "
        "guard is not in the statement"
    )


# --- K-07: editing a month of an approved scenario, and the concurrency token --------------------


def test_k_07_editing_an_allocation_under_an_approved_scenario_is_refused_and_changes_nothing(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-07, first run — the refusal names `approved`, and the old value is still in the database.

    The token carried in the request is the *current* one, read from the position a moment earlier,
    so nothing about concurrency can be what refuses this edit: the only reason available is the
    state of the scenario. That is also why the assertion on the message matters — the two refusals
    share a status code and are told apart by what they say.
    """
    state = _committed_project_with_two_scenarios(engine)
    token = _token_of(committing_client, state["project_id"], state["approved_id"])

    refused = committing_client.patch(
        allocation_path(
            state["project_id"], state["approved_id"], state["approved_position_id"], MARCH
        ),
        json={"updated_at": token, "planned_allocation_hours": "999.00"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"], refused.text
    assert _hours_of(engine, state["approved_position_id"]) == Decimal("120.00")

    # Contrast: the same edit on the draft scenario's position, with its own fresh token, is applied
    # — and the token moves, which is what makes the mechanism a token rather than decoration.
    draft_token = _token_of(committing_client, state["project_id"], state["draft_id"])
    accepted = committing_client.patch(
        allocation_path(state["project_id"], state["draft_id"], state["draft_position_id"], MARCH),
        json={"updated_at": draft_token, "planned_allocation_hours": "999.00"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["allocations"][0]["planned_allocation_hours"] == "999.00"
    assert accepted.json()["updated_at"] != draft_token, "the concurrency token did not rotate"
    assert _hours_of(engine, state["draft_position_id"]) == Decimal("999.00")


def test_k_07_an_approval_committed_just_before_the_update_still_refuses_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-07, second run — the `approved` predicate is inside the statement that writes.

    The approval lands after the scenario has been resolved and after the token was read, and before
    the guarded `UPDATE` runs. This is the mutation the run exists for: `if scenario.status ==
    APPROVED: refuse`, evaluated in Python, passes every other test in this file and lets exactly
    this edit through — a month of an approved calculation changed by a caller who was told "200".

    The hook aims at the `WITH guarded_position …` statement, which is the one statement in which
    the token, the scenario's status and the existence of the month row are all evaluated.
    """
    state = _committed_project_with_two_scenarios(engine)
    token = _token_of(committing_client, state["project_id"], state["draft_id"])
    fired: list[str] = []
    interleave = _committing_the_approval_before(
        engine, fired, state["draft_id"], statement_prefix="with guarded_position"
    )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        response = committing_client.patch(
            allocation_path(
                state["project_id"], state["draft_id"], state["draft_position_id"], MARCH
            ),
            json={"updated_at": token, "planned_allocation_hours": "777.00"},
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the approval never landed inside the window — nothing here is about the race"
    assert response.status_code == 409, response.text
    assert "approved" in response.json()["detail"], response.text
    assert _hours_of(engine, state["draft_position_id"]) == Decimal("120.00"), (
        "a month of a scenario approved before the UPDATE ran was changed anyway — the guard is "
        "not in the statement"
    )


def test_k_07_a_stale_concurrency_token_is_refused_and_overwrites_nothing(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-07, the ADR-0007 half: the token belongs to the **position**, not to the month row.

    The first edit rotates the position's token; the second edit carries the token from before it
    and is refused — including when it names a *different month* of the same position, which is the
    false collision ADR-0007's addendum of 2026-09-19 accepts by name (the position is the unit of
    editing, the month is not). Without that property the token would be per row in all but name,
    and the addendum's choice would be undocumented in the code.

    The refusal must not say "approved": the scenario is a draft, and a message naming the wrong
    mechanism is how a caller retries a write that would have succeeded, or gives up on one that
    would not.
    """
    state = _committed_project_with_two_scenarios(engine)
    # A second month on the same position, so the false collision between two months of one position
    # can be observed at all.
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        position = setup.get(StaffingPosition, state["draft_position_id"])
        assert position is not None
        make_allocation(
            setup,
            position,
            period_month=date(2026, 4, 1),
            availability_hours=Decimal("100.00"),
            planned_allocation_hours=Decimal("50.00"),
            billable_hours=Decimal("25.00"),
        )
        setup.commit()
    stale_token = _token_of(committing_client, state["project_id"], state["draft_id"])

    first = committing_client.patch(
        allocation_path(state["project_id"], state["draft_id"], state["draft_position_id"], MARCH),
        json={"updated_at": stale_token, "billable_hours": "11.00"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert first.status_code == 200, first.text

    with_stale_token_same_month = committing_client.patch(
        allocation_path(state["project_id"], state["draft_id"], state["draft_position_id"], MARCH),
        json={"updated_at": stale_token, "billable_hours": "22.00"},
        headers=as_caller(IN_SCOPE_USER),
    )
    with_stale_token_other_month = committing_client.patch(
        allocation_path(
            state["project_id"], state["draft_id"], state["draft_position_id"], date(2026, 4, 1)
        ),
        json={"updated_at": stale_token, "billable_hours": "33.00"},
        headers=as_caller(IN_SCOPE_USER),
    )

    for refused in (with_stale_token_same_month, with_stale_token_other_month):
        assert refused.status_code == 409, refused.text
        assert "changed since it was read" in refused.json()["detail"], refused.text
        assert "approved" not in refused.json()["detail"], refused.text
    with engine.connect() as connection:
        billable = connection.execute(
            sa.text(
                "SELECT billable_hours FROM staffing_position_allocation"
                " WHERE position_id = :id ORDER BY period_month"
            ),
            {"id": state["draft_position_id"]},
        ).scalars().all()
    assert billable == [Decimal("11.00"), Decimal("25.00")], (
        "a stale token overwrote a value — the token is not compared inside the UPDATE"
    )

    # Contrast: the token from the *last* response is accepted, so the refusals above are about
    # staleness and not about the endpoint refusing every token it is given.
    fresh = committing_client.patch(
        allocation_path(state["project_id"], state["draft_id"], state["draft_position_id"], MARCH),
        json={"updated_at": first.json()["updated_at"], "billable_hours": "44.00"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert fresh.status_code == 200, fresh.text


def test_k_07_a_competing_edit_committed_just_before_the_update_refuses_it_and_keeps_its_value(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-07, the ADR-0007 half **in a race between two connections** — added by QA, 2026-09-19.

    The delivered suite proved the stale token only *sequentially*: it read a token, spent it on one
    edit, and then reused it. Every such sequence is also refused by a token compared in Python
    against a value read a moment earlier, because by then the position row already carries the new
    token — so `updated_at = :expected` moved out of the `UPDATE`'s `WHERE` and into an `if` passed
    all 248 tests of this suite. Measured, not predicted: the mutation survived, and this test is
    what kills it.

    Here the caller's token is **fresh when the request arrives** and goes stale inside the request:
    a competing editor writes the month and rotates the token in the window between the read and the
    write. A Python comparison sees the old value, concludes "no conflict", and applies the edit —
    which silently discards the competitor's `555.00`, i.e. the lost update NF-05 and ADR-0007
    exist to prevent. With the predicate inside the statement, the `UPDATE` matches no position
    row and the caller is told to re-read.

    The assertion that carries the claim is therefore the *surviving value*, not the status code: a
    `409` returned after the overwrite would satisfy a status-only test just as well.
    """
    state = _committed_project_with_two_scenarios(engine)
    token = _token_of(committing_client, state["project_id"], state["draft_id"])
    fired: list[str] = []
    interleave = _committing_a_competing_edit_before(
        engine,
        fired,
        state["draft_position_id"],
        statement_prefix="with guarded_position",
    )

    event.listen(Engine, "before_cursor_execute", interleave)
    try:
        response = committing_client.patch(
            allocation_path(
                state["project_id"], state["draft_id"], state["draft_position_id"], MARCH
            ),
            json={"updated_at": token, "billable_hours": "888.00"},
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", interleave)

    assert fired, "the competing edit never landed inside the window — nothing here is about a race"
    assert response.status_code == 409, response.text
    assert "changed since it was read" in response.json()["detail"], response.text
    assert "approved" not in response.json()["detail"], response.text
    with engine.connect() as connection:
        billable = connection.execute(
            sa.text(
                "SELECT billable_hours FROM staffing_position_allocation"
                " WHERE position_id = :id AND period_month = :month"
            ),
            {"id": state["draft_position_id"], "month": MARCH},
        ).scalar_one()
    assert billable == Decimal("555.00"), (
        "the competing editor's value was overwritten by a caller whose token went stale inside "
        "the request — the token is not compared inside the UPDATE"
    )


def test_a_patch_on_a_month_that_has_no_row_is_not_found_and_rotates_no_token(
    committing_client: TestClient, engine: Engine
) -> None:
    """Not an acceptance criterion — the side effect a token rotation would otherwise have.

    ADR-0004's addendum of 2026-09-19 (point 1) records exactly this shape for archiving: a
    statement that writes `updated_at` invalidates every editor's token even when nothing they cared
    about changed. Here it would be worse than accepted, because *nothing at all* changes — the
    month row does not exist. The existence of the month row is therefore a condition of the
    position update, and this test is what holds it there.

    The `404` is the same body as every other refusal on this path: a month that was never written
    and a scenario the caller may not see must not be distinguishable (ADR-0005, addendum
    2026-09-19, point 4).
    """
    state = _committed_project_with_two_scenarios(engine)
    token = _token_of(committing_client, state["project_id"], state["draft_id"])

    response = committing_client.patch(
        allocation_path(
            state["project_id"], state["draft_id"], state["draft_position_id"], date(2026, 9, 1)
        ),
        json={"updated_at": token, "planned_allocation_hours": "5.00"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 404, response.text
    assert _token_of(committing_client, state["project_id"], state["draft_id"]) == token, (
        "a PATCH on a month that has no row rotated the position's concurrency token"
    )


def test_r_01_a_patch_on_a_month_with_no_row_under_an_approved_scenario_is_not_found(
    committing_client: TestClient, engine: Engine
) -> None:
    """R-01 (reviewer 2026-09-19) — "does it exist" is answered before "may it be written".

    The two refusals compete here, and the order used to be the wrong one:
    `_diagnose_allocation_refusal` asked about `approved` first, so editing a month that has **no
    row** under an approved scenario came back as `409 "this scenario is approved — copy it and
    change the copy"`. That advice cannot work: the copy is a `draft`, so the `approved` reason
    disappears — and the same request against it is a `404`, because the month never existed in the
    first place. A refusal must not name a reason it has not established, and "frozen" is a
    statement about a row.

    It is also the 404-before-409 precedence ADR-0007's addendum states as a general rule, applied
    one level below the scenario: the same code already applied it to a *position* that does not
    belong to the scenario, and a missing month row is the same kind of fact.

    **Both halves are asserted in one test on purpose.** The `404` alone is satisfiable by answering
    `404` to everything, which would quietly destroy K-07; the contrast is the same `PATCH` on the
    month that *does* exist under the *same* approved scenario, which must stay a `409` naming
    `approved`.
    """
    state = _committed_project_with_two_scenarios(engine)
    token = _token_of(committing_client, state["project_id"], state["approved_id"])
    body = {"updated_at": token, "planned_allocation_hours": "5.00"}

    missing_month = committing_client.patch(
        allocation_path(
            state["project_id"],
            state["approved_id"],
            state["approved_position_id"],
            date(2026, 9, 1),
        ),
        json=body,
        headers=as_caller(IN_SCOPE_USER),
    )
    existing_month = committing_client.patch(
        allocation_path(
            state["project_id"], state["approved_id"], state["approved_position_id"], MARCH
        ),
        json=body,
        headers=as_caller(IN_SCOPE_USER),
    )

    assert missing_month.status_code == 404, missing_month.text
    assert "approved" not in missing_month.text.lower(), (
        "a month that has no row was refused with the frozen-scenario reason — advice the caller "
        "cannot act on, because the same request against the copy is a 404"
    )
    assert missing_month.json()["detail"] == STAFFING_NOT_FOUND_DETAIL
    assert existing_month.status_code == 409, existing_month.text
    assert "approved" in existing_month.json()["detail"], existing_month.text
    assert _hours_of(engine, state["approved_position_id"]) == Decimal("120.00")


def test_a_position_addressed_through_a_scenario_it_does_not_belong_to_is_not_found(
    committing_client: TestClient, engine: Engine
) -> None:
    """The write path's own membership check, one level below the scenario (K-02's shape on K-07).

    The approved scenario's position, addressed under the draft scenario of the same project by a
    caller who may see both. Answered `404`, and the row is untouched — a `409` naming `approved`
    would be a refusal computed from a scenario the address did not name, and an accepted edit would
    write into an approved calculation through an address that looked innocent.
    """
    state = _committed_project_with_two_scenarios(engine)
    token = _token_of(committing_client, state["project_id"], state["approved_id"])

    response = committing_client.patch(
        allocation_path(
            state["project_id"], state["draft_id"], state["approved_position_id"], MARCH
        ),
        json={"updated_at": token, "planned_allocation_hours": "1.00"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 404, response.text
    assert _hours_of(engine, state["approved_position_id"]) == Decimal("120.00")


def test_a_refused_insert_leaves_the_transaction_usable_for_the_next_request(
    client: TestClient, db_session: Session
) -> None:
    """The refusal is not a broken transaction — a detail with a history in this repository.

    `create_position` deliberately does **not** roll back when its `INSERT ... SELECT` matches no
    parent: the statement wrote nothing, so there is nothing to undo, and a rollback would discard
    unrelated work the caller's transaction may already hold (the argument `update_project` makes).
    This test is what keeps that reasoning honest: after a refused write, the very next request on
    the same session still works.
    """
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    approved = make_scenario(
        db_session, project, name="Approved v1", status=ScenarioStatus.APPROVED
    )
    draft = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session)

    refused = client.post(
        staffing_path(project.id, approved.id),
        json=staffing_position_payload(dimensions),
        headers=as_caller(IN_SCOPE_USER),
    )
    accepted = client.post(
        staffing_path(project.id, draft.id),
        json=staffing_position_payload(dimensions),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert refused.status_code == 409, refused.text
    assert accepted.status_code == 201, accepted.text
    assert count_positions(db_session) == 1
