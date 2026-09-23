"""Which inputs a scenario is still missing, and whether it may be treated as ready.

F-01: "The system shall identify missing inputs and shall not present incomplete results as
ready for approval." The answer is derived per scenario — each scenario reports its *own* gaps, so
two drafts missing different things produce different lists.

**An assumption resolved through the chain counts as present when *any* level supplies it**
(SC-1-10, gate 1 Q-5; ADR-0012). For those inputs the question is not "is this scenario's column
filled" but "does the rule of `app.domain.assumptions` produce a value": a scenario inheriting the
organisation's 18 % margin is not missing a margin. The resolved values are passed in rather than
computed here, because which organisation level applies (live for a draft, frozen for an approved
scenario) is a data-layer decision and this module queries nothing.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.domain.assumptions import ResolvedAssumption
from app.models.scenario import Scenario

REQUIRED_SCENARIO_INPUTS: tuple[str, ...] = (
    "start_date",
    "end_date",
    "working_calendar",
    "full_time_hours_per_week",
    "currency",
    "target_margin_percent",
)
"""The configurable assumptions F-02 requires every scenario to carry before its result can be
called complete. One name per scenario attribute; the check below reads the attribute — or, for a
name in `app.domain.assumptions.RESOLVABLE_ASSUMPTIONS`, its resolved value — so this tuple and the
model cannot drift apart silently.

`overload_threshold_percent` (SC-1-10) is resolvable but **not** required: nothing consumes it yet
(its consumer is the overload warning SC-3-01 deferred), and requiring an input no calculation reads
would make every existing draft "not ready" for a reason that changes no result."""


@dataclass(frozen=True)
class ScenarioReadiness:
    missing_inputs: tuple[str, ...]
    ready_for_approval: bool


def missing_inputs(
    scenario: Scenario, resolved: Mapping[str, ResolvedAssumption]
) -> tuple[str, ...]:
    """Names of the required inputs this scenario has not got a value for, in a stable order.

    `resolved` is the output of `app.domain.assumptions.resolve_all` for this scenario. It is a
    required argument, deliberately without a default: a default of "no resolved values" would
    silently fall back to reading the scenario's own column — the pre-SC-1-10 behaviour, in which an
    inherited margin counted as missing.
    """
    return tuple(
        field
        for field in REQUIRED_SCENARIO_INPUTS
        if (
            not resolved[field].is_present
            if field in resolved
            else getattr(scenario, field, None) is None
        )
    )


def assess(scenario: Scenario, resolved: Mapping[str, ResolvedAssumption]) -> ScenarioReadiness:
    """A scenario with any missing input is never presented as ready for approval (F-01).

    Readiness is a function of completeness alone — the scenario's *status* does not enter into
    it. An `approved` row with a missing input is reported as not ready and keeps its gap list:
    that combination should not exist, and hiding it behind the status would turn a data defect
    into a silent "ready" claim, which is exactly what F-01 forbids. Whether such a row may be
    approved at all is ADR-0004's question, not this read path's.
    """
    gaps = missing_inputs(scenario, resolved)
    return ScenarioReadiness(missing_inputs=gaps, ready_for_approval=not gaps)


def assess_all(
    scenarios: Sequence[Scenario],
    resolved: Mapping[uuid.UUID, Mapping[str, ResolvedAssumption]],
) -> dict[str, ScenarioReadiness]:
    """Readiness per scenario id — computed per row, never once for the whole project."""
    return {str(scenario.id): assess(scenario, resolved[scenario.id]) for scenario in scenarios}
