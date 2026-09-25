"""SC-5-05, K-01/K-02/K-04/K-05/K-10 — the sum of a scenario's additional costs (F-08; ADR-0014).

Every figure is read through the real endpoint, `GET …/additional-costs`, by the placeholder caller
(`STAFFING_READ` is all it needs — ADR-0014, point 11). The fixtures are chosen so that each
mutation the criterion names produces a *different* number from the right one, and every test
says which.

- **K-01** a one-off cost appears exactly once, in its month, whether it hangs on the scenario or on
  a position — proven on two scenarios of one project (isolation) and on two positions with many
  allocation months each (no fan-out).
- **K-02** a recurring cost appears with its full amount in every month of its closed range and in
  no other — asserted on the *set* of months — including months outside the project's delivery
  period and outside the position's allocation.
- **K-04** input to four decimal places is stored unrounded, five is a `422`, and the result is
  rounded once at the end.
- **K-05** two shapes: `calculated`, or a named state; never a partial sum.
- **K-10** a `rebilled_to_client` cost enters the sum and leaves the revenue byte-for-byte
  unchanged; the additional-cost modules and the revenue/personnel-cost modules never import each
  other.
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

from app.api.schemas.additional_cost import MAX_RECURRING_MONTHS
from app.models import AdditionalCost, CatalogCostCategory, Project, Scenario
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    additional_cost_payload,
    additional_costs_path,
    as_caller,
    commercial_terms_path,
    count_additional_costs,
    make_additional_cost,
    make_allocation,
    make_commercial_terms,
    make_cost_category,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
)

JAN = date(2026, 1, 1)
FEB = date(2026, 2, 1)
MAR = date(2026, 3, 1)
APR = date(2026, 4, 1)
MAY = date(2026, 5, 1)
JUN = date(2026, 6, 1)
JUL = date(2026, 7, 1)


def _read(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict[str, Any]:
    response = client.get(
        additional_costs_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _total(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict[str, Any]:
    return _read(client, project_id, scenario_id)["additional_cost"]


def _months_of(total: dict[str, Any], cost_id: uuid.UUID) -> list[str]:
    [cost] = [c for c in total["assumptions_used"]["costs"] if c["cost_id"] == str(cost_id)]
    return cost["months"]


def _period_occurrences(total: dict[str, Any], cost_id: uuid.UUID) -> list[str]:
    """Every period row the cost appears in, once per appearance — a duplicate is a fan-out."""
    return [
        period["period_month"]
        for period in total["assumptions_used"]["periods"]
        for listed in period["cost_ids"]
        if listed == str(cost_id)
    ]


def _project(session: Session, name: str = "Aurora migration") -> Project:
    return make_project(session, name=name, accessible_to=(IN_SCOPE_USER,))


def _position_with_months(
    session: Session, scenario: Scenario, months: tuple[date, ...], *, suffix: str
) -> Any:
    position = make_staffing_position(
        session, scenario, make_dimension_tuple(session, suffix=suffix), start_date=months[0]
    )
    for month in months:
        make_allocation(session, position, period_month=month)
    return position


# --- K-01: a one-off cost belongs to exactly one period row -------------------------------------


def test_k_01_a_one_off_cost_appears_exactly_once_in_its_month_on_the_scenario_or_a_position(
    client: TestClient, db_session: Session
) -> None:
    """K-01 (D-1, AC-03) — one project, two scenarios; each scenario has two positions with six
    allocation months (January–June).

    Scenario *Baseline*: a one-off scenario-level cost of 1000 in March and a one-off cost of 300 on
    position A in April. Scenario *Variant*: a one-off cost of 50 on its own position in March.

    Right answer for Baseline: **1300.00**, March lists the scenario cost once, April lists the
    position cost once, and no other period row lists either. The three mutations K-01 names each
    give another number:

    - joining the cost to the position's (or the scenario's) allocation months instead of reading
      its own row — the position cost fans out to six (or twelve) rows: 1000 + 6 × 300 = 2800.00,
      and April is not its only period;
    - filtering by the project instead of the scenario — Variant's 50 leaks in: 1350.00;
    - counting a position cost both as a position cost and as a scenario cost — 1600.00.

    Variant is the isolation contrast: **50.00**, and neither of Baseline's costs.
    """
    project = _project(db_session)
    category = make_cost_category(db_session)
    baseline = make_scenario(db_session, project, name="Baseline", currency="EUR")
    variant = make_scenario(db_session, project, name="Variant", currency="EUR")
    six_months = (JAN, FEB, MAR, APR, MAY, JUN)
    position_a = _position_with_months(db_session, baseline, six_months, suffix=" A")
    _position_with_months(db_session, baseline, six_months, suffix=" B")
    variant_position = _position_with_months(db_session, variant, six_months, suffix=" V")

    scenario_cost = make_additional_cost(
        db_session, baseline, category, amount=Decimal("1000.0000"), start_month=MAR
    )
    position_cost = make_additional_cost(
        db_session, baseline, category, amount=Decimal("300.0000"), start_month=APR,
        position=position_a,
    )
    variant_cost = make_additional_cost(
        db_session, variant, category, amount=Decimal("50.0000"), start_month=MAR,
        position=variant_position,
    )

    total = _total(client, project.id, baseline.id)

    assert (total["state"], total["amount"], total["currency"]) == ("calculated", "1300.00", "EUR")
    assert _period_occurrences(total, scenario_cost.id) == ["2026-03-01"]
    assert _period_occurrences(total, position_cost.id) == ["2026-04-01"]
    assert _months_of(total, scenario_cost.id) == ["2026-03-01"]
    assert _months_of(total, position_cost.id) == ["2026-04-01"]
    assert str(variant_cost.id) not in str(total)
    assert len(total["assumptions_used"]["costs"]) == 2

    other = _total(client, project.id, variant.id)
    assert (other["state"], other["amount"]) == ("calculated", "50.00")
    assert _period_occurrences(other, variant_cost.id) == ["2026-03-01"]
    assert str(scenario_cost.id) not in str(other) and str(position_cost.id) not in str(other)


# --- K-02: a recurring cost, every month of its closed range and no other ------------------------


def test_k_02_a_recurring_cost_carries_its_full_amount_in_every_month_of_its_closed_range(
    client: TestClient, db_session: Session
) -> None:
    """K-02 (D-2) — 100.0000 a month, recurring March–June, both ends included.

    The **set** of months is asserted, not only the sum: {March, April, May, June}, and neither
    February nor July. **400.00**. Mutations:

    - the end of the range exclusive — three months, 300.00, June missing;
    - the amount divided by the number of months — 100.00 (25 a month).
    """
    project = _project(db_session)
    category = make_cost_category(db_session)
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    cost = make_additional_cost(
        db_session, scenario, category, amount=Decimal("100.0000"), start_month=MAR,
        end_month=JUN,
    )

    total = _total(client, project.id, scenario.id)

    assert set(_months_of(total, cost.id)) == {
        "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01"
    }
    assert "2026-02-01" not in _months_of(total, cost.id)
    assert "2026-07-01" not in _months_of(total, cost.id)
    assert [p["period_month"] for p in total["assumptions_used"]["periods"]] == [
        "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01"
    ]
    assert (total["state"], total["amount"]) == ("calculated", "400.00")


def test_k_02_months_outside_the_delivery_period_and_the_allocation_still_count(
    client: TestClient, db_session: Session
) -> None:
    """K-02 (ADR-0014, point 3) — no truncation to anything the cost does not itself say.

    The project's delivery period ends on 31 December 2026 (`make_project`); the position has one
    allocation month, November 2026. The cost is recurring November 2026 – February 2027, 250 a
    month, attached to that position: all **four** months count — **1000.00**.

    Mutations:

    - intersecting with the position's allocation months — November only, 250.00;
    - truncating to the delivery period — November and December, 500.00.
    """
    project = _project(db_session)
    assert project.delivery_period_end == date(2026, 12, 31)
    category = make_cost_category(db_session)
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    position = _position_with_months(db_session, scenario, (date(2026, 11, 1),), suffix=" N")
    cost = make_additional_cost(
        db_session, scenario, category, amount=Decimal("250.0000"),
        start_month=date(2026, 11, 1), end_month=date(2027, 2, 1), position=position,
    )

    total = _total(client, project.id, scenario.id)

    assert set(_months_of(total, cost.id)) == {
        "2026-11-01", "2026-12-01", "2027-01-01", "2027-02-01"
    }
    assert (total["state"], total["amount"]) == ("calculated", "1000.00")


# --- K-04: four decimal places in, one rounding out ----------------------------------------------


def test_k_04_four_decimal_places_are_stored_unrounded_and_five_are_a_422_writing_nothing(
    client: TestClient, db_session: Session
) -> None:
    """K-04 (ADR-0002, aneks SC-2-04, point 2; ADR-0008, point 6) — through the real `POST`.

    `1.0025` is stored as `1.0025` — read back from the database and from the API, never `1.00`
    or `1.01`. `1.00255` is a `422` and **no** row is written (the count is asserted, not only the
    status). Mutation "rounding at write" (in the schema, the data layer or a column of scale 2)
    stores `1.00`.
    """
    project = _project(db_session)
    category = make_cost_category(db_session)
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    path = additional_costs_path(project.id, scenario.id)

    refused = client.post(
        path, json=additional_cost_payload(category.id, amount="1.00255"),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert refused.status_code == 422, refused.text
    assert count_additional_costs(db_session) == 0

    accepted = client.post(
        path, json=additional_cost_payload(category.id, amount="1.0025"),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["amount"] == "1.0025"
    stored = db_session.execute(
        sa.select(AdditionalCost.amount).where(
            AdditionalCost.id == uuid.UUID(accepted.json()["id"])
        )
    ).scalar_one()
    assert stored == Decimal("1.0025") and str(stored) == "1.0025"
    assert [c["amount"] for c in _read(client, project.id, scenario.id)["costs"]] == ["1.0025"]


def test_k_04_the_sum_is_rounded_once_at_the_end_never_per_cost_per_month_at_write_or_in_float(
    client: TestClient, db_session: Session
) -> None:
    """K-04 (D-3) — three costs created through the real `POST`, so a rounding anywhere on the way
    in is inside what is tested:

    - A: recurring January–March, 11.9637 a month;
    - B: one-off January, 4.5028;
    - C: one-off February, 9.0811.

    Exact: 3 × 11.9637 + 4.5028 + 9.0811 = 49.4750 → rounded once, half-up: **49.48**. Each
    mutation K-04 names gives another number:

    - rounding per cost: 35.89 + 4.50 + 9.08 = 49.47;
    - rounding per month: 16.47 + 21.04 + 11.96 = 49.47;
    - rounding at write (to the currency's two places): 3 × 11.96 + 4.50 + 9.08 = 49.46;
    - `float` on the path: the binary sum is 49.474999…, which rounds to 49.47 — for either order
      of summation (by cost or by month), and whether the float goes back to `Decimal` exactly or
      through its shortest `repr` (measured when the fixture was chosen).
    """
    project = _project(db_session)
    category = make_cost_category(db_session)
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    path = additional_costs_path(project.id, scenario.id)
    for body in (
        {"amount": "11.9637", "cost_type": "recurring", "start_month": "2026-01-01",
         "end_month": "2026-03-01"},
        {"amount": "4.5028", "start_month": "2026-01-01"},
        {"amount": "9.0811", "start_month": "2026-02-01"},
    ):
        created = client.post(
            path, json=additional_cost_payload(category.id, **body),
            headers=as_caller(IN_SCOPE_USER),
        )
        assert created.status_code == 201, created.text

    total = _total(client, project.id, scenario.id)

    assert (total["state"], total["amount"], total["currency"]) == ("calculated", "49.48", "EUR")
    assert isinstance(total["amount"], str)


# --- K-05: two shapes, never a partial sum -------------------------------------------------------


def test_k_05_no_cost_is_zero_in_the_declared_currency_or_a_named_state_without_one(
    client: TestClient, db_session: Session
) -> None:
    """K-05 — a scenario with no cost: `calculated`, `"0.00"`, in its declared currency; the same
    empty scenario with **no** currency is `no_cost_currency`, `"n/a"`, no currency — never
    `"0.00"` of nothing."""
    project = _project(db_session)
    declared = make_scenario(db_session, project, name="Declared", currency="EUR")
    undeclared = make_scenario(db_session, project, name="Undeclared")

    with_currency = _total(client, project.id, declared.id)
    without_currency = _total(client, project.id, undeclared.id)

    assert (with_currency["state"], with_currency["amount"], with_currency["currency"]) == (
        "calculated", "0.00", "EUR"
    )
    assert (
        without_currency["state"], without_currency["amount"], without_currency["currency"]
    ) == ("no_cost_currency", "n/a", None)


def test_k_05_costs_in_two_currencies_are_a_named_state_never_a_partial_sum(
    client: TestClient, db_session: Session
) -> None:
    """K-05 (D-4), the first branch of `currency_mismatch` — more than one currency among the costs.

    A scenario with no declared currency and two costs, 100 EUR and 200 PLN: `currency_mismatch`,
    `"n/a"`, both currencies named — never `300.00`, never `100.00`, never `0`. The contrast is the
    same scenario shape with both costs in EUR: `calculated`, `300.00`. Mutation: dropping the
    "more than one currency" branch — the answer becomes a converted-by-nobody sum.
    """
    project = _project(db_session)
    category = make_cost_category(db_session)
    mixed = make_scenario(db_session, project, name="Mixed")
    make_additional_cost(db_session, mixed, category, amount=Decimal("100"), start_month=MAR)
    make_additional_cost(
        db_session, mixed, category, amount=Decimal("200"), start_month=APR, currency="PLN"
    )
    uniform = make_scenario(db_session, project, name="Uniform")
    make_additional_cost(db_session, uniform, category, amount=Decimal("100"), start_month=MAR)
    make_additional_cost(db_session, uniform, category, amount=Decimal("200"), start_month=APR)

    refused = _total(client, project.id, mixed.id)
    stated = _total(client, project.id, uniform.id)

    assert (refused["state"], refused["amount"], refused["currency"]) == (
        "currency_mismatch", "n/a", None
    )
    assert refused["assumptions_used"]["currencies"] == ["EUR", "PLN"]
    assert (stated["state"], stated["amount"], stated["currency"]) == (
        "calculated", "300.00", "EUR"
    )


def test_k_05_costs_in_a_currency_other_than_the_scenarios_are_a_named_state(
    client: TestClient, db_session: Session
) -> None:
    """K-05 (D-4), the second branch — one currency among the costs, but not the scenario's.

    The scenario declares EUR; its one cost is 100 PLN: `currency_mismatch`, `"n/a"`. The contrast
    is the same cost in a scenario declaring PLN: `calculated`, `100.00 PLN`. Mutation: dropping
    the comparison with `scenarios.currency` — the answer becomes `100.00 PLN` under a scenario that
    reports in EUR. This branch is independent of the one above: with a single currency, dropping
    the "more than one" branch changes nothing here, and vice versa.
    """
    project = _project(db_session)
    category = make_cost_category(db_session)
    in_eur = make_scenario(db_session, project, name="Reports in EUR", currency="EUR")
    make_additional_cost(
        db_session, in_eur, category, amount=Decimal("100"), start_month=MAR, currency="PLN"
    )
    in_pln = make_scenario(db_session, project, name="Reports in PLN", currency="PLN")
    make_additional_cost(
        db_session, in_pln, category, amount=Decimal("100"), start_month=MAR, currency="PLN"
    )

    refused = _total(client, project.id, in_eur.id)
    stated = _total(client, project.id, in_pln.id)

    assert (refused["state"], refused["amount"], refused["currency"]) == (
        "currency_mismatch", "n/a", None
    )
    assert (stated["state"], stated["amount"], stated["currency"]) == (
        "calculated", "100.00", "PLN"
    )


# --- K-10: rebilled costs enter the sum and never touch the revenue -----------------------------


def test_k_10_a_rebilled_cost_enters_the_sum_and_leaves_the_revenue_byte_for_byte_unchanged(
    client: TestClient, db_session: Session
) -> None:
    """K-10 (D-9; ADR-0014, point 9, Q-5 = A and analyst G-3).

    A scenario whose T&M revenue is calculated and not zero (100 billable hours × 150.0000 =
    15000.00 EUR) and which already has one internal cost of 1000. A `rebilled_to_client` cost of
    234.5600 is added through the real `POST`:

    - the sum of additional costs grows by **exactly** 234.56 — 1000.00 → 1234.56 (the rebilled
      cost is a cost like any other; mutation: excluding it from the sum leaves 1000.00);
    - the revenue response is identical **byte for byte** before and after (mutation: the revenue
      reading `funding_source` and adding the rebilled amount — 15234.56 — or anything else).
    """
    project = _project(db_session)
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    dimensions = make_dimension_tuple(db_session, suffix=" rebilled")
    position = make_staffing_position(db_session, scenario, dimensions, start_date=MAR)
    make_allocation(db_session, position, period_month=MAR)
    make_rate(db_session, dimensions, effective_from=JAN, currency="EUR")
    make_commercial_terms(db_session, scenario)
    category = make_cost_category(db_session, name="Travel")
    make_additional_cost(db_session, scenario, category, amount=Decimal("1000"), start_month=MAR)

    revenue_before = client.get(
        commercial_terms_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert revenue_before.status_code == 200, revenue_before.text
    assert revenue_before.json()["revenue"]["state"] == "calculated"
    assert revenue_before.json()["revenue"]["amount"] == "15000.00"
    assert _total(client, project.id, scenario.id)["amount"] == "1000.00"

    added = client.post(
        additional_costs_path(project.id, scenario.id),
        json=additional_cost_payload(
            category.id, amount="234.5600", funding_source="rebilled_to_client"
        ),
        headers=as_caller(IN_SCOPE_USER),
    )
    assert added.status_code == 201, added.text
    assert added.json()["funding_source"] == "rebilled_to_client"

    revenue_after = client.get(
        commercial_terms_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    after = _total(client, project.id, scenario.id)

    assert (after["state"], after["amount"]) == ("calculated", "1234.56")
    assert revenue_after.status_code == 200
    assert revenue_after.content == revenue_before.content


def _imports_of(relative_path: str) -> set[str]:
    """Every module a source file imports, read from its syntax tree — the helper of
    `test_personnel_cost.py`, for the same reason: `sys.modules` would list whatever the run
    happened to import elsewhere."""
    tree = ast.parse(Path(BACKEND_ROOT, relative_path).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


ADDITIONAL_COST_MODULES = {"app.data.additional_cost", "app.domain.additional_cost"}
REVENUE_MODULES = {
    "app.data.commercial_terms",
    "app.domain.revenue",
    "app.domain.revenue_time_and_material",
    "app.domain.revenue_story_points",
}
PERSONNEL_COST_MODULES = {"app.data.personnel_cost", "app.domain.personnel_cost"}


def test_k_10_the_additional_cost_and_the_revenue_and_personnel_cost_modules_never_meet() -> None:
    """K-10 (D-8; ADR-0014, "Konsekwencje"; rule 10 of the Invariant Guardian) — structurally.

    The two additional-cost modules import nothing of the revenue path and nothing of the personnel
    cost; the six revenue and personnel-cost modules (T&M and Story Points since SC-4-04) import
    nothing of the additional cost. The
    contrast keeps this from passing by reading files that import nothing: the copy registry
    (`app.data.project_writes`) does import the additional-cost data module, and the additional-cost
    data module does import its own domain module.
    """
    for path in ("app/data/additional_cost.py", "app/domain/additional_cost.py"):
        imports = _imports_of(path)
        assert not (imports & REVENUE_MODULES), f"{path} imports the revenue path"
        assert not (imports & PERSONNEL_COST_MODULES), f"{path} imports the personnel cost"
    for path in (
        "app/data/commercial_terms.py",
        "app/domain/revenue.py",
        "app/domain/revenue_time_and_material.py",
        "app/domain/revenue_story_points.py",
        "app/data/personnel_cost.py",
        "app/domain/personnel_cost.py",
    ):
        assert not (_imports_of(path) & ADDITIONAL_COST_MODULES), (
            f"{path} imports the additional cost"
        )

    assert "app.data.additional_cost" in _imports_of("app/data/project_writes.py")
    assert "app.domain.additional_cost" in _imports_of("app/data/additional_cost.py")


def _source_of(module: str) -> str | None:
    """The file of an `app.*` module, relative to the backend root — or `None` for a package or a
    module outside `app` (nothing of `app` is reached through a third-party module)."""
    path = Path(BACKEND_ROOT, *module.split(".")).with_suffix(".py")
    if path.is_file():
        return path.relative_to(BACKEND_ROOT).as_posix()
    package = Path(BACKEND_ROOT, *module.split("."), "__init__.py")
    return package.relative_to(BACKEND_ROOT).as_posix() if package.is_file() else None


def _reachable_from(module: str) -> set[str]:
    """Every `app.*` module reachable from `module` through import statements, at any depth."""
    reached: set[str] = set()
    pending = [module]
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        source = _source_of(current)
        if source is None:
            continue
        pending.extend(name for name in _imports_of(source) if name.startswith("app."))
    return reached - {module}


def test_k_10_no_import_path_at_any_depth_joins_the_additional_cost_to_revenue_or_personnel_cost(
) -> None:
    """K-10 (D-8), the graph and not only its edges (QA, SC-5-05).

    The test above reads the *direct* imports of seven files. Measured on 2026-09-23: adding
    `import app.domain.revenue` to `app.data.scenario_guard` (which the additional-cost data module
    imports), or `import app.domain.additional_cost` to `app.data.write_errors` (which
    `app.data.commercial_terms` imports), leaves that test green while the modules now meet through
    one shared helper. Here the whole reachable set is walked, both directions.

    The two model-free sides only: `app.models.additional_cost` is a table definition and **is**
    reachable from the revenue path today (`commercial_terms` → `staffing` → the model, because the
    staffing copier copies the position-attached costs, ADR-0014 point 10) — that is the contrast
    below, and it proves the walk follows more than one hop: `commercial_terms` does not import the
    model directly.
    """
    for module in sorted(ADDITIONAL_COST_MODULES):
        crossing = _reachable_from(module) & (REVENUE_MODULES | PERSONNEL_COST_MODULES)
        assert crossing == set(), f"{module} reaches {sorted(crossing)}"
    for module in sorted(REVENUE_MODULES | PERSONNEL_COST_MODULES):
        crossing = _reachable_from(module) & ADDITIONAL_COST_MODULES
        assert crossing == set(), f"{module} reaches {sorted(crossing)}"

    assert "app.models.additional_cost" not in _imports_of("app/data/commercial_terms.py")
    assert "app.models.additional_cost" in _reachable_from("app.data.commercial_terms")


def test_the_category_is_a_label_renaming_it_renames_it_on_every_cost_and_moves_no_figure(
    client: TestClient, db_session: Session
) -> None:
    """ADR-0014, point 2 (Q-4 = A) — not a criterion; the accepted limitation, pinned.

    The category name is read live (group 1, no snapshot): after a rename the cost carries the new
    name, and the sum is unchanged — the category takes no part in the calculation.
    """
    project = _project(db_session)
    category = make_cost_category(db_session, name="Licences")
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    make_additional_cost(db_session, scenario, category, amount=Decimal("10"), start_month=MAR)
    before = _read(client, project.id, scenario.id)

    db_session.execute(
        sa.update(CatalogCostCategory)
        .where(CatalogCostCategory.id == category.id)
        .values(name="Software licences")
    )
    db_session.flush()
    after = _read(client, project.id, scenario.id)

    assert before["costs"][0]["category_name"] == "Licences"
    assert after["costs"][0]["category_name"] == "Software licences"
    assert after["additional_cost"]["amount"] == before["additional_cost"]["amount"] == "10.00"


def test_k_04_an_edit_with_five_decimal_places_is_a_422_and_leaves_the_row_as_it_was(
    client: TestClient, db_session: Session
) -> None:
    """K-04 on the edit path — the same bound as on create, so a `PATCH` cannot become the way
    around it. The contrast is the same edit at four places: `200`, stored as sent."""
    project = _project(db_session)
    category = make_cost_category(db_session)
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    cost = make_additional_cost(
        db_session, scenario, category, amount=Decimal("10"), start_month=MAR
    )
    token = _read(client, project.id, scenario.id)["costs"][0]["updated_at"]
    path = f"{additional_costs_path(project.id, scenario.id)}/{cost.id}"

    refused = client.patch(
        path, json={"updated_at": token, "amount": "2.00005"}, headers=as_caller(IN_SCOPE_USER)
    )
    assert refused.status_code == 422, refused.text
    assert _read(client, project.id, scenario.id)["costs"][0]["amount"] == "10.0000"

    accepted = client.patch(
        path, json={"updated_at": token, "amount": "2.0005"}, headers=as_caller(IN_SCOPE_USER)
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["amount"] == "2.0005"


def test_the_request_schema_refuses_a_period_that_does_not_match_the_type_with_a_422(
    client: TestClient, db_session: Session
) -> None:
    """The schema half of K-03's rules (the database half is `test_additional_cost_schema.py`): a
    recurring cost without an end, a one-off cost with one, an inverted range, a mid-month date and
    a non-positive amount are each a `422` naming the problem — never a `500` from a violated
    CHECK — and nothing is written."""
    project = _project(db_session)
    category = make_cost_category(db_session)
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    path = additional_costs_path(project.id, scenario.id)

    for body in (
        {"cost_type": "recurring", "start_month": "2026-03-01"},
        {"cost_type": "one_off", "start_month": "2026-03-01", "end_month": "2026-06-01"},
        {"cost_type": "recurring", "start_month": "2026-06-01", "end_month": "2026-03-01"},
        {"start_month": "2026-03-15"},
        {"amount": "0"},
        {"amount": "-5.0000"},
        {"funding_source": "vendor"},
    ):
        response = client.post(
            path, json=additional_cost_payload(category.id, **body),
            headers=as_caller(IN_SCOPE_USER),
        )
        assert response.status_code == 422, (body, response.text)
    assert count_additional_costs(db_session) == 0


def test_r_02_a_recurring_cost_of_sixty_months_is_accepted_and_of_sixty_one_is_a_422(
    client: TestClient, db_session: Session
) -> None:
    """R-02 (reviewer, gate 2; ADR-0014, point 4) — a recurring range is bounded at
    `MAX_RECURRING_MONTHS` (= `MAX_ALLOCATION_MONTHS`, 60), both ends included, in the request
    schema. The shape of `test_staffing_positions.py`'s bound test: one past the bound is a `422`
    naming the rule with **no** row written, and exactly at the bound is accepted and written.

    January 2026 – December 2030 is 60 months; to January 2031, 61. The same bound on an edit that
    carries both ends. Mutation: an off-by-one (`>=`) refuses the 60-month range; no bound accepts
    the 61-month one.
    """
    assert MAX_RECURRING_MONTHS == 60
    project = _project(db_session)
    category = make_cost_category(db_session)
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    path = additional_costs_path(project.id, scenario.id)

    def recurring(end_month: str) -> dict[str, object]:
        return additional_cost_payload(
            category.id, cost_type="recurring", start_month="2026-01-01", end_month=end_month
        )

    too_long = client.post(path, json=recurring("2031-01-01"), headers=as_caller(IN_SCOPE_USER))
    assert too_long.status_code == 422, too_long.text
    assert "60 months" in too_long.text
    assert count_additional_costs(db_session) == 0

    at_the_bound = client.post(
        path, json=recurring("2030-12-01"), headers=as_caller(IN_SCOPE_USER)
    )
    assert at_the_bound.status_code == 201, at_the_bound.text
    assert count_additional_costs(db_session) == 1
    created = at_the_bound.json()
    assert len(_months_of(_total(client, project.id, scenario.id), created["id"])) == 60

    widened = client.patch(
        f"{path}/{created['id']}",
        json={
            "updated_at": created["updated_at"],
            "start_month": "2026-01-01",
            "end_month": "2031-01-01",
        },
        headers=as_caller(IN_SCOPE_USER),
    )
    assert widened.status_code == 422, widened.text
