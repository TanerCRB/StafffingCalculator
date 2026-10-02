"""SC-5-04, K-06/K-07 and FA-9/FA-12 — the request surface of the stored FTE: who may write it, who
may see what it produces, what the schema refuses, how a basis switch behaves, that an approved
scenario freezes it, and that a copy keeps it (F-07; Issue #79; ADR-0013, ADR-0005 and ADR-0004,
addenda 2026-09-29 SC-5-04).

Real PostgreSQL through the real endpoints. The stored FTE is a personnel-cost input (ADR-0005
addendum 2026-09-29, point 1): it is **never** a field of `GET …/staffing-positions` — proved by
field-set equality against the sets `test_staffing_positions_cost_basis_hidden.py` already pins
— and its amount and assumptions are withheld from a caller without `PERSONNEL_COSTS_READ` ∧
`can_view_personnel_costs`, while the state stays visible. The marker value **0.4321** (FTE) and
the amount it produces, **8425.95** (0.4321 x 195 x 100 on the 7.5 h Monday-Saturday calendar of
March 2026), are unique in this suite, so `not in response.text` cannot pass by coincidence."""

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.models import ScenarioStatus
from app.models.staffing import StaffingPosition
from tests.conftest import (
    IN_SCOPE_USER,
    MONDAY_TO_SATURDAY,
    as_caller,
    caller_holding,
    count_positions,
    grant_personnel_cost_visibility,
    make_allocation,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    staffing_path,
    staffing_position_payload,
)
from tests.test_assigned_fte_cost_scenario import personnel_cost_path
from tests.test_cost_rate_unit import _calendar
from tests.test_scenario_results import results_path
from tests.test_scenario_what_if import what_if_path
from tests.test_staffing_positions_cost_basis_hidden import ALLOCATION_FIELDS, POSITION_FIELDS

MAR = date(2026, 3, 1)
MARKER_FTE = "0.4321"
MARKER_AMOUNT = "8425.95"

WRITER = (Permission.STAFFING_WRITE,)
WRITER_WITH_COSTS = (Permission.STAFFING_WRITE, Permission.PERSONNEL_COSTS_READ)
EVERYTHING = tuple(Permission)


def _setup(
    session: Session, *, suffix: str, flag: bool = True, status=ScenarioStatus.DRAFT,
    with_calendar: bool = True, with_rate: bool = True,
):
    """A project the caller may see (`flag`: with the cost flag), a scenario declaring PLN, a fresh
    tuple on the module's Monday-Saturday calendar and an open-ended hourly cost rate of 100."""
    project = make_project(
        session, name=f"FTE api {suffix}", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,) if flag else (),
    )
    scenario = make_scenario(session, project, name=f"Baseline {suffix}", currency="PLN",
                             status=status)
    calendar = (
        _calendar(session, hours="7.50", name=f"Sat {suffix}", pattern=MONDAY_TO_SATURDAY)
        if with_calendar else None
    )
    dimensions = make_dimension_tuple(session, suffix=f" {suffix}", calendar=calendar)
    if with_rate:
        make_rate(session, dimensions, effective_from=date(2026, 1, 1), currency="PLN",
                  default_cost_rate=Decimal("100.0000"))
    return project, scenario, dimensions


def _fte_position(session: Session, scenario, dimensions, fte: str = MARKER_FTE):
    position = make_staffing_position(
        session, scenario, dimensions, headcount=2, start_date=MAR, cost_basis="assigned_fte",
        assigned_fte=Decimal(fte),
    )
    make_allocation(session, position, period_month=MAR)
    return position


def _row(session: Session, position_id: uuid.UUID) -> sa.Row:
    session.expire_all()
    return session.execute(
        sa.text(
            "SELECT cost_basis, assigned_fte, fixed_amount, fixed_amount_currency"
            " FROM staffing_position WHERE id = :id"
        ),
        {"id": position_id},
    ).one()


def _token(client: TestClient, project_id, scenario_id) -> str:
    response = client.get(staffing_path(project_id, scenario_id), headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 200, response.text
    return response.json()["positions"][0]["updated_at"]


def _patch(client: TestClient, project, scenario, position, body: dict[str, Any], *perms):
    token = _token(client, project.id, scenario.id)
    with caller_holding(*(perms or WRITER_WITH_COSTS)):
        return client.patch(
            f"{staffing_path(project.id, scenario.id)}/{position.id}",
            json={"updated_at": token, **body},
        )


def _cost(client: TestClient, project_id, scenario_id, *perms) -> dict[str, Any]:
    with caller_holding(*(perms or EVERYTHING)):
        response = client.get(personnel_cost_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return response.json()["personnel_cost"]


# --- FA-9: the FTE is in no STAFFING_READ-only response, and the field set is unchanged -----------


def test_fa_9_the_staffing_positions_field_set_is_identical_for_an_fte_position(
    client: TestClient, db_session: Session
) -> None:
    """FA-9 — an FTE position with the marker value, read back through `GET`, `POST` and the
    cost-basis `PATCH` responses: the position's and the allocation's field sets equal the
    pinned sets (nothing added — no `assigned_fte`, no `cost_basis` — and nothing missing), and
    neither the field name nor the value appears in any body. Mutation: `assigned_fte` added to
    `StaffingPositionRead` to "round-trip what was just written"."""
    project, scenario, dimensions = _setup(db_session, suffix="fa9-set")

    with caller_holding(*WRITER_WITH_COSTS):
        created = client.post(
            staffing_path(project.id, scenario.id),
            json=staffing_position_payload(
                dimensions, cost_basis="assigned_fte", assigned_fte=MARKER_FTE
            ),
        )
    assert created.status_code == 201, created.text
    listed = client.get(staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
    edited = _patch(
        client, project, scenario, make_position_stub(created.json()["id"]),
        {"assigned_fte": "0.5000"},
    )
    assert listed.status_code == edited.status_code == 200, edited.text

    bodies = [created.json(), listed.json()["positions"][0], edited.json()]
    for body in bodies:
        assert set(body) == POSITION_FIELDS
        assert set(body["allocations"][0]) == ALLOCATION_FIELDS
    for response in (created, listed, edited):
        assert "assigned_fte" not in response.text
        assert "cost_basis" not in response.text
        assert MARKER_FTE not in response.text and "0.5000" not in response.text


class _Stub:
    def __init__(self, position_id: str) -> None:
        self.id = position_id


def make_position_stub(position_id: str) -> _Stub:
    return _Stub(position_id)


def test_fa_9_no_read_only_response_and_no_response_without_the_conjunction_carries_the_fte(
    client: TestClient, db_session: Session
) -> None:
    """FA-9/K-07 — the stored value **and the amount it produces** are absent from every response a
    caller without the conjunction can reach: the staffing grid (`STAFFING_READ` only), and the
    cost, results and what-if endpoints for a caller who lacks the permission, and for one who
    lacks the project flag. Contrast: the caller with both sees both, so the absence is the gate
    and not an empty scenario."""
    project, scenario, dimensions = _setup(db_session, suffix="fa9-read")
    _fte_position(db_session, scenario, dimensions)
    flagless_project, flagless_scenario, flagless_dimensions = _setup(
        db_session, suffix="fa9-read-noflag", flag=False
    )
    _fte_position(db_session, flagless_scenario, flagless_dimensions)

    with caller_holding(Permission.STAFFING_READ):
        grid = client.get(staffing_path(project.id, scenario.id))
    denied = []
    without_permission = tuple(p for p in Permission if p is not Permission.PERSONNEL_COSTS_READ)
    for permissions, p, s in (
        (without_permission, project, scenario),
        (EVERYTHING, flagless_project, flagless_scenario),
    ):
        with caller_holding(*permissions):
            denied += [
                client.get(personnel_cost_path(p.id, s.id)),
                client.get(results_path(p.id, s.id)),
                client.get(what_if_path(p.id, s.id, "10")),
            ]
    with caller_holding(*EVERYTHING):
        allowed = client.get(personnel_cost_path(project.id, scenario.id))

    for response in (grid, *denied):
        assert response.status_code == 200, response.text
        assert MARKER_FTE not in response.text, response.request.url
        assert MARKER_AMOUNT not in response.text, response.request.url
    assert MARKER_AMOUNT in allowed.text and MARKER_FTE in allowed.text


@pytest.mark.parametrize(
    ("permissions", "flag", "visible"),
    [
        pytest.param(EVERYTHING, True, True, id="both-halves"),
        pytest.param(
            tuple(p for p in Permission if p is not Permission.PERSONNEL_COSTS_READ),
            True, False, id="permission-missing",
        ),
        pytest.param(EVERYTHING, False, False, id="project-flag-missing"),
        pytest.param(
            tuple(p for p in Permission if p is not Permission.PERSONNEL_COSTS_READ),
            False, False, id="both-missing",
        ),
    ],
)
def test_fa_9_the_amount_and_the_assumptions_need_both_halves_the_state_stays_visible(
    client: TestClient, db_session: Session, permissions, flag: bool, visible: bool
) -> None:
    """FA-9 — `assigned_fte_amount` and `assigned_fte_assumptions_used` are `null` unless both the
    permission and the project's flag say yes (the four combinations); `assigned_fte_state` is
    `calculated` for every caller and carries no figure, and `assigned_fte_currency` is not
    gated (as `fixed_amount_currency` is not). Mutation: the two fields missing from
    `SCENARIO_COST_FIELDS`."""
    project, scenario, dimensions = _setup(db_session, suffix="fa9-gate", flag=flag)
    _fte_position(db_session, scenario, dimensions)

    cost = _cost(client, project.id, scenario.id, *permissions)

    assert cost["assigned_fte_state"] == "calculated"
    assert cost["assigned_fte_currency"] == "PLN"
    if visible:
        assert cost["assigned_fte_amount"] == MARKER_AMOUNT
        assert cost["assigned_fte_above_headcount_position_ids"] == []
        lines = cost["assigned_fte_assumptions_used"]["lines"]
        assert [line["assigned_fte"] for line in lines] == [MARKER_FTE]
        assert isinstance(lines[0]["assigned_fte"], str)  # a decimal string, never a JSON float
    else:
        assert cost["assigned_fte_amount"] is None
        assert cost["assigned_fte_assumptions_used"] is None
        assert cost["assigned_fte_above_headcount_position_ids"] is None


def test_assigned_fte_above_headcount_marker_is_machine_readable_and_uses_cost_gate(
    client: TestClient, db_session: Session
) -> None:
    project, scenario, dimensions = _setup(db_session, suffix="fte-plausibility")
    position = _fte_position(db_session, scenario, dimensions, fte="2.50")

    visible = _cost(client, project.id, scenario.id)
    hidden = _cost(
        client,
        project.id,
        scenario.id,
        *(p for p in Permission if p is not Permission.PERSONNEL_COSTS_READ),
    )

    assert visible["assigned_fte_above_headcount_position_ids"] == [str(position.id)]
    assert hidden["assigned_fte_above_headcount_position_ids"] is None


def test_fa_9_a_named_state_is_visible_to_a_caller_who_may_not_see_amounts(
    client: TestClient, db_session: Session
) -> None:
    """FA-9 — `state` stays visible and carries no amount: an FTE position with no allocation rows
    answers `no_planned_months` to a caller without the conjunction, with the amount `null`, not
    `"n/a"` (`"n/a"` would tell that caller the amount does not exist rather than that they may not
    see it)."""
    project, scenario, dimensions = _setup(db_session, suffix="fa9-state", flag=False)
    make_staffing_position(db_session, scenario, dimensions, start_date=MAR,
                           cost_basis="assigned_fte", assigned_fte=Decimal(MARKER_FTE))

    cost = _cost(client, project.id, scenario.id)

    assert cost["assigned_fte_state"] == "no_planned_months"
    assert cost["assigned_fte_amount"] is None and cost["assigned_fte_assumptions_used"] is None


# --- FA-9: creating or switching to the FTE basis needs the conjunction --------------------------


def _post_fte(client: TestClient, project, scenario, dimensions, *permissions):
    with caller_holding(*permissions):
        return client.post(
            staffing_path(project.id, scenario.id),
            json=staffing_position_payload(
                dimensions, cost_basis="assigned_fte", assigned_fte=MARKER_FTE
            ),
        )


def test_fa_9_creating_an_fte_position_needs_the_permission_and_the_project_flag(
    client: TestClient, db_session: Session
) -> None:
    """FA-9 — `POST` with `cost_basis = 'assigned_fte'`: `STAFFING_WRITE` alone is `403`; the
    permission without the project's flag is `403`; both is `201`. A refused request writes
    nothing (the row count does not move), and the contrast in the same test is a `worked_time`
    create by the same weak caller, which is still `201` — the extra check is only for a request
    that would persist the figure. Mutation: the `assigned_fte` basis missing from the
    conjunction's trigger."""
    project, scenario, dimensions = _setup(db_session, suffix="fa9-post")
    flagless, flagless_scenario, flagless_dimensions = _setup(
        db_session, suffix="fa9-post-noflag", flag=False
    )
    before = count_positions(db_session)

    weak = _post_fte(client, project, scenario, dimensions, *WRITER)
    no_flag = _post_fte(client, flagless, flagless_scenario, flagless_dimensions,
                        *WRITER_WITH_COSTS)
    assert (weak.status_code, no_flag.status_code) == (403, 403)
    assert count_positions(db_session) == before

    with caller_holding(*WRITER):
        plain = client.post(staffing_path(project.id, scenario.id),
                            json=staffing_position_payload(dimensions))
    assert plain.status_code == 201, plain.text

    both = _post_fte(client, project, scenario, dimensions, *WRITER_WITH_COSTS)
    assert both.status_code == 201, both.text
    stored = _row(db_session, uuid.UUID(both.json()["id"]))
    assert (stored.cost_basis, stored.assigned_fte) == ("assigned_fte", Decimal("0.4321"))


def test_fa_9_the_403_of_a_denied_fte_write_is_not_a_hint_about_the_scenario(
    client: TestClient, db_session: Session
) -> None:
    """FA-9 — 404 before 403, and a 403 that names neither project nor scenario (NF-11): a
    caller out of scope gets the same `404` an absent scenario gets, and the `403` body of a
    caller in scope without the conjunction carries no id and no value."""
    project, scenario, dimensions = _setup(db_session, suffix="fa9-403")

    missing = _post_fte(client, project, make_scenario_stub(), dimensions, *WRITER)
    denied = _post_fte(client, project, scenario, dimensions, *WRITER)

    assert missing.status_code == 404
    assert denied.status_code == 403
    for text in (denied.text,):
        assert str(project.id) not in text and str(scenario.id) not in text
        assert MARKER_FTE not in text


class _ScenarioStub:
    id = uuid.uuid4()


def make_scenario_stub() -> _ScenarioStub:
    return _ScenarioStub()


def test_fa_9_switching_the_basis_needs_the_conjunction_and_changes_nothing_without_it(
    client: TestClient, db_session: Session
) -> None:
    """FA-9 — every cost-basis `PATCH` needs the conjunction (unchanged rule, now also for the FTE
    basis): `STAFFING_WRITE` alone is `403` and the row is untouched; with both halves the same body
    is applied."""
    project, scenario, dimensions = _setup(db_session, suffix="fa9-patch")
    position = make_staffing_position(db_session, scenario, dimensions, start_date=MAR)
    make_allocation(db_session, position, period_month=MAR)
    body = {"cost_basis": "assigned_fte", "assigned_fte": MARKER_FTE}

    refused = _patch(client, project, scenario, position, body, *WRITER)
    assert refused.status_code == 403
    assert _row(db_session, position.id).cost_basis == "worked_time"

    accepted = _patch(client, project, scenario, position, body)
    assert accepted.status_code == 200, accepted.text
    stored = _row(db_session, position.id)
    assert (stored.cost_basis, stored.assigned_fte) == ("assigned_fte", Decimal("0.4321"))
    assert _cost(client, project.id, scenario.id)["assigned_fte_amount"] == MARKER_AMOUNT


def test_qa_the_patch_needs_both_halves_of_the_conjunction_alone_each_is_403(
    client: TestClient, db_session: Session
) -> None:
    """QA (K-07/FA-9) — the create test proves each half of the conjunction separately, the `PATCH`
    test above only that `STAFFING_WRITE` alone is refused. A `PATCH` guarded by the permission
    alone (or by the flag alone) passed it. Three rows on one plan, each a body that would persist
    the FTE — a switch onto the basis and a correction of the value: the permission **without** the
    project flag is `403`; the flag **without** the permission is `403`; nothing changes in the row
    in either case. Contrast: both halves, the same bodies, `200`.
    Mutation: the `PATCH`'s gate reduced to one half of the conjunction, or removed."""
    project, scenario, dimensions = _setup(db_session, suffix="qa-patch-flag")
    flagless, flagless_scenario, flagless_dimensions = _setup(
        db_session, suffix="qa-patch-noflag", flag=False
    )
    switch = {"cost_basis": "assigned_fte", "assigned_fte": MARKER_FTE}
    correction = {"assigned_fte": "0.2500"}

    for (proj, scen, dims), perms, expected in (
        ((flagless, flagless_scenario, flagless_dimensions), WRITER_WITH_COSTS, 403),
        ((project, scenario, dimensions), WRITER, 403),
        ((project, scenario, dimensions), WRITER_WITH_COSTS, 200),
    ):
        on_worked = make_staffing_position(db_session, scen, dims, start_date=MAR)
        make_allocation(db_session, on_worked, period_month=MAR)
        on_fte = _fte_position(db_session, scen, dims, "0.5000")
        first = _patch(client, proj, scen, on_worked, switch, *perms)
        second = _patch(client, proj, scen, on_fte, correction, *perms)
        assert (first.status_code, second.status_code) == (expected, expected), (perms, proj.id)
        if expected == 403:
            assert _row(db_session, on_worked.id).cost_basis == "worked_time"
            assert _row(db_session, on_fte.id).assigned_fte == Decimal("0.5000")
        else:
            assert _row(db_session, on_worked.id).assigned_fte == Decimal("0.4321")
            assert _row(db_session, on_fte.id).assigned_fte == Decimal("0.2500")


# --- the request schema: a 422 names the field ----------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        pytest.param({"assigned_fte": "0"}, "assigned_fte", id="zero"),
        pytest.param({"assigned_fte": "-0.5000"}, "assigned_fte", id="negative"),
        pytest.param({"assigned_fte": "0.33335"}, "assigned_fte", id="five-decimals"),
        pytest.param({"assigned_fte": "1000000.0000"}, "assigned_fte", id="too-many-digits"),
        pytest.param({"assigned_fte": "abc"}, "assigned_fte", id="not-a-number"),
        pytest.param({"assigned_fte": None}, "assigned_fte", id="missing-on-its-basis"),
        pytest.param(
            {"assigned_fte": "0.5000", "fixed_amount": "10.0000", "fixed_amount_currency": "PLN"},
            "fixed_amount", id="together-with-an-amount",
        ),
    ],
)
def test_fa_1_the_create_request_refuses_a_bad_fte_naming_the_field_and_writes_nothing(
    client: TestClient, db_session: Session, overrides: dict[str, Any], field: str
) -> None:
    """FA-1 (request half; the database half is `test_assigned_fte_schema.py`) — a `422` whose error
    location names the field, before any write. Five decimal places are refused, never rounded
    (ADR-0008, point 6). Contrast: the clean body is `201`."""
    project, scenario, dimensions = _setup(db_session, suffix="fa1-422")
    before = count_positions(db_session)
    body = staffing_position_payload(dimensions, cost_basis="assigned_fte", assigned_fte="0.5000")
    body = body | {key: value for key, value in overrides.items() if value is not None}
    if overrides.get("assigned_fte", "") is None:
        del body["assigned_fte"]

    with caller_holding(*WRITER_WITH_COSTS):
        refused = client.post(staffing_path(project.id, scenario.id), json=body)

    assert refused.status_code == 422, refused.text
    assert any(field in ".".join(map(str, e["loc"])) or field in e["msg"]
               for e in refused.json()["detail"])
    assert count_positions(db_session) == before
    with caller_holding(*WRITER_WITH_COSTS):
        clean = client.post(
            staffing_path(project.id, scenario.id),
            json=staffing_position_payload(dimensions, cost_basis="assigned_fte",
                                           assigned_fte="0.5000"),
        )
    assert clean.status_code == 201, clean.text


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({"cost_basis": "worked_time", "assigned_fte": "0.5000"}, id="worked-time"),
        pytest.param({"assigned_fte": "0.5000"}, id="default-basis"),
        pytest.param(
            {"cost_basis": "fixed_amount", "fixed_amount": "10.0000",
             "fixed_amount_currency": "PLN", "assigned_fte": "0.5000"},
            id="fixed-amount",
        ),
    ],
)
def test_fa_1_a_stray_fte_on_another_basis_is_a_422(
    client: TestClient, db_session: Session, extra: dict[str, Any]
) -> None:
    """FA-1 — an FTE named with any other basis is refused at the boundary (the database's
    `assigned_fte_only_on_its_basis` is the guarantee behind it). Mutation: the validator removed
    (the request would reach the database and come back as a `409`)."""
    project, scenario, dimensions = _setup(db_session, suffix="fa1-stray")

    with caller_holding(*WRITER_WITH_COSTS):
        refused = client.post(
            staffing_path(project.id, scenario.id),
            json=staffing_position_payload(dimensions, **extra),
        )

    assert refused.status_code == 422, refused.text


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"cost_basis": "assigned_fte"}, id="switch-without-a-value"),
        pytest.param({"cost_basis": "assigned_fte", "assigned_fte": None}, id="switch-with-null"),
        pytest.param({"assigned_fte": None}, id="null-without-a-switch"),
        pytest.param({"cost_basis": "worked_time", "assigned_fte": "0.5000"}, id="stray"),
        pytest.param({"cost_basis": "assigned_fte", "assigned_fte": "0.33335"}, id="five-decimals"),
        pytest.param(
            {"cost_basis": "assigned_fte", "assigned_fte": "0.5000", "fixed_amount": "10.0000",
             "fixed_amount_currency": "PLN"},
            id="with-an-amount",
        ),
        pytest.param({"assigned_fte": "0.5000", "fixed_amount": "10.0000",
                      "fixed_amount_currency": "PLN"}, id="both-figures-no-switch"),
    ],
)
def test_fa_1_the_patch_request_refuses_an_inconsistent_fte_and_changes_nothing(
    client: TestClient, db_session: Session, body: dict[str, Any]
) -> None:
    """FA-1 — the cost-basis `PATCH` refuses (422) a switch to the FTE basis without a value, an
    explicit `null`, a stray value on another basis, a fifth decimal and an FTE together with an
    amount; the row is untouched each time."""
    project, scenario, dimensions = _setup(db_session, suffix="fa1-patch")
    position = _fte_position(db_session, scenario, dimensions, "0.7500")

    refused = _patch(client, project, scenario, position, body)

    assert refused.status_code == 422, refused.text
    stored = _row(db_session, position.id)
    assert (stored.cost_basis, stored.assigned_fte) == ("assigned_fte", Decimal("0.7500"))


_NON_FINITE_FORMS = [
    pytest.param('"NaN"', id="string-nan"),
    pytest.param('"Infinity"', id="string-infinity"),
    pytest.param('"-Infinity"', id="string-negative-infinity"),
    pytest.param('"sNaN"', id="string-signalling-nan"),
    pytest.param("NaN", id="json-nan"),
    pytest.param("Infinity", id="json-infinity"),
    pytest.param("-Infinity", id="json-negative-infinity"),
]


@pytest.mark.parametrize("literal", _NON_FINITE_FORMS)
def test_fa_1_a_non_finite_fte_is_a_422_on_post_and_writes_nothing(
    client: TestClient, db_session: Session, literal: str
) -> None:
    """FA-1 (reviewer, Pydantic boundary) - NaN and Infinity, sent as a string and as the bare JSON
    tokens Python's `json` module also emits, are a `422` on `POST`, before any write. The body is
    sent as raw text because `json=` would refuse to encode the bare tokens."""
    project, scenario, dimensions = _setup(db_session, suffix="fa1-nonfinite-post")
    before = count_positions(db_session)
    body = json.dumps(
        staffing_position_payload(dimensions, cost_basis="assigned_fte", assigned_fte="@@")
    ).replace('"@@"', literal)

    with caller_holding(*WRITER_WITH_COSTS):
        refused = client.post(
            staffing_path(project.id, scenario.id),
            content=body,
            headers={"Content-Type": "application/json"},
        )

    assert refused.status_code == 422, refused.text
    if literal in {"NaN", "Infinity", "-Infinity"}:
        assert literal.lower() not in refused.text.lower()
    assert count_positions(db_session) == before


@pytest.mark.parametrize("literal", _NON_FINITE_FORMS)
def test_fa_1_a_non_finite_fte_is_a_422_on_patch_and_changes_nothing(
    client: TestClient, db_session: Session, literal: str
) -> None:
    """FA-1 (reviewer, Pydantic boundary) - the same seven forms on the cost-basis `PATCH`: `422`,
    the stored FTE untouched."""
    project, scenario, dimensions = _setup(db_session, suffix="fa1-nonfinite-patch")
    position = _fte_position(db_session, scenario, dimensions, "0.7500")
    token = _token(client, project.id, scenario.id)
    body = json.dumps({"updated_at": token, "assigned_fte": "@@"}).replace('"@@"', literal)

    with caller_holding(*WRITER_WITH_COSTS):
        refused = client.patch(
            f"{staffing_path(project.id, scenario.id)}/{position.id}",
            content=body,
            headers={"Content-Type": "application/json"},
        )

    assert refused.status_code == 422, refused.text
    if literal in {"NaN", "Infinity", "-Infinity"}:
        assert literal.lower() not in refused.text.lower()
    stored = _row(db_session, position.id)
    assert (stored.cost_basis, stored.assigned_fte) == ("assigned_fte", Decimal("0.7500"))


def test_k_04_ordinary_validation_error_keeps_the_standard_422_detail_shape(
    client: TestClient, db_session: Session
) -> None:
    """K-04: handling a non-finite number does not alter ordinary Pydantic 422 responses."""
    project, scenario, dimensions = _setup(db_session, suffix="ordinary-422-shape")
    body = staffing_position_payload(
        dimensions, cost_basis="assigned_fte", assigned_fte="0.12345"
    )

    with caller_holding(*WRITER_WITH_COSTS):
        refused = client.post(staffing_path(project.id, scenario.id), json=body)

    assert refused.status_code == 422
    [detail] = refused.json()["detail"]
    assert set(detail) == {"type", "loc", "msg", "input", "ctx"}
    assert detail["type"] == "decimal_max_places"
    assert detail["loc"] == ["body", "assigned_fte"]
    assert detail["ctx"] == {"decimal_places": 4}
    assert detail["input"] == "0.12345"


# --- a basis switch is one statement that clears what the new basis must not carry ---------------


def test_k_06_switching_between_bases_clears_the_figure_the_new_basis_must_not_carry(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — one `PATCH` is one `UPDATE`: fixed amount -> FTE clears the amount and currency
    (`assigned_fte_not_with_fixed_amount` would otherwise refuse the row), FTE -> fixed amount
    and FTE -> worked time clear the FTE (`assigned_fte_only_on_its_basis`). A correction of the
    FTE of a position already on the basis needs no `cost_basis`. Read back from the database
    each time. Mutation: the clearing removed (every switch would come back as a `409`)."""
    project, scenario, dimensions = _setup(db_session, suffix="k06-switch")
    position = make_staffing_position(
        db_session, scenario, dimensions, start_date=MAR, cost_basis="fixed_amount",
        fixed_amount=Decimal("500.0000"), fixed_amount_currency="PLN",
    )
    make_allocation(db_session, position, period_month=MAR)

    to_fte = _patch(client, project, scenario, position,
                    {"cost_basis": "assigned_fte", "assigned_fte": "0.5000"})
    assert to_fte.status_code == 200, to_fte.text
    row = _row(db_session, position.id)
    assert (row.cost_basis, row.assigned_fte, row.fixed_amount, row.fixed_amount_currency) == (
        "assigned_fte", Decimal("0.5000"), None, None,
    )

    corrected = _patch(client, project, scenario, position, {"assigned_fte": "0.6000"})
    assert corrected.status_code == 200, corrected.text
    assert _row(db_session, position.id).assigned_fte == Decimal("0.6000")

    to_fixed = _patch(
        client, project, scenario, position,
        {"cost_basis": "fixed_amount", "fixed_amount": "700.0000", "fixed_amount_currency": "PLN"},
    )
    assert to_fixed.status_code == 200, to_fixed.text
    row = _row(db_session, position.id)
    assert (row.cost_basis, row.assigned_fte, row.fixed_amount) == (
        "fixed_amount", None, Decimal("700.0000"),
    )

    back = _patch(client, project, scenario, position,
                  {"cost_basis": "assigned_fte", "assigned_fte": "0.2500"})
    assert back.status_code == 200, back.text
    to_worked = _patch(client, project, scenario, position, {"cost_basis": "worked_time"})
    assert to_worked.status_code == 200, to_worked.text
    row = _row(db_session, position.id)
    assert (row.cost_basis, row.assigned_fte) == ("worked_time", None)


def test_k_06_an_fte_correction_on_a_position_not_on_the_fte_basis_is_a_409_and_changes_nothing(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — `assigned_fte` named without `cost_basis` on a `worked_time` row: refused by state
    (`CostBasisMismatch`, in the statement's own `WHERE`), the row and its token untouched.
    Contrast: the same body on an FTE position is applied."""
    project, scenario, dimensions = _setup(db_session, suffix="k06-mismatch")
    worked = make_staffing_position(db_session, scenario, dimensions, start_date=MAR)

    refused = _patch(client, project, scenario, worked, {"assigned_fte": "0.5000"})

    assert refused.status_code == 409, refused.text
    assert "assigned_fte" in refused.json()["detail"]
    assert _row(db_session, worked.id).assigned_fte is None

    other_project, other_scenario, other_dimensions = _setup(db_session, suffix="k06-mismatch-ok")
    fte = _fte_position(db_session, other_scenario, other_dimensions)
    accepted = _patch(client, other_project, other_scenario, fte, {"assigned_fte": "0.5000"})
    assert accepted.status_code == 200, accepted.text


# --- K-06: an approved scenario freezes the stored FTE --------------------------------------------


def test_k_06_an_approved_scenario_refuses_an_fte_write_and_keeps_the_stored_value(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — the write guard covers the new column with no code of its own (same `UPDATE`, same
    `unapproved_scenario` predicate): a `PATCH` correcting the FTE of an approved scenario's
    position is `409` naming `approved`, the value is unchanged; a `POST` of an FTE position
    into it is `409` and writes nothing. Contrast: the draft twin accepts the same `PATCH`."""
    project, scenario, dimensions = _setup(db_session, suffix="k06-approved",
                                           status=ScenarioStatus.APPROVED)
    position = _fte_position(db_session, scenario, dimensions, "0.7500")
    draft = make_scenario(db_session, project, name="Draft twin", currency="PLN")
    twin = _fte_position(db_session, draft, dimensions, "0.7500")
    before = count_positions(db_session)

    refused = _patch(client, project, scenario, position, {"assigned_fte": "0.5000"})
    created = _post_fte(client, project, scenario, dimensions, *WRITER_WITH_COSTS)
    accepted = _patch(client, project, draft, twin, {"assigned_fte": "0.5000"})

    assert refused.status_code == 409 and "approved" in refused.json()["detail"], refused.text
    assert created.status_code == 409, created.text
    assert count_positions(db_session) == before
    assert _row(db_session, position.id).assigned_fte == Decimal("0.7500")
    assert accepted.status_code == 200, accepted.text
    assert _row(db_session, twin.id).assigned_fte == Decimal("0.5000")


# --- FA-12: a copy keeps the basis and the FTE on an independent row ------------------------------


def test_fa_12_a_copy_keeps_the_fte_basis_and_the_value_on_an_independent_row(
    client: TestClient, db_session: Session
) -> None:
    """FA-12 — copying the project copies the FTE position with `cost_basis = 'assigned_fte'`
    and its stored FTE (a copier that returned the column's default would produce a row the
    database refuses, or a `worked_time` one); the copy is its **own** row — the source's FTE
    changed afterwards does not move the copy's — and the copy costs the same 8425.95 through
    the real endpoint. The general drift guard (`test_staffing_copy.py`) proves the column is
    not left out of the copier's set; this test is the one that fails a copier returning
    defaults."""
    project, scenario, dimensions = _setup(db_session, suffix="fa12")
    position = _fte_position(db_session, scenario, dimensions)
    db_session.commit()

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    copy_project_id = uuid.UUID(response.json()["id"])
    db_session.expire_all()
    copied = db_session.execute(
        sa.select(StaffingPosition).where(StaffingPosition.id != position.id,
                                          StaffingPosition.cost_basis == "assigned_fte")
    ).scalars().all()
    assert len(copied) == 1
    copy = copied[0]
    assert (copy.cost_basis, copy.assigned_fte) == ("assigned_fte", Decimal("0.4321"))
    assert copy.id != position.id and copy.scenario_id != scenario.id

    db_session.execute(
        sa.update(StaffingPosition).where(StaffingPosition.id == position.id)
        .values(assigned_fte=Decimal("0.0001"))
    )
    db_session.flush()
    db_session.expire_all()
    assert db_session.get(StaffingPosition, copy.id).assigned_fte == Decimal("0.4321")

    # A copied project grants its creator access without the cost flag (SC-1-08): set it, so the
    # amount is visible to compare.
    grant_personnel_cost_visibility(db_session, project_id=copy_project_id, user_id=IN_SCOPE_USER)
    copy_cost = _cost(client, copy_project_id, copy.scenario_id)
    assert (copy_cost["assigned_fte_state"], copy_cost["assigned_fte_amount"]) == (
        "calculated", MARKER_AMOUNT,
    )


def test_qa_a_basis_switch_clears_by_default_and_never_overrides_what_the_caller_named() -> None:
    """QA (K-06) — `_values_for_a_basis_switch` uses `setdefault`, so a value the caller **named**
    reaches the statement as named and the CHECK refuses the contradiction, rather than the code
    silently replacing it. The request schema refuses such a body first, so no endpoint test can
    tell `setdefault` from an override; the function is asked directly. Contrast in each row: the
    same switch with nothing named clears the figure the new basis must not carry.
    Mutation: `setdefault(...)` replaced by an assignment."""
    from app.data.staffing import _values_for_a_basis_switch as switch

    fte, amount = Decimal("0.5000"), Decimal("100.0000")
    assert switch({"cost_basis": "worked_time"}) == {
        "cost_basis": "worked_time", "assigned_fte": None,
    }
    assert switch({"cost_basis": "worked_time", "assigned_fte": fte})["assigned_fte"] == fte
    assert switch({"cost_basis": "fixed_amount", "fixed_amount": amount,
                   "fixed_amount_currency": "PLN"})["assigned_fte"] is None
    assert switch({"cost_basis": "assigned_fte", "assigned_fte": fte}) == {
        "cost_basis": "assigned_fte", "assigned_fte": fte,
        "fixed_amount": None, "fixed_amount_currency": None,
    }
    named = switch({"cost_basis": "assigned_fte", "assigned_fte": fte, "fixed_amount": amount})
    assert named["fixed_amount"] == amount and named["fixed_amount_currency"] is None
    # No basis named: nothing is implied.
    assert switch({"fixed_amount": amount}) == {"fixed_amount": amount}
