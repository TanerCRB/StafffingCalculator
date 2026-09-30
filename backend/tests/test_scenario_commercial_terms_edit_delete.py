"""SC-4-08 focused end-to-end proofs for replacement, readable inputs, and deletion."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.data.commercial_terms import DETAIL_TABLE_BY_MODEL
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    caller_holding,
    commercial_terms_path,
    make_project,
    make_scenario,
    make_scenario_delivery_segment,
    outcome_payload,
)


def test_k_01_story_points_full_replacement_updates_readable_rule_and_revenue(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="SC-4-08 edit", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Story points")
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    created = client.post(path, json={
        "model_type": "story_points", "price_per_point": "100.0000",
        "accepted_points": 10, "currency": "PLN",
    }, headers=headers)
    assert created.status_code == 201, created.text
    initial = client.get(path, headers=headers).json()
    assert initial["commercial_terms"]["price_per_point"] == "100.0000"
    assert initial["commercial_terms"]["accepted_points"] == 10
    marker = initial["commercial_terms"]["updated_at"]

    edited = client.patch(path, json={
        "model_type": "story_points", "price_per_point": "125.0000",
        "accepted_points": 12, "currency": "PLN", "updated_at": marker,
    }, headers=headers)
    assert edited.status_code == 200, edited.text
    terms = edited.json()["commercial_terms"]
    assert (terms["price_per_point"], terms["accepted_points"]) == ("125.0000", 12)
    assert terms["updated_at"] != marker
    assert edited.json()["revenue"]["amount"] == "1500.00"


def test_k_05_current_marker_delete_returns_204_and_get_reports_no_terms(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="SC-4-08 delete", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="T&M")
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    created = client.post(path, json={"model_type": "time_and_material"}, headers=headers)
    assert created.status_code == 201, created.text
    marker = client.get(path, headers=headers).json()["commercial_terms"]["updated_at"]

    deleted = client.request("DELETE", path, json={"updated_at": marker}, headers=headers)

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(path, headers=headers).json()["revenue"]["state"] == "no_commercial_terms"
    recreated = client.post(path, json={"model_type": "time_and_material"}, headers=headers)
    assert recreated.status_code == 201, recreated.text


def test_k_05_stale_delete_marker_is_a_409_and_leaves_the_rule(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="SC-4-08 stale delete", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Story points")
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    client.post(path, json={
        "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
        "currency": "PLN",
    }, headers=headers)
    old_marker = client.get(path, headers=headers).json()["commercial_terms"]["updated_at"]
    client.patch(path, json={
        "model_type": "story_points", "price_per_point": "120", "accepted_points": 2,
        "currency": "PLN", "updated_at": old_marker,
    }, headers=headers)

    refused = client.request("DELETE", path, json={"updated_at": old_marker}, headers=headers)

    assert refused.status_code == 409
    assert "concurrency marker" in refused.json()["detail"]
    assert client.get(path, headers=headers).json()["commercial_terms"] is not None


def test_k_04_out_of_scope_delete_is_the_same_404_as_missing_scenario(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="SC-4-08 foreign", accessible_to=(OUT_OF_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Foreign")
    path = commercial_terms_path(project.id, scenario.id)
    foreign_headers = as_caller(OUT_OF_SCOPE_USER)
    created = client.post(path, json={
        "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
        "currency": "PLN",
    }, headers=foreign_headers)
    assert created.status_code == 201
    marker = created.json()["commercial_terms"]["updated_at"]
    foreign = client.request(
        "DELETE", path, json={"updated_at": marker}, headers=as_caller(IN_SCOPE_USER)
    )
    missing = client.request(
        "DELETE",
        commercial_terms_path(project.id, uuid.UUID(int=0)),
        json={"updated_at": marker},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json())


def test_k_01_outcome_full_replacement_updates_revenue(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="SC-4-08 outcome", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Outcome")
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    created = client.post(path, json=outcome_payload(), headers=headers)
    assert created.status_code == 201, created.text
    marker = created.json()["commercial_terms"]["updated_at"]
    replacement = outcome_payload(fixed_fee="25000", success_bonus="5000")
    replacement["updated_at"] = marker

    edited = client.patch(path, json=replacement, headers=headers)

    assert edited.status_code == 200, edited.text
    assert edited.json()["revenue"]["amount"] == "25000.00"
    assert edited.json()["commercial_terms"]["updated_at"] != marker


def test_k_01_fixed_price_full_replacement_updates_stored_input_and_revenue(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="SC-4-08 fixed", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Fixed Price")
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    created = client.post(path, json={
        "model_type": "fixed_price", "agreed_price": "1200.0000", "currency": "PLN",
    }, headers=headers)
    assert created.status_code == 201, created.text
    marker = created.json()["commercial_terms"]["updated_at"]
    edited = client.patch(path, json={
        "model_type": "fixed_price", "agreed_price": "1400.0000", "currency": "PLN",
        "updated_at": marker,
    }, headers=headers)
    assert edited.status_code == 200, edited.text
    assert edited.json()["commercial_terms"]["agreed_price"] == "1400.0000"
    assert edited.json()["revenue"]["amount"] == "1400.00"


@pytest.mark.parametrize(
    ("model_type", "body"),
    [
        ("time_and_material", {"model_type": "time_and_material"}),
        ("story_points", {
            "model_type": "story_points", "price_per_point": "100", "accepted_points": 1,
            "currency": "PLN",
        }),
        ("outcome_based", outcome_payload()),
        ("fixed_price", {
            "model_type": "fixed_price", "agreed_price": "100", "currency": "PLN",
        }),
    ],
)
def test_k_05_delete_removes_aggregate_and_the_model_detail(
    client: TestClient, db_session: Session, model_type: str, body: dict[str, object]
) -> None:
    project = make_project(
        db_session, name=f"SC-4-08 delete {model_type}", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, project, name=model_type)
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    created = client.post(path, json=body, headers=headers)
    assert created.status_code == 201, created.text
    marker = created.json()["commercial_terms"]["updated_at"]
    response = client.request("DELETE", path, json={"updated_at": marker}, headers=headers)
    assert response.status_code == 204, response.text
    assert response.content == b""
    table = DETAIL_TABLE_BY_MODEL[model_type]
    assert db_session.execute(
        sa.select(sa.func.count()).select_from(table).where(
            table.c.commercial_terms_id == created.json()["commercial_terms"]["id"]
        )
    ).scalar_one() == 0
    assert db_session.execute(sa.text(
        "SELECT count(*) FROM commercial_terms WHERE scenario_id=:id"
    ), {"id": scenario.id}).scalar_one() == 0


def test_k_02_bad_full_replacement_and_model_switch_are_422_without_data_change(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="SC-4-08 invalid", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Story points")
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    client.post(path, json={
        "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
        "currency": "PLN",
    }, headers=headers)
    before = db_session.execute(sa.text(
        "SELECT price_per_point, accepted_points FROM story_points_terms"
    )).one()
    marker = client.get(path, headers=headers).json()["commercial_terms"]["updated_at"]

    incomplete = client.patch(path, json={
        "model_type": "story_points", "price_per_point": "0", "accepted_points": 3,
        "currency": "PLN", "updated_at": marker,
    }, headers=headers)
    switched = client.patch(path, json={
        "model_type": "fixed_price", "agreed_price": "100", "currency": "PLN",
        "updated_at": marker,
    }, headers=headers)

    assert incomplete.status_code == 422
    assert switched.status_code == 422, switched.text
    assert db_session.execute(sa.text(
        "SELECT price_per_point, accepted_points FROM story_points_terms"
    )).one() == before


def test_k_02_invalid_outcome_full_replacements_leave_the_detail_unchanged(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(
        db_session, name="SC-4-08 invalid outcome", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, project, name="Outcome")
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    created = client.post(path, json=outcome_payload(), headers=headers)
    marker = created.json()["commercial_terms"]["updated_at"]
    before = client.get(path, headers=headers).json()["commercial_terms"]["outcome_terms"]
    invalid_bodies = (
        outcome_payload(probabilities=("10", "20", "30", "30")),
        outcome_payload(probabilities=("25.003", "25", "25", "24.997")),
        outcome_payload(revenue_min="2", revenue_max="1"),
        outcome_payload(fixed_fee="-1"),
        {**outcome_payload(), "fixed_fee": None},
    )
    for invalid in invalid_bodies:
        invalid["updated_at"] = marker
        refused = client.patch(path, json=invalid, headers=headers)
        assert refused.status_code == 422, (invalid, refused.text)
        after = client.get(path, headers=headers).json()["commercial_terms"]["outcome_terms"]
        assert after == before


def test_k_01_time_and_material_edit_is_refused_after_scope_resolution(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="SC-4-08 T&M edit", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="T&M")
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    created = client.post(path, json={"model_type": "time_and_material"}, headers=headers)
    marker = created.json()["commercial_terms"]["updated_at"]
    refused = client.patch(path, json={"model_type": "time_and_material", "updated_at": marker},
                           headers=headers)
    assert refused.status_code == 409
    assert "Time & Material" in refused.json()["detail"]


def test_k_03_stale_story_points_marker_is_a_named_409(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="SC-4-08 stale", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Story points")
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    client.post(path, json={
        "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
        "currency": "PLN",
    }, headers=headers)
    marker = client.get(path, headers=headers).json()["commercial_terms"]["updated_at"]
    replacement = {
        "model_type": "story_points", "price_per_point": "120", "accepted_points": 3,
        "currency": "PLN", "updated_at": marker,
    }
    assert client.patch(path, json=replacement, headers=headers).status_code == 200
    stale = client.patch(path, json=replacement, headers=headers)
    assert stale.status_code == 409
    assert "concurrency marker" in stale.json()["detail"]


def test_k_03_an_approval_committed_before_full_replacement_refuses_the_edit(
    committing_client: TestClient, engine: Engine
) -> None:
    with Session(bind=engine, expire_on_commit=False) as setup:
        project = make_project(setup, name="SC-4-08 approved", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Story points")
        setup.commit()
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    committing_client.post(path, json={
        "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
        "currency": "PLN",
    }, headers=headers)
    marker = committing_client.get(path, headers=headers).json()["commercial_terms"]["updated_at"]
    fired: list[str] = []

    def approve_first(connection: Any, cursor: Any, statement: str, parameters: Any,
                      context: Any, many: bool) -> None:
        lowered = statement.lower()
        if fired or "scenarios" not in lowered or "for update" not in lowered:
            return
        fired.append(statement)
        with engine.begin() as competing:
            competing.execute(sa.text(
                "UPDATE scenarios SET status='approved' WHERE id=:id"
            ), {"id": scenario.id})

    event.listen(Engine, "before_cursor_execute", approve_first)
    try:
        refused = committing_client.patch(path, json={
            "model_type": "story_points", "price_per_point": "120", "accepted_points": 3,
            "currency": "PLN", "updated_at": marker,
        }, headers=headers)
    finally:
        event.remove(Engine, "before_cursor_execute", approve_first)

    assert fired
    assert refused.status_code == 409
    assert "approved" in refused.json()["detail"]


def test_k_03_an_approval_committed_before_delete_refuses_and_keeps_the_rule(
    committing_client: TestClient, engine: Engine
) -> None:
    with Session(bind=engine, expire_on_commit=False) as setup:
        project = make_project(
            setup, name="SC-4-08 approved delete", accessible_to=(IN_SCOPE_USER,)
        )
        scenario = make_scenario(setup, project, name="Story points")
        setup.commit()
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    created = committing_client.post(path, json={
        "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
        "currency": "PLN",
    }, headers=headers)
    marker = created.json()["commercial_terms"]["updated_at"]
    fired: list[str] = []

    def approve_first(connection: Any, cursor: Any, statement: str, parameters: Any,
                      context: Any, many: bool) -> None:
        lowered = statement.lower()
        if fired or "scenarios" not in lowered or "for update" not in lowered:
            return
        fired.append(statement)
        with engine.begin() as competing:
            competing.execute(sa.text(
                "UPDATE scenarios SET status='approved' WHERE id=:id"
            ), {"id": scenario.id})

    event.listen(Engine, "before_cursor_execute", approve_first)
    try:
        refused = committing_client.request(
            "DELETE", path, json={"updated_at": marker}, headers=headers
        )
    finally:
        event.remove(Engine, "before_cursor_execute", approve_first)
    assert fired
    assert refused.status_code == 409
    assert "approved" in refused.json()["detail"]
    still_there = committing_client.get(path, headers=headers)
    assert still_there.status_code == 200
    assert still_there.json()["commercial_terms"] is not None


def test_r_01_a_segment_rule_committed_after_delete_preflight_prevents_partial_delete(
    committing_client: TestClient, engine: Engine
) -> None:
    with Session(bind=engine, expire_on_commit=False) as setup:
        project = make_project(
            setup, name="SC-4-08 delete ambiguity", accessible_to=(IN_SCOPE_USER,)
        )
        scenario = make_scenario(setup, project, name="Story points")
        segment = make_scenario_delivery_segment(setup, scenario)
        setup.commit()
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    created = committing_client.post(path, json={
        "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
        "currency": "PLN",
    }, headers=headers)
    marker = created.json()["commercial_terms"]["updated_at"]
    fired: list[str] = []
    scoped_rule_id = uuid.uuid4()

    def add_scoped_rule(connection: Any, cursor: Any, statement: str, parameters: Any,
                        context: Any, many: bool) -> None:
        lowered = statement.lower()
        if fired or "scenarios" not in lowered or "for update" not in lowered:
            return
        fired.append(statement)
        with engine.begin() as competing:
            competing.execute(sa.text(
                "INSERT INTO commercial_terms (id, scenario_id, model_type, scope_ref) "
                "VALUES (:rule, :scenario, 'time_and_material', :segment)"
            ), {"rule": scoped_rule_id, "scenario": scenario.id, "segment": segment.id})
            competing.execute(sa.text(
                "INSERT INTO tm_terms (commercial_terms_id, model_type) "
                "VALUES (:rule, 'time_and_material')"
            ), {"rule": scoped_rule_id})

    event.listen(Engine, "before_cursor_execute", add_scoped_rule)
    try:
        response = committing_client.request(
            "DELETE", path, json={"updated_at": marker}, headers=headers
        )
    finally:
        event.remove(Engine, "before_cursor_execute", add_scoped_rule)

    assert fired, "the scoped rule was not committed in the delete statement window"
    assert response.status_code == 409, response.text
    assert "more than one set of commercial terms" in response.json()["detail"]
    with engine.connect() as connection:
        rules = connection.execute(sa.text(
            "SELECT count(*) FROM commercial_terms WHERE scenario_id=:id"
        ), {"id": scenario.id}).scalar_one()
        story_details = connection.execute(sa.text(
            "SELECT count(*) FROM story_points_terms WHERE commercial_terms_id=:id"
        ), {"id": uuid.UUID(created.json()["commercial_terms"]["id"])}).scalar_one()
        scoped_details = connection.execute(sa.text(
            "SELECT count(*) FROM tm_terms WHERE commercial_terms_id=:id"
        ), {"id": scoped_rule_id}).scalar_one()
    assert (rules, story_details, scoped_details) == (2, 1, 1)


def test_r_01_delete_waits_for_locked_creator_then_sees_its_committed_scoped_rule(
    committing_client: TestClient, engine: Engine
) -> None:
    with Session(bind=engine, expire_on_commit=False) as setup:
        project = make_project(
            setup, name="SC-4-08 delete locked creator", accessible_to=(IN_SCOPE_USER,)
        )
        scenario = make_scenario(setup, project, name="Story points")
        segment = make_scenario_delivery_segment(setup, scenario)
        setup.commit()
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    created = committing_client.post(path, json={
        "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
        "currency": "PLN",
    }, headers=headers)
    marker = created.json()["commercial_terms"]["updated_at"]
    creator_rule_id = uuid.uuid4()
    delete_waiting_for_scenario = Event()
    fired: list[str] = []

    def observe_delete_lock(connection: Any, cursor: Any, statement: str, parameters: Any,
                            context: Any, many: bool) -> None:
        lowered = statement.lower()
        if "scenarios" not in lowered or "for update" not in lowered:
            return
        if fired:
            return
        fired.append(statement)
        delete_waiting_for_scenario.set()

    creator = engine.connect()
    creator_transaction = creator.begin()
    try:
        creator.execute(sa.text(
            "SELECT id FROM scenarios WHERE id=:scenario FOR UPDATE"
        ), {"scenario": scenario.id})
        event.listen(Engine, "before_cursor_execute", observe_delete_lock)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending_delete = pool.submit(
                committing_client.request, "DELETE", path,
                json={"updated_at": marker}, headers=headers,
            )
            lock_was_reached = delete_waiting_for_scenario.wait(timeout=5)
            creator.execute(sa.text(
                "INSERT INTO commercial_terms (id, scenario_id, model_type, scope_ref) "
                "VALUES (:rule, :scenario, 'time_and_material', :segment)"
            ), {"rule": creator_rule_id, "scenario": scenario.id, "segment": segment.id})
            creator.execute(sa.text(
                "INSERT INTO tm_terms (commercial_terms_id, model_type) "
                "VALUES (:rule, 'time_and_material')"
            ), {"rule": creator_rule_id})
            creator_transaction.commit()
            refused = pending_delete.result(timeout=10)
    finally:
        event.remove(Engine, "before_cursor_execute", observe_delete_lock)
        if creator_transaction.is_active:
            creator_transaction.rollback()
        creator.close()

    assert fired, "DELETE did not attempt the preflight scenario lock"
    assert lock_was_reached, "DELETE did not reach the scenario lock while the creator held it"
    assert refused.status_code == 409, refused.text
    assert "more than one set of commercial terms" in refused.json()["detail"]
    with engine.connect() as connection:
        rules = connection.execute(sa.text(
            "SELECT count(*) FROM commercial_terms WHERE scenario_id=:id"
        ), {"id": scenario.id}).scalar_one()
        story_details = connection.execute(sa.text(
            "SELECT count(*) FROM story_points_terms WHERE commercial_terms_id=:id"
        ), {"id": created.json()["commercial_terms"]["id"]}).scalar_one()
        scoped_details = connection.execute(sa.text(
            "SELECT count(*) FROM tm_terms WHERE commercial_terms_id=:id"
        ), {"id": creator_rule_id}).scalar_one()
    assert (rules, story_details, scoped_details) == (2, 1, 1)


@pytest.mark.parametrize("operation", ["edit", "delete"])
def test_b_01_revoked_project_access_after_preflight_blocks_guarded_write(
    committing_client: TestClient, engine: Engine, operation: str
) -> None:
    with Session(bind=engine, expire_on_commit=False) as setup:
        project = make_project(
            setup, name=f"SC-4-08 revoked access {operation}", accessible_to=(IN_SCOPE_USER,)
        )
        scenario = make_scenario(setup, project, name="Story points")
        setup.commit()
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    created = committing_client.post(path, json={
        "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
        "currency": "PLN",
    }, headers=headers)
    marker = created.json()["commercial_terms"]["updated_at"]
    fired: list[str] = []

    def revoke_after_preflight(connection: Any, cursor: Any, statement: str, parameters: Any,
                               context: Any, many: bool) -> None:
        lowered = statement.lower()
        trigger = (
            "update commercial_terms set"
            if operation == "edit"
            else "delete from commercial_terms"
        )
        if fired or trigger not in lowered:
            return
        fired.append(statement)
        with engine.begin() as competing:
            competing.execute(sa.text(
                "DELETE FROM project_access WHERE user_id=:user AND project_id=:project"
            ), {"user": IN_SCOPE_USER, "project": project.id})

    event.listen(Engine, "before_cursor_execute", revoke_after_preflight)
    try:
        if operation == "edit":
            response = committing_client.patch(path, json={
                "model_type": "story_points", "price_per_point": "150",
                "accepted_points": 3, "currency": "PLN", "updated_at": marker,
            }, headers=headers)
        else:
            response = committing_client.request(
                "DELETE", path, json={"updated_at": marker}, headers=headers
            )
    finally:
        event.remove(Engine, "before_cursor_execute", revoke_after_preflight)

    assert fired, "membership was not revoked between scope preflight and guarded DML"
    assert response.status_code == 404, response.text
    with engine.connect() as connection:
        aggregate_count = connection.execute(sa.text(
            "SELECT count(*) FROM commercial_terms WHERE scenario_id=:scenario"
        ), {"scenario": scenario.id}).scalar_one()
        details = connection.execute(sa.text(
            "SELECT price_per_point, accepted_points FROM story_points_terms "
            "WHERE commercial_terms_id=:rule"
        ), {"rule": created.json()["commercial_terms"]["id"]}).one_or_none()
    assert aggregate_count == 1
    assert details == (100, 2)


def test_k_03_a_competing_edit_wins_and_the_stale_writer_gets_409(
    committing_client: TestClient, engine: Engine
) -> None:
    with Session(bind=engine, expire_on_commit=False) as setup:
        project = make_project(setup, name="SC-4-08 race", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Story points")
        setup.commit()
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    committing_client.post(path, json={
        "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
        "currency": "PLN",
    }, headers=headers)
    marker = committing_client.get(path, headers=headers).json()["commercial_terms"]["updated_at"]
    fired: list[str] = []
    competitor_rowcounts: list[tuple[int, int]] = []

    def competitor(connection: Any, cursor: Any, statement: str, parameters: Any,
                   context: Any, many: bool) -> None:
        if fired or "update commercial_terms set" not in statement.lower():
            return
        fired.append(statement)
        with engine.begin() as competing:
            detail_update = competing.execute(sa.text(
                "UPDATE story_points_terms SET price_per_point=150 WHERE commercial_terms_id="
                "(SELECT id FROM commercial_terms WHERE scenario_id=:id)"
            ), {"id": scenario.id})
            marker_update = competing.execute(sa.text(
                "UPDATE commercial_terms SET updated_at=clock_timestamp() WHERE scenario_id=:id"
            ), {"id": scenario.id})
            competitor_rowcounts.append((detail_update.rowcount, marker_update.rowcount))

    event.listen(Engine, "before_cursor_execute", competitor)
    try:
        response = committing_client.patch(path, json={
            "model_type": "story_points", "price_per_point": "120", "accepted_points": 3,
            "currency": "PLN", "updated_at": marker,
        }, headers=headers)
    finally:
        event.remove(Engine, "before_cursor_execute", competitor)
    assert fired
    assert competitor_rowcounts == [(1, 1)], competitor_rowcounts
    assert response.status_code == 409
    assert "concurrency marker" in response.json()["detail"]


def test_k_03_two_api_writers_using_one_marker_have_exactly_one_winner(
    committing_client: TestClient, engine: Engine
) -> None:
    with Session(bind=engine, expire_on_commit=False) as setup:
        project = make_project(setup, name="SC-4-08 two writers", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Story points")
        setup.commit()
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    committing_client.post(path, json={
        "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
        "currency": "PLN",
    }, headers=headers)
    marker = committing_client.get(path, headers=headers).json()["commercial_terms"]["updated_at"]
    gate = Barrier(2)

    def write(price: str):
        gate.wait(timeout=10)
        return committing_client.patch(path, json={
            "model_type": "story_points", "price_per_point": price, "accepted_points": 3,
            "currency": "PLN", "updated_at": marker,
        }, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(write, ("120", "130")))
    assert sorted(response.status_code for response in responses) == [200, 409]


def test_k_05_copy_keeps_edited_values_after_later_source_edit(
    client: TestClient, db_session: Session
) -> None:
    from tests.test_scenario_duplication import duplicate_path

    project = make_project(db_session, name="SC-4-08 copy", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Source")
    path = commercial_terms_path(project.id, scenario.id)
    headers = as_caller(IN_SCOPE_USER)
    created = client.post(path, json={
        "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
        "currency": "PLN",
    }, headers=headers)
    marker = created.json()["commercial_terms"]["updated_at"]
    edited = client.patch(path, json={
        "model_type": "story_points", "price_per_point": "125", "accepted_points": 12,
        "currency": "PLN", "updated_at": marker,
    }, headers=headers)
    assert edited.status_code == 200

    copied = client.post(duplicate_path(project.id, scenario.id), headers=headers)
    assert copied.status_code == 201, copied.text
    copy_id = uuid.UUID(copied.json()["id"])
    copy_path = commercial_terms_path(project.id, copy_id)
    copy_before = client.get(copy_path, headers=headers).json()["commercial_terms"]
    source_marker = client.get(path, headers=headers).json()["commercial_terms"]["updated_at"]
    source_edit = client.patch(path, json={
        "model_type": "story_points", "price_per_point": "150", "accepted_points": 8,
        "currency": "PLN", "updated_at": source_marker,
    }, headers=headers)
    assert source_edit.status_code == 200, source_edit.text

    copy_after = client.get(copy_path, headers=headers).json()["commercial_terms"]
    assert copy_after["id"] == copy_before["id"]
    assert (copy_after["price_per_point"], copy_after["accepted_points"]) == ("125.0000", 12)
    assert (source_edit.json()["commercial_terms"]["price_per_point"],
            source_edit.json()["commercial_terms"]["accepted_points"]) == ("150.0000", 8)


def test_k_04_edit_and_delete_require_commercial_write(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(db_session, name="SC-4-08 permission", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Story points")
    path = commercial_terms_path(project.id, scenario.id)
    with caller_holding(Permission.COMMERCIAL_WRITE):
        created = client.post(path, json={
            "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
            "currency": "PLN",
        })
    assert created.status_code == 201
    marker = created.json()["commercial_terms"]["updated_at"]
    body = {
        "model_type": "story_points", "price_per_point": "120", "accepted_points": 3,
        "currency": "PLN", "updated_at": marker,
    }
    with caller_holding(*(set(Permission) - {Permission.COMMERCIAL_WRITE})):
        edited = client.patch(path, json=body)
        deleted = client.request("DELETE", path, json={"updated_at": marker})
    assert edited.status_code == deleted.status_code == 403


def test_k_04_granting_commercial_write_reverses_edit_and_delete_denials(
    client: TestClient, db_session: Session
) -> None:
    project = make_project(
        db_session, name="SC-4-08 permission contrast", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, project, name="Story points")
    path = commercial_terms_path(project.id, scenario.id)
    body = {
        "model_type": "story_points", "price_per_point": "100", "accepted_points": 2,
        "currency": "PLN",
    }
    with caller_holding(Permission.COMMERCIAL_WRITE):
        created = client.post(path, json=body)
    assert created.status_code == 201, created.text
    marker = created.json()["commercial_terms"]["updated_at"]
    replacement = {
        "model_type": "story_points", "price_per_point": "120", "accepted_points": 3,
        "currency": "PLN", "updated_at": marker,
    }

    with caller_holding(*(set(Permission) - {Permission.COMMERCIAL_WRITE})):
        denied_edit = client.patch(path, json=replacement)
    assert denied_edit.status_code == 403

    with caller_holding(Permission.COMMERCIAL_WRITE):
        allowed_edit = client.patch(path, json=replacement)
    assert allowed_edit.status_code == 200, allowed_edit.text
    assert allowed_edit.json()["commercial_terms"]["accepted_points"] == 3
    current_marker = allowed_edit.json()["commercial_terms"]["updated_at"]

    with caller_holding(*(set(Permission) - {Permission.COMMERCIAL_WRITE})):
        denied_delete = client.request("DELETE", path, json={"updated_at": current_marker})
    assert denied_delete.status_code == 403

    with caller_holding(Permission.COMMERCIAL_WRITE):
        allowed_delete = client.request("DELETE", path, json={"updated_at": current_marker})
    assert allowed_delete.status_code == 204
