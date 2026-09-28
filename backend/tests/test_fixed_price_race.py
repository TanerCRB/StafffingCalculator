"""SC-4-02, K-07 (race half) — a Fixed Price revenue is status-compared by the race guard of
`GET …/results`, `GET …/compare` and `GET …/what-if` (component (a) of
`app.data.scenario_results.refuse_a_status_race`), although its `rate_source` is not chosen from
the scenario's status.

**Why (human decision of 2026-09-28 on Issue #66, option A; ADR-0015, addendum 2026-09-28
(SC-4-02)).** ADR-0015 (addendum SC-7-03, point 2) exempts a status-independent revenue (Story
Points, Outcome-based) from (a) only while its rule rows have no edit path in a draft (reviewer
R-06). Fixed Price has one — the price edit, D-6 = A — so "the same before and after an approval"
does not hold for it: the interleaving "revenue read → price edit → approval → cost read" would
answer `200` with `scenario_status: "Approved"` and a price the approved scenario never had. Its
`rate_source` (`fixed_price_terms`) is therefore in
`app.domain.revenue.DRAFT_EDITABLE_RULE_SOURCES`, and an approval landing between the revenue read
and the cost read is a `409` — exactly as for T&M.

**The harness is SC-7-03's**, reused rather than rewritten:
`tests/test_scenario_results_status_guard.py::_get_with_an_approval_raced_in` (a cursor hook that
fires once, after a statement only the revenue read issues, and commits a real approval through the
real endpoint on another connection and thread before the cost read refreshes the scenario), its
`_committed_scenario` plan (100 planned and billable hours at 120/200; base cost 12000.00) with the
rule replaced by a Fixed Price one, and `_assert_generic_409`.

The steady state of K-07 is unchanged and proven next to the race: without the interleaving, a
Fixed Price draft answers `200` on `…/results` and on what-if.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.data.scenario_results import ScenarioResultsRaceDetected, refuse_a_status_race
from app.domain.revenue import (
    DRAFT_EDITABLE_RULE_SOURCES,
    RATE_SOURCE_FIXED_PRICE_TERMS,
    RATE_SOURCE_NOT_APPLICABLE,
    RATE_SOURCE_STORY_POINTS_TERMS,
    STATUS_DEPENDENT_SOURCES,
)
from app.models.scenario import Scenario, ScenarioStatus
from tests.conftest import caller_holding, make_fixed_price_terms
from tests.test_scenario_results import EVERYTHING
from tests.test_scenario_results_status_guard import (
    NO_RULE,
    PERMISSION_SETS,
    _assert_generic_409,
    _committed_scenario,
    _get_with_an_approval_raced_in,
    _path,
)
from tests.test_scenario_what_if import SCENARIO_WHAT_IF_NOT_FOUND_DETAIL

FIXED_PRICE_REVENUE_READ_TRIGGER = "agreed_price"
"""The Fixed Price revenue read's lookup of its `fixed_price_terms` row
(`app.data.commercial_terms._fixed_price_details_of` selects `agreed_price`) — issued by
`commercial_terms_for_caller` after its refresh froze `s_P`, never by the personnel-cost or
additional-cost reads, and never by `_rule_of`'s `EXISTS` (which names no column). Fires on the
first such statement, i.e. inside the revenue read, before the cost read's refresh."""

AGREED_PRICE = "150000.00"
"""`conftest.make_fixed_price_terms`' default price, 150000.0000 PLN — a figure no T&M reading of
the same plan (100 billable hours × 200 = 20000.00) could produce, so a leaking mix is
recognisable."""


def _committed_fixed_price_scenario(engine: Engine) -> dict[str, uuid.UUID]:
    """SC-7-03's committed draft (`_committed_scenario`, no rule), then a committed Fixed Price rule
    of 150000.0000 PLN on it: revenue 150000.00, base cost 12000.00, profit 138000.00."""
    state = _committed_scenario(engine, NO_RULE)
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        scenario = setup.get(Scenario, state["scenario_id"])
        assert scenario is not None
        make_fixed_price_terms(setup, scenario)
        setup.commit()
    return state


# --- the race: an approval between the FP revenue read and the cost read is a 409 ----------------


@PERMISSION_SETS
@pytest.mark.parametrize("endpoint", ["results", "what_if"])
def test_k_07_an_approval_raced_between_the_fixed_price_revenue_and_cost_reads_is_a_409(
    committing_client: TestClient,
    engine: Engine,
    caller_permissions: frozenset[Permission],
    endpoint: str,
) -> None:
    """K-07 race (ADR-0015, addendum 2026-09-28 (SC-4-02)) — the approval commits on a second
    connection after the Fixed Price revenue read froze `draft` and before the cost read refreshes
    the scenario (`s_P = draft`, `s_K = s_D = approved`):

    - `…/results` → `409` with the fixed message, never a `200` with `scenario_status: "Approved"`
      next to the draft's price;
    - what-if → `409`, not the `404` of a scenario approved through every read.

    The body names no `rate_source` and no status, whether or not the caller may see personnel
    costs (K-06 of SC-7-03), and none of the figures 150000.00 / 12000.00 / 138000.00.

    Mutations killed: `fixed_price_terms` removed from `DRAFT_EDITABLE_RULE_SOURCES` (or the set
    dropped from the guard) — `…/results` answers `200` (the Story Points / Outcome answer of
    `test_a15_8_*`) and what-if `404`."""
    state = _committed_fixed_price_scenario(engine)

    response = _get_with_an_approval_raced_in(
        committing_client,
        state,
        _path(endpoint, state),
        caller_permissions,
        trigger=FIXED_PRICE_REVENUE_READ_TRIGGER,
    )

    _assert_generic_409(response)
    assert response.json() != {"detail": SCENARIO_WHAT_IF_NOT_FOUND_DETAIL}
    assert "fixed_price_terms" not in response.text
    for figure in (AGREED_PRICE, "12000.00", "138000.00", "13200.00"):
        assert figure not in response.text


# --- the contrast: no interleaving, the K-07 steady state is unchanged ---------------------------


@pytest.mark.parametrize("endpoint", ["results", "what_if"])
def test_k_07_contrast_no_approval_in_flight_a_fixed_price_draft_answers_200(
    committing_client: TestClient, engine: Engine, endpoint: str
) -> None:
    """The contrast — the same committed fixture, no concurrent write: `200` with the real numbers
    (`…/results`: cost 12000.00, profit 138000.00; what-if: cost raised 10% to 13200.00, profit
    136800.00), and the revenue still reports `rate_source = fixed_price_terms`. What stops the race
    test above from being satisfied by a guard that refuses every Fixed Price scenario."""
    state = _committed_fixed_price_scenario(engine)

    with caller_holding(*EVERYTHING):
        response = committing_client.get(_path(endpoint, state))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scenario_status"] == "Draft"
    assert body["revenue"]["amount"] == AGREED_PRICE
    assert body["revenue"]["assumptions_used"]["rate_source"] == "fixed_price_terms"
    if endpoint == "results":
        assert (body["personnel_cost"]["amount"], body["profit"]) == ("12000.00", "138000.00")
    else:
        assert (body["personnel_cost"]["amount"], body["profit"]) == ("13200.00", "136800.00")


# --- the classification itself, read off the production sets and the guard ----------------------


def test_k_07_fixed_price_terms_is_classified_as_status_compared_and_nothing_else_moves() -> None:
    """The classification made explicit (ADR-0015, addendum SC-7-03, point 7): `fixed_price_terms`
    is in `DRAFT_EDITABLE_RULE_SOURCES` — the set is exactly that one value — and **not** in
    `STATUS_DEPENDENT_SOURCES`, which keeps meaning "values chosen from the status". Story Points
    and Outcome-based stay outside both (their rows have no draft edit path).

    The guard, called directly: a Fixed Price revenue with `s_P ≠ s_K` is refused; with the three
    statuses equal it is not; a Story Points revenue with the same `s_P ≠ s_K` is not refused
    (byte-for-byte `main`'s SP/OB behaviour). Mutation killed: `fixed_price_terms` removed from the
    set — the first `pytest.raises` fails, without any database."""
    assert DRAFT_EDITABLE_RULE_SOURCES == {RATE_SOURCE_FIXED_PRICE_TERMS}
    assert RATE_SOURCE_FIXED_PRICE_TERMS not in STATUS_DEPENDENT_SOURCES
    for status_independent in (RATE_SOURCE_STORY_POINTS_TERMS, RATE_SOURCE_NOT_APPLICABLE):
        assert status_independent not in STATUS_DEPENDENT_SOURCES
        assert status_independent not in DRAFT_EDITABLE_RULE_SOURCES

    draft, approved = ScenarioStatus.DRAFT, ScenarioStatus.APPROVED
    with pytest.raises(ScenarioResultsRaceDetected):
        refuse_a_status_race(
            revenue_source=RATE_SOURCE_FIXED_PRICE_TERMS,
            revenue_status=draft,
            cost_status=approved,
            additional_cost_status=approved,
        )
    refuse_a_status_race(
        revenue_source=RATE_SOURCE_FIXED_PRICE_TERMS,
        revenue_status=approved,
        cost_status=approved,
        additional_cost_status=approved,
    )
    refuse_a_status_race(
        revenue_source=RATE_SOURCE_STORY_POINTS_TERMS,
        revenue_status=draft,
        cost_status=approved,
        additional_cost_status=approved,
    )
