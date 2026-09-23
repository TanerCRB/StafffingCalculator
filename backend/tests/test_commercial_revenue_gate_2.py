"""SC-4-01, gate-2 findings R-01..R-04 — each proven by its own test, next to a contrast.

- **R-01** (Medium, fixed): a month is priced when the internal windows overlapping it **cover every
  day of it and share one (selling rate, currency)** — not only when a single window contains it.
  A mid-month change of the *cost* rate alone no longer unprices the month; a change of the
  *selling* rate or of the currency still does (the existing
  `test_commercial_revenue.py::test_k_03_a_rate_change_inside_a_month_…` keeps that half), and so
  does a gap. The same predicate freezes the snapshot and reads it back.
- **R-02/R-03** (defensive): a rule of a model *this version of the code* does not know answers with
  the named state `unsupported_model_type` on the read, and refuses the project copy with a `409`
  and nothing copied — never a `KeyError`/`500`, never a rule copied without its details. Simulated
  by removing the known model from the code's registries (`monkeypatch`), because the database
  CHECK admits no other model yet.
- **R-04**: the two implementer decisions, now pinned — an empty plan with a rule is `0.00` in the
  scenario's currency (or the named `no_revenue_currency` when it declares none), and a month with
  zero billable hours and no rate is still `no_rate`.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.data import commercial_terms as commercial_terms_module
from app.models import CatalogDefaultRate
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    commercial_terms_path,
    make_allocation,
    make_commercial_terms,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
)

MAR = date(2026, 3, 1)
APR = date(2026, 4, 1)
TM = {"model_type": "time_and_material"}
SELLING = Decimal("200.0000")


def _plan(session: Session, *, months=(MAR,), currency: str | None = None, name="Aurora"):
    """A project in scope, a draft scenario, one position (headcount 2) with the given months, each
    at 100 billable hours (the `make_allocation` default)."""
    project = make_project(session, name=name, accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline", currency=currency)
    dimensions = make_dimension_tuple(session, suffix=f" {name}")
    position = make_staffing_position(session, scenario, dimensions, start_date=months[0])
    for month in months:
        make_allocation(session, position, period_month=month)
    return project, scenario, dimensions, position


def _set_rule(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> None:
    response = client.post(
        commercial_terms_path(project_id, scenario_id), json=TM, headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 201, response.text


def _read(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict:
    response = client.get(
        commercial_terms_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- R-01: a cost-only boundary inside a month does not unprice it --------------------------------


def _split_march(
    session: Session, dimensions, *, second_currency="PLN", second_from=date(2026, 3, 16)
):
    """Two internal windows split inside March: the same selling rate, **different cost rates**.

    The first ends on 15 March; the second starts on `second_from` (the 16th — adjacent — unless a
    test opens a gap).
    """
    first = make_rate(
        session,
        dimensions,
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 3, 15),
        default_cost_rate=Decimal("120.0000"),
        default_selling_rate=SELLING,
        currency="PLN",
    )
    second = make_rate(
        session,
        dimensions,
        effective_from=second_from,
        default_cost_rate=Decimal("135.0000"),
        default_selling_rate=SELLING,
        currency=second_currency,
    )
    return first, second


def test_r_01_a_mid_month_change_of_the_cost_rate_alone_leaves_the_month_priced(
    client: TestClient, db_session: Session
) -> None:
    """R-01 — two windows in March, same selling rate and currency, different cost: 100 × 200.

    Before the fix this was `no_rate` (no single window contains March). Both windows are named in
    `assumptions_used`, because both price the month. Mutation: back to "one window contains the
    month" (`valid_period @> whole_month`) — `no_rate` again.

    The contrast — a mid-month change of the **selling** rate still gives `no_rate` — is the
    existing `test_commercial_revenue.py::test_k_03_a_rate_change_inside_a_month_leaves_that_month_
    without_a_rate`, untouched by this fix and still green.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    first, second = _split_march(db_session, dimensions)
    _set_rule(client, project.id, scenario.id)

    revenue = _read(client, project.id, scenario.id)["revenue"]

    assert (revenue["state"], revenue["amount"], revenue["currency"]) == (
        "calculated",
        "20000.00",
        "PLN",
    )
    assert [w["source_rate_id"] for w in revenue["assumptions_used"]["rate_windows"]] == [
        str(first.id),
        str(second.id),
    ]


def test_r_01_a_mid_month_change_of_currency_at_the_same_selling_figure_is_still_no_rate(
    client: TestClient, db_session: Session
) -> None:
    """R-01, the currency half of "one price" — 200 PLN then 200 EUR inside March is two prices.

    Mutation: comparing only `default_selling_rate` across the month's windows (dropping the
    currency from the predicate) — March would be priced.
    """
    project, scenario, dimensions, position = _plan(db_session)
    _split_march(db_session, dimensions, second_currency="EUR")
    _set_rule(client, project.id, scenario.id)

    revenue = _read(client, project.id, scenario.id)["revenue"]

    assert (revenue["state"], revenue["amount"]) == ("no_rate", "n/a")
    assert revenue["assumptions_used"]["unresolved_months"] == [
        {"position_id": str(position.id), "period_month": "2026-03-01"}
    ]


def test_r_01_a_gap_between_two_equal_windows_inside_a_month_is_still_no_rate(
    client: TestClient, db_session: Session
) -> None:
    """R-01, the coverage half — the same price on both sides, but 16 March belongs to no window.

    Mutation: "all overlapping windows share one price" without "and cover every day" — March would
    be priced across a day the catalogue does not price.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    _split_march(db_session, dimensions, second_from=date(2026, 3, 17))
    _set_rule(client, project.id, scenario.id)

    assert _read(client, project.id, scenario.id)["revenue"]["state"] == "no_rate"


def test_r_01_the_approval_freezes_every_piece_of_a_split_month_and_the_snapshot_prices_it(
    client: TestClient, db_session: Session
) -> None:
    """R-01 on the snapshot path — both windows of March are frozen, and the approved scenario reads
    20000.00 from them after the catalogue's selling rates are raised.

    Mutations: freezing only the window that contains the month's first day (one row — the snapshot
    reader then sees a gap and answers `no_rate`); a snapshot reader still asking "one window
    contains the month" (`no_rate`); a reader reading the live catalogue (91 000-ish, not 20000.00).
    """
    project, scenario, dimensions, _ = _plan(db_session)
    first, second = _split_march(db_session, dimensions)
    _set_rule(client, project.id, scenario.id)

    approval = client.post(approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))

    assert approval.status_code == 200, approval.text
    assert approval.json()["snapshot"]["catalog_default_rates"] == 2
    db_session.execute(
        sa.update(CatalogDefaultRate)
        .where(CatalogDefaultRate.id.in_([first.id, second.id]))
        .values(default_selling_rate=Decimal("910.0000"))
    )
    db_session.flush()
    db_session.expire_all()

    revenue = _read(client, project.id, scenario.id)["revenue"]
    assert (revenue["state"], revenue["amount"], revenue["assumptions_used"]["rate_source"]) == (
        "calculated",
        "20000.00",
        "approved_snapshot",
    )


# --- R-02/R-03: a model this version of the code does not know ------------------------------------


def test_r_02_a_rule_of_a_model_this_version_cannot_price_is_a_named_state_not_a_500(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-02 — the dispatcher meets a `model_type` it has no formula for: `200`, state
    `unsupported_model_type`, amount `"n/a"`, and the stored model named in the answer.

    Simulated by emptying `REVENUE_BY_MODEL`, i.e. a code version that does not know the stored
    model — the mixed-version window of ADR-0001 in which a later model's rows exist before every
    instance runs the code for them. `client` re-raises server exceptions, so the `KeyError` this
    replaces would fail the test outright. The contrast is the same read with the registry intact.
    """
    project, scenario, dimensions, _ = _plan(db_session)
    make_rate(db_session, dimensions, effective_from=date(2026, 1, 1), currency="PLN")
    make_commercial_terms(db_session, scenario)
    assert _read(client, project.id, scenario.id)["revenue"]["state"] == "calculated"

    monkeypatch.setattr(commercial_terms_module, "REVENUE_BY_MODEL", {})
    body = _read(client, project.id, scenario.id)

    assert body["commercial_terms"]["model_type"] == "time_and_material"
    assert body["revenue"]["state"] == "unsupported_model_type"
    assert body["revenue"]["amount"] == "n/a"
    assert body["revenue"]["assumptions_used"]["model_type"] == "time_and_material"


def test_r_03_copying_a_rule_of_a_model_this_version_cannot_copy_is_refused_and_copies_nothing(
    committing_client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-03 — the copier meets a model outside `DETAIL_TABLE_BY_MODEL`: the whole project copy is a
    `409` naming the model, and nothing of it is committed — no project, no scenario, no rule.

    Before the fix the copier copied `commercial_terms` and silently skipped the details row: a
    permanent `incomplete_commercial_terms` on the copy with no path to repair it. Counted from a
    separate connection, so "the transaction was rolled back" is a fact about committed rows. The
    contrast is the same copy with the registry intact: `201` and two rules.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project, scenario, _, _ = _plan(setup)
        make_commercial_terms(setup, scenario)
        project_id = project.id
        setup.commit()

    def counts() -> tuple[int, int, int]:
        with engine.connect() as connection:
            return tuple(
                connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in ("projects", "scenarios", "commercial_terms")
            )

    before = counts()
    monkeypatch.setattr(commercial_terms_module, "DETAIL_TABLE_BY_MODEL", {})
    refused = committing_client.post(
        f"/projects/{project_id}/copy", headers=as_caller(IN_SCOPE_USER)
    )

    assert refused.status_code == 409, refused.text
    assert "time_and_material" in refused.json()["detail"]
    assert counts() == before == (1, 1, 1)

    monkeypatch.undo()
    accepted = committing_client.post(
        f"/projects/{project_id}/copy", headers=as_caller(IN_SCOPE_USER)
    )
    assert accepted.status_code == 201, accepted.text
    assert counts() == (2, 2, 2)


# --- R-04: the two implementer decisions, pinned --------------------------------------------------


def test_r_04_a_rule_over_a_plan_with_no_allocation_rows_is_zero_in_the_scenarios_currency(
    client: TestClient, db_session: Session
) -> None:
    """R-04 (1) — a position with no month rows, a rule, a scenario declared in EUR: `0.00 EUR`.

    A true sum over an empty plan, reached only *with* a rule (so never mistaken for
    `no_commercial_terms`), and it names a currency, as ADR-0003's result shape requires. The
    contrast is the next test: the same empty plan without a scenario currency is a named state.
    """
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    make_staffing_position(db_session, scenario, make_dimension_tuple(db_session), start_date=MAR)
    _set_rule(client, project.id, scenario.id)

    revenue = _read(client, project.id, scenario.id)["revenue"]

    assert (revenue["state"], revenue["amount"], revenue["currency"]) == (
        "calculated",
        "0.00",
        "EUR",
    )
    assert revenue["assumptions_used"]["rate_windows"] == []


def test_r_04_an_empty_plan_in_a_scenario_without_a_currency_is_a_named_state_never_null_currency(
    client: TestClient, db_session: Session
) -> None:
    """R-04 (1), second half — no priced month and no scenario currency: `no_revenue_currency`.

    Before the fix this was `calculated` with `currency: null` — a result without the currency
    ADR-0003 point 9 requires. Mutation: returning `0.00` with `currency: null` again.
    """
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    _set_rule(client, project.id, scenario.id)

    revenue = _read(client, project.id, scenario.id)["revenue"]

    assert (revenue["state"], revenue["amount"], revenue["currency"]) == (
        "no_revenue_currency",
        "n/a",
        None,
    )


def test_r_04_a_month_with_zero_billable_hours_and_no_rate_is_still_no_rate(
    client: TestClient, db_session: Session
) -> None:
    """R-04 (2) — "is this month priced" is a question about the catalogue, not about the hours.

    March has 100 billable hours and a rate; April has **zero** billable hours and no window at all.
    The answer is `no_rate` naming April — not 20000.00. Mutation: skipping zero-hour months before
    the rate check (e.g. `WHERE billable_hours > 0`, or `continue` on a zero product) — the revenue
    becomes 20000.00. The contrast is the same plan with April priced: 20000.00, April adding 0.
    """
    project, scenario, dimensions, position = _plan(db_session, months=(MAR,))
    make_allocation(db_session, position, period_month=APR, billable_hours=Decimal("0.00"))
    march_only = make_rate(
        db_session,
        dimensions,
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 3, 31),
        default_selling_rate=SELLING,
        currency="PLN",
    )
    _set_rule(client, project.id, scenario.id)

    unpriced = _read(client, project.id, scenario.id)["revenue"]
    assert (unpriced["state"], unpriced["amount"]) == ("no_rate", "n/a")
    assert unpriced["assumptions_used"]["unresolved_months"] == [
        {"position_id": str(position.id), "period_month": "2026-04-01"}
    ]

    db_session.execute(
        sa.update(CatalogDefaultRate)
        .where(CatalogDefaultRate.id == march_only.id)
        .values(effective_to=None)
    )
    db_session.flush()
    db_session.expire_all()
    priced = _read(client, project.id, scenario.id)["revenue"]
    assert (priced["state"], priced["amount"]) == ("calculated", "20000.00")
