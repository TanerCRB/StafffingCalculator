"""SC-6-08, K-03 - the reserve: a fixed amount, month-granular, one rounding, no partial sum
(F-09 pt 4; ADR-0021, point 4; ADR-0014, points 3, 4, 6, 7 by reference).

The reserve total is a separate named result (`GET .../risk-reserves`); it has two shapes, never a
third - a stated amount with its currency, or a named state with `"n/a"`.

Real PostgreSQL through the API under test (the request schema's `422` and the database's CHECKs
are two proofs of one rule: the CHECKs are in `tests/test_risk_schema.py`).
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.schemas.additional_cost import MAX_RECURRING_MONTHS
from app.domain.risk_reserve import ReserveLine, ReserveTotalResult, reserve_months, reserve_total
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    count_reserves,
    make_project,
    make_reserve,
    make_scenario,
    reserve_path,
    reserves_path,
)

MAR = date(2026, 3, 1)
JUN = date(2026, 6, 1)
HEADERS = as_caller(IN_SCOPE_USER)


def _scenario(session: Session, *, currency: str | None = "EUR") -> dict[str, Any]:
    project = make_project(session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    return {
        "project": project,
        "scenario": make_scenario(session, project, name="B", currency=currency),
    }


def _payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "amount": "1200.0000",
        "currency": "EUR",
        "reserve_type": "one_off",
        "start_month": "2026-03-01",
    }
    return body | overrides


def _read(client: TestClient, fixture: dict[str, Any], query: str = "") -> dict[str, Any]:
    response = client.get(
        reserves_path(fixture["project"].id, fixture["scenario"].id) + query, headers=HEADERS
    )
    assert response.status_code == 200, response.text
    return response.json()


def _total(client: TestClient, fixture: dict[str, Any]) -> dict[str, Any]:
    return _read(client, fixture)["reserve_total"]


def _post(client: TestClient, fixture: dict[str, Any], **overrides: object) -> Any:
    return client.post(
        reserves_path(fixture["project"].id, fixture["scenario"].id),
        json=_payload(**overrides),
        headers=HEADERS,
    )


# --- the sum: one-off, recurring with the full amount in every month, one rounding ---------------


def test_k_03_a_one_off_and_a_recurring_reserve_sum_month_by_month(
    client: TestClient, db_session: Session
) -> None:
    """K-03 - a one-off of 100.00 (March) and a recurring 250.5000 for March..June (four months,
    both
    ends included, the **full** amount in each): 100 + 4 x 250.5 = 1102.00. Contrast: the recurring
    one alone is 1002.00, so the one-off really contributes. Mutations: dividing the recurring
    amount by the number of months (250.50), or excluding the closed range's last month (851.50)."""
    fixture = _scenario(db_session)
    make_reserve(
        db_session, fixture["scenario"], amount=Decimal("250.5000"), start_month=MAR, end_month=JUN
    )
    assert _total(client, fixture) == {
        "state": "calculated",
        "amount": "1002.00",
        "currency": "EUR",
        "currencies": ["EUR"],
    }

    make_reserve(db_session, fixture["scenario"], amount=Decimal("100.0000"), start_month=MAR)
    assert _total(client, fixture)["amount"] == "1102.00"


def test_k_03_the_sum_is_rounded_once_at_the_end_not_per_reserve_or_per_month(
    client: TestClient, db_session: Session
) -> None:
    """K-03 - one recurring reserve of 0.0040 for three months and one one-off of 0.0040: the exact
    sum is 0.0160, stated as 0.02. Rounding each month (or each reserve) would give 0.00 for every
    piece. Mutation: `round_money` applied per (reserve, month) entry returns 0.00."""
    fixture = _scenario(db_session)
    make_reserve(
        db_session,
        fixture["scenario"],
        amount=Decimal("0.0040"),
        start_month=MAR,
        end_month=date(2026, 5, 1),
    )
    make_reserve(db_session, fixture["scenario"], amount=Decimal("0.0040"), start_month=MAR)

    assert _total(client, fixture)["amount"] == "0.02"


def test_k_03_four_decimals_are_accepted_unrounded_and_five_are_a_422_that_writes_nothing(
    client: TestClient, db_session: Session
) -> None:
    """K-03 - `12.3456` is stored and returned as `12.3456` (input precision above the minor unit is
    kept); `12.34567` is a `422` and no row is written - never `12.3457` in its place. The same for
    a zero and a negative amount. Contrast: the four-place request is the `201`."""
    fixture = _scenario(db_session)

    accepted = _post(client, fixture, amount="12.3456")
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["amount"] == "12.3456"
    rows_before = count_reserves(db_session)

    for amount in ("12.34567", "0", "0.0000", "-5.0000"):
        refused = _post(client, fixture, amount=amount)
        assert refused.status_code == 422, (amount, refused.text)
    assert count_reserves(db_session) == rows_before == 1


def test_k_03_a_recurring_reserve_is_limited_to_the_recurring_month_bound(
    client: TestClient, db_session: Session
) -> None:
    """K-03 (ADR-0014, point 4) - a range of `MAX_RECURRING_MONTHS` months is accepted; one month
    more is a `422` and writes nothing. A recurring reserve with no end, and a one-off with one, are
    `422`s as well. Mutation: removing the bound accepts the 61-month reserve."""
    fixture = _scenario(db_session)
    last_ok = date(
        2026 + (MAR.month - 1 + MAX_RECURRING_MONTHS - 1) // 12,
        (MAR.month - 1 + MAX_RECURRING_MONTHS - 1) % 12 + 1,
        1,
    )
    first_refused = date(
        2026 + (MAR.month - 1 + MAX_RECURRING_MONTHS) // 12,
        (MAR.month - 1 + MAX_RECURRING_MONTHS) % 12 + 1,
        1,
    )

    ok = _post(client, fixture, reserve_type="recurring", end_month=last_ok.isoformat())
    assert ok.status_code == 201, ok.text
    rows = count_reserves(db_session)

    for overrides in (
        {"reserve_type": "recurring", "end_month": first_refused.isoformat()},
        {"reserve_type": "recurring"},
        {"reserve_type": "one_off", "end_month": "2026-04-01"},
        {"start_month": "2026-03-15"},
    ):
        assert _post(client, fixture, **overrides).status_code == 422, overrides
    assert count_reserves(db_session) == rows


# --- the states: calculated, currency_mismatch (both variants), no_cost_currency -----------------


def test_k_03_no_reserve_in_a_scenario_with_a_currency_is_calculated_zero(
    client: TestClient, db_session: Session
) -> None:
    """K-03 - an empty scenario that declares EUR: `calculated`, `0.00`, EUR - not `n/a`, not a
    missing key. Contrast: with no declared currency the same empty scenario is `no_cost_currency`
    with `n/a` and a `null` currency (a `0.00` of nothing would not name its currency)."""
    declared = _scenario(db_session, currency="EUR")
    assert _total(client, declared) == {
        "state": "calculated",
        "amount": "0.00",
        "currency": "EUR",
        "currencies": [],
    }
    undeclared = _scenario(db_session, currency=None)
    assert _total(client, undeclared) == {
        "state": "no_cost_currency",
        "amount": "n/a",
        "currency": None,
        "currencies": [],
    }


def test_k_03_reserves_in_two_currencies_are_currency_mismatch_with_no_partial_sum(
    client: TestClient, db_session: Session
) -> None:
    """K-03 (R-07, variant 1) - EUR and PLN reserves in an EUR scenario: `currency_mismatch`, amount
    `n/a`, currency `null`, and neither 100.00 (the EUR part) nor 500.00 appears as a total.
    Mutation: summing the reserves that match the scenario currency returns 100.00."""
    fixture = _scenario(db_session, currency="EUR")
    make_reserve(
        db_session, fixture["scenario"], amount=Decimal("100"), start_month=MAR, currency="EUR"
    )
    make_reserve(
        db_session, fixture["scenario"], amount=Decimal("500"), start_month=MAR, currency="PLN"
    )

    assert _total(client, fixture) == {
        "state": "currency_mismatch",
        "amount": "n/a",
        "currency": None,
        "currencies": ["EUR", "PLN"],
    }


def test_k_03_a_reserve_in_a_currency_other_than_the_scenarios_is_currency_mismatch(
    client: TestClient, db_session: Session
) -> None:
    """K-03 (R-07, variant 2) - one currency among the reserves, but not the scenario's declared
    one: still `currency_mismatch` (nothing is converted, ADR-0006). Contrast: the same PLN reserve
    in a scenario that declares PLN - or declares nothing - is `calculated` in PLN."""
    fixture = _scenario(db_session, currency="EUR")
    make_reserve(
        db_session, fixture["scenario"], amount=Decimal("500"), start_month=MAR, currency="PLN"
    )
    assert _total(client, fixture)["state"] == "currency_mismatch"

    for currency in ("PLN", None):
        matching = _scenario(db_session, currency=currency)
        make_reserve(
            db_session, matching["scenario"], amount=Decimal("500"), start_month=MAR, currency="PLN"
        )
        assert _total(client, matching) == {
            "state": "calculated",
            "amount": "500.00",
            "currency": "PLN",
            "currencies": ["PLN"],
        }


def test_k_03_the_total_covers_every_reserve_not_only_the_page(
    client: TestClient, db_session: Session
) -> None:
    """K-03 / ADR-0017 - three reserves paged two at a time: `total` is 3, the page holds 2, and
    `reserve_total` is the sum of **all three** on every page. An offset past the end is a `200`
    with an empty list and the true total; a limit of 0 is a `422`, never clamped."""
    fixture = _scenario(db_session)
    for month, amount in ((MAR, "10"), (date(2026, 4, 1), "20"), (date(2026, 5, 1), "30")):
        make_reserve(db_session, fixture["scenario"], amount=Decimal(amount), start_month=month)

    first = _read(client, fixture, "?limit=2")
    second = _read(client, fixture, "?limit=2&offset=2")
    beyond = _read(client, fixture, "?limit=2&offset=50")
    assert (first["total"], len(first["reserves"])) == (3, 2)
    assert (second["total"], len(second["reserves"])) == (3, 1)
    assert (beyond["total"], beyond["reserves"]) == (3, [])
    for page in (first, second, beyond):
        assert page["reserve_total"]["amount"] == "60.00"
    assert {r["id"] for r in first["reserves"]}.isdisjoint({r["id"] for r in second["reserves"]})
    for query in ("?limit=0", "?limit=1001", "?offset=-1", "?limit=abc"):
        response = client.get(
            reserves_path(fixture["project"].id, fixture["scenario"].id) + query, headers=HEADERS
        )
        assert response.status_code == 422, (query, response.text)


def test_k_03_a_reserve_can_be_edited_and_deleted_and_the_total_follows(
    client: TestClient, db_session: Session
) -> None:
    """K-03 - `PATCH` moves the amount (and the marker), `DELETE` removes the row; the total follows
    each. `risk_id: null` unlinks, and an empty body or a null amount is a `422`."""
    fixture = _scenario(db_session)
    created = _post(client, fixture, amount="100.0000").json()
    path = reserve_path(fixture["project"].id, fixture["scenario"].id, created["id"])

    edited = client.patch(
        path, json={"updated_at": created["updated_at"], "amount": "150.0000"}, headers=HEADERS
    )
    assert edited.status_code == 200, edited.text
    assert _total(client, fixture)["amount"] == "150.00"
    for body in (
        {"updated_at": edited.json()["updated_at"]},
        {"updated_at": edited.json()["updated_at"], "amount": None},
    ):
        assert client.patch(path, json=body, headers=HEADERS).status_code == 422

    deleted = client.request(
        "DELETE", path, json={"updated_at": edited.json()["updated_at"]}, headers=HEADERS
    )
    assert deleted.status_code == 204
    assert _total(client, fixture)["amount"] == "0.00"
    assert (
        db_session.execute(
            sa.select(sa.func.count()).select_from(sa.table("risk_reserve"))
        ).scalar_one()
        == 0
    )


# --- R-01: the span bound on an edit that names one end only -------------------------------------


def _stored(session: Session, reserve_id: uuid.UUID) -> tuple[Any, ...]:
    """The row as the database holds it, by raw SQL - never the ORM identity map."""
    session.rollback()
    return tuple(
        session.execute(
            sa.text("SELECT start_month, end_month, updated_at FROM risk_reserve WHERE id = :id"),
            {"id": reserve_id},
        ).one()
    )


def _edit(client: TestClient, fixture: dict[str, Any], reserve: dict[str, Any], **fields: object):
    return client.patch(
        reserve_path(fixture["project"].id, fixture["scenario"].id, reserve["id"]),
        json={"updated_at": reserve["updated_at"]} | fields,
        headers=HEADERS,
    )


def test_k_03_an_edit_naming_only_end_month_cannot_stretch_a_reserve_past_the_bound(
    client: TestClient, db_session: Session
) -> None:
    """K-03 / R-01 - a stored recurring reserve (2026-03..2026-06) edited with `end_month` alone:
    2999-12-01 is a `409` naming the span, nothing is written (raw re-read: same row, same marker)
    and the read endpoint still answers. Contrast: an `end_month` that keeps the span at exactly
    `MAX_RESERVE_MONTHS` months succeeds; one month further is refused. Mutation: removing the span
    condition from the guarded `UPDATE` accepts 2999-12-01."""
    fixture = _scenario(db_session)
    created = _post(client, fixture, reserve_type="recurring", end_month="2026-06-01").json()
    before = _stored(db_session, uuid.UUID(created["id"]))

    refused = _edit(client, fixture, created, end_month="2999-12-01")
    assert refused.status_code == 409, refused.text
    assert "at most 60 months" in refused.json()["detail"]
    assert _stored(db_session, uuid.UUID(created["id"])) == before
    assert _total(client, fixture)["amount"] == "4800.00"

    last_ok = date(2026 + (MAR.month - 1 + 59) // 12, (MAR.month - 1 + 59) % 12 + 1, 1)
    over = date(2026 + (MAR.month - 1 + 60) // 12, (MAR.month - 1 + 60) % 12 + 1, 1)
    assert _edit(client, fixture, created, end_month=over.isoformat()).status_code == 409
    assert _stored(db_session, uuid.UUID(created["id"])) == before
    ok = _edit(client, fixture, created, end_month=last_ok.isoformat())
    assert ok.status_code == 200, ok.text
    assert ok.json()["end_month"] == last_ok.isoformat()


def test_k_03_an_edit_naming_only_start_month_cannot_stretch_a_reserve_past_the_bound(
    client: TestClient, db_session: Session
) -> None:
    """K-03 / R-01 - the same for `start_month` alone: 0001-01-01 is a `409`, nothing written.
    Contrast: moving the start later (a shorter span) succeeds. Mutation: as above."""
    fixture = _scenario(db_session)
    created = _post(client, fixture, reserve_type="recurring", end_month="2026-06-01").json()
    before = _stored(db_session, uuid.UUID(created["id"]))

    refused = _edit(client, fixture, created, start_month="0001-01-01")
    assert refused.status_code == 409, refused.text
    assert "at most 60 months" in refused.json()["detail"]
    assert _stored(db_session, uuid.UUID(created["id"])) == before

    ok = _edit(client, fixture, created, start_month="2026-05-01")
    assert ok.status_code == 200, ok.text
    assert _total(client, fixture)["amount"] == "2400.00"


def test_k_03_the_span_refusal_keeps_the_order_404_then_approved_then_marker_then_span(
    client: TestClient, db_session: Session
) -> None:
    """K-03 / R-01 - an oversize edit with a stale marker is the marker's `409` (re-reading may
    make it valid), not the span's; an oversize edit of a reserve of another scenario is a `404`;
    one under an approved scenario is the approved `409`."""
    fixture = _scenario(db_session)
    created = _post(client, fixture, reserve_type="recurring", end_month="2026-06-01").json()

    stale = _edit(
        client,
        fixture,
        created | {"updated_at": "2020-01-01T00:00:00+00:00"},
        end_month="2999-12-01",
    )
    assert stale.status_code == 409 and "concurrency marker" in stale.json()["detail"]

    other = _scenario(db_session)
    foreign = client.patch(
        reserve_path(other["project"].id, other["scenario"].id, created["id"]),
        json={"updated_at": created["updated_at"], "end_month": "2999-12-01"},
        headers=HEADERS,
    )
    assert foreign.status_code == 404

    db_session.execute(
        sa.text("UPDATE scenarios SET status = 'approved' WHERE id = :id"),
        {"id": fixture["scenario"].id},
    )
    db_session.commit()
    frozen = _edit(client, fixture, created, end_month="2999-12-01")
    assert frozen.status_code == 409 and "approved" in frozen.json()["detail"]


def test_k_03_a_one_off_reserve_is_unaffected_by_the_span_bound(
    client: TestClient, db_session: Session
) -> None:
    """K-03 / R-01 - a one-off has no end: moving its `start_month` (even to 0001-01-01) is not a
    span and succeeds; setting a recurring reserve's `end_month` to null is left to the type CHECK
    (a `409` naming it), not to the span bound."""
    fixture = _scenario(db_session)
    one_off = _post(client, fixture).json()
    moved = _edit(client, fixture, one_off, start_month="2030-01-01")
    assert moved.status_code == 200, moved.text
    assert moved.json()["end_month"] is None


def test_k_03_the_data_layer_bound_equals_the_request_schema_bound() -> None:
    """The data layer cannot import `app.api`; this pins its copy to the schema's constant."""
    from app.api.schemas.risk import MAX_RESERVE_MONTHS as SCHEMA_BOUND
    from app.data.risk_reserve import MAX_RESERVE_MONTHS as DATA_BOUND

    assert DATA_BOUND == SCHEMA_BOUND == 60


# --- the domain function, on its own -------------------------------------------------------------


def _line(
    amount: str, *, currency: str = "EUR", start: date = MAR, end: date | None = None
) -> ReserveLine:
    return ReserveLine(
        reserve_id=uuid.uuid4(),
        risk_id=None,
        reserve_type="one_off" if end is None else "recurring",
        amount=Decimal(amount),
        currency=currency,
        start_month=start,
        end_month=end,
    )


def test_k_03_the_closed_range_includes_both_ends_across_a_year_boundary() -> None:
    """K-03 - `[2026-11, 2027-02]` is four months: Nov, Dec, Jan, Feb - both ends in, the year
    rolled
    over. A one-off is its own month only."""
    line = _line("1", start=date(2026, 11, 1), end=date(2027, 2, 1))
    assert reserve_months(line) == (
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    )
    assert reserve_months(_line("1")) == (MAR,)
    answer = reserve_total([line], scenario_currency="EUR")
    assert isinstance(answer, ReserveTotalResult) and answer.amount == Decimal("4.00")
