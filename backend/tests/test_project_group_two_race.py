"""SC-1-10, K-08 — an edit of a group-2 project field against a concurrent approval (gate 1, P-C).

**The claim.** For every field of `FROZEN_BY_APPROVED_SCENARIO` — the three that existed before this
task (`reporting_currency`, `delivery_period_*`) and the two it adds (the margin and threshold
overrides) — an edit through `PATCH /projects/{id}` and an approval through
`POST …/scenarios/{id}/approve` of a scenario of the same project serialise. The forbidden outcome
is the pair *"the scenario is approved"* and *"a group-2 value it was approved with changed
afterwards"*, observed from a separate connection.

**Both orders, because they do not kill the same mutations** (measured 2026-09-23, see the report):

- **the edit first** — the edit has run its `UPDATE` and not committed; the approval starts. Legal
  only if the approval *waits* for the edit's commit, so that what it approves already carries the
  new value. Without the approving half (`approving_project_lock`) the approval commits first and
  the edit's commit then changes an approved calculation — observed here as "approved with the old
  value" followed by "approved with the new one". This is the order the analyst predicted fails on
  the code before SC-1-10; with both locks removed (the pre-SC-1-10 shape) it fails for every field.
  Removing only the editing half leaves this order green: the edit's own `UPDATE` holds the row.
- **the approval first** — the approval holds its locks and is paused before its status update; the
  edit starts. Legal only if the edit waits and is then refused (409) with the value unchanged.
  Removing **either** half fails it: without the approving lock there is nothing to wait for, and
  without the editing lock the `UPDATE`'s `NOT EXISTS (… approved)` is evaluated against a snapshot
  taken before the approval committed, so the edit lands under an approved scenario.

**Real concurrency, two connections, the real endpoints** — the shape of K-20 in
`tests/test_staffing_approved_guards.py`: one side runs in a background thread, the other is paused
by a `*_cursor_execute` hook, and the test waits until PostgreSQL itself reports an ungranted lock.
`blocked` is asserted in both orders: a run in which nothing waited proves nothing about
serialisation.

**Not changed by this file:** `test_the_frozen_field_guard_is_evaluated_in_the_same_statement_as_
the_write` (`tests/test_project_write_actions_guards.py`, line 150). It proves the guard is inside
the `UPDATE` (an approval committed *before* the statement), not this commit window, and it stays as
it was.
"""

import threading
import uuid
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.data.project_writes import FROZEN_BY_APPROVED_SCENARIO
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    make_project,
    make_scenario,
    wait_until_a_lock_request_is_pending,
)

GROUP_TWO_EDITS: Mapping[str, tuple[Any, Mapping[str, Any]]] = {
    "reporting_currency": ("USD", {"reporting_currency": "USD"}),
    "delivery_period": (
        {"start": "2026-02-01", "end": "2026-11-30"},
        {"delivery_period_start": date(2026, 2, 1), "delivery_period_end": date(2026, 11, 30)},
    ),
    "target_margin_percent": ("15.000", {"target_margin_percent": Decimal("15.000")}),
    "overload_threshold_percent": (
        "110.000",
        {"overload_threshold_percent": Decimal("110.000")},
    ),
}
"""One `PATCH` per request field touching group 2: the body value, and the column values it writes.

Written out by hand rather than derived from `FROZEN_BY_APPROVED_SCENARIO`, for the reason
`COPIED_SCENARIO_FIELDS` is (`tests/test_project_copy.py`): deriving it would compare the mechanism
with itself. The drift guard below ties the two together, so a column joining group 2 fails here
until it is raced too. Every new value differs from the fixture's initial one (`make_project`:
`EUR`, 2026-01-01..2026-12-31, no overrides), or "the value changed" could not be observed."""


def test_k_08_every_group_two_field_is_raced(engine: Engine) -> None:
    """The drift guard: the raced columns are exactly the frozen set — no more, no fewer."""
    raced = {column for _, columns in GROUP_TWO_EDITS.values() for column in columns}
    assert raced == set(FROZEN_BY_APPROVED_SCENARIO), (
        "FROZEN_BY_APPROVED_SCENARIO and the fields raced here disagree: "
        f"not raced {sorted(set(FROZEN_BY_APPROVED_SCENARIO) - raced)}, "
        f"raced but not frozen {sorted(raced - set(FROZEN_BY_APPROVED_SCENARIO))}"
    )


def _committed_draft(engine: Engine) -> dict[str, uuid.UUID]:
    """A committed project with one committed, empty draft scenario, visible to every
    connection."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Baseline")
        state = {"project_id": project.id, "scenario_id": scenario.id}
        setup.commit()
    return state


def _token(client: TestClient, project_id: uuid.UUID) -> str:
    response = client.get(f"/projects/{project_id}", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    return response.json()["updated_at"]


def _observe(
    engine: Engine, state: Mapping[str, uuid.UUID], columns: Mapping[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """(is the scenario approved, the group-2 columns now) — committed state, read on a separate
    connection."""
    with engine.connect() as observer:
        status = observer.execute(
            sa.text("SELECT status FROM scenarios WHERE id = :id"), {"id": state["scenario_id"]}
        ).scalar_one()
        row = observer.execute(
            sa.text(f"SELECT {', '.join(columns)} FROM projects WHERE id = :id"),
            {"id": state["project_id"]},
        ).one()
    return status == "approved", dict(row._mapping)


def _patch(
    client: TestClient, state: Mapping[str, uuid.UUID], token: str, field: str, value: Any
) -> Any:
    return client.patch(
        f"/projects/{state['project_id']}",
        json={"updated_at": token, field: value},
        headers=as_caller(IN_SCOPE_USER),
    )


def _approve(client: TestClient, state: Mapping[str, uuid.UUID]) -> Any:
    return client.post(
        approve_path(state["project_id"], state["scenario_id"]), headers=as_caller(IN_SCOPE_USER)
    )


@pytest.mark.parametrize("field", sorted(GROUP_TWO_EDITS))
def test_k_08_an_approval_started_during_an_uncommitted_edit_waits_for_it(
    committing_client: TestClient, engine: Engine, field: str
) -> None:
    """K-08, the edit first. The approval must not commit before the edit it overlaps.

    The edit is paused **after** its `UPDATE projects` has run — holding the row — and before its
    commit. The approval is then started in a background thread; the hook waits for PostgreSQL to
    report it blocked. The thread reads the committed state the moment its approval returns: if the
    approval could finish while the edit was still open, that read shows *approved with the old
    value*, and the edit's commit a moment later moves a value of an approved calculation.

    With no serialising lock the approval never blocks; the hook then waits for it to finish before
    letting the edit commit, so the forbidden interleaving is produced deterministically rather than
    by luck — and every assertion below fails on it, not only `blocked`.
    """
    body_value, columns = GROUP_TWO_EDITS[field]
    state = _committed_draft(engine)
    token = _token(committing_client, state["project_id"])
    outcome: dict[str, Any] = {}
    fired: list[str] = []

    def approve_while_the_edit_is_open(
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

        def approval() -> None:
            try:
                outcome["approval"] = _approve(committing_client, state)
                outcome["seen_when_approved"] = _observe(engine, state, columns)
            except BaseException as error:  # noqa: BLE001 — reported, never swallowed
                outcome["error"] = error

        thread = threading.Thread(target=approval, daemon=True)
        thread.start()
        outcome["thread"] = thread
        outcome["blocked"] = wait_until_a_lock_request_is_pending(engine, timeout=3.0)
        if not outcome["blocked"]:
            # Nothing serialises the two: let the approval run to its commit *before* the edit
            # commits — the interleaving this criterion forbids, made certain instead of likely.
            thread.join(timeout=30)

    event.listen(Engine, "after_cursor_execute", approve_while_the_edit_is_open)
    try:
        edit = _patch(committing_client, state, token, field, body_value)
    finally:
        event.remove(Engine, "after_cursor_execute", approve_while_the_edit_is_open)

    assert fired, "the edit never reached its UPDATE — nothing below is about the race"
    thread = outcome["thread"]
    thread.join(timeout=30)
    assert not thread.is_alive(), "the approval never finished — it is still waiting for a lock"
    assert "error" not in outcome, outcome.get("error")

    assert edit.status_code == 200, edit.text
    assert outcome["approval"].status_code == 200, outcome["approval"].text
    approved_then, values_then = outcome["seen_when_approved"]
    approved_now, values_now = _observe(engine, state, columns)
    assert approved_then and approved_now
    assert values_now == dict(columns), "the edit's value is not what the project holds"
    assert values_then == values_now, (
        f"{field}: the scenario was approved while the project still held {values_then}, and the "
        f"edit then changed it to {values_now} — a value of an approved calculation moved"
    )
    assert outcome["blocked"], (
        "the approval never waited for the edit — nothing serialises the two, and the pass above "
        "would be luck"
    )


@pytest.mark.parametrize("field", sorted(GROUP_TWO_EDITS))
def test_k_08_an_edit_started_during_an_uncommitted_approval_waits_and_is_refused(
    committing_client: TestClient, engine: Engine, field: str
) -> None:
    """K-08, the approval first. The edit must wait for the approval and then be refused.

    The approval is paused just before its status update — after its locks and its snapshot, before
    its commit (the pause point of K-20). The edit runs in a background thread and must block; once
    the approval commits, the edit's guard must see `approved` and refuse with a 409, leaving every
    group-2 column as the approval found it.
    """
    body_value, columns = GROUP_TWO_EDITS[field]
    state = _committed_draft(engine)
    token = _token(committing_client, state["project_id"])
    _, values_before = _observe(engine, state, columns)
    outcome: dict[str, Any] = {}
    fired: list[str] = []

    def edit_while_the_approval_is_open(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or "update scenarios set status" not in statement.lower():
            return
        fired.append(statement)

        def edit() -> None:
            try:
                outcome["edit"] = _patch(committing_client, state, token, field, body_value)
            except BaseException as error:  # noqa: BLE001 — reported, never swallowed
                outcome["error"] = error

        thread = threading.Thread(target=edit, daemon=True)
        thread.start()
        outcome["thread"] = thread
        outcome["blocked"] = wait_until_a_lock_request_is_pending(engine, timeout=3.0)
        if not outcome["blocked"]:
            # Nothing serialises the two: let the edit commit while the approval is still open.
            thread.join(timeout=30)

    event.listen(Engine, "before_cursor_execute", edit_while_the_approval_is_open)
    try:
        approval = _approve(committing_client, state)
    finally:
        event.remove(Engine, "before_cursor_execute", edit_while_the_approval_is_open)

    assert fired, "the approval never reached its status update"
    thread = outcome["thread"]
    thread.join(timeout=30)
    assert not thread.is_alive(), "the edit never finished — it is still waiting for a lock"
    assert "error" not in outcome, outcome.get("error")

    assert approval.status_code == 200, approval.text
    approved, values_after = _observe(engine, state, columns)
    assert approved, "the approval itself failed — the pair below is satisfied vacuously"
    assert values_after == values_before, (
        f"{field} changed from {values_before} to {values_after} although the edit began while "
        "the approval was already in flight"
    )
    assert outcome["edit"].status_code == 409, outcome["edit"].text
    assert "approved" in outcome["edit"].json()["detail"], outcome["edit"].text
    assert outcome["blocked"], "the edit never waited for the approval's lock"


def test_k_08_contrast_an_edit_committed_before_the_approval_stays_and_the_approval_succeeds(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-08's contrast: the ordinary sequence — edit, then approve — keeps both.

    What stops the two runs above from being satisfied by a mechanism that refuses every group-2
    edit once an approval has ever been attempted, or that refuses approvals after an edit.
    """
    state = _committed_draft(engine)
    token = _token(committing_client, state["project_id"])

    edit = _patch(committing_client, state, token, "target_margin_percent", "15.000")
    assert edit.status_code == 200, edit.text
    approval = _approve(committing_client, state)
    assert approval.status_code == 200, approval.text

    approved, values = _observe(engine, state, {"target_margin_percent": None})
    assert approved
    assert values == {"target_margin_percent": Decimal("15.000")}
