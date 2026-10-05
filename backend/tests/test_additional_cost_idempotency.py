"""SC-5-13 K-06: caller-scoped, durable idempotency for additional-cost creation."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.models import AdditionalCost, AdditionalCostCreateIdempotency
from tests.conftest import (
    IN_SCOPE_USER,
    additional_cost_payload,
    additional_costs_path,
    as_caller,
    caller_holding,
    make_cost_category,
    make_project,
    make_scenario,
)


def _address(session: Session, suffix: str = "") -> tuple[str, uuid.UUID]:
    project = make_project(session, name=f"Idempotency{suffix}", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Draft")
    category = make_cost_category(session, name=f"Licences{suffix}")
    return additional_costs_path(project.id, scenario.id), category.id


def test_sc_5_13_06_same_key_replay_returns_one_persisted_cost(
    client: TestClient, db_session: Session
) -> None:
    path, category_id = _address(db_session)
    key = str(uuid.uuid4())
    headers = as_caller(IN_SCOPE_USER) | {"Idempotency-Key": key}
    payload = additional_cost_payload(category_id)

    first = client.post(path, json=payload, headers=headers)
    second = client.post(path, json=payload, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json() == first.json()
    assert db_session.scalar(sa.select(sa.func.count()).select_from(AdditionalCost)) == 1
    assert (
        db_session.scalar(sa.select(sa.func.count()).select_from(AdditionalCostCreateIdempotency))
        == 1
    )


def test_sc_5_13_06_concurrent_same_key_creates_persist_one_cost(
    committing_client: TestClient, engine: sa.Engine
) -> None:
    with Session(engine) as session:
        path, category_id = _address(session, "Concurrent")
        session.commit()
    key = str(uuid.uuid4())
    headers = as_caller(IN_SCOPE_USER) | {"Idempotency-Key": key}
    payload = additional_cost_payload(category_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: committing_client.post(path, json=payload, headers=headers), range(2)
            )
        )
    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()
    with Session(engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(AdditionalCost)) == 1


def test_sc_5_13_06_same_key_different_payload_is_409_without_write(
    client: TestClient, db_session: Session
) -> None:
    path, category_id = _address(db_session, "Mismatch")
    key = str(uuid.uuid4())
    headers = as_caller(IN_SCOPE_USER) | {"Idempotency-Key": key}
    payload = additional_cost_payload(category_id)
    assert client.post(path, json=payload, headers=headers).status_code == 201

    changed = client.post(path, json=payload | {"amount": "1201.0000"}, headers=headers)

    assert changed.status_code == 409
    assert db_session.scalar(sa.select(sa.func.count()).select_from(AdditionalCost)) == 1


def test_sc_5_13_06_key_outcome_lasts_while_cost_exists(
    client: TestClient, db_session: Session
) -> None:
    path, category_id = _address(db_session, "Retention")
    key = str(uuid.uuid4())
    headers = as_caller(IN_SCOPE_USER) | {"Idempotency-Key": key}
    payload = additional_cost_payload(category_id)
    first = client.post(path, json=payload, headers=headers)
    db_session.expire_all()

    replay = client.post(path, json=payload, headers=headers)

    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert db_session.scalar(sa.select(sa.func.count()).select_from(AdditionalCost)) == 1


def test_sc_5_13_06_missing_key_keeps_legacy_create_behavior(
    client: TestClient, db_session: Session
) -> None:
    path, category_id = _address(db_session, "Legacy")
    headers = as_caller(IN_SCOPE_USER)
    payload = additional_cost_payload(category_id)

    first = client.post(path, json=payload, headers=headers)
    second = client.post(path, json=payload, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert db_session.scalar(sa.select(sa.func.count()).select_from(AdditionalCost)) == 2


def test_sc_5_13_07_replay_rechecks_current_write_permission(
    client: TestClient, db_session: Session
) -> None:
    path, category_id = _address(db_session, "Permission")
    key = str(uuid.uuid4())
    headers = as_caller(IN_SCOPE_USER) | {"Idempotency-Key": key}
    payload = additional_cost_payload(category_id)
    with caller_holding(Permission.STAFFING_WRITE):
        created = client.post(path, json=payload, headers=headers)
    assert created.status_code == 201

    with caller_holding():
        denied = client.post(path, json=payload, headers=headers)

    assert denied.status_code == 403
    assert db_session.scalar(sa.select(sa.func.count()).select_from(AdditionalCost)) == 1
