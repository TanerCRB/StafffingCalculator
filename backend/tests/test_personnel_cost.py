"""SC-5-01, K-01/K-02/K-03/K-07 — the base personnel cost of a scenario from worked time (F-07).

Every figure is read through the real endpoint, `GET …/personnel-cost`, by a caller for whom the
cost gate is **open** — `PERSONNEL_COSTS_READ` held (through `dependency_overrides`, the only way:
the placeholder does not grant it, ADR-0005 aneks 2026-09-23 SC-5-01 point 6) **and** the
`project_access` flag set on the scenario's project. The gate itself is K-04's subject, in
`test_personnel_cost_access.py`; here it must not be the reason a figure is missing.

**Why the fixtures look the way they do.** Every allocation carries three *different* hour figures
(availability 160, planned 120, billable 100), every position a headcount of 2, and every rate a
cost (120) different from its selling rate (200). Each of those makes one mutation visible by a
different wrong number: the cost from billable hours (12000.00), `× headcount` (28800.00), the
selling column (24000.00), availability (19200.00) — against the right one, 120 × 120 = 14400.00.
"""

import ast
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.models import CatalogDefaultRate, Scenario
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    DimensionTuple,
    allocation_path,
    approve_path,
    as_caller,
    caller_holding,
    commercial_terms_path,
    make_allocation,
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

COST = Decimal("120.0000")
SELLING = Decimal("200.0000")
TM = {"model_type": "time_and_material"}


def personnel_cost_path(project_id: uuid.UUID, scenario_id: uuid.UUID) -> str:
    return f"/projects/{project_id}/scenarios/{scenario_id}/personnel-cost"


def _plan(
    session: Session,
    *,
    months: tuple[date, ...] = (MAR,),
    currency: str | None = None,
    dimensions: DimensionTuple | None = None,
    project_name: str = "Aurora migration",
) -> tuple[Any, Scenario, DimensionTuple, Any]:
    """A project in scope **with the cost flag set** for the caller, a draft scenario, one position
    (headcount 2) with the given months at the three different default hour figures."""
    project = make_project(
        session,
        name=project_name,
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario = make_scenario(session, project, name="Baseline", currency=currency)
    dimensions = dimensions or make_dimension_tuple(session, suffix=f" {project_name}")
    position = make_staffing_position(
        session, scenario, dimensions, headcount=2, start_date=months[0]
    )
    for month in months:
        make_allocation(session, position, period_month=month)
    return project, scenario, dimensions, position


def _cost(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    """The cost as a caller with the gate open reads it — every permission, flag set by `_plan`."""
    with caller_holding(*Permission):
        response = client.get(personnel_cost_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return response.json()["personnel_cost"]


def _revenue(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    response = client.get(
        commercial_terms_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    return response.json()["revenue"]


def _set_rule(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> None:
    response = client.post(
        commercial_terms_path(project_id, scenario_id), json=TM, headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 201, response.text


def _approve(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    response = client.post(approve_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    return response.json()


def _set_catalog(session: Session, rate_ids: list[uuid.UUID], **values: object) -> None:
    session.execute(
        sa.update(CatalogDefaultRate).where(CatalogDefaultRate.id.in_(rate_ids)).values(**values)
    )
    session.flush()
    session.expire_all()


def _edit_month(
    client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID, position_id: uuid.UUID,
    month: date, **hours: str,
) -> None:
    """The real allocation edit (`PATCH`), with the position's current concurrency token."""
    token = client.get(
        staffing_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    ).json()["positions"][0]["updated_at"]
    response = client.patch(
        allocation_path(project_id, scenario_id, position_id, month),
        json={"updated_at": token, **hours},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert response.status_code == 200, response.text


def _without_source(cost: dict) -> dict:
    """The cost with `rate_source` taken out — the one field that *must* differ across an approval
    (live catalogue → snapshot); everything else must not."""
    assumptions = dict(cost["assumptions_used"])
    assumptions.pop("rate_source")
    return {**cost, "assumptions_used": assumptions}


# --- K-01: Σ (planned hours × cost rate), rounded once, no × headcount ----------------------------


def test_k_01_one_hundred_twenty_planned_hours_at_one_hundred_twenty_is_14400(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — 120 planned hours × 120 PLN/h = 14400.00 PLN, as a fixed-point string.

    The four mutations K-01 names each produce a different number: `billable_hours` instead of the
    plan (12000.00), `× headcount` (28800.00), `default_selling_rate` instead of the cost rate
    (24000.00); the fourth — rounding per month — is the next test. The answer names what it
    depends on: the plan as the hours source, the internal vendor axis, the live catalogue, and the
    one window with its **cost** rate (never the selling rate, which appears nowhere).
    """
    project, scenario, dimensions, _ = _plan(db_session)
    rate = make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
    )

    cost = _cost(client, project.id, scenario.id)

    assert (cost["state"], cost["amount"], cost["currency"]) == ("calculated", "14400.00", "PLN")
    assert isinstance(cost["amount"], str)
    assert cost["cost_basis"] == "base"
    assert cost["assumptions_used"] == {
        "hours_source": "planned_allocation_hours",
        "vendor_axis": "internal",
        "rate_source": "live_catalog",
        "rate_windows": [
            {
                "source_rate_id": str(rate.id),
                "effective_from": "2026-01-01",
                "effective_to": None,
                "default_cost_rate": "120.0000",
                "currency": "PLN",
            }
        ],
        "unresolved_months": [],
        "currencies": ["PLN"],
    }


def test_k_01_the_sum_is_rounded_once_at_the_end_never_per_month(
    client: TestClient, db_session: Session
) -> None:
    """K-01, the rounding point — two months of 1.00 planned hour at 100.0050 per hour.

    Rounded once at the end (ADR-0002; ADR-0013, point 3): 200.0100 → **200.01**. Rounded per month
    (the mutation): 100.01 + 100.01 = 200.02. Not rounded at all: `"200.010000"`, which the exact
    string equality refuses too.
    """
    project = make_project(
        db_session, name="Rounding", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session, suffix=" rounding")
    position = make_staffing_position(db_session, scenario, dimensions, start_date=FEB)
    for month in (FEB, MAR):
        make_allocation(
            db_session, position, period_month=month, planned_allocation_hours=Decimal("1.00")
        )
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=Decimal("100.0050"), currency="PLN",
    )

    cost = _cost(client, project.id, scenario.id)

    assert (cost["state"], cost["amount"]) == ("calculated", "200.01")


def test_k_01_the_cost_moves_with_the_plan_and_the_cost_rate_and_with_nothing_else(
    client: TestClient, db_session: Session
) -> None:
    """K-01's contrasts, through the real edit paths — four edits, one variable each:

    1. `billable_hours` 100 → 150 (real `PATCH`): the cost does **not** move;
    2. `default_selling_rate` 200 → 455 in the catalogue: the cost does **not** move;
    3. `planned_allocation_hours` 120 → 130 (real `PATCH`): the cost moves to 130 × 120 = 15600.00;
    4. `default_cost_rate` 120 → 125 in the catalogue: the cost moves to 130 × 125 = 16250.00.

    The two "does not move" halves are only worth something next to the two "moves" halves: a cost
    that ignored every edit would pass 1 and 2 alone.
    """
    project, scenario, dimensions, position = _plan(db_session)
    rate = make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
    )
    assert _cost(client, project.id, scenario.id)["amount"] == "14400.00"

    _edit_month(client, project.id, scenario.id, position.id, MAR, billable_hours="150.00")
    assert _cost(client, project.id, scenario.id)["amount"] == "14400.00", (
        "the cost moved with billable_hours — it is not reading the plan"
    )
    _set_catalog(db_session, [rate.id], default_selling_rate=Decimal("455.0000"))
    assert _cost(client, project.id, scenario.id)["amount"] == "14400.00", (
        "the cost moved with the selling rate"
    )

    _edit_month(
        client, project.id, scenario.id, position.id, MAR, planned_allocation_hours="130.00"
    )
    assert _cost(client, project.id, scenario.id)["amount"] == "15600.00"
    _set_catalog(db_session, [rate.id], default_cost_rate=Decimal("125.0000"))
    assert _cost(client, project.id, scenario.id)["amount"] == "16250.00"


# --- K-02: the cost-rate predicate, independent of the selling one -------------------------------


def _split_march(
    session: Session,
    dimensions: DimensionTuple,
    *,
    first: dict[str, object],
    second: dict[str, object],
    second_from: date = date(2026, 3, 16),
) -> tuple[CatalogDefaultRate, CatalogDefaultRate]:
    """Two internal windows split inside March: to 15 March, and from `second_from` (the 16th —
    adjacent — unless a test opens a gap). `first`/`second` override the rates and currency."""
    defaults: dict[str, object] = {
        "default_cost_rate": COST, "default_selling_rate": SELLING, "currency": "PLN"
    }
    one = make_rate(
        session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 3, 15),
        **(defaults | first),
    )
    two = make_rate(session, dimensions, effective_from=second_from, **(defaults | second))
    return one, two


def test_k_02_a_cost_rate_change_inside_a_month_leaves_that_month_without_a_cost_rate(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — cost 120 to 15 March, 135 from the 16th, the selling rate unchanged: `no_cost_rate`
    naming March, and no amount. February and April are costed, and are not summed without March.

    Mutations: "the window covering the month's first day" (March at 120), "any overlapping window"
    (March at either rate or twice), and the cost predicate built from the *selling* one (March
    costed, because the selling rate does not change) — each gives an amount here.

    The contrast inside the test: the **revenue** of the same plan is priced, so the month is not
    simply broken for everyone — it is the cost column that splits it.
    """
    project, scenario, dimensions, position = _plan(db_session, months=(FEB, MAR, APR))
    _split_march(
        db_session, dimensions, first={}, second={"default_cost_rate": Decimal("135.0000")}
    )
    _set_rule(client, project.id, scenario.id)

    cost = _cost(client, project.id, scenario.id)

    assert (cost["state"], cost["amount"], cost["currency"]) == ("no_cost_rate", "n/a", None)
    assert cost["assumptions_used"]["unresolved_months"] == [
        {"position_id": str(position.id), "period_month": "2026-03-01"}
    ]
    assert _revenue(client, project.id, scenario.id)["state"] == "calculated"


def test_k_02_a_selling_rate_change_inside_a_month_does_not_block_the_cost(
    client: TestClient, db_session: Session
) -> None:
    """K-02, the other direction (ADR-0013, point 1) — selling 200 to 15 March and 220 from the
    16th, the cost 120 throughout: the cost is 14400.00, and **both** windows are named, since both
    cost the month. The revenue of the same month is `no_rate` — the contrast that proves the two
    predicates are two, not one.

    Mutation: the cost path asking `month_is_priced` (or reading `priced_month_windows`) — March
    would then be `no_cost_rate`.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    first, second = _split_march(
        db_session, dimensions, first={}, second={"default_selling_rate": Decimal("220.0000")}
    )
    _set_rule(client, project.id, scenario.id)

    cost = _cost(client, project.id, scenario.id)

    assert (cost["state"], cost["amount"], cost["currency"]) == ("calculated", "14400.00", "PLN")
    assert [w["source_rate_id"] for w in cost["assumptions_used"]["rate_windows"]] == [
        str(first.id),
        str(second.id),
    ]
    assert _revenue(client, project.id, scenario.id)["state"] == "no_rate"


def test_k_02_contrast_a_cost_rate_change_on_the_first_of_the_month_costs_every_month(
    client: TestClient, db_session: Session
) -> None:
    """K-02's contrast — the change on 1 March: February at 120, March at 130, each month whole.
    120 × 120 + 120 × 130 = 30000.00. Without it the test above would be satisfied by a resolution
    that never costs anything."""
    project, scenario, dimensions, _ = _plan(db_session, months=(FEB, MAR))
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 2, 28),
        default_cost_rate=COST, currency="PLN",
    )
    make_rate(
        db_session, dimensions, effective_from=date(2026, 3, 1),
        default_cost_rate=Decimal("130.0000"), currency="PLN",
    )

    cost = _cost(client, project.id, scenario.id)

    assert (cost["state"], cost["amount"]) == ("calculated", "30000.00")


def test_k_02_a_gap_between_two_equal_cost_windows_inside_a_month_is_no_cost_rate(
    client: TestClient, db_session: Session
) -> None:
    """K-02, the coverage half — the same cost on both sides, but 16 March belongs to no window.

    Mutation: "all overlapping windows share one cost rate" without "and cover every day".
    """
    project, scenario, dimensions, _ = _plan(db_session)
    _split_march(db_session, dimensions, first={}, second={}, second_from=date(2026, 3, 17))

    cost = _cost(client, project.id, scenario.id)

    assert (cost["state"], cost["amount"]) == ("no_cost_rate", "n/a")


def test_k_02_a_currency_change_inside_a_month_at_the_same_cost_figure_is_no_cost_rate(
    client: TestClient, db_session: Session
) -> None:
    """K-02, the currency half of "one cost rate" — 120 PLN then 120 EUR inside March is two rates.

    Mutation: comparing only `default_cost_rate` across the month's windows — March would be costed.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    _split_march(db_session, dimensions, first={}, second={"currency": "EUR"})

    cost = _cost(client, project.id, scenario.id)

    assert (cost["state"], cost["amount"]) == ("no_cost_rate", "n/a")


def test_k_02_a_subcontractor_window_never_stands_in_for_a_missing_internal_cost_rate(
    client: TestClient, db_session: Session
) -> None:
    """K-02, the vendor axis — `vendor_id IS NULL` means internal, never "any" (ADR-0013, point 1).

    A subcontractor's window covers March for the same tuple, at a cost of 999; the organisation's
    own does not: `no_cost_rate`. The contrast adds the internal window: 14400.00 at **its** rate,
    and the vendor's 999 still nowhere. Mutation: dropping `vendor_id IS NULL` — the vendor's cost
    becomes the cost (119880.00), or March is covered twice.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        vendor_id=make_vendor(db_session).id, default_cost_rate=Decimal("999.0000"),
        currency="PLN",
    )
    assert _cost(client, project.id, scenario.id)["state"] == "no_cost_rate"

    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), default_cost_rate=COST,
        currency="PLN",
    )
    cost = _cost(client, project.id, scenario.id)
    assert (cost["state"], cost["amount"]) == ("calculated", "14400.00")
    assert [w["default_cost_rate"] for w in cost["assumptions_used"]["rate_windows"]] == [
        "120.0000"
    ]


def _imports_of(relative_path: str) -> set[str]:
    """Every module a source file imports, read from its syntax tree — not from `sys.modules`,
    which would also list whatever the test run happened to import elsewhere."""
    tree = ast.parse(Path(BACKEND_ROOT, relative_path).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_k_02_the_cost_path_and_the_revenue_path_never_import_each_other() -> None:
    """K-02 "independently of the selling predicate", structurally (ADR-0013, point 1; ADR-0004,
    aneks 2026-09-23 SC-5-01, point 3; rule 10 of the Invariant Guardian).

    The two cost modules import nothing of the revenue path, and the four revenue modules (T&M and
    Story Points since SC-4-04) import nothing of the cost path. The only module that knows both
    predicates is the approval copier — asserted as the contrast, so this test cannot pass by
    reading files that import nothing.
    """
    revenue_modules = {"app.data.commercial_terms", "app.domain.revenue",
                       "app.domain.revenue_time_and_material", "app.domain.revenue_story_points"}
    cost_modules = {"app.data.personnel_cost", "app.domain.personnel_cost"}

    for path in ("app/data/personnel_cost.py", "app/domain/personnel_cost.py"):
        assert not (_imports_of(path) & revenue_modules), f"{path} imports the revenue path"
    for path in (
        "app/data/commercial_terms.py",
        "app/domain/revenue.py",
        "app/domain/revenue_time_and_material.py",
        "app/domain/revenue_story_points.py",
    ):
        assert not (_imports_of(path) & cost_modules), f"{path} imports the cost path"

    copier = _imports_of("app/data/scenario_approval.py")
    assert {"app.data.commercial_terms", "app.data.personnel_cost"} <= copier


def test_c5_rate_windows_shares_geometry_never_a_rate_column() -> None:
    """ADR-0004, aneks 2026-09-23 SC-5-01, control C-5 — the geometry `commercial_terms.py` and
    `personnel_cost.py` both import (`app.data.rate_windows`) stays neutral: it imports neither
    calculation path and never names either rate column, so a future edit to it cannot quietly
    re-couple cost to revenue through the module they already share.

    Mutation: `rate_windows.py` imports `app.data.personnel_cost` or `app.data.commercial_terms`,
    or spells `default_selling_rate`/`default_cost_rate` — either must fail this test.
    """
    revenue_modules = {"app.data.commercial_terms", "app.domain.revenue",
                       "app.domain.revenue_time_and_material", "app.domain.revenue_story_points"}
    cost_modules = {"app.data.personnel_cost", "app.domain.personnel_cost"}

    imports = _imports_of("app/data/rate_windows.py")
    assert not (imports & revenue_modules), "rate_windows.py imports the revenue path"
    assert not (imports & cost_modules), "rate_windows.py imports the cost path"

    source = Path(BACKEND_ROOT, "app/data/rate_windows.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names_and_attrs = {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute))
    }
    assert "default_selling_rate" not in names_and_attrs
    assert "default_cost_rate" not in names_and_attrs


# --- K-03: two shapes, never 0, never a partial sum, explicitly base ------------------------------


def test_k_03_one_month_without_a_cost_rate_withholds_the_whole_cost_never_a_partial_sum(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — February and April costed, March in a catalogue gap: no amount at all.

    The partial sum would be 28800.00 (two costed months); zero would be 0.00. Mutation: `if
    month.rate is None: continue` in the formula — a silently understated cost (ADR-0013, point 2).
    """
    project, scenario, dimensions, position = _plan(db_session, months=(FEB, MAR, APR))
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 2, 28),
        default_cost_rate=COST, currency="PLN",
    )
    make_rate(
        db_session, dimensions, effective_from=date(2026, 4, 1), default_cost_rate=COST,
        currency="PLN",
    )

    cost = _cost(client, project.id, scenario.id)

    assert (cost["state"], cost["amount"], cost["currency"]) == ("no_cost_rate", "n/a", None)
    assert cost["amount"] not in {"28800.00", "0.00", "0"}
    assert cost["assumptions_used"]["unresolved_months"] == [
        {"position_id": str(position.id), "period_month": "2026-03-01"}
    ]


def test_k_03_a_month_with_zero_planned_hours_and_no_cost_rate_is_still_no_cost_rate(
    client: TestClient, db_session: Session
) -> None:
    """K-03 with ADR-0013 point 4 — "is this month costed" is a question about the catalogue.

    March: 120 planned hours and a rate. April: **zero** planned hours and no window. The answer is
    `no_cost_rate` naming April — not 14400.00. The contrast opens the window over April: 14400.00,
    April adding 0. Mutation: skipping zero-hour months before the rate check.
    """
    project, scenario, dimensions, position = _plan(db_session)
    make_allocation(
        db_session, position, period_month=APR, planned_allocation_hours=Decimal("0.00")
    )
    march_only = make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 3, 31),
        default_cost_rate=COST, currency="PLN",
    )

    uncosted = _cost(client, project.id, scenario.id)
    assert (uncosted["state"], uncosted["amount"]) == ("no_cost_rate", "n/a")
    assert uncosted["assumptions_used"]["unresolved_months"] == [
        {"position_id": str(position.id), "period_month": "2026-04-01"}
    ]

    _set_catalog(db_session, [march_only.id], effective_to=None)
    assert _cost(client, project.id, scenario.id)["amount"] == "14400.00"


def test_k_03_cost_rates_in_two_currencies_are_a_named_state_not_a_converted_sum(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — `currency_mismatch`, in both of its shapes, next to its contrast.

    1. Two positions costed in PLN and EUR: `currency_mismatch`, both currencies named.
    2. Every rate in PLN, the scenario declared in EUR: `currency_mismatch`.
    3. The contrast — the same plan declared in PLN: `calculated`, 14400.00 PLN. Without it, a
       comparison that can never be equal would pass 2 (the SC-4-01 QA finding, mirrored).
    """
    project, scenario, dimensions, _ = _plan(db_session)
    make_rate(db_session, dimensions, effective_from=date(2026, 1, 1), currency="PLN")
    other = make_dimension_tuple(db_session, suffix=" (EUR)")
    euro_position = make_staffing_position(db_session, scenario, other, start_date=MAR)
    make_allocation(db_session, euro_position, period_month=MAR)
    make_rate(db_session, other, effective_from=date(2026, 1, 1), currency="EUR")

    mixed = _cost(client, project.id, scenario.id)
    assert (mixed["state"], mixed["amount"]) == ("currency_mismatch", "n/a")
    assert mixed["assumptions_used"]["currencies"] == ["EUR", "PLN"]

    for declared, expected in (("EUR", ("currency_mismatch", "n/a")),
                               ("PLN", ("calculated", "14400.00"))):
        project_n, scenario_n, dimensions_n, _ = _plan(
            db_session, currency=declared, project_name=f"Declared {declared}"
        )
        make_rate(
            db_session, dimensions_n, effective_from=date(2026, 1, 1), default_cost_rate=COST,
            currency="PLN",
        )
        cost = _cost(client, project_n.id, scenario_n.id)
        assert (cost["state"], cost["amount"]) == expected, declared


def test_k_03_an_empty_plan_is_zero_in_the_scenarios_currency_or_a_named_state_without_one(
    client: TestClient, db_session: Session
) -> None:
    """K-03 at the edge — a position with no month rows.

    With a declared currency the true sum is `0.00 EUR` (`calculated`); without one there is no
    currency to name, so the answer is the named state `no_cost_currency` — never `0.00` of nothing
    and never `currency: null` on a calculated result (the mirror of the revenue's R-04).
    """
    results = {}
    for currency in ("EUR", None):
        project = make_project(
            db_session, name=f"Empty {currency}", accessible_to=(IN_SCOPE_USER,),
            cost_visible_to=(IN_SCOPE_USER,),
        )
        scenario = make_scenario(db_session, project, name="Baseline", currency=currency)
        make_staffing_position(
            db_session, scenario, make_dimension_tuple(db_session, suffix=f" {currency}"),
            start_date=MAR,
        )
        cost = _cost(client, project.id, scenario.id)
        results[currency] = (cost["state"], cost["amount"], cost["currency"])

    assert results == {
        "EUR": ("calculated", "0.00", "EUR"),
        None: ("no_cost_currency", "n/a", None),
    }


RESPONSE_FIELDS = {"scenario_id", "scenario_status", "personnel_cost"}
COST_FIELDS = {
    "state",
    "cost_basis",
    "amount",
    "currency",
    "assumptions_used",
    # SC-5-06 (gate 1, Q-1: the component lives in `PersonnelCostRead`, and this equality is
    # re-armed with it — still an equality, so any other field added later fails here as before).
    "paid_absence_state",
    "paid_absence_amount",
    "paid_absence_budget_amount",
    "paid_absence_currency",
    "paid_absence_assumptions_used",
    # SC-5-03 (ADR-0013, aneks 2026-09-25 SC-5-03, point 1: the fixed-amount basis lives beside the
    # base cost in this same payload) — re-armed again, still an equality.
    "fixed_amount_state",
    "fixed_amount_amount",
    "fixed_amount_currency",
    "fixed_amount_assumptions_used",
}
FIXED_AMOUNT_ASSUMPTIONS_FIELDS = {"lines", "currencies"}
FIXED_AMOUNT_LINE_FIELDS = {"position_id", "amount", "currency"}
ASSUMPTIONS_FIELDS = {
    "hours_source",
    "vendor_axis",
    "rate_source",
    "rate_windows",
    "unresolved_months",
    "currencies",
}
WINDOW_FIELDS = {
    "source_rate_id", "effective_from", "effective_to", "default_cost_rate", "currency"
}
UNRESOLVED_FIELDS = {"position_id", "period_month"}


def _assert_field_sets(body: dict[str, Any]) -> None:
    """Every level of the payload, compared by **equality** with the decided field set — so a
    loaded cost, an overhead, a profit, a margin or a selling rate added at any level fails here
    the day it is added (ADR-0013, point 5)."""
    assert set(body) == RESPONSE_FIELDS
    cost = body["personnel_cost"]
    assert set(cost) == COST_FIELDS
    assert set(cost["assumptions_used"]) == ASSUMPTIONS_FIELDS
    for window in cost["assumptions_used"]["rate_windows"]:
        assert set(window) == WINDOW_FIELDS
    for month in cost["assumptions_used"]["unresolved_months"]:
        assert set(month) == UNRESOLVED_FIELDS
    assert set(cost["fixed_amount_assumptions_used"]) == FIXED_AMOUNT_ASSUMPTIONS_FIELDS
    for line in cost["fixed_amount_assumptions_used"]["lines"]:
        assert set(line) == FIXED_AMOUNT_LINE_FIELDS


def test_k_03_the_answer_is_explicitly_the_base_cost_and_carries_nothing_else(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — "explicitly base": `cost_basis == "base"` and the field set by equality, on a
    calculated cost **and** on a named state (each carries a list the other leaves empty).

    The selling rate (200.0000) appears nowhere in either payload: the cost path does not read it.
    """
    project, scenario, dimensions, position = _plan(db_session)
    make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST, default_selling_rate=SELLING, currency="PLN",
    )
    with caller_holding(*Permission):
        calculated = client.get(personnel_cost_path(project.id, scenario.id))
    make_allocation(db_session, position, period_month=date(2025, 12, 1))
    with caller_holding(*Permission):
        named = client.get(personnel_cost_path(project.id, scenario.id))

    for response, state in ((calculated, "calculated"), (named, "no_cost_rate")):
        assert response.status_code == 200, response.text
        body = response.json()
        _assert_field_sets(body)
        assert body["personnel_cost"]["state"] == state
        assert body["personnel_cost"]["cost_basis"] == "base"
        assert "200.0000" not in response.text
    assert calculated.json()["personnel_cost"]["assumptions_used"]["rate_windows"]
    assert named.json()["personnel_cost"]["assumptions_used"]["unresolved_months"]


# --- K-07: AC-04 — the approved cost is the draft's cost, and the catalogue cannot move it --------


def _twin_draft(
    session: Session, scenario: Scenario, dimensions: DimensionTuple, months: tuple[date, ...]
) -> Scenario:
    """A draft of the same project with the same tuple and months — the live contrast."""
    draft = make_scenario(session, scenario.project, name="Draft twin")
    twin = make_staffing_position(session, draft, dimensions, headcount=2, start_date=months[0])
    for month in months:
        make_allocation(session, twin, period_month=month)
    return draft


def test_k_07_a_month_with_a_mid_month_selling_change_keeps_its_cost_through_approval(
    client: TestClient, db_session: Session
) -> None:
    """K-07 on the class the ADR-0004 aneks 2026-09-23 SC-5-01 exists for (its C-1 and C-2).

    March: one cost rate (120), the selling rate changing on the 16th. The draft's cost is
    14400.00; the revenue is `no_rate`, so **the selling predicate freezes nothing** for March.

    1. The approval freezes both March windows anyway — through the cost predicate (C-1).
    2. The approved cost equals the draft's just before approval, field for field except the rate
       source, which becomes the snapshot.
    3. The catalogue's cost rates are then raised to 999: the approved cost does not move; the draft
       twin does (119880.00) — the contrast that the edit reached the catalogue.
    4. The approved **revenue** is still `no_rate` (C-2): the widened snapshot does not price it.

    Mutation this kills, and the reason the test exists: the copier's scope left at the selling
    predicate alone — the approval then freezes zero windows, and the approved cost is
    `no_cost_rate` for ever.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    first, second = _split_march(
        db_session, dimensions, first={}, second={"default_selling_rate": Decimal("220.0000")}
    )
    draft = _twin_draft(db_session, scenario, dimensions, (MAR,))
    _set_rule(client, project.id, scenario.id)
    assert _revenue(client, project.id, scenario.id)["state"] == "no_rate"
    before = _cost(client, project.id, scenario.id)
    assert (before["state"], before["amount"]) == ("calculated", "14400.00")

    approval = _approve(client, project.id, scenario.id)

    assert approval["snapshot"]["catalog_default_rates"] == 2
    after = _cost(client, project.id, scenario.id)
    assert after["assumptions_used"]["rate_source"] == "approved_snapshot"
    assert _without_source(after) == _without_source(before)

    _set_catalog(db_session, [first.id, second.id], default_cost_rate=Decimal("999.0000"))
    assert _cost(client, project.id, scenario.id) == after, (
        "an approved cost moved with the catalogue (AC-04)"
    )
    assert _cost(client, project.id, draft.id)["amount"] == "119880.00"
    assert _revenue(client, project.id, scenario.id)["state"] == "no_rate"


def test_k_07_a_month_with_a_mid_month_cost_change_stays_without_a_cost_rate_through_approval(
    client: TestClient, db_session: Session
) -> None:
    """K-07 on the class that was frozen already (by the selling predicate since R-01) — nothing
    broken by the widening.

    March: cost 120 then 135 from the 16th, one selling rate. Draft: `no_cost_rate`, revenue
    priced. The approval freezes both windows (the selling predicate); the approved cost is the same
    named state, naming the same month. The catalogue is then edited so both windows carry one
    cost (150): the draft twin becomes `calculated` (18000.00), the approved scenario stays
    `no_cost_rate` — it re-asks the cost predicate of **its frozen** rows, which still disagree.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    first, second = _split_march(
        db_session, dimensions, first={}, second={"default_cost_rate": Decimal("135.0000")}
    )
    draft = _twin_draft(db_session, scenario, dimensions, (MAR,))
    _set_rule(client, project.id, scenario.id)
    before = _cost(client, project.id, scenario.id)
    assert (before["state"], before["amount"]) == ("no_cost_rate", "n/a")

    approval = _approve(client, project.id, scenario.id)

    assert approval["snapshot"]["catalog_default_rates"] == 2
    after = _cost(client, project.id, scenario.id)
    assert after["assumptions_used"]["rate_source"] == "approved_snapshot"
    assert _without_source(after) == _without_source(before)

    _set_catalog(db_session, [first.id, second.id], default_cost_rate=Decimal("150.0000"))
    assert _cost(client, project.id, scenario.id) == after
    assert _cost(client, project.id, draft.id)["amount"] == "18000.00"
    assert _revenue(client, project.id, scenario.id)["amount"] == "20000.00"


def test_k_07_an_approved_cost_across_two_cost_windows_is_resolved_per_month_from_the_snapshot(
    client: TestClient, db_session: Session
) -> None:
    """K-07, the snapshot reader resolves per month (ADR-0004 aneks SC-5-01, point 4) — February
    at 120, March at 130, a boundary on the 1st: 30000.00 before approval, after it, and after both
    cost rates are raised to 999. A reader taking "the" frozen row of the tuple would cost both
    months alike; a reader of the live catalogue would answer 239760.00.
    """
    project, scenario, dimensions, _ = _plan(db_session, months=(FEB, MAR))
    feb_rate = make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 2, 28),
        default_cost_rate=COST, currency="PLN",
    )
    mar_rate = make_rate(
        db_session, dimensions, effective_from=date(2026, 3, 1),
        default_cost_rate=Decimal("130.0000"), currency="PLN",
    )
    before = _cost(client, project.id, scenario.id)
    assert before["amount"] == "30000.00"

    _approve(client, project.id, scenario.id)
    after = _cost(client, project.id, scenario.id)
    assert _without_source(after) == _without_source(before)

    _set_catalog(db_session, [feb_rate.id, mar_rate.id], default_cost_rate=Decimal("999.0000"))
    assert _cost(client, project.id, scenario.id) == after


def test_k_07_an_approved_cost_is_read_from_its_own_snapshot_never_from_another_approvals(
    client: TestClient, db_session: Session
) -> None:
    """K-07, the snapshot's owner on the **cost** path (QA, SC-5-01).

    Two scenarios of one tuple, in two projects, approved on either side of a catalogue edit of the
    cost rate: the first froze the window at 120, the second at 150 — one `source_rate_id`, two
    frozen values. Each is costed from its own snapshot only: 14400.00 and 18000.00, each naming its
    own frozen rate. The two figures differ, so the pair is its own contrast.

    Why a cost test and not only the revenue's `test_k_09_an_approved_scenario_reads_only_its_own_
    snapshot_never_another_approvals`: the condition `frozen.scenario_id == position.scenario_id`
    moved to the shared `app.data.rate_windows` in SC-5-01, and removing it was killed by that
    revenue test alone — every cost test ran with a single approved scenario, so the cost reader's
    own-snapshot property was proven only through the revenue path. Mutation this kills on the cost
    path: `frozen_windows_overlapping` without the scenario condition — March then sees both frozen
    rows (62 days over a 31-day month, two cost rates) and both scenarios answer `no_cost_rate`.
    """
    first_project, first, dimensions, _ = _plan(db_session, project_name="First cost approval")
    rate = make_rate(
        db_session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST, currency="PLN",
    )
    second_project, second, _, _ = _plan(
        db_session, project_name="Second cost approval", dimensions=dimensions
    )
    _approve(client, first_project.id, first.id)
    _set_catalog(db_session, [rate.id], default_cost_rate=Decimal("150.0000"))
    _approve(client, second_project.id, second.id)

    first_cost = _cost(client, first_project.id, first.id)
    second_cost = _cost(client, second_project.id, second.id)

    assert (first_cost["state"], first_cost["amount"]) == ("calculated", "14400.00"), (
        "an approved cost was read from another scenario's snapshot"
    )
    assert (second_cost["state"], second_cost["amount"]) == ("calculated", "18000.00")
    for cost, frozen_rate in ((first_cost, "120.0000"), (second_cost, "150.0000")):
        assert cost["assumptions_used"]["rate_source"] == "approved_snapshot"
        assert [
            (w["source_rate_id"], w["default_cost_rate"])
            for w in cost["assumptions_used"]["rate_windows"]
        ] == [(str(rate.id), frozen_rate)]
