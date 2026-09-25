"""SC-1-11, K-07 — a write under an `approved` scenario is refused, and the refusal survives a real
race with the approval on two connections (ADR-0004, addendum 2026-09-25 SC-1-11, point 2).

There is no HTTP endpoint over this table (ADR-0016, point 8), so every write here calls
`app.data.scenario_delivery_segment.create_scenario_delivery_segment` directly — the function ADR-
0016 requires to exist for exactly this reason. The race still goes through the **real** approval
endpoint (`POST .../approve`), because that is what makes the serialising half of the seam
(`app.data.scenario_guard.unapproved_scenario`, embedded in `create_scenario_delivery_segment`'s own
`INSERT … SELECT`) part of what is under test rather than part of the test — the same reasoning
`test_staffing_approved_guards.py` and `test_additional_cost_guards.py` already apply to their own
tables.
"""

import threading
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.data.scenario_delivery_segment import (
    ScenarioDeliverySegmentFrozen,
    ScenarioDeliverySegmentScenarioNotFound,
    ScenarioDeliverySegmentWriteRejected,
    create_scenario_delivery_segment,
)
from app.models import ScenarioDeliverySegment, ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    count_scenario_delivery_segments,
    make_project,
    make_scenario,
    wait_until_a_lock_request_is_pending,
)
from tests.test_staffing_approved_guards import _scenario_is_approved

HEADERS = as_caller(IN_SCOPE_USER)


def _segment_count_of(session: Session, scenario_id: uuid.UUID) -> int:
    session.expire_all()
    return session.execute(
        sa.select(sa.func.count())
        .select_from(ScenarioDeliverySegment)
        .where(ScenarioDeliverySegment.scenario_id == scenario_id)
    ).scalar_one()


# --- K-07: the plain refusal, no race involved -----------------------------------------------


def test_k_07_an_insert_under_an_approved_scenario_is_refused_and_writes_nothing(
    db_session: Session,
) -> None:
    """K-07 — a scenario at `approved` refuses the insert; a draft of the same shape accepts it.
    Both counted directly, not inferred from the exception alone."""
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    approved = make_scenario(
        db_session, project, name="Frozen", status=ScenarioStatus.APPROVED
    )
    draft = make_scenario(db_session, project, name="Open")
    before = count_scenario_delivery_segments(db_session)

    with pytest.raises(ScenarioDeliverySegmentFrozen):
        create_scenario_delivery_segment(db_session, approved.id, "Discovery")

    assert count_scenario_delivery_segments(db_session) == before
    assert _segment_count_of(db_session, approved.id) == 0

    # Contrast: the same write against a draft succeeds and writes exactly one row.
    created = create_scenario_delivery_segment(db_session, draft.id, "Discovery")
    assert created.scenario_id == draft.id
    assert created.name == "Discovery"
    assert _segment_count_of(db_session, draft.id) == 1


def test_k_07_an_insert_under_a_non_existent_scenario_is_not_found_not_approved(
    db_session: Session,
) -> None:
    """The R-01 order (`app.data.additional_cost._diagnose_insert_refusal`'s reasoning) — a
    scenario that does not exist at all is `ScenarioDeliverySegmentScenarioNotFound`, not
    `ScenarioDeliverySegmentFrozen`: advising "copy the scenario" would be advice about a row that
    is not there."""
    with pytest.raises(ScenarioDeliverySegmentScenarioNotFound):
        create_scenario_delivery_segment(db_session, uuid.uuid4(), "Discovery")


# --- K-07: the race with the approval, on two connections ---------------------------------------


def _committed_draft_scenario(engine: Engine) -> dict[str, Any]:
    """A committed, empty draft scenario — nothing else needs to exist for a segment insert to be
    guarded, unlike a table that reads a sibling position."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Race segment", accessible_to=(IN_SCOPE_USER,))
        draft = make_scenario(setup, project, name="Baseline")
        setup.commit()
        return {"project_id": project.id, "draft_id": draft.id}


def _race_a_segment_insert_against_the_approval(
    committing_client: TestClient, engine: Engine, state: dict[str, Any]
) -> dict[str, Any]:
    """`_race_a_child_write_against_the_approval` (`tests/test_staffing_approved_guards.py`),
    adapted for a write with no HTTP endpoint: the writer thread calls the data-layer function on
    its own session instead of issuing a request through `committing_client`."""
    outcome: dict[str, Any] = {}
    fired: list[str] = []

    def run_the_writer_and_wait_for_it_to_block(
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

        def writer() -> None:
            try:
                with Session(bind=engine, expire_on_commit=False, future=True) as session:
                    outcome["segment"] = create_scenario_delivery_segment(
                        session, state["draft_id"], "Race"
                    )
            except ScenarioDeliverySegmentWriteRejected as rejected:
                outcome["rejected"] = rejected
            except BaseException as error:  # noqa: BLE001 — reported, never swallowed
                outcome["error"] = error

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        outcome["thread"] = thread
        outcome["blocked"] = wait_until_a_lock_request_is_pending(engine)

    event.listen(Engine, "before_cursor_execute", run_the_writer_and_wait_for_it_to_block)
    try:
        outcome["approval"] = committing_client.post(
            approve_path(state["project_id"], state["draft_id"]), headers=HEADERS
        )
    finally:
        event.remove(Engine, "before_cursor_execute", run_the_writer_and_wait_for_it_to_block)

    assert fired, "the approval never reached its status update"
    thread = outcome.get("thread")
    assert thread is not None
    thread.join(timeout=30)
    assert not thread.is_alive(), "the segment insert never finished — it is still holding a lock"
    return outcome


def test_k_07_an_approval_committing_concurrently_with_a_segment_insert_leaves_no_row(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-07, the race — `INSERT` while the real approval holds the scenario lock taken by
    `app.data.scenario_guard.draft_scenario`.

    The postcondition is the pair: "the scenario is approved" and "a segment row exists that was
    written after the approval began" never hold together. `blocked` is asserted — a run in which
    the insert never waited says nothing about serialisation. Mutation: dropping `FOR UPDATE` from
    `unapproved_scenario` (or not embedding it in `create_scenario_delivery_segment`'s statement) —
    the insert does not block, reads `draft` and commits a segment into a calculation approved a
    moment later.
    """
    state = _committed_draft_scenario(engine)
    with Session(bind=engine) as check:
        before = _segment_count_of(check, state["draft_id"])

    outcome = _race_a_segment_insert_against_the_approval(committing_client, engine, state)

    assert "error" not in outcome, outcome.get("error")
    assert outcome["approval"].status_code == 200, outcome["approval"].text
    assert outcome["blocked"], "the segment insert never waited for a lock"
    approved = _scenario_is_approved(engine, state["draft_id"])
    with Session(bind=engine) as check:
        after = _segment_count_of(check, state["draft_id"])
    assert not (approved and after > before), (
        f"approved={approved} and the segment count went {before} → {after}: a row landed in a "
        "calculation that was already being approved"
    )
    assert approved, "the approval itself failed — the pair above is satisfied vacuously"
    assert "rejected" in outcome, "the insert must have been refused, not merely silent"
    assert isinstance(outcome["rejected"], ScenarioDeliverySegmentFrozen)
