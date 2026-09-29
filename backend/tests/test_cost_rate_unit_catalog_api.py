"""SC-5-08, K-06 — the catalogue API carries `cost_rate_unit`, gated like every cost field
(ADR-0005, addendum 2026-09-29: points 1, 3, 5 and 7 Q-B; ADR-0002, addendum 2026-09-29).

Three claims, one file:

1. **The gate.** `cost_rate_unit` is in `CATALOG_PERSONNEL_COST_FIELDS`: a caller without
   `PERSONNEL_COSTS_READ` gets the row with the field emptied (`200`, never `403`) on every path
   that carries a rate, a caller with it gets the stored value — and the selling rate's `unit` is
   never gated. It is **not** in `SCENARIO_COST_FIELDS`, and no scenario cost response carries a
   top-level unit field (point 3's condition, proved by comparing key sets, not assumed).
2. **The write path.** An omitted unit stores `hour`; a value outside `hour`/`day`/`month` is a
   `422` and writes nothing (the boundary, not the `500` of the CHECK constraint, which stays the
   guarantee and is proven against the database directly).
3. **The pair rule** (Q-B). A `PATCH` naming exactly one of `default_cost_rate` / `cost_rate_unit`
   is a `422` with no write, in both directions; both, or neither, is an edit.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import response_shaping
from app.core.identity import Permission
from app.models import CatalogDefaultRate
from tests.conftest import (
    IN_SCOPE_USER,
    DimensionTuple,
    as_caller,
    caller_holding,
    count_rates,
    make_dimension_tuple,
    make_rate,
    rate_payload,
)
from tests.test_cost_rate_unit import _calendar, _cost, _plan

COST_RATE = Decimal("812.3400")
WITH_COST = (Permission.CATALOG_READ, Permission.CATALOG_WRITE, Permission.PERSONNEL_COSTS_READ)
WITHOUT_COST = (Permission.CATALOG_READ, Permission.CATALOG_WRITE)


def _monthly_rate(session: Session) -> tuple[CatalogDefaultRate, DimensionTuple]:
    dimensions = make_dimension_tuple(session, suffix=" k06")
    rate = make_rate(
        session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30),
        default_cost_rate=COST_RATE, cost_rate_unit="month",
    )
    return rate, dimensions


def _read(client: TestClient, rate_id: uuid.UUID) -> dict:
    listed = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))
    assert listed.status_code == 200, listed.text
    (row,) = [row for row in listed.json()["rates"] if row["id"] == str(rate_id)]
    return row


def _stored(session: Session, rate_id: uuid.UUID) -> tuple[Decimal, str]:
    session.expire_all()
    row = session.execute(
        sa.select(CatalogDefaultRate.default_cost_rate, CatalogDefaultRate.cost_rate_unit).where(
            CatalogDefaultRate.id == rate_id
        )
    ).one()
    return row.default_cost_rate, row.cost_rate_unit


# --- the gate -----------------------------------------------------------------------------------


def test_k_06_the_unit_is_removed_with_the_cost_rate_on_every_path_and_the_selling_unit_is_not(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — list, resolution, create and edit, each read twice: without the permission the row is
    whole (`200`) with `cost_rate_unit` and `default_cost_rate` `null` and `unit` still `hour`; with
    it, both stored values. Mutation: the gate for the field removed from response shaping (the
    denied rows would carry `month`)."""
    rate, dimensions = _monthly_rate(db_session)

    def paths(permissions):
        created_tuple = make_dimension_tuple(db_session, suffix=f" k06 created {len(permissions)}")
        with caller_holding(*permissions):
            listed = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))
            resolved = client.get(
                "/catalog/rates/effective",
                params={**dimensions.as_query(), "on_date": "2026-03-15"},
                headers=as_caller(IN_SCOPE_USER),
            )
            created = client.post(
                "/catalog/rates",
                json=rate_payload(created_tuple, default_cost_rate="500.0000",
                                  cost_rate_unit="day"),
                headers=as_caller(IN_SCOPE_USER),
            )
            listed_row = next(row for row in listed.json()["rates"] if row["id"] == str(rate.id))
            edited = client.patch(
                f"/catalog/rates/{rate.id}",
                json={"updated_at": listed_row["updated_at"], "default_selling_rate": "151.0000"},
                headers=as_caller(IN_SCOPE_USER),
            )
        assert [r.status_code for r in (listed, resolved, created, edited)] == [200, 200, 201, 200]
        rows = {
            "list": listed_row,
            "resolve": resolved.json(),
            "create": created.json(),
            "edit": edited.json(),
        }
        return rows, [r.text for r in (listed, resolved, created, edited)]

    denied, denied_bodies = paths(WITHOUT_COST)
    granted, _ = paths(WITH_COST)

    assert {path: row["cost_rate_unit"] for path, row in denied.items()} == dict.fromkeys(
        denied
    )
    assert {path: row["default_cost_rate"] for path, row in denied.items()} == dict.fromkeys(
        denied
    )
    assert {path: row["unit"] for path, row in denied.items()} == dict.fromkeys(denied, "hour")
    assert {path: row["cost_rate_unit"] for path, row in granted.items()} == {
        "list": "month", "resolve": "month", "create": "day", "edit": "month",
    }
    assert str(COST_RATE) not in "".join(denied_bodies)


def test_k_06_the_gated_field_sets_are_exactly_what_the_decision_says() -> None:
    """K-06 / ADR-0005 addendum 2026-09-29, points 1-3 — `CATALOG_PERSONNEL_COST_FIELDS` holds the
    cost rate and its unit; the selling rate's `unit` is not in it; `cost_rate_unit` is not in
    `SCENARIO_COST_FIELDS` (it reaches a scenario response only inside `assumptions_used`).
    Mutation: `unit` gated instead of `cost_rate_unit`, or the unit added to the scenario set."""
    assert response_shaping.CATALOG_PERSONNEL_COST_FIELDS == frozenset(
        {"default_cost_rate", "cost_rate_unit"}
    )
    assert "unit" not in response_shaping.CATALOG_PERSONNEL_COST_FIELDS
    assert not any(
        "unit" in field for field in response_shaping.SCENARIO_COST_FIELDS
    ), "a unit field joined the scenario cost gate without a decision"


def test_k_06_no_scenario_cost_response_carries_a_top_level_unit_field(
    client: TestClient, db_session: Session
) -> None:
    """K-06 / point 3's condition — the top-level keys of the scenario cost response are the same
    for an hourly and a monthly scenario, for a caller with and without the conjunction, and none
    of them names a unit. Set equality, before/after style: the unit reaches this response nowhere
    but (if at all) inside `assumptions_used`."""
    calendar = _calendar(db_session, hours="7.50", name="Cal k06 keys")
    hourly = _plan(db_session, unit="hour", rate="120.0000", hours=("120.00",), calendar=calendar,
                   suffix="k06-hour")
    monthly = _plan(db_session, unit="month", rate="3300.0000", hours=("120.00",),
                    calendar=calendar, suffix="k06-month")

    with_conjunction = [_cost(client, p.id, s.id) for p, s, *_ in (hourly, monthly)]
    with caller_holding(*(set(Permission) - {Permission.PERSONNEL_COSTS_READ})):
        response = client.get(
            f"/projects/{monthly[0].id}/scenarios/{monthly[1].id}/personnel-cost"
        )
    assert response.status_code == 200, response.text
    without = response.json()["personnel_cost"]

    key_sets = [set(body) for body in (*with_conjunction, without)]
    assert key_sets[0] == key_sets[1] == key_sets[2]
    assert not [key for key in key_sets[0] if "unit" in key]
    assert without["amount"] is None and with_conjunction[1]["amount"] == "2400.00"


# --- the write path -----------------------------------------------------------------------------


def test_k_06_an_omitted_unit_stores_hour_and_a_given_one_is_stored(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — create without `cost_rate_unit` → the stored value is `hour`; with `month` → `month`.
    Read from the database, because the response is gated for the default caller. Mutation: the
    default dropped (the omitted value would reach the column as `NULL` and fail), or the given
    value ignored."""
    omitted = make_dimension_tuple(db_session, suffix=" k06 omitted")
    given = make_dimension_tuple(db_session, suffix=" k06 given")

    first = client.post("/catalog/rates", json=rate_payload(omitted),
                        headers=as_caller(IN_SCOPE_USER))
    second = client.post("/catalog/rates", json=rate_payload(given, cost_rate_unit="month"),
                         headers=as_caller(IN_SCOPE_USER))

    assert (first.status_code, second.status_code) == (201, 201), (first.text, second.text)
    assert _stored(db_session, uuid.UUID(first.json()["id"]))[1] == "hour"
    assert _stored(db_session, uuid.UUID(second.json()["id"]))[1] == "month"


@pytest.mark.parametrize("value", ["week", "Hour", "", "hours", None])
def test_k_06_a_unit_outside_the_three_is_a_422_and_writes_nothing(
    client: TestClient, db_session: Session, value: object
) -> None:
    """K-06 — `week`, a wrong case, an empty string, a plural and an explicit `null` on create:
    `422` (never the `500` of the CHECK), no row written."""
    dimensions = make_dimension_tuple(db_session, suffix=" k06 invalid")
    before = count_rates(db_session)

    response = client.post("/catalog/rates", json=rate_payload(dimensions, cost_rate_unit=value),
                           headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 422, response.text
    assert "cost_rate_unit" in response.text
    assert count_rates(db_session) == before


def test_k_06_the_database_refuses_a_unit_outside_the_three_on_a_path_without_the_schema(
    db_session: Session,
) -> None:
    """K-06 / K-07 — the CHECK is the guarantee: a direct insert with `week` is refused by
    `ck_catalog_default_rates_cost_rate_unit_is_known`; `hour`, `day` and `month` are accepted."""
    for unit in ("hour", "day", "month"):
        dimensions = make_dimension_tuple(db_session, suffix=f" k06 check {unit}")
        make_rate(db_session, dimensions, effective_from=date(2026, 1, 1), cost_rate_unit=unit)
    refused = make_dimension_tuple(db_session, suffix=" k06 check week")

    with pytest.raises(sa.exc.IntegrityError, match="cost_rate_unit_is_known"):
        make_rate(db_session, refused, effective_from=date(2026, 1, 1), cost_rate_unit="week")


# --- the pair rule ------------------------------------------------------------------------------


def test_k_06_a_patch_with_only_the_amount_is_a_422_and_writes_nothing(
    client: TestClient, db_session: Session
) -> None:
    """K-06 / Q-B — `default_cost_rate` without `cost_rate_unit`: `422`, the stored amount, the
    stored unit **and the concurrency marker** untouched. Mutation: the pair rule removed (the
    amount would be written against the stored `month` row — the silent re-pricing the rule exists
    for)."""
    rate, _ = _monthly_rate(db_session)
    row = _read(client, rate.id)

    response = client.patch(
        f"/catalog/rates/{rate.id}",
        json={"updated_at": row["updated_at"], "default_cost_rate": "999.0000"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert _stored(db_session, rate.id) == (COST_RATE, "month")
    assert _read(client, rate.id)["updated_at"] == row["updated_at"]


def test_k_06_a_patch_with_only_the_unit_is_a_422_and_writes_nothing(
    client: TestClient, db_session: Session
) -> None:
    """K-06 / Q-B — the reverse direction: `cost_rate_unit` alone is refused, nothing written."""
    rate, _ = _monthly_rate(db_session)
    row = _read(client, rate.id)

    response = client.patch(
        f"/catalog/rates/{rate.id}",
        json={"updated_at": row["updated_at"], "cost_rate_unit": "hour"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert _stored(db_session, rate.id) == (COST_RATE, "month")


def test_k_06_a_patch_with_both_edits_the_pair_and_one_with_neither_leaves_both_alone(
    client: TestClient, db_session: Session
) -> None:
    """K-06 / Q-B, the contrasts — both fields: `200`, both written (also for a caller who cannot
    read them back); neither (a selling-rate edit): `200`, both unchanged. An invalid unit or an
    explicit `null` in a pair is still a `422`."""
    rate, _ = _monthly_rate(db_session)
    marker = _read(client, rate.id)["updated_at"]

    both = client.patch(
        f"/catalog/rates/{rate.id}",
        json={"updated_at": marker, "default_cost_rate": "999.0000", "cost_rate_unit": "month"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert both.status_code == 200, both.text
    assert _stored(db_session, rate.id) == (Decimal("999.0000"), "month")
    assert both.json()["cost_rate_unit"] is None

    neither = client.patch(
        f"/catalog/rates/{rate.id}",
        json={"updated_at": both.json()["updated_at"], "default_selling_rate": "151.0000"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert neither.status_code == 200, neither.text
    assert _stored(db_session, rate.id) == (Decimal("999.0000"), "month")

    for pair in (
        {"default_cost_rate": "1.0000", "cost_rate_unit": "week"},
        {"default_cost_rate": "1.0000", "cost_rate_unit": None},
    ):
        refused = client.patch(
            f"/catalog/rates/{rate.id}",
            json={"updated_at": neither.json()["updated_at"], **pair},
            headers=as_caller(IN_SCOPE_USER),
        )
        assert refused.status_code == 422, refused.text
    assert _stored(db_session, rate.id) == (Decimal("999.0000"), "month")


# --- R-02: a blind writer states the unit as a precondition ------------


def _edit_body(marker: str, unit: str) -> dict:
    return {"updated_at": marker, "default_cost_rate": "999.0000", "cost_rate_unit": unit}


def test_r_02_a_blind_writer_with_the_wrong_unit_gets_a_409_writes_nothing_body_leaks_no_unit(
    client: TestClient, db_session: Session
) -> None:
    """R-02 / ADR-0005 addendum 2026-09-29, Q-B — the stored row is `month` at 812.3400. A caller
    without `PERSONNEL_COSTS_READ` sends the amount with `hour`: `409`, the stored amount, unit and
    concurrency marker untouched, and a body that names neither the stored unit nor any rate (the
    refusal says which condition failed, and that is all). Mutation: the precondition removed from
    the `UPDATE` (the blind write would land against the monthly row — the silent re-pricing)."""
    rate, _ = _monthly_rate(db_session)
    marker = _read(client, rate.id)["updated_at"]

    with caller_holding(*WITHOUT_COST):
        response = client.patch(f"/catalog/rates/{rate.id}", json=_edit_body(marker, "hour"),
                                headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 409, response.text
    assert "cost_rate_unit_precondition" in response.text
    for leaked in ("month", "812", "hour", "999"):
        assert leaked not in response.text.replace("cost_rate_unit", ""), leaked
    assert _stored(db_session, rate.id) == (COST_RATE, "month")
    assert _read(client, rate.id)["updated_at"] == marker


def test_r_02_a_blind_writer_stating_the_stored_unit_writes_the_amount(
    client: TestClient, db_session: Session
) -> None:
    """R-02, the contrast — the same blind caller and body with the unit that *is* stored (`month`):
    `200`, the amount written, the unit unchanged. A rule that refused every blind edit would fail
    here."""
    rate, _ = _monthly_rate(db_session)
    marker = _read(client, rate.id)["updated_at"]

    with caller_holding(*WITHOUT_COST):
        response = client.patch(f"/catalog/rates/{rate.id}", json=_edit_body(marker, "month"),
                                headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    assert response.json()["cost_rate_unit"] is None
    assert _stored(db_session, rate.id) == (Decimal("999.0000"), "month")


def test_r_02_a_caller_who_can_read_the_cost_may_change_the_unit(
    client: TestClient, db_session: Session
) -> None:
    """R-02, the second contrast — the *identical* request that the blind caller is refused
    (`hour` against a stored `month`), made by a caller holding `PERSONNEL_COSTS_READ`: `200`, both
    written, and the response shows the new unit. Mutation: the permission test inverted or
    dropped (the privileged caller would be refused, or the blind one let through)."""
    rate, _ = _monthly_rate(db_session)
    marker = _read(client, rate.id)["updated_at"]

    with caller_holding(*WITH_COST):
        response = client.patch(f"/catalog/rates/{rate.id}", json=_edit_body(marker, "hour"),
                                headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    assert response.json()["cost_rate_unit"] == "hour"
    assert _stored(db_session, rate.id) == (Decimal("999.0000"), "hour")


def test_r_02_a_stale_marker_is_still_the_marker_conflict_for_a_blind_writer(
    client: TestClient, db_session: Session
) -> None:
    """R-02 — precedence: a blind writer with the *right* unit but a stale marker still gets the
    marker's `409` (`condition=updated_at_marker`), so the two refusals stay distinguishable."""
    rate, _ = _monthly_rate(db_session)

    with caller_holding(*WITHOUT_COST):
        response = client.patch(
            f"/catalog/rates/{rate.id}",
            json=_edit_body("2001-01-01T00:00:00Z", "month"),
            headers=as_caller(IN_SCOPE_USER),
        )

    assert response.status_code == 409, response.text
    assert "updated_at_marker" in response.text
    assert _stored(db_session, rate.id) == (COST_RATE, "month")
