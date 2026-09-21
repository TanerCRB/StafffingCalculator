"""SC-2-01 (K-03) and SC-2-03 (K-06) — the catalogue's cost rate is a denied *field*, not a hidden
row, and a subcontractor's price list is gated by exactly the same field.

The shape is SC-1-08's, repeated one table over, with two differences that both matter:

1. **It runs on a real column.** `default_cost_rate` exists, so `CATALOG_PERSONNEL_COST_FIELDS` is
   non-empty in production and these tests need no stand-in field and no monkeypatch. SC-1-08's
   proofs had to substitute `description` and then prove the substitution was load-bearing
   (`test_k_06_…`); here, emptying the gated field set is a mutation that fails the assertions below
   directly. This is the "dowód niepusty" half of K-03.
2. **The gate has one factor, not two.** Outside a project context `project_access.can_view_
   personnel_costs` has no subject, so the catalogue's cost rate is guarded by the global
   `PERSONNEL_COSTS_READ` alone (ADR-0005, addendum 2026-09-19 "pierwszy zbiór danych bez zasięgu
   projektu", point 3). The addendum names this as a weakening and fixes its direction: it holds
   only where there is no project. Nothing here asserts anything about a rate inside a project
   response — that path does not exist yet, and when it does the conjunction applies to it (see the
   report's "what this does not prove").

The denied caller is the placeholder identity, i.e. the one the running system actually has: nothing
in production grants `PERSONNEL_COSTS_READ` (same addendum, point 6), so the positive branch is
reachable only by substituting the identity — as in `test_project_detail_personnel_costs.py`.
"""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import response_shaping
from app.core.identity import Permission
from tests.conftest import (
    IN_SCOPE_USER,
    DimensionTuple,
    as_caller,
    caller_holding,
    make_dimension_tuple,
    make_rate,
    make_vendor,
    rate_payload,
)

COST_RATE = Decimal("123.4567")
SELLING_RATE = Decimal("200.5000")
WINDOW_START = date(2026, 1, 1)
WINDOW_END = date(2026, 6, 30)


def _rate_for_one_tuple(session: Session) -> DimensionTuple:
    dimensions = make_dimension_tuple(session)
    make_rate(
        session,
        dimensions,
        effective_from=WINDOW_START,
        effective_to=WINDOW_END,
        default_cost_rate=COST_RATE,
        default_selling_rate=SELLING_RATE,
    )
    return dimensions


def _assert_row_is_whole_except_the_cost(row: dict, dimensions: DimensionTuple) -> None:
    """Everything except the cost rate is present, exact, and unchanged by the denial.

    The list of what survives is the point of the criterion: a gate that dropped the row, the
    tuple or the selling rate would be a refusal of the *resource*, and the catalogue would stop
    being usable for the planning it exists for."""
    assert row["role_id"] == str(dimensions.role_id)
    assert row["seniority_id"] == str(dimensions.seniority_id)
    assert row["location_id"] == str(dimensions.location_id)
    assert row["engagement_type_id"] == str(dimensions.engagement_type_id)
    assert row["default_selling_rate"] == str(SELLING_RATE)
    assert row["currency"] == "EUR"
    assert row["unit"] == "hour"
    assert row["effective_from"] == WINDOW_START.isoformat()
    assert row["effective_to"] == WINDOW_END.isoformat()


def test_k_03_the_cost_rate_is_removed_from_the_row_not_the_row_from_the_response(
    client: TestClient, db_session: Session
) -> None:
    """K-03. `200`, row present, dimensions and selling rate exact, `default_cost_rate` `None`.

    The status code carries half the criterion. A `403` would say "you may not see this rate" and
    take the selling rate with it; a `404` would make "you may not see this cost" indistinguishable
    from "this tuple has no rate" — and the resolution endpoint uses `404` for precisely that
    second thing, so the two must not be spelled the same way.

    The contrast is the same request by the same user with `PERSONNEL_COSTS_READ` substituted in, so
    the only difference between the two runs is the permission.
    """
    dimensions = _rate_for_one_tuple(db_session)

    denied = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))
    with caller_holding(Permission.CATALOG_READ, Permission.PERSONNEL_COSTS_READ):
        granted = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))

    assert denied.status_code == 200, denied.text
    (denied_row,) = denied.json()["rates"]
    assert denied_row["default_cost_rate"] is None
    _assert_row_is_whole_except_the_cost(denied_row, dimensions)
    assert str(COST_RATE) not in denied.text, "the cost rate is in the payload under another name"

    assert granted.status_code == 200, granted.text
    (granted_row,) = granted.json()["rates"]
    assert granted_row["default_cost_rate"] == str(COST_RATE), (
        "with the permission held the exact stored value must come through, unrounded"
    )
    _assert_row_is_whole_except_the_cost(granted_row, dimensions)


def test_k_03_every_catalogue_path_carrying_a_rate_applies_the_cost_gate(
    client: TestClient, db_session: Session
) -> None:
    """K-03 across all three rate paths — list, resolution, and the `POST` response.

    The write path is in here deliberately: `POST /catalog/rates` answers with the row it created,
    and a caller may hold `CATALOG_WRITE` without `PERSONNEL_COSTS_READ`. A gate wired into the
    read paths only would let that caller write a cost rate and read it straight back out of the
    response — the shape of leak ADR-0005's addendum names for the project write actions
    (2026-09-19, point 7).

    Compared as one mapping rather than three assertions, so a path that quietly answers with
    the cost rate cannot hide behind the two that do not."""
    dimensions = _rate_for_one_tuple(db_session)
    second = make_dimension_tuple(db_session, suffix=" (for the write path)")

    listed = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))
    resolved = client.get(
        "/catalog/rates/effective",
        params={**dimensions.as_query(), "on_date": "2026-03-15"},
        headers=as_caller(IN_SCOPE_USER),
    )
    created = client.post(
        "/catalog/rates",
        json=rate_payload(second, default_cost_rate=str(COST_RATE)),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert [listed.status_code, resolved.status_code, created.status_code] == [200, 200, 201], (
        listed.text,
        resolved.text,
        created.text,
    )
    assert {
        "list": listed.json()["rates"][0]["default_cost_rate"],
        "effective": resolved.json()["default_cost_rate"],
        "create": created.json()["default_cost_rate"],
    } == {"list": None, "effective": None, "create": None}
    # The write really happened — otherwise "the create response carries no cost rate" would be
    # satisfied by a request that failed.
    assert created.json()["default_selling_rate"] == "150.0000"
    assert str(COST_RATE) not in created.text


def test_k_06_the_cost_rate_of_a_vendor_row_is_gated_exactly_like_an_internal_one(
    client: TestClient, db_session: Session
) -> None:
    """K-06 (SC-2-03). A rate row carrying a vendor goes through the same gate, on all three paths.

    **Named as over-protection, not as a claim about personal data** (ADR-0005, addendum
    2026-09-21, point 3). What a subcontractor charges *the company* is not an individual's
    personnel cost in the sense of NF-11/AC-06, and nothing here says it is. The decision is to keep
    one gate on one field rather than teach the shaping layer to classify a row by whether it names
    a vendor — because that branch is the mutation this test kills: "a vendor's price is not a
    personnel cost, so pass it through" would leak `default_cost_rate` for exactly the rows the
    catalogue gained in this task, and every existing SC-2-01 proof would stay green.

    All three rate paths are asserted together (list, resolution, `POST` response), for the reason
    `test_k_03_every_catalogue_path_carrying_a_rate_applies_the_cost_gate` gives: a path that
    quietly answers with the cost rate must not be able to hide behind the two that do not.

    Two contrasts. Within the denial: `default_selling_rate` and `vendor_id` come through intact —
    the vendor is *not* a gated field, so a gate that removed it would be refusing data
    `CATALOG_READ` covers. Across callers: the same requests with `PERSONNEL_COSTS_READ` carry the
    exact stored cost.
    """
    dimensions = make_dimension_tuple(db_session, suffix=" (vendor gate)")
    vendor = make_vendor(db_session, name="Contoso")
    make_rate(
        db_session,
        dimensions,
        effective_from=WINDOW_START,
        effective_to=WINDOW_END,
        vendor_id=vendor.id,
        default_cost_rate=COST_RATE,
        default_selling_rate=SELLING_RATE,
    )
    for_the_write_path = make_dimension_tuple(db_session, suffix=" (vendor write)")
    resolution = {
        **dimensions.as_query(),
        "on_date": "2026-03-15",
        "vendor_id": str(vendor.id),
    }

    listed = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))
    resolved = client.get(
        "/catalog/rates/effective", params=resolution, headers=as_caller(IN_SCOPE_USER)
    )
    created = client.post(
        "/catalog/rates",
        json=rate_payload(
            for_the_write_path, vendor_id=str(vendor.id), default_cost_rate=str(COST_RATE)
        ),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert [listed.status_code, resolved.status_code, created.status_code] == [200, 200, 201], (
        listed.text,
        resolved.text,
        created.text,
    )
    denied_rows = {
        "list": listed.json()["rates"][0],
        "effective": resolved.json(),
        "create": created.json(),
    }
    assert {path: row["default_cost_rate"] for path, row in denied_rows.items()} == {
        "list": None,
        "effective": None,
        "create": None,
    }, "a row naming a vendor carried its cost rate to a caller without PERSONNEL_COSTS_READ"
    assert {path: row["vendor_id"] for path, row in denied_rows.items()} == {
        "list": str(vendor.id),
        "effective": str(vendor.id),
        "create": str(vendor.id),
    }, "the vendor was removed with the cost rate — it is not a gated field"
    assert all(row["default_selling_rate"] for row in denied_rows.values())
    assert str(COST_RATE) not in listed.text + resolved.text + created.text

    with caller_holding(Permission.CATALOG_READ, Permission.PERSONNEL_COSTS_READ):
        granted = client.get(
            "/catalog/rates/effective", params=resolution, headers=as_caller(IN_SCOPE_USER)
        )

    assert granted.status_code == 200, granted.text
    assert granted.json()["default_cost_rate"] == str(COST_RATE)
    assert granted.json()["vendor_id"] == str(vendor.id)


def test_k_03_the_gated_field_set_is_not_empty_so_the_denials_above_are_not_vacuous(
    client: TestClient, db_session: Session
) -> None:
    """K-03's self-invalidating assertion, in the spirit of SC-1-08's `test_k_06_…`.

    SC-1-08 could only gate a stand-in field, and its proofs depended on a monkeypatch whose
    load-bearingness had to be asserted separately. Here the column is real, so the equivalent
    check is the opposite one: the production field set must contain the cost rate, and the
    response for a caller *with* the permission must carry a value — otherwise every `is None`
    in this file could be satisfied by a column that is always empty, or by a gate that removes
    everything."""
    dimensions = _rate_for_one_tuple(db_session)

    assert response_shaping.CATALOG_PERSONNEL_COST_FIELDS == frozenset({"default_cost_rate"}), (
        "the catalogue cost gate has nothing to remove — every denial in this file is vacuous"
    )

    with caller_holding(Permission.CATALOG_READ, Permission.PERSONNEL_COSTS_READ):
        granted = client.get(
            "/catalog/rates/effective",
            params={**dimensions.as_query(), "on_date": "2026-03-15"},
            headers=as_caller(IN_SCOPE_USER),
        )

    assert granted.status_code == 200, granted.text
    assert granted.json()["default_cost_rate"] == str(COST_RATE)
