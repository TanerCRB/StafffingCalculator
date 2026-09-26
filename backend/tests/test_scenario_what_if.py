"""SC-6-04, K-01..K-06 — the salary-raise "what-if": a scenario's whole-life result recomputed
against a hypothetical percentage change to its personnel cost rates, never persisted (F-09 pt.3,
Issue #88; ADR-0015).

`GET /projects/{project_id}/scenarios/{scenario_id}/what-if?salary_raise_percent=<decimal>` reuses
every fixture and figure `tests/test_scenario_results.py` already proved (`_full_scenario`,
`_ensure_statutory_bypass`, `EVERYTHING`, AC-01's own numbers: revenue 20000.00, base cost 12000.00,
additional cost 2000.00, included_cost 14000.00, profit 6000.00, margin 30.00, markup 42.86) —
composed rather than reimplemented, so a divergence between the two endpoints' arithmetic cannot
hide behind two different fixtures computing two different "expected" numbers.
"""

import ast
import uuid
from dataclasses import replace as dataclass_replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.data.scenario_what_if as scenario_what_if_module
from app.core.identity import CallerIdentity, Permission
from app.data.personnel_cost import scenario_cost_for_caller as real_scenario_cost_for_caller
from app.models import ScenarioStatus
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    allocation_path,
    as_caller,
    caller_holding,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    staffing_path,
)
from tests.test_scenario_results import (
    EVERYTHING,
    MAR,
    _ensure_statutory_bypass,
    _full_scenario,
    results_path,
)

FEB = date(2026, 2, 1)

WITHOUT_PERSONNEL_COSTS_READ = EVERYTHING - {Permission.PERSONNEL_COSTS_READ}

SCENARIO_WHAT_IF_NOT_FOUND_DETAIL = "Scenario not found."
"""Duplicated from `app.api.scenario_what_if` on purpose (the same reasoning
`test_scenario_results_access.py` gives for its own copy of the sibling constant): the test asserts
the literal body a client receives, not a reference to the module under test agreeing with itself.
"""


def what_if_path(
    project_id: uuid.UUID, scenario_id: uuid.UUID, salary_raise_percent: str
) -> str:
    return (
        f"/projects/{project_id}/scenarios/{scenario_id}/what-if"
        f"?salary_raise_percent={salary_raise_percent}"
    )


def _without_rate_source(personnel_cost: dict[str, Any]) -> dict[str, Any]:
    """`personnel_cost` with `assumptions_used.rate_source` taken out — the one field K-01 expects
    to differ between the real result and a `0%` what-if (`live_catalog` vs
    `what_if_hypothetical`); everything else must not."""
    assumptions = dict(personnel_cost["assumptions_used"])
    assumptions.pop("rate_source")
    return {**personnel_cost, "assumptions_used": assumptions}


def _edit_hours(
    client: TestClient,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    month: date,
    **hours: str,
) -> None:
    """The real allocation edit (`PATCH`), with the position's current concurrency token — the same
    pattern `tests/test_personnel_cost.py::_edit_month` uses. The closest real, persisted write this
    repository has to a "rate edit" that moves a scenario's personnel cost: there is no HTTP write
    path for `catalog_default_rates` (`tests/conftest.py::make_rate`/`_set_catalog` write it
    directly, no endpoint), so K-02's contrast bracket uses the staffing hours edit instead — a
    different figure feeding the same formula (`planned_allocation_hours`, ADR-0013 point 3), which
    is what actually exercises "does a genuine write move `GET .../results`", the fact K-02 needs
    proven and not the literal word "rate"."""
    token = client.get(
        staffing_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER)
    ).json()["positions"][0]["updated_at"]
    response = client.patch(
        allocation_path(project_id, scenario_id, position_id, month),
        json={"updated_at": token, **hours},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert response.status_code == 200, response.text


# --- K-01: two carriers, neither sufficient alone -------------------------------------------------


def _imports_of(relative_path: str) -> set[str]:
    tree = ast.parse(Path(BACKEND_ROOT, relative_path).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _imported_names_of(relative_path: str) -> set[str]:
    tree = ast.parse(Path(BACKEND_ROOT, relative_path).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _attribute_names_of(relative_path: str) -> set[str]:
    tree = ast.parse(Path(BACKEND_ROOT, relative_path).read_text(encoding="utf-8"))
    return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}


def test_k_01_the_what_if_module_calls_the_four_reused_functions_and_writes_nothing() -> None:
    """K-01, structural half (ADR-0015, point 2) — `app.data.scenario_what_if`:

    1. imports the four functions/readers ADR-0015 names as the reuse boundary
       (`base_personnel_cost`, `paid_absence_cost`, `_worked_months`, `paid_absence_months`) and
       their frozen input/output dataclasses (`WorkedMonth`, `MonthCostRate`), never a parallel
       arithmetic of its own;
    2. never calls `session.execute`/`.add`/`.flush`/`.merge`/`.commit`/`.delete` anywhere in its
       own source — every statement runs inside the four reused readers (and the three scope/race
       calls the module composes, `commercial_terms_for_caller`/`scenario_cost_for_caller`/
       `additional_costs_for_caller`), never here;
    3. imports no SQLAlchemy-mapped model beyond `app.models.scenario` (`Scenario`/
       `ScenarioStatus`) — the one ORM object on this path, read-only (`.status`), never assigned
       to.

    Mutation this kills: a `session.add(...)`/`.flush()` written directly into this module, which
    would attach a mutation to the session's identity map even though `WorkedMonth`/`MonthCostRate`/
    `CostRateWindow` are plain frozen dataclasses (`app.domain.personnel_cost`) that cannot carry
    one on their own. Contrast, proving this is not a vacuous "the file is short" check:
    `app.data.personnel_cost` (the module the four reused functions actually live in) *does* call
    `session.execute` — this test asserts that of the *what-if* module specifically, not of the
    call graph it reuses.
    """
    relative_path = "app/data/scenario_what_if.py"

    imported_names = _imported_names_of(relative_path)
    assert {
        "base_personnel_cost", "WorkedMonth", "MonthCostRate", "WHAT_IF_HYPOTHETICAL",
    } <= imported_names
    assert "paid_absence_cost" in imported_names
    assert {"_worked_months", "scenario_cost_for_caller", "ScenarioCostView"} <= imported_names
    assert "paid_absence_months" in imported_names

    called_attrs = _attribute_names_of(relative_path)
    assert not ({"execute", "add", "flush", "merge", "commit", "delete"} & called_attrs), (
        called_attrs
    )

    # The contrast: the module it reuses *does* call `session.execute` — proving the check above is
    # about this module's own source, not a universally empty set.
    assert "execute" in _attribute_names_of("app/data/personnel_cost.py")

    imports = _imports_of(relative_path)
    orm_model_imports = {module for module in imports if module.startswith("app.models")}
    assert orm_model_imports == {"app.models.scenario"}, orm_model_imports


def test_k_01_zero_percent_raise_is_byte_identical_to_the_real_result_except_rate_source(
    client: TestClient, db_session: Session
) -> None:
    """K-01, behavioural half — `salary_raise_percent=0` (`Decimal`'s neutral element for this
    multiplier: `1 + 0/100` is the exact `Decimal("1")`) reproduces `GET .../results`'s own figures
    to the digit, on the same scenario, in the same request window.

    The one field that *must* differ is named and asserted to differ
    (`personnel_cost.assumptions_used.rate_source`: `live_catalog` on the real endpoint,
    `what_if_hypothetical` here) — `_without_rate_source` takes only that one field out before the
    equality check, so a mutation smuggling a second, accidental difference anywhere else in the
    payload still fails this test.
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="ZeroRaise")

    with caller_holding(*EVERYTHING):
        real = client.get(results_path(project.id, scenario.id))
        what_if = client.get(what_if_path(project.id, scenario.id, "0"))
    assert real.status_code == 200, real.text
    assert what_if.status_code == 200, what_if.text
    real_body = real.json()
    what_if_body = what_if.json()

    assert what_if_body["scenario_id"] == real_body["scenario_id"]
    assert what_if_body["scenario_status"] == real_body["scenario_status"]
    assert Decimal(what_if_body["salary_raise_percent"]) == Decimal("0")
    assert what_if_body["revenue"] == real_body["revenue"]
    assert what_if_body["additional_cost"] == real_body["additional_cost"]
    assert _without_rate_source(what_if_body["personnel_cost"]) == _without_rate_source(
        real_body["personnel_cost"]
    )
    assert what_if_body["personnel_cost"]["assumptions_used"]["rate_source"] == (
        "what_if_hypothetical"
    )
    assert real_body["personnel_cost"]["assumptions_used"]["rate_source"] == "live_catalog"
    assert what_if_body["included_cost"] == real_body["included_cost"] == "14000.00"
    assert what_if_body["profit"] == real_body["profit"] == "6000.00"
    assert what_if_body["margin"] == real_body["margin"] == "30.00"
    assert what_if_body["markup"] == real_body["markup"] == "42.86"


def test_k_01_nonzero_raise_differs_on_a_mixed_state_month_proving_per_month_substitution(
    client: TestClient, db_session: Session
) -> None:
    """K-01, the contrast that tells true per-month resolution apart from a naive
    `included_cost × (1+p)` aggregate shortcut. February is resolved (one catalogue window), March
    has none at all — ADR-0013's "one unresolved month withholds the whole component" makes the
    *aggregate* `personnel_cost.state`/`included_cost` `no_cost_rate`/`"n/a"` at every raise
    magnitude, `0%` included: a shortcut applied at the aggregate level has no number to scale and
    would answer identically at `0%` and at `10%`, both `"n/a"`.

    A correct, per-month substitution still raises the *resolved* February window before the
    withhold decision, so `assumptions_used.rate_windows` differs between the two calls even while
    `state`/`included_cost` do not — the one place a naive shortcut and a true substitution are
    forced to disagree on this fixture.
    """
    project = make_project(
        db_session, name="MixedState", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario = make_scenario(db_session, project, name="MixedState", currency="PLN")
    dimensions = make_dimension_tuple(db_session, suffix=" MixedState")
    position = make_staffing_position(db_session, scenario, dimensions, start_date=FEB)
    make_allocation(db_session, position, period_month=FEB)
    make_allocation(db_session, position, period_month=MAR)
    make_rate(
        db_session, dimensions, effective_from=FEB, effective_to=date(2026, 2, 28),
        default_cost_rate=Decimal("120.0000"), currency="PLN",
    )
    # March has no catalogue window at all — `no_cost_rate` for that month, and ADR-0013 point 2
    # withholds the whole component for it, not only March's share.

    with caller_holding(*EVERYTHING):
        zero = client.get(what_if_path(project.id, scenario.id, "0"))
        raised = client.get(what_if_path(project.id, scenario.id, "10"))
    assert zero.status_code == 200, zero.text
    assert raised.status_code == 200, raised.text
    zero_cost = zero.json()["personnel_cost"]
    raised_cost = raised.json()["personnel_cost"]

    assert zero_cost["state"] == "no_cost_rate"
    assert raised_cost["state"] == "no_cost_rate"
    assert zero.json()["included_cost"] == "n/a"
    assert raised.json()["included_cost"] == "n/a"

    zero_windows = zero_cost["assumptions_used"]["rate_windows"]
    raised_windows = raised_cost["assumptions_used"]["rate_windows"]
    assert len(zero_windows) == len(raised_windows) == 1
    assert zero_windows[0]["default_cost_rate"] == "120.0000"
    assert raised_windows[0]["default_cost_rate"] == "132.00000"
    assert zero_windows[0]["default_cost_rate"] != raised_windows[0]["default_cost_rate"]


# --- K-02: no scenario data row changes, proven with a genuine write as the contrast --------------


def test_k_02_calling_what_if_writes_nothing_and_a_real_write_still_shows_up(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — `session.new`/`.dirty`/`.deleted` are empty before and after a what-if call (the same
    `Session` the `client` fixture binds every request to, so a flush inside the request would be
    visible here), and `GET .../results` is byte-identical read before and after it. The contrast
    brackets the identical before/after comparison around a real, persisted write through the
    existing allocation-edit endpoint (`PATCH .../allocations/{month}`) — that second read *must*
    differ, or the equality check above would be proving nothing but "this test does not happen to
    write" (see `_edit_hours` for why this, not a rate edit, is the write used).
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, position = _full_scenario(db_session, name="NoWrite")
    db_session.flush()

    before = (set(db_session.new), set(db_session.dirty), set(db_session.deleted))

    with caller_holding(*EVERYTHING):
        before_results = client.get(results_path(project.id, scenario.id))
        what_if = client.get(what_if_path(project.id, scenario.id, "25"))
        after_results = client.get(results_path(project.id, scenario.id))

    assert what_if.status_code == 200, what_if.text
    assert before_results.status_code == after_results.status_code == 200
    assert before_results.json() == after_results.json()

    after = (set(db_session.new), set(db_session.dirty), set(db_session.deleted))
    assert before == after == (set(), set(), set())

    # The contrast: the same before/after comparison, this time around a genuine, persisted write.
    with caller_holding(*EVERYTHING):
        before_write = client.get(results_path(project.id, scenario.id)).json()
    _edit_hours(
        client, project.id, scenario.id, position.id, MAR, planned_allocation_hours="150.00"
    )
    with caller_holding(*EVERYTHING):
        after_write = client.get(results_path(project.id, scenario.id)).json()
    assert before_write != after_write


def _scenario_row(db_session: Session, scenario_id: uuid.UUID) -> tuple[object, ...]:
    """The scenario's own row, by a raw `SELECT` — deliberately bypassing the ORM identity map.

    `session.expire_all()` first, so this is a genuine round trip to the database and not a read of
    Python-side state `db_session` already holds. `updated_at` is included as the canary: every
    column of this table shares one `onupdate=func.now()` trigger path (`app.models.scenario`), so
    *any* real `UPDATE` of this row — whichever column it touches — moves this one timestamp.
    """
    db_session.expire_all()
    return db_session.execute(
        sa.text(
            "SELECT name, status, currency, target_margin_percent, updated_at "
            "FROM scenarios WHERE id = :id"
        ),
        {"id": str(scenario_id)},
    ).one()


def test_k_02_the_scenario_row_itself_is_unchanged_in_the_database_not_just_the_session_bookkeeping(
    client: TestClient, db_session: Session
) -> None:
    """K-02, hardened — a contrast on the *mechanism* the test above uses to prove zero persistence,
    not on its scenario.

    `session.new`/`.dirty`/`.deleted` being empty (the test above) is not, by itself, proof that no
    row changed: SQLAlchemy's default `autoflush` clears an attribute assigned on an
    already-identity-mapped object out of `session.dirty` the moment *any* later statement runs in
    the same session — which `app.data.scenario_what_if` issues several of, via the reused readers,
    after the point at which it first touches `cost_view.scenario`. A stray assignment onto that
    object (e.g. a debugging label, or any other mutation of the `Scenario` the module already
    holds) would autoflush into the transaction and be gone from `dirty` by the time the test above
    takes its "after" snapshot, even though the row underneath had genuinely changed.

    This test re-reads the row directly instead (`_scenario_row`, a raw `SELECT` past the identity
    map), before and after a what-if call, and asserts byte-for-byte equality including `updated_at`
    — the one column every write to this row moves, whichever other column it was about.
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="RowUnchanged")
    db_session.flush()

    before = _scenario_row(db_session, scenario.id)
    with caller_holding(*EVERYTHING):
        what_if = client.get(what_if_path(project.id, scenario.id, "25"))
    assert what_if.status_code == 200, what_if.text
    after = _scenario_row(db_session, scenario.id)

    assert before == after


# --- K-03: out of scope is indistinguishable from absent ------------------------------------------


def test_k_03_a_scenario_outside_the_callers_scope_is_the_same_404_as_no_scenario(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — the same three-address proof `test_scenario_results_access.py`'s own K-03 makes, for
    a caller holding **every** permission (so the `404` cannot be an accidental permission gate)."""
    _ensure_statutory_bypass(db_session)
    mine_project, mine_scenario, _ = _full_scenario(db_session, name="MineWhatIf")
    theirs_project = make_project(
        db_session, name="TheirsWhatIf", accessible_to=(OUT_OF_SCOPE_USER,)
    )
    theirs_scenario = make_scenario(db_session, theirs_project, name="Theirs")

    addresses = [
        what_if_path(theirs_project.id, theirs_scenario.id, "0"),
        what_if_path(mine_project.id, uuid.uuid4(), "0"),
        what_if_path(uuid.uuid4(), mine_scenario.id, "0"),
    ]
    with caller_holding(*EVERYTHING):
        responses = [client.get(path) for path in addresses]

    for response in responses:
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": SCENARIO_WHAT_IF_NOT_FOUND_DETAIL}
    assert len({response.content for response in responses}) == 1

    with caller_holding(*EVERYTHING):
        mine_response = client.get(what_if_path(mine_project.id, mine_scenario.id, "0"))
    assert mine_response.status_code == 200, mine_response.text
    assert mine_response.json()["profit"] == "6000.00"


# --- K-04: the personnel-cost conjunction is inherited, not reinvented on the endpoint ------------


def test_k_04_personnel_cost_fields_are_null_without_the_flag_but_the_endpoint_stays_reachable(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — `RESULTS_READ` alone reaches the endpoint (`200`); `profit`/`margin`/`markup`/
    `included_cost`/`personnel_cost.amount` are `null` without `PERSONNEL_COSTS_READ`, while
    `revenue`/`additional_cost` stay visible — the same shape `SC-7-01`'s own K-06 proved for
    `GET .../results`, inherited unchanged (`app.api.response_shaping.
    shape_scenario_what_if_salary_raise` calls the same `_without_scenario_personnel_costs`/
    `_without_scenario_profitability`).

    Mutation this kills: moving the conjunction from the field-shaping layer onto this endpoint's
    `require_permission` dependency — row 1 below would then be `403`, not `200` with the four
    fields `null` (the exact mutation the SC-7-01 mutation log already recorded once, per
    `docs/architecture/capabilities.md`).
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="GateWhatIf")
    path = what_if_path(project.id, scenario.id, "0")

    with caller_holding(*WITHOUT_PERSONNEL_COSTS_READ):
        withheld = client.get(path)
    assert withheld.status_code == 200, withheld.text
    body = withheld.json()
    assert body["profit"] is None
    assert body["margin"] is None
    assert body["markup"] is None
    assert body["included_cost"] is None
    assert body["personnel_cost"]["amount"] is None
    assert body["revenue"]["amount"] == "20000.00"
    assert body["additional_cost"]["amount"] == "2000.00"
    assert "6000.00" not in withheld.text
    assert "12000.00" not in withheld.text

    with caller_holding(*EVERYTHING):
        shown = client.get(path)
    assert shown.status_code == 200, shown.text
    shown_body = shown.json()
    assert shown_body["profit"] == "6000.00"
    assert shown_body["personnel_cost"]["amount"] == "12000.00"


# --- K-05: revenue is invariant to the raise, cost-derived fields are not -------------------------


def test_k_05_revenue_is_identical_across_raise_magnitudes_but_cost_fields_differ(
    client: TestClient, db_session: Session
) -> None:
    """K-05 — two calls on the same scenario, differing only in `salary_raise_percent` (5% and
    50%): `revenue` and `additional_cost` are byte-identical (the raise never touches them, F-06);
    `personnel_cost.amount` and `profit` differ, and to the exact figures the formula predicts (5%:
    12000×1.05 = 12600.00; 50%: 12000×1.50 = 18000.00).

    Mutation this kills: the multiplier leaking into `revenue` instead of (or alongside) the cost
    fields — plausible since `revenue` and `personnel_cost.amount` are sibling `Decimal` fields on
    the same composed response.
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="RaiseMagnitudes")

    with caller_holding(*EVERYTHING):
        small = client.get(what_if_path(project.id, scenario.id, "5"))
        big = client.get(what_if_path(project.id, scenario.id, "50"))
    assert small.status_code == 200, small.text
    assert big.status_code == 200, big.text
    small_body, big_body = small.json(), big.json()

    assert small_body["revenue"] == big_body["revenue"] == small_body["revenue"]
    assert small_body["revenue"]["amount"] == "20000.00"
    assert small_body["additional_cost"] == big_body["additional_cost"]
    assert small_body["personnel_cost"]["amount"] == "12600.00"
    assert big_body["personnel_cost"]["amount"] == "18000.00"
    assert small_body["personnel_cost"]["amount"] != big_body["personnel_cost"]["amount"]
    assert small_body["profit"] != big_body["profit"]


# --- K-06: an approved scenario is refused exactly like out of scope, never a distinct shape ------


def test_k_06_an_approved_scenario_gets_the_same_404_as_out_of_scope_never_a_distinct_shape(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — a scenario `approved` from the start (so the inherited race guard has nothing to
    catch: both the revenue and the cost read agree `approved_snapshot`) still answers the same
    `404`, with the same body, `SCENARIO_WHAT_IF_NOT_FOUND_DETAIL` — never a `403`, never a `409`,
    never a distinguishable "this exists but is approved" shape (ADR-0015, point 5). The caller
    holds every permission, including `PERSONNEL_COSTS_READ`, so the refusal cannot be a permission
    gate in disguise.
    """
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session, name="ApprovedWhatIf", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario = make_scenario(
        db_session, project, name="Approved", status=ScenarioStatus.APPROVED, currency="PLN"
    )

    with caller_holding(*EVERYTHING):
        response = client.get(what_if_path(project.id, scenario.id, "10"))

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": SCENARIO_WHAT_IF_NOT_FOUND_DETAIL}


# --- S-01 (Invariant Guardian, 2026-09-24): the endpoint's own 409, closed at unit level ----------


def test_race_guard_a_disagreeing_status_at_read_answers_409_with_a_generic_message(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-01 (Invariant Guardian, 2026-09-24) — `app.data.scenario_what_if` inherits
    `ScenarioResultsRaceDetected` unchanged (ADR-0015, point 5: "what-if must inherit the identical
    race guard"), but nothing had exercised *this* endpoint's own `409` before this test — not even
    at unit level. The sibling `GET .../results` proves the same guard with a real, two-connection
    concurrency test (`tests/test_scenario_results_race.py`); a real-concurrency equivalent for
    what-if is a separate, larger gap, named and accepted open for now (see the module docstring's
    "What this change does not prove"). This test closes the narrower "literally zero proof" gap:
    it forces the two REAL reads (before any raise is applied) to disagree, the same shape
    `commercial.status_at_read != cost_view.status_at_read` takes when an approval genuinely lands
    between them (SC-7-03, gate-1 Q1/A: the injection point moved from `rate_source` to the status
    each read froze — the assertions below are unchanged; the real-concurrency what-if proof is now
    `tests/test_scenario_results_status_guard.py`, K-04).

    `scenario_cost_for_caller` is patched **inside `app.data.scenario_what_if`'s own namespace**
    (the name a plain `from app.data.personnel_cost import scenario_cost_for_caller` bound there,
    not the origin module's attribute — patching `app.data.personnel_cost.scenario_cost_for_caller`
    would not affect the already-bound reference `scenario_what_if.py` calls). It still delegates to
    the real function and only swaps the view's `status_at_read` to `approved` afterwards, so
    everything else about the view — scope, the personnel-cost flag, the real amount — is genuine;
    the revenue read really saw `draft` (this scenario is a real draft), so the two disagree exactly
    as they would under an actual race, and the `409` this produces runs the endpoint's real
    `except ScenarioResultsRaceDetected` handler, not a stand-in for it.
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="RaceWhatIf")

    def _disagreeing_cost_view(
        session: Session,
        caller: CallerIdentity,
        project_id: uuid.UUID,
        scenario_id: uuid.UUID,
    ) -> object:
        view = real_scenario_cost_for_caller(session, caller, project_id, scenario_id)
        if view is None:
            return None
        return dataclass_replace(view, status_at_read=ScenarioStatus.APPROVED)

    monkeypatch.setattr(
        scenario_what_if_module, "scenario_cost_for_caller", _disagreeing_cost_view
    )

    with caller_holding(*EVERYTHING):
        response = client.get(what_if_path(project.id, scenario.id, "10"))

    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": (
            "The scenario's approval status changed while its result was being computed. Retry "
            "the request."
        )
    }
    # R-02's generic-message rule, inherited: the disagreement itself must never be readable.
    assert "live_catalog" not in response.text
    assert "approved_snapshot" not in response.text


# --- R-01 (Reviewer, 2026-09-24): salary_raise_percent is floored at -100 -------------------------


def test_r_01_a_raise_below_minus_100_percent_is_refused_with_422(
    client: TestClient, db_session: Session
) -> None:
    """R-01 (Reviewer, 2026-09-24) — below `-100`, the raised rate `cost_rate * (1 + p/100)` turns
    negative. Nothing in `base_personnel_cost`/`paid_absence_cost` rejects a negative `Decimal`
    rate (a real catalogue rate is never negative, so ADR-0013 never needed that check) — without a
    floor this would answer a confident `200` with a personnel cost, a profit larger than the
    revenue, and a margin/markup above `100%`, all physically meaningless (an employee cannot be
    paid a negative wage) but shaped exactly like a real result.
    `app.api.schemas.scenario_what_if.SalaryRaisePercentQuery`'s `ge=SALARY_RAISE_PERCENT_FLOOR`
    refuses this at request validation, before the database is ever asked.
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="FloorRefused")

    with caller_holding(*EVERYTHING):
        response = client.get(what_if_path(project.id, scenario.id, "-100.001"))

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail[0]["loc"][-1] == "salary_raise_percent"
    assert detail[0]["type"] == "greater_than_equal"


def test_r_01_exactly_minus_100_percent_is_accepted_and_zeroes_the_rate_not_negates_it(
    client: TestClient, db_session: Session
) -> None:
    """R-01's contrast — the floor is not an off-by-one trap: exactly `-100` is accepted (`1 +
    (-100)/100` is the exact `Decimal("0")`, a real catalogue rate could be that, never negative),
    and answers a `personnel_cost.amount` of `0.00` — the zero `PersonnelCostResult` the formula
    already has a name for — never a negative figure.
    """
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name="FloorAccepted")

    with caller_holding(*EVERYTHING):
        response = client.get(what_if_path(project.id, scenario.id, "-100"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["personnel_cost"]["state"] == "calculated"
    assert body["personnel_cost"]["amount"] == "0.00"
    assert not body["personnel_cost"]["amount"].startswith("-")
