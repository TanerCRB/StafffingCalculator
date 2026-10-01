import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api.schemas.commercial_terms import (
    PriceAdjustmentCreateRequest,
    PriceAdjustmentEditRequest,
)
from app.core.identity import CallerIdentity, Permission
from app.data.commercial_terms import commercial_terms_for_caller
from app.domain.revenue_fixed_price import AgreedPrice, fixed_price_revenue
from app.models import CommercialTerms, FixedPriceAdjustment, FixedPriceTerms, ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    caller_holding,
    commercial_terms_path,
    count_snapshot_rows,
    make_fixed_price_terms,
    make_project,
    make_scenario,
)


def adjustment_path(project_id, scenario_id):
    return f"{commercial_terms_path(project_id, scenario_id)}/price-adjustments"


def test_adjustment_amount_is_nonnegative_and_kind_defines_delta_sign() -> None:
    with pytest.raises(ValidationError):
        PriceAdjustmentCreateRequest(
            request_id=uuid.uuid4(), kind="increase", amount="-1", currency="PLN"
        )
    assert PriceAdjustmentCreateRequest(
        request_id=uuid.uuid4(), kind="increase", amount="0", currency="PLN"
    ).amount == 0
    increase = fixed_price_revenue(
        AgreedPrice(Decimal("100"), "PLN", (Decimal("10"),)), scenario_currency="PLN"
    )
    decrease = fixed_price_revenue(
        AgreedPrice(Decimal("100"), "PLN", (Decimal("-10"),)), scenario_currency="PLN"
    )
    assert increase.revenue == Decimal("110.00")
    assert decrease.revenue == Decimal("90.00")


def test_adjustment_edit_requires_a_non_null_field() -> None:
    with pytest.raises(ValidationError):
        PriceAdjustmentEditRequest()
    with pytest.raises(ValidationError):
        PriceAdjustmentEditRequest(amount=None)


def test_fixed_price_revenue_includes_approved_adjustments_only(
    db_session: Session,
) -> None:
    project = make_project(db_session, name="Adjustment revenue", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Adjustment revenue")
    terms = make_fixed_price_terms(
        db_session, scenario, agreed_price=Decimal("100"), currency="PLN"
    )
    for kind, amount, state in (
        ("increase", "10", "approved"),
        ("decrease", "3", "approved"),
        ("increase", "50", "pending"),
        ("decrease", "20", "rejected"),
    ):
        db_session.add(
            FixedPriceAdjustment(
                commercial_terms_id=terms.id,
                kind=kind,
                amount=Decimal(amount),
                currency="PLN",
                status=state,
            )
        )
    db_session.flush()
    caller = CallerIdentity(IN_SCOPE_USER, frozenset({Permission.COMMERCIAL_READ}))
    view = commercial_terms_for_caller(db_session, caller, project.id, scenario.id)
    assert view is not None
    assert view.revenue.revenue == Decimal("107.00")
    assert view.revenue.assumptions_used.price_adjustments == "included"


def test_adjustment_write_permission_currency_decision_and_terminal_lifecycle(
    client, db_session: Session
) -> None:
    project = make_project(db_session, name="Adjustment API", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Adjustment API")
    terms = make_fixed_price_terms(
        db_session, scenario, agreed_price=Decimal("100"), currency="PLN"
    )
    path = adjustment_path(project.id, scenario.id)
    payload = {
        "request_id": str(uuid.uuid4()),
        "kind": "increase",
        "amount": "10",
        "currency": "PLN",
    }

    with caller_holding():
        no_write = client.post(path, json=payload, headers=as_caller(IN_SCOPE_USER))
    with caller_holding(Permission.COMMERCIAL_ADJUSTMENT_APPROVE):
        approval_only_write = client.post(path, json=payload, headers=as_caller(IN_SCOPE_USER))
    assert no_write.status_code == 403
    assert approval_only_write.status_code == 403
    assert db_session.query(FixedPriceAdjustment).count() == 0

    with caller_holding(Permission.COMMERCIAL_WRITE):
        mismatch = client.post(
            path, json={**payload, "currency": "EUR"}, headers=as_caller(IN_SCOPE_USER)
        )
        created = client.post(path, json=payload, headers=as_caller(IN_SCOPE_USER))
        retried = client.post(path, json=payload, headers=as_caller(IN_SCOPE_USER))
        reused_key = client.post(
            path,
            json={**payload, "amount": "11"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert mismatch.status_code == 409
    assert created.status_code == 201, created.text
    assert retried.status_code == 201, retried.text
    assert retried.json() == created.json()
    assert reused_key.status_code == 409
    other_scenario = make_scenario(db_session, project, name="Independent request key")
    make_fixed_price_terms(
        db_session, other_scenario, agreed_price=Decimal("100"), currency="PLN"
    )
    db_session.flush()
    db_session.expire_all()
    with caller_holding(Permission.COMMERCIAL_WRITE):
        independent = client.post(
            adjustment_path(project.id, other_scenario.id),
            json=payload,
            headers=as_caller(IN_SCOPE_USER),
        )
    assert independent.status_code == 201, independent.text
    assert independent.json()["id"] != created.json()["id"]
    assert set(created.json()) == {"id", "status"}
    assert created.json()["status"] == "pending"
    adjustment_id = created.json()["id"]
    with caller_holding(Permission.COMMERCIAL_ADJUSTMENT_APPROVE):
        approval_only_edit = client.patch(
            f"{path}/{adjustment_id}",
            json={"amount": "12"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert approval_only_edit.status_code == 403
    with caller_holding(Permission.COMMERCIAL_WRITE):
        edited = client.patch(
            f"{path}/{adjustment_id}",
            json={"amount": "12"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert edited.status_code == 200, edited.text
    with caller_holding(Permission.COMMERCIAL_WRITE):
        retry_after_edit = client.post(path, json=payload, headers=as_caller(IN_SCOPE_USER))
        changed_replay = client.post(
            path,
            json={**payload, "amount": "12"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert retry_after_edit.status_code == 201, retry_after_edit.text
    assert retry_after_edit.json() == created.json()
    assert changed_replay.status_code == 409
    assert edited.json() == {"id": adjustment_id, "status": "pending"}

    decision_path = f"{path}/{adjustment_id}/decision"
    denied = client.post(
        decision_path, json={"status": "approved"}, headers=as_caller(IN_SCOPE_USER)
    )
    assert denied.status_code == 403
    with caller_holding(Permission.COMMERCIAL_ADJUSTMENT_APPROVE):
        approved = client.post(
            decision_path, json={"status": "approved"}, headers=as_caller(IN_SCOPE_USER)
        )
        terminal_decision = client.post(
            decision_path, json={"status": "rejected"}, headers=as_caller(IN_SCOPE_USER)
        )
    with caller_holding(Permission.COMMERCIAL_WRITE):
        terminal_edit = client.patch(
            f"{path}/{adjustment_id}", json={"amount": "13"}, headers=as_caller(IN_SCOPE_USER)
        )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert set(approved.json()) == {"status"}
    assert terminal_edit.status_code == 409
    assert terminal_decision.status_code == 409

    # A correction is a new proposal; the decided row is preserved.
    with caller_holding(Permission.COMMERCIAL_WRITE):
        correction = client.post(
            path,
            json={**payload, "request_id": str(uuid.uuid4()), "amount": "13"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert correction.status_code == 201, correction.text
    assert correction.json()["status"] == "pending"
    correction_id = correction.json()["id"]
    correction_decision_path = f"{path}/{correction_id}/decision"
    with caller_holding(Permission.COMMERCIAL_ADJUSTMENT_APPROVE):
        rejected = client.post(
            correction_decision_path,
            json={"status": "rejected"},
            headers=as_caller(IN_SCOPE_USER),
        )
        rejected_transition = client.post(
            correction_decision_path,
            json={"status": "approved"},
            headers=as_caller(IN_SCOPE_USER),
        )
    with caller_holding(Permission.COMMERCIAL_WRITE):
        rejected_edit = client.patch(
            f"{path}/{correction_id}", json={"amount": "14"}, headers=as_caller(IN_SCOPE_USER)
        )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected_transition.status_code == 409
    assert rejected_edit.status_code == 409
    with caller_holding(Permission.COMMERCIAL_ADJUSTMENT_APPROVE):
        approval_only_read = client.get(path, headers=as_caller(IN_SCOPE_USER))
    assert approval_only_read.status_code == 403
    with caller_holding(Permission.COMMERCIAL_READ):
        listed = client.get(path, headers=as_caller(IN_SCOPE_USER))
    assert listed.status_code == 200, listed.text
    assert {entry["id"] for entry in listed.json()} == {adjustment_id, correction_id}
    db_session.expire_all()
    old = db_session.get(FixedPriceAdjustment, adjustment_id)
    assert old is not None and old.status == "approved" and old.amount == Decimal("12")
    adjustment_count = db_session.query(FixedPriceAdjustment).filter_by(
        commercial_terms_id=terms.id
    ).count()
    assert adjustment_count == 2


def test_scenario_duplicate_copies_adjustments_as_pending_and_no_snapshot_rows(
    client, db_session: Session
) -> None:
    project = make_project(db_session, name="Adjustment copy", accessible_to=(IN_SCOPE_USER,))
    source = make_scenario(
        db_session, project, name="Approved source", status=ScenarioStatus.APPROVED
    )
    terms = make_fixed_price_terms(db_session, source, agreed_price=Decimal("100"), currency="PLN")
    db_session.add_all(
        [
            FixedPriceAdjustment(
                commercial_terms_id=terms.id,
                kind="increase",
                amount=Decimal("10"),
                currency="PLN",
                status="approved",
            ),
            FixedPriceAdjustment(
                commercial_terms_id=terms.id,
                kind="decrease",
                amount=Decimal("5"),
                currency="PLN",
                status="rejected",
            ),
        ]
    )
    db_session.flush()

    response = client.post(
        f"/projects/{project.id}/scenarios/{source.id}/duplicate",
        headers=as_caller(IN_SCOPE_USER),
    )
    assert response.status_code == 201, response.text
    duplicate_id = uuid.UUID(response.json()["id"])
    duplicate_terms = (
        db_session.query(FixedPriceAdjustment)
        .join(CommercialTerms, FixedPriceAdjustment.commercial_terms_id == CommercialTerms.id)
        .filter(CommercialTerms.scenario_id == duplicate_id)
        .all()
    )
    source_adjustment_ids = {
        row.id
        for row in db_session.query(FixedPriceAdjustment).filter_by(commercial_terms_id=terms.id)
    }
    assert all(row.id not in source_adjustment_ids for row in duplicate_terms)
    assert {(row.kind, row.amount, row.currency, row.status) for row in duplicate_terms} == {
        ("increase", Decimal("10"), "PLN", "pending"),
        ("decrease", Decimal("5"), "PLN", "pending"),
    }
    assert count_snapshot_rows(db_session, duplicate_id) == 0


def test_fixed_price_currency_cannot_change_while_adjustments_use_another_currency(
    client, db_session: Session
) -> None:
    project = make_project(
        db_session, name="Adjustment currency edit", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(db_session, project, name="Adjustment currency edit")
    terms = make_fixed_price_terms(
        db_session, scenario, agreed_price=Decimal("100"), currency="PLN"
    )
    db_session.add(
        FixedPriceAdjustment(
            commercial_terms_id=terms.id,
            kind="increase",
            amount=Decimal("10"),
            currency="PLN",
            status="approved",
        )
    )
    db_session.flush()
    path = commercial_terms_path(project.id, scenario.id)
    current = client.get(path, headers=as_caller(IN_SCOPE_USER))
    assert current.status_code == 200, current.text
    current_terms = current.json()["commercial_terms"]
    response = client.patch(
        path,
        json={
            "model_type": "fixed_price",
            "agreed_price": current_terms["agreed_price"],
            "currency": "EUR",
            "updated_at": current_terms["updated_at"],
        },
        headers=as_caller(IN_SCOPE_USER),
    )
    assert response.status_code == 409, response.text
    fixed_price_terms = db_session.get(FixedPriceTerms, terms.id)
    assert fixed_price_terms is not None and fixed_price_terms.currency == "PLN"

    matching_currency = client.patch(
        path,
        json={
            "model_type": "fixed_price",
            "agreed_price": current_terms["agreed_price"],
            "currency": "PLN",
            "updated_at": current_terms["updated_at"],
        },
        headers=as_caller(IN_SCOPE_USER),
    )
    assert matching_currency.status_code == 200, matching_currency.text


def test_adjustment_create_edit_and_decision_commit_and_can_be_listed(
    committing_client, engine: Engine
) -> None:
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Committed adjustment", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Committed adjustment")
        terms = make_fixed_price_terms(
            setup, scenario, agreed_price=Decimal("100"), currency="PLN"
        )
        setup.commit()

    path = adjustment_path(project.id, scenario.id)
    with caller_holding(Permission.COMMERCIAL_WRITE):
        created = committing_client.post(
            path,
            json={
                "request_id": str(uuid.uuid4()),
                "kind": "increase",
                "amount": "10",
                "currency": "PLN",
            },
            headers=as_caller(IN_SCOPE_USER),
        )
        assert created.status_code == 201, created.text
        assert set(created.json()) == {"id", "status"}
        assert created.json()["status"] == "pending"
        adjustment_id = created.json()["id"]
        edited = committing_client.patch(
            f"{path}/{adjustment_id}",
            json={"amount": "12"},
            headers=as_caller(IN_SCOPE_USER),
        )
    assert edited.status_code == 200, edited.text
    with caller_holding(Permission.COMMERCIAL_ADJUSTMENT_APPROVE):
        decided = committing_client.post(
            f"{path}/{adjustment_id}/decision",
            json={"status": "approved"},
            headers=as_caller(IN_SCOPE_USER),
        )
        hidden_read = committing_client.get(path, headers=as_caller(IN_SCOPE_USER))
    assert decided.status_code == 200, decided.text
    assert decided.json() == {"status": "approved"}
    assert hidden_read.status_code == 403

    with caller_holding(Permission.COMMERCIAL_READ):
        listed = committing_client.get(path, headers=as_caller(IN_SCOPE_USER))
    assert listed.status_code == 200, listed.text
    assert listed.json() == [
        {
            "id": adjustment_id,
            "kind": "increase",
            "amount": "12.0000",
            "currency": "PLN",
            "status": "approved",
        }
    ]
    with Session(bind=engine, expire_on_commit=False, future=True) as verification:
        stored = verification.get(FixedPriceAdjustment, uuid.UUID(adjustment_id))
        assert stored is not None
        assert stored.commercial_terms_id == terms.id
        assert stored.status == "approved" and stored.amount == Decimal("12")


def test_adjustment_create_retry_after_scenario_approval_returns_existing_row(
    client, db_session: Session
) -> None:
    project = make_project(db_session, name="Approved retry", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Approved retry")
    make_fixed_price_terms(db_session, scenario, agreed_price=Decimal("100"), currency="PLN")
    path = adjustment_path(project.id, scenario.id)
    payload = {
        "request_id": str(uuid.uuid4()),
        "kind": "increase",
        "amount": "10",
        "currency": "PLN",
    }
    with caller_holding(Permission.COMMERCIAL_WRITE):
        first = client.post(path, json=payload, headers=as_caller(IN_SCOPE_USER))
    scenario.status = ScenarioStatus.APPROVED
    db_session.commit()
    with caller_holding(Permission.COMMERCIAL_WRITE):
        retried = client.post(path, json=payload, headers=as_caller(IN_SCOPE_USER))

    assert first.status_code == 201, first.text
    assert retried.status_code == 201, retried.text
    assert retried.json() == first.json()
    assert db_session.query(FixedPriceAdjustment).count() == 1
