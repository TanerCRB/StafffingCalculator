"""SC-5-02 (Issue #77, F-07) — personnel surcharges and the fully loaded cost, K-01..K-08.

`app.domain.personnel_cost.fully_loaded_personnel_cost`/`app.domain.paid_absence_cost.
fully_loaded_paid_absence_cost` are new, third and fourth consumers of the shared rate dictionary
`app.data.personnel_cost._worked_months` already builds for SC-5-01/SC-5-06 — this file proves the
formula, the gate and the freeze, never a second resolution mechanism.

Every figure is read through the real endpoints (`GET …/personnel-cost`, `GET …/what-if`), by a
caller for whom the cost gate is **open** — `PERSONNEL_COSTS_READ` held (through
`dependency_overrides`) **and** the `project_access` flag set on the scenario's project — exactly
the convention `test_personnel_cost.py`/`test_paid_absence_cost.py` already use.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.models.approved_snapshot import ApprovedSnapshotCatalogDefaultRate
from app.models.catalog import CatalogDefaultRate
from tests.conftest import (
    IN_SCOPE_USER,
    DimensionTuple,
    approve_path,
    as_caller,
    caller_holding,
    make_allocation,
    make_commercial_terms,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    make_vendor,
)
from tests.test_paid_absence_cost import _catalog as _absence_catalog
from tests.test_paid_absence_cost import _scenario as _absence_scenario
from tests.test_paid_absence_cost import _update as _update_row
from tests.test_scenario_what_if import what_if_path

MAR = date(2026, 3, 1)
COST = Decimal("120.0000")
SELLING = Decimal("200.0000")
EVERYTHING = frozenset(Permission)


def personnel_cost_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/scenarios/{scenario_id}/personnel-cost"


def _plan(
    session: Session,
    *,
    project_name: str = "Aurora surcharge",
    dimensions: DimensionTuple | None = None,
) -> tuple[Any, Any, DimensionTuple, Any]:
    """A project in scope, cost-visible, a draft scenario, one position (headcount 2, 120 planned
    hours in March) — the same shape `test_personnel_cost.py::_plan` builds, kept local so this file
    does not depend on another test module's fixture changing shape under it."""
    project = make_project(
        session, name=project_name, accessible_to=(IN_SCOPE_USER,), cost_visible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(session, project, name="Baseline")
    dimensions = dimensions or make_dimension_tuple(session, suffix=f" {project_name}")
    position = make_staffing_position(session, scenario, dimensions, headcount=2, start_date=MAR)
    make_allocation(session, position, period_month=MAR)
    return project, scenario, dimensions, position


def _cost(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict[str, Any]:
    with caller_holding(*EVERYTHING):
        response = client.get(personnel_cost_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return response.json()["personnel_cost"]


# --- K-01: fully loaded cost = base + surcharge, a field of its own -------------------------------


def test_k_01_the_fully_loaded_cost_is_the_base_cost_plus_its_surcharge(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — 120 planned hours × 120/h = 14400.00 base; a 25% surcharge adds 3600.00; the fully
    loaded cost is 18000.00 — a field of its own, `amount` (the base cost) untouched.

    Mutation this kills: a formula that ignores the surcharge and returns a copy of the base cost —
    `fully_loaded_amount` would then equal `amount`, which this test refuses.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
        surcharge_percent=Decimal("25.000"), includes_surcharge=False,
    )

    cost = _cost(client, project.id, scenario.id)

    assert cost["amount"] == "14400.00", "the base cost must stay exactly SC-5-01's figure"
    assert cost["fully_loaded_amount"] == "18000.00"
    assert cost["surcharge_amount"] == "3600.00"
    assert cost["fully_loaded_amount"] != cost["amount"]
    window = cost["assumptions_used"]["rate_windows"][0]
    assert (window["surcharge_percent"], window["includes_surcharge"]) == ("25.000", False)


# --- K-02: no double counting when the base rate already includes the surcharge -------------------


def test_k_02_the_surcharge_is_not_added_again_when_the_rate_already_includes_it(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — the negative test. The identical 25% surcharge, but `includes_surcharge = true`: the
    fully loaded cost equals the base cost, and the surcharge amount is `0.00`.

    Contrast with `test_k_01_…` above: the only difference between the two fixtures is the flag, and
    it is what a mutation "delete the `includes_surcharge` branch, always add the percent" would
    erase — that mutation makes this test's `fully_loaded_amount` become `"18000.00"`.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
        surcharge_percent=Decimal("25.000"), includes_surcharge=True,
    )

    cost = _cost(client, project.id, scenario.id)

    assert cost["amount"] == "14400.00"
    assert cost["fully_loaded_amount"] == "14400.00" == cost["amount"]
    assert cost["surcharge_amount"] == "0.00"


def test_k_02_a_month_the_base_cost_cannot_state_the_fully_loaded_cost_cannot_state_either(
    client: TestClient, db_session: Session
) -> None:
    """The fully loaded cost shares the base cost's three checks (`_resolve_months`) — a mutation
    giving the fully loaded cost its own, independent resolution could compute a number for a month
    `amount` withholds, which would be a fully loaded cost of a state the base cost says does not
    exist."""
    project, scenario, dimensions, _ = _plan(db_session)
    # No rate at all for this tuple — `no_cost_rate`.

    cost = _cost(client, project.id, scenario.id)

    assert cost["state"] == "no_cost_rate"
    assert cost["amount"] == "n/a"
    assert cost["fully_loaded_amount"] == "n/a"
    assert cost["surcharge_amount"] == "n/a"


# --- Guardian S-01 / Reviewer R-01: mid-month surcharge-only change must not silently mis-cost ----


def test_s_01_a_mid_month_surcharge_percent_change_leaves_the_month_without_a_cost_rate(
    client: TestClient, db_session: Session
) -> None:
    """Guardian S-01 / Reviewer R-01 (gate 2 review, 2026-09-25) — closing, for the surcharge, the
    exact boundary SC-5-01 already drew for the base rate
    (`test_personnel_cost.py::test_k_02_a_cost_rate_change_inside_a_month_leaves_that_month_
    without_a_cost_rate`): two windows over March, identical `default_cost_rate`/`currency`, a
    surcharge percentage that changes on the 16th. `month_has_cost_rate` must say no —
    `no_cost_rate`, naming March — never a silently-picked "first window" surcharge.

    Mutation this kills: removing the `surcharge_percent` arm of `month_has_cost_rate`'s uniformity
    check — the month would then resolve, costed (and fully loaded) at the first window's 10%
    instead of correctly refusing. Before this fix such a mutation survived, because the check did
    not exist at all.
    """
    project, scenario, dimensions, position = _plan(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 3, 15),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
        surcharge_percent=Decimal("10.000"), includes_surcharge=False,
    )
    make_rate(
        db_session, dimensions, effective_from=date(2026, 3, 16),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
        surcharge_percent=Decimal("40.000"), includes_surcharge=False,
    )

    cost = _cost(client, project.id, scenario.id)

    assert cost["state"] == "no_cost_rate"
    assert cost["amount"] == "n/a"
    assert cost["fully_loaded_amount"] == "n/a", (
        "a mid-month surcharge-only change must not silently cost the whole month at one window's "
        "percentage"
    )
    assert cost["surcharge_amount"] == "n/a"
    assert cost["assumptions_used"]["unresolved_months"] == [
        {"position_id": str(position.id), "period_month": "2026-03-01"}
    ]


def test_s_01_a_mid_month_includes_surcharge_flag_change_leaves_the_month_without_a_cost_rate(
    client: TestClient, db_session: Session
) -> None:
    """The other half of the same uniformity check — the boolean flag, not the percentage. Same
    percentage (25%), the flag flips on the 16th: still `no_cost_rate`.

    Mutation this kills: removing the `includes_surcharge` arm (`bool_and = bool_or`) specifically —
    a mutant that keeps the `surcharge_percent` check alone would still pass
    `test_s_01_a_mid_month_surcharge_percent_change_…` above but let this one through silently.
    """
    project, scenario, dimensions, position = _plan(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 3, 15),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
        surcharge_percent=Decimal("25.000"), includes_surcharge=False,
    )
    make_rate(
        db_session, dimensions, effective_from=date(2026, 3, 16),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
        surcharge_percent=Decimal("25.000"), includes_surcharge=True,
    )

    cost = _cost(client, project.id, scenario.id)

    assert cost["state"] == "no_cost_rate"
    assert cost["fully_loaded_amount"] == "n/a"
    assert cost["assumptions_used"]["unresolved_months"] == [
        {"position_id": str(position.id), "period_month": "2026-03-01"}
    ]


# --- K-04: the raw percent/flag are organisational data (`CATALOG_READ` alone), not the amount ----


def test_k_04_the_raw_surcharge_percent_and_flag_are_visible_under_catalog_read_alone(
    client: TestClient, db_session: Session
) -> None:
    """K-04, positive half (ADR-0005, addendum 2026-09-25, Q4) — mirrors the leave budget's own
    catalogue field (SC-3-03), not `default_cost_rate` (SC-2-01): a caller who cannot see
    `default_cost_rate` still sees `surcharge_percent`/`includes_surcharge` on the same row, because
    the percentage reveals nothing without the base rate it multiplies, which is gated separately.
    """
    dimensions = make_dimension_tuple(db_session, suffix=" K-04 catalog")
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
        surcharge_percent=Decimal("17.500"), includes_surcharge=True,
    )

    denied = client.get("/catalog/rates", headers=as_caller(IN_SCOPE_USER))

    assert denied.status_code == 200, denied.text
    (row,) = denied.json()["rates"]
    assert row["default_cost_rate"] is None, "the cost rate itself must still be gated"
    assert row["surcharge_percent"] == "17.500"
    assert row["includes_surcharge"] is True


def test_k_04_the_surcharge_amount_is_never_served_by_the_catalogs_one_factor_path(
    client: TestClient, db_session: Session
) -> None:
    """K-04, negative half (ADR-0005, addendum 2026-09-22 SC-3-03 pt 4, applied to SC-5-02) — the
    composed *amount* never reaches a caller through `CATALOG_READ` alone, only through the
    conjunction (`PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs`), identically to `amount`.

    Mutation this kills: `fully_loaded_amount`/`surcharge_amount` implemented via
    `CATALOG_PERSONNEL_COST_FIELDS`/`_without_catalog_personnel_costs` instead of
    `SCENARIO_COST_FIELDS`/`_without_scenario_personnel_costs` — such a wiring would still deny the
    fields on the catalogue row (which carries no amount to deny) while never denying them on the
    scenario payload below, because the wrong gate was never wired into this endpoint at all.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
        surcharge_percent=Decimal("25.000"), includes_surcharge=False,
    )

    with caller_holding(*(EVERYTHING - {Permission.PERSONNEL_COSTS_READ})):
        denied = client.get(personnel_cost_path(project.id, scenario.id))

    assert denied.status_code == 200, denied.text
    cost = denied.json()["personnel_cost"]
    assert cost["state"] == "calculated", "the refusal is of the field, never of the scenario"
    assert cost["amount"] is None
    assert cost["fully_loaded_amount"] is None
    assert cost["surcharge_amount"] is None
    assert "18000.00" not in denied.text and "3600.00" not in denied.text


# --- K-05: the paid-absence component gets the surcharge the same way the base cost does ----------


def test_k_05_the_paid_absence_component_gets_the_surcharge_too(
    client: TestClient, db_session: Session
) -> None:
    """K-05 (ADR-0013, addendum 2026-09-23 SC-5-06 pt 5, applied by the 2026-09-25 SC-5-02
    addendum) — proven by contrast with `test_k_01_…`/`test_k_02_…` above: the identical
    25% surcharge, applied to the paid-absence component's own (manual + budget) hours at the
    identical cost rate.

    Mutation this kills (Analyst's K-05 mutation): the surcharge multiplier applied only around
    `base_personnel_cost`'s hours, never around `paid_absence_cost`'s — `paid_absence_fully_loaded_
    amount` would then equal `paid_absence_amount` exactly, which this test refuses.
    """
    catalog = _absence_catalog(db_session, suffix=" k05")
    _update_row(
        db_session, CatalogDefaultRate, catalog.rate.id,
        surcharge_percent=Decimal("25.000"), includes_surcharge=False,
    )
    project, scenario, _ = _absence_scenario(db_session, catalog, name="Surcharge K05")

    cost = _cost(client, project.id, scenario.id)

    assert cost["amount"] == "14400.00"
    assert cost["fully_loaded_amount"] == "18000.00"
    assert cost["paid_absence_amount"] == "11700.00"
    assert cost["paid_absence_fully_loaded_amount"] == "14625.00", "11700.00 × 1.25"
    assert cost["paid_absence_surcharge_amount"] == "2925.00"
    assert cost["paid_absence_fully_loaded_amount"] != cost["paid_absence_amount"]


# --- K-06: the surcharge is frozen in the approval snapshot, in this same task --------------------


def test_k_06_the_frozen_surcharge_column_does_not_move_after_a_post_approval_catalog_edit(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — the canary (ADR-0004, addendum 2026-09-25 SC-5-02, point 6; the M-1/SC-3-02 pattern):
    `surcharge_percent`/`includes_surcharge` are frozen on `approved_snapshot_catalog_default_rate`
    at approval, in the same row and the same transaction as `default_cost_rate`, and editing the
    catalogue afterward moves neither.

    Asserted on the frozen row directly, not through the personnel-cost endpoint — a deliberate
    choice of the more direct proof, not the only one available: `app.data.personnel_cost.
    costed_month_windows` **does** read this column back through the endpoint since the gate 2 fix
    (`test_s_01_…` above and `test_qa_finding_…` below prove the read-back is correct), so the
    endpoint could prove the freeze too, one multiplication removed. The database row is still the
    more direct proof of the freeze *itself*, independent of whatever the reader does with it.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    rate = make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
        surcharge_percent=Decimal("12.500"), includes_surcharge=True,
    )

    response = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    assert response.json()["snapshot"]["catalog_default_rates"] == 1

    frozen = db_session.execute(
        sa.select(ApprovedSnapshotCatalogDefaultRate).where(
            ApprovedSnapshotCatalogDefaultRate.scenario_id == scenario.id
        )
    ).scalar_one()
    assert (frozen.surcharge_percent, frozen.includes_surcharge) == (Decimal("12.500"), True)

    db_session.execute(
        sa.update(CatalogDefaultRate)
        .where(CatalogDefaultRate.id == rate.id)
        .values(surcharge_percent=Decimal("99.000"), includes_surcharge=False)
    )
    db_session.flush()
    db_session.expire_all()

    still_frozen = db_session.execute(
        sa.select(ApprovedSnapshotCatalogDefaultRate).where(
            ApprovedSnapshotCatalogDefaultRate.scenario_id == scenario.id
        )
    ).scalar_one()
    assert (still_frozen.surcharge_percent, still_frozen.includes_surcharge) == (
        Decimal("12.500"), True,
    ), "the frozen surcharge moved after a post-approval catalogue edit"


# --- K-07: what-if raises the surcharge amount automatically, through the shared rate dict --------


def test_k_07_a_salary_raise_what_if_raises_the_surcharge_amount_proportionally(
    client: TestClient, db_session: Session
) -> None:
    """K-07 (ADR-0015, addendum 2026-09-25 SC-5-02) — the surcharge is a percentage of the cost rate
    the what-if mechanism already substitutes once, on the shared `WorkedMonth`/`MonthCostRate`
    dictionary (`app.data.scenario_what_if._raised_rate`); no separate substitution exists for it.

    A 10% salary raise: the base cost rises from 14400.00 to 15840.00 (×1.10) and the surcharge
    amount rises from 3600.00 to 3960.00 (×1.10) — the identical multiplier, read off the *raised*
    `cost_rate`, never a second read of the catalogue.

    Mutation this kills (the ADR's own named condition): the surcharge formula reading
    `default_cost_rate` from a fresh catalogue query instead of the substituted dictionary — the
    surcharge amount would then stay 3600.00 under the raise while the base cost moved, which this
    test refuses.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
        surcharge_percent=Decimal("25.000"), includes_surcharge=False,
    )
    make_commercial_terms(db_session, scenario)

    with caller_holding(*EVERYTHING):
        zero = client.get(what_if_path(project.id, scenario.id, "0"))
        raised = client.get(what_if_path(project.id, scenario.id, "10"))

    assert zero.status_code == 200, zero.text
    assert raised.status_code == 200, raised.text
    zero_cost = zero.json()["personnel_cost"]
    raised_cost = raised.json()["personnel_cost"]

    assert zero_cost["fully_loaded_amount"] == "18000.00"
    assert zero_cost["surcharge_amount"] == "3600.00"
    assert raised_cost["amount"] == "15840.00", "14400.00 × 1.10"
    assert raised_cost["surcharge_amount"] == "3960.00", (
        "3600.00 × 1.10 — proportional, no new code"
    )
    assert raised_cost["fully_loaded_amount"] == "19800.00"


# --- K-08: a vendor row's surcharge is writable but has no business meaning -----------------------


def test_k_08_a_vendor_rates_surcharge_is_writable_but_never_affects_the_formula(
    client: TestClient, db_session: Session
) -> None:
    """K-08 — the vendor-row edge case named in ADR-0013's addendum of 2026-09-25: `catalog_default_
    rates.vendor_id NOT NULL` rows carry the same two columns (one table, Q5) and the write path
    accepts a non-zero percent on them, but the base cost formula reads `vendor_id IS NULL`
    exclusively (ADR-0013, point 1, unchanged) — the vendor row's surcharge has no effect.

    Documents the absence of effect, per the Issue's own instruction — it does not, and is not
    meant to, prevent the write: there is no database constraint forcing this column to `0` on a
    vendor row.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
        surcharge_percent=Decimal("0"), includes_surcharge=False,
    )
    vendor = make_vendor(db_session, name="Vendor K-08 surcharge")
    vendor_rate = make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), vendor_id=vendor.id,
        default_cost_rate=Decimal("500.0000"), default_selling_rate=Decimal("600.0000"),
        currency="PLN", surcharge_percent=Decimal("80.000"), includes_surcharge=True,
    )
    db_session.refresh(vendor_rate)
    assert vendor_rate.surcharge_percent == Decimal("80.000"), (
        "the write itself must succeed — this criterion documents no effect, not a refusal"
    )

    cost = _cost(client, project.id, scenario.id)

    assert cost["amount"] == "14400.00"
    assert cost["fully_loaded_amount"] == "14400.00" == cost["amount"]
    assert cost["surcharge_amount"] == "0.00"
    assert len(cost["assumptions_used"]["rate_windows"]) == 1, (
        "the vendor's window must not be read for the internal formula at all"
    )


# --- QA finding: approving a scenario with a configured surcharge moves its own cost --------------


def test_qa_finding_approving_a_scenario_with_a_configured_surcharge_changes_its_own_cost(
    client: TestClient, db_session: Session
) -> None:
    """QA finding (SC-5-02 review, 2026-09-25), fixed — `costed_month_windows`'s snapshot branch
    named a literal `0`/`false` for `surcharge_percent`/`includes_surcharge` instead of reading
    `ApprovedSnapshotCatalogDefaultRate`'s own columns, reading ADR-0005's addendum
    2026-09-25 SC-5-02, point 3 ("SC-5-02 itself does not have to expose any path that
    returns this column" —
    *does not have to*, not *must not*) as a prohibition rather than a permission. The
    consequence was a silent
    regression: approving a scenario with a non-zero, not-already-included surcharge moved its OWN
    `fully_loaded_amount`/`surcharge_amount` down to the base cost, with no catalogue edit in
    between — the exact invariant `test_personnel_cost.py`'s K-07/M-1 tests already prove for
    `amount` (an approval must not move a cost that nothing edited).

    Fixed by reading `window.surcharge_percent`/`window.includes_surcharge` on both branches of
    `costed_month_windows`, symmetrically with `cost_rate`/`currency` — completing the freeze
    `test_k_06_…` already proves, through the reader that already exists for the base cost, rather
    than building a new one.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
        surcharge_percent=Decimal("25.000"), includes_surcharge=False,
    )
    before = _cost(client, project.id, scenario.id)
    assert (before["fully_loaded_amount"], before["surcharge_amount"]) == (
        "18000.00", "3600.00",
    )

    response = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text

    after = _cost(client, project.id, scenario.id)
    assert after["fully_loaded_amount"] == before["fully_loaded_amount"] == "18000.00", (
        "approval, with no catalogue edit, must not move the fully loaded cost — it did: "
        f"{before['fully_loaded_amount']!r} before, {after['fully_loaded_amount']!r} after"
    )
    assert after["surcharge_amount"] == before["surcharge_amount"] == "3600.00"
