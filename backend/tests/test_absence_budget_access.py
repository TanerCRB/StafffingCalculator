"""SC-3-03, K-09 — a refusal is a refusal, and a budget is not a personnel cost (F-05).

Two halves, guarded by two different mechanisms, and the criterion keeps them apart on purpose:

**(a) The permission gate.** The budget is organisational data: `CATALOG_READ` opens it and nothing
else does (ADR-0005, addendum 2026-09-22 SC-3-03, points 2-3). Two claims that pull in opposite
directions are proven side by side — a caller holding **every project permission** and no catalogue
permission is refused with a `403` whose body carries neither a figure nor a source, while a caller
holding `CATALOG_READ` and **not** `PERSONNEL_COSTS_READ` gets the whole number, unwhitened. The
second is the one that would be easy to get wrong in the "safe" direction: a number of days is not
an amount, and gating it on the cost permission would give that permission a third meaning and, in
today's system, make the feature unreachable — `PERSONNEL_COSTS_READ` is not in the placeholder's
set at all.

**(b) The scope boundary, on the read that carries the budget into a scenario.** The staffing read
answers `404` for a scenario outside the caller's scope with the *same* body as for one that does
not exist, and the budget fields change nothing about that: it is inherited from
`app.data.project_reads.project_for_caller` and kept here as a canary — a budget lookup written as a
second query, outside that path, is exactly how an "exists but not yours" answer appears.
"""

import uuid
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from tests.conftest import (
    BUDGET_SOURCE,
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    STATUTORY_LEAVE_TYPE_NAME,
    UNKNOWN_USER,
    as_caller,
    budget_payload,
    caller_holding,
    count_absence_budgets,
    make_absence_budget,
    make_absence_type,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
    staffing_path,
)

BUDGETS_PATH = "/catalog/absence-budgets"

EVERY_PROJECT_PERMISSION = (
    Permission.PROJECT_READ,
    Permission.PROJECT_CREATE,
    Permission.PROJECT_EDIT,
    Permission.PROJECT_COPY,
    Permission.PROJECT_ARCHIVE,
)
"""What the denied caller holds instead of a catalogue permission — the shape
`tests/test_catalog_access.py` established. Handing them everything *but* `CATALOG_*` is what makes
the mutation "swap the required permission for a project one" fail here rather than survive."""

BUDGET_DAYS = Decimal("26.00")
YEAR_2026 = (date(2026, 1, 1), date(2026, 12, 31))


def _one_budget(session: Session):
    """One calendar, one dimension tuple, one flagged absence type and one budget row."""
    calendar = make_working_calendar(
        session, name="Poland 7.5h", standard_hours_per_day=Decimal("7.50")
    )
    dimensions = make_dimension_tuple(session, calendar=calendar)
    make_absence_type(session, name=STATUTORY_LEAVE_TYPE_NAME, is_statutory_leave=True)
    make_absence_budget(
        session,
        calendar,
        dimensions.engagement_type_id,
        budget_days=BUDGET_DAYS,
        effective_from=YEAR_2026[0],
        effective_to=YEAR_2026[1],
    )
    return calendar, dimensions


# --- (a) the permission gate, and only it ---------------------------------------------------------


def test_k_09_the_budget_is_gated_by_the_ordinary_catalogue_read_permission_and_by_nothing_else(
    client: TestClient, db_session: Session
) -> None:
    """K-09 (a) — refused without `CATALOG_READ`, whole without `PERSONNEL_COSTS_READ`.

    Four readings in one test, because each is the contrast that keeps another honest:

    1. **Every project permission, no catalogue permission** → `403`, and the body carries neither
       the number nor the source. Asserted on the text and not only on the status code: a `403`
       whose detail quoted the row would be a refusal that leaked what it refused.
    2. **`CATALOG_READ` alone** → `200` with the complete row. This is the direction the criterion
       cares about most: the caller holds *nothing else at all*, in particular not
       `PERSONNEL_COSTS_READ`, and still receives `budget_days` and `source` in full.
    3. **`CATALOG_READ` plus `PERSONNEL_COSTS_READ`** → byte-for-byte the same payload as 2.

       **What 2 and 3 together do and do not kill** (QA, 2026-09-22 — the claim that stood here was
       wrong and is corrected rather than dropped). They kill "gate this payload on
       `PERSONNEL_COSTS_READ`" in *any* form a developer would write it, because the two callers
       differ by exactly that permission and the payloads are compared whole. They do **not** kill
       "add a budget field name to `CATALOG_PERSONNEL_COST_FIELDS`", which was the mutation this
       docstring used to claim: that set is consulted by `_without_catalog_personnel_costs`, which
       only ever shapes a `CatalogRate` — a budget payload never passes through it, so adding a name
       to the set changes nothing here and nothing anywhere else. The set is proven by the tests of
       the field it really governs (`tests/test_catalog_personnel_cost_visibility.py`).
    4. **A caller with no `project_access` row at all** (`UNKNOWN_USER`) → the same payload again:
       the catalogue has no scope, and this is the same claim
       `tests/test_catalog_access.py::test_k_01_…` makes for the first five dictionaries.

    The write path is gated separately: `CATALOG_READ` alone cannot create a budget, which is what
    keeps `CATALOG_WRITE` from being decoration (NF-10 puts maintaining the catalogue with an
    organisation administrator).
    """
    calendar, dimensions = _one_budget(db_session)
    make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    with caller_holding(*EVERY_PROJECT_PERMISSION):
        denied = client.get(BUDGETS_PATH, headers=as_caller(IN_SCOPE_USER))
    assert denied.status_code == 403, denied.text
    assert "26.00" not in denied.text
    assert BUDGET_SOURCE not in denied.text

    with caller_holding(Permission.CATALOG_READ):
        catalogue_only = client.get(BUDGETS_PATH, headers=as_caller(IN_SCOPE_USER))
    assert catalogue_only.status_code == 200, catalogue_only.text
    [entry] = catalogue_only.json()["budgets"]
    assert entry["budget_days"] == "26.00", (
        "the figure was whitened for a caller without PERSONNEL_COSTS_READ — a budget is a number "
        "of days, not a personnel cost (ADR-0005, addendum 2026-09-22 SC-3-03, point 3)"
    )
    assert entry["source"] == BUDGET_SOURCE
    assert entry["calendar_id"] == str(calendar.id)
    assert entry["engagement_type_id"] == str(dimensions.engagement_type_id)

    with caller_holding(Permission.CATALOG_READ, Permission.PERSONNEL_COSTS_READ):
        with_costs = client.get(BUDGETS_PATH, headers=as_caller(IN_SCOPE_USER))
    assert with_costs.json() == catalogue_only.json(), (
        "the payload differs by PERSONNEL_COSTS_READ — some field of it is being gated on the cost "
        "permission"
    )

    without_projects = client.get(BUDGETS_PATH, headers=as_caller(UNKNOWN_USER))
    assert without_projects.status_code == 200
    assert without_projects.json() == catalogue_only.json()


def test_k_09_writing_a_budget_needs_catalog_write_and_a_reader_is_refused(
    client: TestClient, db_session: Session
) -> None:
    """The write half of the same gate: `CATALOG_READ` is not `CATALOG_WRITE`.

    The refused caller holds every project permission **and** `CATALOG_READ`, so the only thing
    missing is the one permission the endpoint declares — which is what makes this a test of that
    declaration rather than of authentication. Nothing is written, asserted with a count rather than
    inferred from the status code.
    """
    calendar, dimensions = _one_budget(db_session)
    before = count_absence_budgets(db_session)
    body = budget_payload(
        calendar.id,
        dimensions.engagement_type_id,
        effective_from="2027-01-01",
        effective_to="2027-12-31",
    )

    with caller_holding(*EVERY_PROJECT_PERMISSION, Permission.CATALOG_READ):
        refused = client.post(BUDGETS_PATH, json=body, headers=as_caller(IN_SCOPE_USER))

    assert refused.status_code == 403, refused.text
    assert count_absence_budgets(db_session) == before, "the refused write was saved"

    with caller_holding(Permission.CATALOG_WRITE):
        created = client.post(BUDGETS_PATH, json=body, headers=as_caller(IN_SCOPE_USER))
    assert created.status_code == 201, created.text
    assert count_absence_budgets(db_session) == before + 1


def test_k_09_reading_the_budget_without_any_identity_is_a_401(
    client: TestClient, db_session: Session
) -> None:
    """Deny by default, one step further out: no identity header at all.

    A `401` and not a `200` with an empty body: the catalogue has no scope, but "no scope" is not
    "no authentication", and an endpoint reachable without a caller would be the one place where
    that distinction is quietly lost. The same claim the SC-3-02 dictionaries make.
    """
    _one_budget(db_session)

    response = client.get(BUDGETS_PATH)

    assert response.status_code == 401, response.text
    assert "26.00" not in response.text


def test_k_09_the_permission_vocabulary_did_not_grow_in_this_task(
    client: TestClient, db_session: Session
) -> None:
    """The canary: `Permission` still has exactly these ten members (ADR-0005, addendum SC-3-03).

    Point 2 of that addendum says in so many words that the budget is the eighth catalogue table and
    takes **no permission of its own**, and that `PLACEHOLDER_PERMISSIONS` does not grow either. A
    `BUDGET_READ` added "for clarity" fails this assertion the day it is written, which is the only
    moment at which undoing it is cheap.

    Asserted by set **equality**, not by length: a permission renamed or swapped keeps the count and
    changes the meaning. The length assertion is kept beside it as a second reading of the same
    fact, exactly as `tests/test_catalog_access.py` writes it.
    """
    assert set(Permission) == {
        Permission.PROJECT_READ,
        Permission.PROJECT_CREATE,
        Permission.PROJECT_EDIT,
        Permission.PROJECT_COPY,
        Permission.PROJECT_ARCHIVE,
        Permission.PERSONNEL_COSTS_READ,
        Permission.CATALOG_READ,
        Permission.CATALOG_WRITE,
        Permission.STAFFING_READ,
        Permission.STAFFING_WRITE,
        # Re-armed in SC-4-01, not loosened: ADR-0005's addendum of 2026-09-23 (SC-4-01, point 2)
        # adds this pair for the commercial rule — a table of a scenario, not of the catalogue — so
        # the claim of this canary (the catalogue tables take no permission of their own) holds.
        Permission.COMMERCIAL_READ,
        Permission.COMMERCIAL_WRITE,
        # Re-armed again in SC-7-01, not loosened: ADR-0005's aneks of 2026-09-24 adds this one for
        # the whole-scenario result — a composition over three already-permissioned calculations,
        # not a catalogue table — so the claim of this canary holds unchanged.
        Permission.RESULTS_READ,
        # Re-armed in SC-6-01 (Issue #11, gate 1 decision 2), not loosened: `SCENARIO_COPY` is a
        # scenario action, not a catalogue table, so this canary's claim is unaffected by it.
        Permission.SCENARIO_COPY,
        # Re-armed in SC-2-06 (Issue #31, gate 1 decision 6), not loosened: ADR-0005's aneks of
        # 2026-09-27 (point 3) adds this pair for the person register — a register of its own,
        # explicitly *not* a catalogue dictionary — so this canary's claim holds unchanged.
        Permission.PEOPLE_READ,
        Permission.PEOPLE_WRITE,
    }, (
        "the permission vocabulary changed in SC-3-03. ADR-0005's addendum of 2026-09-22 (SC-3-03, "
        "point 2) decides that the absence budget is the eighth table of the catalogue and takes "
        "no permission of its own."
    )
    assert len(Permission) == 16


def test_the_budget_carrying_staffing_read_is_not_gated_on_the_personnel_cost_permission(
    client: TestClient, db_session: Session
) -> None:
    """The same claim on the other read that carries a budget figure: the staffing grid.

    A caller with `STAFFING_READ` and `CATALOG_READ` but **not** `PERSONNEL_COSTS_READ` sees the
    budget's share of the month in full. This is where ADR-0005's addendum (SC-3-03, point 4) draws
    its line: the *days* are not a cost, and the first response to carry the *cost* of those days
    reinstates the SC-1-08 conjunction and has to prove it with a criterion of its own. Nothing in
    this task carries an amount, so nothing here may be read as that gate being in place.
    """
    calendar, dimensions = _one_budget(db_session)
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=date(2026, 3, 1)
    )
    make_allocation(db_session, position, period_month=date(2026, 3, 1))

    with caller_holding(Permission.STAFFING_READ, Permission.CATALOG_READ):
        response = client.get(
            staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )

    assert response.status_code == 200, response.text
    [allocation] = response.json()["positions"][0]["allocations"]
    assert allocation["absence_budget_hours"] == "16.25"
    assert allocation["absence_budget_source"]["budget_days"] == "26.00"
    assert allocation["absence_budget_source"]["source"] == BUDGET_SOURCE


# --- (b) the scope boundary on the read that carries the budget -----------------------------------


def test_k_09_a_scenario_outside_the_callers_scope_is_not_found_rather_than_forbidden_on_the_budget_carrying_read(  # noqa: E501 — the criterion names this test; the name is the contract, not a style choice
    client: TestClient, db_session: Session
) -> None:
    """K-09 (b) — the staffing read answers identically for "not yours" and "does not exist".

    Inherited from `app.data.project_reads.project_for_caller` and kept here as a canary: the budget
    is read on this path, and a budget lookup written as a second query outside that path is exactly
    how a `403` — i.e. a confirmation that the scenario exists — would appear. The scenario the
    out-of-scope caller asks about **does** exist and **does** carry a budget, so a leak would have
    something to leak.

    The status code, the body and the content length are all compared, because a body that differed
    only in length would still be a side channel.
    """
    calendar, dimensions = _one_budget(db_session)
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    position = make_staffing_position(
        db_session, scenario, dimensions, headcount=1, start_date=date(2026, 3, 1)
    )
    make_allocation(db_session, position, period_month=date(2026, 3, 1))
    # It really is there for somebody: the caller in scope reads the budget-carrying payload.
    in_scope = client.get(
        staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert in_scope.status_code == 200
    assert in_scope.json()["positions"][0]["allocations"][0]["absence_budget_hours"] == "16.25"

    invented = uuid.uuid4()
    outside = client.get(
        staffing_path(project.id, scenario.id), headers=as_caller(OUT_OF_SCOPE_USER)
    )
    nonexistent = client.get(
        staffing_path(uuid.uuid4(), invented), headers=as_caller(OUT_OF_SCOPE_USER)
    )

    assert outside.status_code == nonexistent.status_code == 404, outside.text
    assert outside.json() == nonexistent.json()
    assert outside.headers.get("content-length") == nonexistent.headers.get("content-length")
    assert "26.00" not in outside.text
    assert BUDGET_SOURCE not in outside.text
