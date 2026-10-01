import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app.core.identity import Permission
from app.models import ExchangeRate, Scenario, ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    as_caller,
    caller_holding,
    make_project,
)


def _payload(**overrides: object) -> dict[str, object]:
    return {
        "source_currency": "EUR",
        "target_currency": "USD",
        "effective_from": "2026-01-01",
        "effective_to": "2026-01-31",
        "rate": "1.2500000000",
        "source": "Finance entry",
        **overrides,
    }


def test_k_05_postgres_rejects_overlap_but_accepts_other_pairs_and_adjacent_windows(
    client, db_session
) -> None:
    headers = as_caller(IN_SCOPE_USER)
    assert (
        client.post("/catalog/exchange-rates", json=_payload(), headers=headers).status_code == 201
    )
    adjacent = client.post(
        "/catalog/exchange-rates",
        json=_payload(effective_from="2026-02-01", effective_to=None),
        headers=headers,
    )
    other_pair = client.post(
        "/catalog/exchange-rates",
        json=_payload(source_currency="GBP", target_currency="USD"),
        headers=headers,
    )
    overlap = client.post("/catalog/exchange-rates", json=_payload(), headers=headers)

    assert adjacent.status_code == 201
    assert other_pair.status_code == 201
    assert overlap.status_code == 409


def test_k_06_rate_overrides_require_project_scope(client, db_session) -> None:
    project = make_project(db_session, name="FX scoped", accessible_to=(OUT_OF_SCOPE_USER,))
    body = _payload(project_id=str(project.id))

    response = client.post("/catalog/exchange-rates", json=body, headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 404
    assert db_session.query(ExchangeRate).count() == 0


def test_k_06_rate_api_requires_catalogue_permission(client) -> None:
    with caller_holding(Permission.PROJECT_READ):
        response = client.get("/catalog/exchange-rates", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 403


def test_k_07_approved_scenario_rejects_rate_override(client, db_session) -> None:
    project = make_project(db_session, name="FX approved", accessible_to=(IN_SCOPE_USER,))
    scenario = Scenario(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Approved",
        status=ScenarioStatus.APPROVED,
        start_date=date(2026, 1, 1),
    )
    db_session.add(scenario)
    db_session.flush()

    response = client.post(
        "/catalog/exchange-rates",
        json=_payload(project_id=str(project.id), scenario_id=str(scenario.id)),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 409
    assert db_session.query(ExchangeRate).count() == 0


def test_effective_date_exclusion_is_a_database_constraint(db_session) -> None:
    first = ExchangeRate(
        id=uuid.uuid4(),
        source_currency="EUR",
        target_currency="USD",
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 1, 31),
        rate=Decimal("1.2"),
        source="first",
    )
    second = ExchangeRate(
        id=uuid.uuid4(),
        source_currency="EUR",
        target_currency="USD",
        effective_from=date(2026, 1, 31),
        effective_to=date(2026, 2, 28),
        rate=Decimal("1.3"),
        source="overlapping",
    )
    db_session.add_all([first, second])

    try:
        db_session.flush()
    except IntegrityError:
        db_session.rollback()
    else:
        raise AssertionError("PostgreSQL accepted overlapping exchange-rate windows.")
