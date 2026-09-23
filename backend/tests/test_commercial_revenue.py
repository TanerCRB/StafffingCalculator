"""SC-4-01, K-01/K-02/K-03/K-08/K-09/K-10 — Time & Material revenue of a scenario (F-06.1).

Every figure here goes through the real endpoints: the rule is created by `POST …/commercial-terms`
wherever the criterion is not about the rule itself, and the revenue is read by `GET`. The rows the
criteria are *about* — catalogue windows, positions, months — are fixture writes, because the
constraints and the resolution under test are properties of the database, not of a request schema.

**Why the fixtures look the way they do.** Every allocation fixture carries three *different* hour
figures (availability 160, planned 120, billable 100) and every position a headcount of 2, and every
rate a cost (120) different from its selling rate (200). Each of those is a mutation made visible:
revenue from planned hours, revenue from availability, `billable × headcount`, or the cost column
read instead of the selling one would each produce a different number from the one asserted.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    ApprovedSnapshotCatalogDefaultRate,
    CatalogDefaultRate,
    Scenario,
)
from tests.conftest import (
    IN_SCOPE_USER,
    DimensionTuple,
    allocation_path,
    approve_path,
    as_caller,
    commercial_terms_path,
    count_snapshot_rows,
    make_allocation,
    make_commercial_terms,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    make_vendor,
    staffing_path,
)

FEB = date(2026, 2, 1)
MAR = date(2026, 3, 1)
APR = date(2026, 4, 1)
JUN = date(2026, 6, 1)
JUL = date(2026, 7, 1)

SELLING = Decimal("200.0000")
COST = Decimal("120.0000")
"""A cost rate that differs from the selling rate, so reading the wrong column changes the answer
(ADR-0003, point 4; rule 10 of the Invariant Guardian)."""

TM = {"model_type": "time_and_material"}


def _plan(
    session: Session,
    *,
    months: tuple[date, ...] = (MAR,),
    currency: str | None = None,
    dimensions: DimensionTuple | None = None,
    project_name: str = "Aurora migration",
    scenario_name: str = "Baseline",
) -> tuple[Any, Scenario, DimensionTuple, Any]:
    """A project in scope, a draft scenario, one position (headcount 2) with the given months."""
    project = make_project(session, name=project_name, accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name=scenario_name, currency=currency)
    dimensions = dimensions or make_dimension_tuple(session)
    position = make_staffing_position(
        session, scenario, dimensions, headcount=2, start_date=months[0]
    )
    for month in months:
        make_allocation(session, position, period_month=month)
    return project, scenario, dimensions, position


def _set_rule(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    response = client.post(
        commercial_terms_path(project_id, scenario_id), json=TM, headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _revenue(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    response = client.get(
        commercial_terms_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    return response.json()["revenue"]


# --- K-01: AC-01, the revenue half ---------------------------------------------------------------


def test_k_01_one_hundred_billable_hours_at_two_hundred_pln_is_twenty_thousand_pln(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — 100 billable hours × 200 PLN/h = 20000 PLN (AC-01, revenue part).

    The amount crosses the boundary as a fixed-point **string** (ADR-0002), and the answer names
    what it depends on (F-06.5): the model, `billable_hours`, the internal vendor axis, the live
    catalogue, and the one window used with its own id and selling rate.

    Mutations this kills, each by a different wrong number: `× headcount` (40000.00), the cost
    column (12000.00), planned hours (24000.00), available hours (32000.00).
    """
    project, scenario, dimensions, _ = _plan(db_session)
    rate = make_rate(
        db_session,
        dimensions,
        effective_from=date(2026, 1, 1),
        default_selling_rate=SELLING,
        default_cost_rate=COST,
        currency="PLN",
    )

    created = _set_rule(client, project.id, scenario.id)
    revenue = _revenue(client, project.id, scenario.id)

    assert revenue["state"] == "calculated"
    assert revenue["amount"] == "20000.00"
    assert isinstance(revenue["amount"], str)
    assert revenue["currency"] == "PLN"
    assert created["revenue"] == revenue, "the write and the read disagree about the same figure"
    assert revenue["assumptions_used"] == {
        "model_type": "time_and_material",
        "hours_source": "billable_hours",
        "vendor_axis": "internal",
        "rate_source": "live_catalog",
        "rate_windows": [
            {
                "source_rate_id": str(rate.id),
                "effective_from": "2026-01-01",
                "effective_to": None,
                "default_selling_rate": "200.0000",
                "currency": "PLN",
            }
        ],
        "unresolved_months": [],
        "currencies": ["PLN"],
    }


# --- K-02: billable hours, not the plan -----------------------------------------------------------


def test_k_02_raising_the_plan_at_constant_billable_hours_leaves_the_revenue_unchanged(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — the revenue reads `billable_hours` and nothing else (ADR-0003, point 6).

    The plan and the availability are raised through the **real** allocation edit, the billable
    figure is left alone, and the revenue does not move. The contrast is the same edit on the
    billable figure: the revenue then moves by exactly 50 h × 200 — so the first assertion is not
    satisfied by a revenue that ignores edits altogether.

    Mutation: revenue from `planned_allocation_hours` (or from availability, or from a derived
    capacity) — the first edit then changes the figure.
    """
    project, scenario, dimensions, position = _plan(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), default_selling_rate=SELLING,
        currency="PLN",
    )
    _set_rule(client, project.id, scenario.id)
    assert _revenue(client, project.id, scenario.id)["amount"] == "20000.00"

    def edit(**hours: str) -> None:
        token = client.get(
            staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        ).json()["positions"][0]["updated_at"]
        response = client.patch(
            allocation_path(project.id, scenario.id, position.id, MAR),
            json={"updated_at": token, **hours},
            headers=as_caller(IN_SCOPE_USER),
        )
        assert response.status_code == 200, response.text

    edit(planned_allocation_hours="300.00", availability_hours="400.00")
    assert _revenue(client, project.id, scenario.id)["amount"] == "20000.00", (
        "the revenue moved with the plan — it is not reading billable_hours"
    )

    edit(billable_hours="150.00")
    assert _revenue(client, project.id, scenario.id)["amount"] == "30000.00"


# --- K-03: the window covering the WHOLE month ----------------------------------------------------


def test_k_03_a_rate_change_inside_a_month_leaves_that_month_without_a_rate(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — a window ending on 15 March and one starting on the 16th price February and April,
    and **not** March: `no_rate`, naming the position and March, and no amount at all.

    Mutations this kills: "the window covering the first day of the month" (`valid_period @>
    period_month`) — March would be priced at the old rate; "any window overlapping the month"
    (`&&`) — March would match two windows and be priced twice or arbitrarily.
    """
    project, scenario, dimensions, position = _plan(db_session, months=(FEB, MAR, APR))
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        effective_to=date(2026, 3, 15), default_selling_rate=SELLING, currency="PLN",
    )
    make_rate(
        db_session, dimensions, effective_from=date(2026, 3, 16),
        default_selling_rate=Decimal("220.0000"), currency="PLN",
    )
    _set_rule(client, project.id, scenario.id)

    revenue = _revenue(client, project.id, scenario.id)

    assert revenue["state"] == "no_rate"
    assert revenue["amount"] == "n/a"
    assert revenue["currency"] is None
    assert revenue["assumptions_used"]["unresolved_months"] == [
        {"position_id": str(position.id), "period_month": "2026-03-01"}
    ]


def test_k_03_contrast_a_rate_change_exactly_on_the_first_of_the_month_prices_every_month(
    client: TestClient, db_session: Session
) -> None:
    """K-03's contrast — the same two rates, the change on 1 March: every month is covered whole.

    February at 200, March and April at 220: 100 × 200 + 2 × 100 × 220 = 64000.00, and both windows
    named in `assumptions_used`, in the order they start. Without this contrast the test above would
    be satisfied by a resolution that never prices anything.
    """
    project, scenario, dimensions, _ = _plan(db_session, months=(FEB, MAR, APR))
    old = make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        effective_to=date(2026, 2, 28), default_selling_rate=SELLING, currency="PLN",
    )
    new = make_rate(
        db_session, dimensions, effective_from=date(2026, 3, 1),
        default_selling_rate=Decimal("220.0000"), currency="PLN",
    )
    _set_rule(client, project.id, scenario.id)

    revenue = _revenue(client, project.id, scenario.id)

    assert revenue["state"] == "calculated"
    assert revenue["amount"] == "64000.00"
    assert [w["source_rate_id"] for w in revenue["assumptions_used"]["rate_windows"]] == [
        str(old.id),
        str(new.id),
    ]


def test_k_03_a_vendor_window_for_the_same_tuple_never_prices_an_internal_position(
    client: TestClient, db_session: Session
) -> None:
    """K-03, the vendor axis — `vendor_id IS NULL` means internal, never "any" (ADR-0003, point 4).

    A subcontractor's window covers March for the same tuple; the organisation's own does not. The
    answer is `no_rate` for March. Mutation: dropping `vendor_id IS NULL` from the resolution — the
    vendor's price then silently becomes the revenue.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    vendor = make_vendor(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), vendor_id=vendor.id,
        default_selling_rate=Decimal("999.0000"), currency="PLN",
    )
    _set_rule(client, project.id, scenario.id)

    revenue = _revenue(client, project.id, scenario.id)

    assert revenue["state"] == "no_rate"
    assert revenue["amount"] == "n/a"


# --- K-10: a missing input is a named state, never 0 and never a partial sum ----------------------


def test_k_10_a_scenario_without_a_rule_has_no_revenue_not_a_revenue_of_zero(
    client: TestClient, db_session: Session
) -> None:
    """K-10 (a) — no rule: `no_commercial_terms`, `"n/a"`, and the rule itself `null`.

    Priced windows exist for every month, so the only thing missing is the rule — a dispatcher that
    defaulted to T&M would produce 20000.00 here, and one that answered zero would produce 0.00.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    make_rate(db_session, dimensions, effective_from=date(2026, 1, 1), currency="PLN")

    response = client.get(
        commercial_terms_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["commercial_terms"] is None
    assert body["revenue"]["state"] == "no_commercial_terms"
    assert body["revenue"]["amount"] == "n/a"
    assert body["revenue"]["assumptions_used"]["model_type"] is None


def test_k_10_a_rule_without_its_details_row_is_incomplete_not_priced(
    client: TestClient, db_session: Session
) -> None:
    """K-10 (b) — `incomplete_commercial_terms` (ADR-0003, point 3: the database enforces the type
    of the details row, not its existence). Mutation: pricing a T&M rule whether or not `tm_terms`
    exists — 20000.00 here instead of the named state."""
    project, scenario, dimensions, _ = _plan(db_session)
    make_rate(db_session, dimensions, effective_from=date(2026, 1, 1), currency="PLN")
    make_commercial_terms(db_session, scenario, with_details=False)

    revenue = _revenue(client, project.id, scenario.id)

    assert revenue["state"] == "incomplete_commercial_terms"
    assert revenue["amount"] == "n/a"


def test_k_10_one_unpriced_month_withholds_the_whole_revenue_and_never_a_partial_sum(
    client: TestClient, db_session: Session
) -> None:
    """K-10 (c) — February and April priced, March in a catalogue gap: no amount at all.

    The partial sum would be 40000.00 (two priced months); zero would be 0.00. Both are asserted
    absent. Mutation: `if month.rate is None: continue` in the formula (a silently understated
    revenue — ADR-0003, point 9).
    """
    project, scenario, dimensions, position = _plan(db_session, months=(FEB, MAR, APR))
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        effective_to=date(2026, 2, 28), currency="PLN",
    )
    make_rate(db_session, dimensions, effective_from=date(2026, 4, 1), currency="PLN")
    _set_rule(client, project.id, scenario.id)

    revenue = _revenue(client, project.id, scenario.id)

    assert revenue["state"] == "no_rate"
    assert revenue["amount"] == "n/a"
    assert revenue["amount"] not in {"40000.00", "0.00", "0"}
    assert revenue["assumptions_used"]["unresolved_months"] == [
        {"position_id": str(position.id), "period_month": "2026-03-01"}
    ]


def test_k_10_rates_in_two_currencies_are_a_named_state_not_a_converted_sum(
    client: TestClient, db_session: Session
) -> None:
    """K-10 (d), ADR-0003 point 8 — two positions priced in PLN and EUR: `currency_mismatch`.

    And the second shape of the same state: every rate in PLN, the scenario declared in EUR. No
    `exchange_rates` exist (ADR-0006), so a `1:1` sum would be invented.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    make_rate(db_session, dimensions, effective_from=date(2026, 1, 1), currency="PLN")
    other = make_dimension_tuple(db_session, suffix=" (EUR)")
    euro_position = make_staffing_position(db_session, scenario, other, start_date=MAR)
    make_allocation(db_session, euro_position, period_month=MAR)
    make_rate(db_session, other, effective_from=date(2026, 1, 1), currency="EUR")
    _set_rule(client, project.id, scenario.id)

    mixed = _revenue(client, project.id, scenario.id)
    assert mixed["state"] == "currency_mismatch"
    assert mixed["amount"] == "n/a"
    assert mixed["assumptions_used"]["currencies"] == ["EUR", "PLN"]

    project_2, declared_eur, dimensions_2, _ = _plan(
        db_session,
        currency="EUR",
        project_name="Borealis",
        dimensions=make_dimension_tuple(db_session, suffix=" (declared EUR)"),
    )
    make_rate(db_session, dimensions_2, effective_from=date(2026, 1, 1), currency="PLN")
    _set_rule(client, project_2.id, declared_eur.id)

    assert _revenue(client, project_2.id, declared_eur.id)["state"] == "currency_mismatch"


def test_k_10_contrast_a_scenario_declared_in_the_currency_of_its_rates_is_priced(
    client: TestClient, db_session: Session
) -> None:
    """K-10 (d)'s contrast for the *declared* shape (QA, SC-4-01) — one element changed: the
    scenario's own currency.

    Two identical plans priced by PLN windows: the scenario declared in PLN is `calculated`, the one
    declared in EUR is `currency_mismatch`. The test above covers only the refusing half, and before
    this contrast existed a comparison that could never be equal (`currencies != scenario_currency`,
    a tuple against a string) survived the whole suite — every scenario with a declared currency
    would have been `currency_mismatch` for ever, and nothing was red.
    """
    project_pln, declared_pln, dimensions_pln, _ = _plan(
        db_session,
        currency="PLN",
        project_name="Declared PLN",
        dimensions=make_dimension_tuple(db_session, suffix=" (declared PLN)"),
    )
    make_rate(
        db_session, dimensions_pln, effective_from=date(2026, 1, 1),
        default_selling_rate=SELLING, currency="PLN",
    )
    project_eur, declared_eur, dimensions_eur, _ = _plan(
        db_session,
        currency="EUR",
        project_name="Declared EUR",
        dimensions=make_dimension_tuple(db_session, suffix=" (declared EUR)"),
    )
    make_rate(
        db_session, dimensions_eur, effective_from=date(2026, 1, 1),
        default_selling_rate=SELLING, currency="PLN",
    )
    _set_rule(client, project_pln.id, declared_pln.id)
    _set_rule(client, project_eur.id, declared_eur.id)

    priced = _revenue(client, project_pln.id, declared_pln.id)
    assert (priced["state"], priced["amount"], priced["currency"]) == (
        "calculated",
        "20000.00",
        "PLN",
    )
    assert priced["assumptions_used"]["currencies"] == ["PLN"]
    refused = _revenue(client, project_eur.id, declared_eur.id)
    assert (refused["state"], refused["amount"]) == ("currency_mismatch", "n/a")


# --- K-08: the approval freezes only the windows the calculation reads ----------------------------


def _frozen_rates(
    session: Session, scenario_id: uuid.UUID
) -> list[ApprovedSnapshotCatalogDefaultRate]:
    session.expire_all()
    return list(
        session.execute(
            sa.select(ApprovedSnapshotCatalogDefaultRate).where(
                ApprovedSnapshotCatalogDefaultRate.scenario_id == scenario_id
            )
        ).scalars()
    )


def test_k_08_approval_freezes_only_the_rate_windows_the_scenarios_months_read(
    client: TestClient, db_session: Session
) -> None:
    """K-08 — one row: the internal window covering February and March of the tuple planned.

    The catalogue holds five windows and the approval reads one of them. Not frozen: last year's
    window and next half-year's window of the same tuple (no month reaches them), a subcontractor's
    window for the same tuple (not internal), and another tuple's window (no position). **Two
    positions** of the same tuple plan the same two months, so four position-months read the one
    window — and it is frozen **once** (the deduplication canary, SC-3-02 S-01/R-01).

    The frozen row carries values, including the cost rate SC-4-01 does not read (ADR-0004, addendum
    SC-4-01, point 2b). Mutations: the whole catalogue frozen (5 rows), vendor windows included (2),
    `DISTINCT` above the generated id (4), the cost left out (a `NULL` refused by the column).
    """
    project, scenario, dimensions, _ = _plan(db_session, months=(FEB, MAR))
    second = make_staffing_position(db_session, scenario, dimensions, start_date=FEB)
    make_allocation(db_session, second, period_month=FEB)
    make_allocation(db_session, second, period_month=MAR)
    make_rate(
        db_session, dimensions, effective_from=date(2025, 1, 1), effective_to=date(2025, 12, 31)
    )
    read = make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30),
        default_selling_rate=SELLING, default_cost_rate=COST, currency="PLN",
    )
    make_rate(db_session, dimensions, effective_from=date(2026, 7, 1))
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        vendor_id=make_vendor(db_session).id,
    )
    another_tuple = make_dimension_tuple(db_session, suffix=" other")
    make_rate(db_session, another_tuple, effective_from=date(2026, 1, 1))

    response = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    assert response.json()["snapshot"]["catalog_default_rates"] == 1
    frozen = _frozen_rates(db_session, scenario.id)
    assert [row.source_rate_id for row in frozen] == [read.id]
    row = frozen[0]
    assert (
        row.source_role_id,
        row.source_seniority_id,
        row.source_location_id,
        row.source_engagement_type_id,
        row.source_vendor_id,
    ) == (
        dimensions.role_id,
        dimensions.seniority_id,
        dimensions.location_id,
        dimensions.engagement_type_id,
        None,
    )
    assert (row.default_selling_rate, row.default_cost_rate, row.currency, row.unit) == (
        SELLING,
        COST,
        "PLN",
        "hour",
    )
    assert (row.effective_from, row.effective_to) == (date(2026, 1, 1), date(2026, 6, 30))


def test_k_08_contrast_a_scenario_spanning_two_windows_freezes_both_and_a_gap_month_freezes_none(
    client: TestClient, db_session: Session
) -> None:
    """K-08's contrast — June and July read two windows, and both are frozen; a month no window
    covers whole freezes nothing. Without it the test above would be satisfied by a copier that
    freezes "the window covering the first month" and stops there."""
    project, scenario, dimensions, _ = _plan(db_session, months=(JUN, JUL, date(2026, 8, 1)))
    first = make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30)
    )
    second = make_rate(
        db_session, dimensions, effective_from=date(2026, 7, 1), effective_to=date(2026, 7, 31)
    )
    # August: a window starting mid-month covers it only in part — nothing to freeze.
    make_rate(db_session, dimensions, effective_from=date(2026, 8, 10))

    response = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 200, response.text
    assert response.json()["snapshot"]["catalog_default_rates"] == 2
    assert {row.source_rate_id for row in _frozen_rates(db_session, scenario.id)} == {
        first.id,
        second.id,
    }


def test_k_08_a_copy_of_an_approved_scenario_holds_no_snapshot_row_including_the_rate_snapshot(
    client: TestClient, db_session: Session
) -> None:
    """K-08's canary — the copy is a `draft` that has not been through an approval (ADR-0004,
    addendum 2026-09-22 SC-3-02, point 2; SC-4-01 point 2d: the canary covers the new table).

    The source holds one frozen rate before and after the copy (the contrast), the copy holds zero —
    counted both in the new table directly and across every snapshot table. Mutation: the rate
    snapshot registered in `SCENARIO_CHILD_COPIERS`.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    make_rate(db_session, dimensions, effective_from=date(2026, 1, 1))
    _set_rule(client, project.id, scenario.id)
    assert (
        client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
        .status_code == 200
    )
    assert len(_frozen_rates(db_session, scenario.id)) == 1

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    copy_scenario_id = db_session.execute(
        sa.select(Scenario.id).where(Scenario.project_id == uuid.UUID(response.json()["id"]))
    ).scalar_one()

    assert _frozen_rates(db_session, copy_scenario_id) == []
    assert count_snapshot_rows(db_session, copy_scenario_id) == 0
    assert len(_frozen_rates(db_session, scenario.id)) == 1, "copying moved the source's snapshot"


# --- K-09: AC-04/AC-10 — the catalogue moves, the approved revenue does not -----------------------


def test_k_09_editing_the_catalogue_after_approval_moves_no_approved_revenue_figure(
    client: TestClient, db_session: Session
) -> None:
    """K-09 — an approved scenario spanning two windows (February at 200, March at 220) keeps
    42000.00 after both source rates are raised and a third window is added over its gap month.

    Three claims in one run, because they fail under three different mutations:

    - **the snapshot is read, not the catalogue** — the rates are edited in place, so a reader of
      the live table answers 91000.00 (mutation: `rate_source` chosen by anything but the status);
    - **the reader resolves per month** — two frozen windows, each month priced by the one that
      contains it whole (addendum SC-4-01, point 2e). A reader that took "the" frozen row of a tuple
      would price both months alike;
    - **a month unpriced at approval stays unpriced** — May had no window at approval, one is added
      afterwards, and the approved revenue still says `no_rate` for May in the second scenario
      below.

    The contrast is a **draft** of the same tuple and months: it does move, to the edited rates.
    """
    project, approved, dimensions, _ = _plan(db_session, months=(FEB, MAR))
    feb_rate = make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 2, 28),
        default_selling_rate=SELLING, currency="PLN",
    )
    mar_rate = make_rate(
        db_session, dimensions, effective_from=date(2026, 3, 1), effective_to=date(2026, 3, 31),
        default_selling_rate=Decimal("220.0000"), currency="PLN",
    )
    draft = make_scenario(db_session, approved.project, name="Draft twin")
    twin = make_staffing_position(db_session, draft, dimensions, headcount=2, start_date=FEB)
    make_allocation(db_session, twin, period_month=FEB)
    make_allocation(db_session, twin, period_month=MAR)
    _set_rule(client, project.id, approved.id)
    _set_rule(client, project.id, draft.id)
    assert (
        client.post(approve_path(project.id, approved.id), headers=as_caller(IN_SCOPE_USER))
        .status_code == 200
    )
    before = _revenue(client, project.id, approved.id)
    assert (before["state"], before["amount"]) == ("calculated", "42000.00")
    assert before["assumptions_used"]["rate_source"] == "approved_snapshot"

    db_session.execute(
        sa.update(CatalogDefaultRate)
        .where(CatalogDefaultRate.id.in_([feb_rate.id, mar_rate.id]))
        .values(default_selling_rate=Decimal("455.0000"))
    )
    db_session.flush()
    db_session.expire_all()

    after = _revenue(client, project.id, approved.id)
    assert after == before, "an approved revenue moved with the catalogue (AC-04, AC-10)"

    live = _revenue(client, project.id, draft.id)
    assert (live["amount"], live["assumptions_used"]["rate_source"]) == (
        "91000.00",
        "live_catalog",
    )


def test_k_09_a_month_unpriced_at_approval_stays_unpriced_when_the_catalogue_fills_the_gap(
    client: TestClient, db_session: Session
) -> None:
    """K-09, the other direction — the catalogue *gains* a window after approval.

    At approval, May had no window: `no_rate`. A window covering May is added afterwards. The
    approved scenario still answers `no_rate` for May (the snapshot has no UPDATE path, ADR-0004
    addendum SC-4-01 point 2c), while a draft of the same plan is now priced — the contrast.
    Mutation: a snapshot reader that "fills in" a missing frozen window from the live catalogue.
    """
    may = date(2026, 5, 1)
    project, approved, dimensions, position = _plan(db_session, months=(APR, may))
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 4, 30)
    )
    draft = make_scenario(db_session, approved.project, name="Draft twin")
    twin = make_staffing_position(db_session, draft, dimensions, start_date=APR)
    make_allocation(db_session, twin, period_month=APR)
    make_allocation(db_session, twin, period_month=may)
    _set_rule(client, project.id, approved.id)
    _set_rule(client, project.id, draft.id)
    assert (
        client.post(approve_path(project.id, approved.id), headers=as_caller(IN_SCOPE_USER))
        .status_code == 200
    )

    make_rate(db_session, dimensions, effective_from=date(2026, 5, 1))
    db_session.expire_all()

    frozen = _revenue(client, project.id, approved.id)
    assert frozen["state"] == "no_rate"
    assert frozen["assumptions_used"]["unresolved_months"] == [
        {"position_id": str(position.id), "period_month": "2026-05-01"}
    ]
    assert _revenue(client, project.id, draft.id)["state"] == "calculated"


def test_k_09_an_approved_scenario_reads_only_its_own_snapshot_never_another_approvals(
    client: TestClient, db_session: Session
) -> None:
    """K-09, the snapshot's owner (QA, SC-4-01) — the frozen windows are keyed by the scenario, and
    the reader must ask for **its** scenario's rows.

    Two approved scenarios of one tuple, in two projects, approved on either side of a catalogue
    edit: the first froze the window at 200, the second at 300 — the same `source_rate_id`, two
    frozen values. Each prices March from its own snapshot only: 20000.00 and 30000.00. The two
    figures differ, so the pair is its own contrast.

    Mutation this kills, and which survived the suite before this test: `_frozen_window_of` without
    `frozen.scenario_id == position.scenario_id`. Every other K-09 run has a single approved
    scenario, so a reader matching the snapshot by tuple alone read the right rows by accident; here
    it joins both scenarios' rows and the first answers 50000.00 — another approval's price, from a
    project the reader may not even be able to see.
    """
    first_project, first, dimensions, _ = _plan(db_session, project_name="First approval")
    rate = make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_selling_rate=SELLING, currency="PLN",
    )
    second_project, second, _, _ = _plan(
        db_session, project_name="Second approval", dimensions=dimensions
    )
    _set_rule(client, first_project.id, first.id)
    _set_rule(client, second_project.id, second.id)
    assert (
        client.post(approve_path(first_project.id, first.id), headers=as_caller(IN_SCOPE_USER))
        .status_code == 200
    )

    db_session.execute(
        sa.update(CatalogDefaultRate)
        .where(CatalogDefaultRate.id == rate.id)
        .values(default_selling_rate=Decimal("300.0000"))
    )
    db_session.flush()
    db_session.expire_all()
    assert (
        client.post(approve_path(second_project.id, second.id), headers=as_caller(IN_SCOPE_USER))
        .status_code == 200
    )

    first_revenue = _revenue(client, first_project.id, first.id)
    second_revenue = _revenue(client, second_project.id, second.id)
    assert (first_revenue["state"], first_revenue["amount"]) == ("calculated", "20000.00"), (
        "an approved scenario was priced from another scenario's snapshot"
    )
    assert [
        w["default_selling_rate"] for w in first_revenue["assumptions_used"]["rate_windows"]
    ] == ["200.0000"]
    assert (second_revenue["state"], second_revenue["amount"]) == ("calculated", "30000.00")
    assert first_revenue["assumptions_used"]["rate_source"] == "approved_snapshot"
