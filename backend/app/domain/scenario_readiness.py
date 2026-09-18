"""Which inputs a scenario is still missing, and whether it may be treated as ready.

F-01: "The system shall identify missing inputs and shall not present incomplete results as
ready for approval." The answer is derived from the scenario row itself — each scenario reports
its *own* gaps, so two drafts missing different things produce different lists.
"""

from collections.abc import Sequence
from dataclasses import dataclass

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
called complete. One name per scenario attribute; the check below reads the attribute, so this
tuple and the model cannot drift apart silently."""


@dataclass(frozen=True)
class ScenarioReadiness:
    missing_inputs: tuple[str, ...]
    ready_for_approval: bool


def missing_inputs(scenario: Scenario) -> tuple[str, ...]:
    """Names of the required inputs this scenario has not got a value for, in a stable order."""
    return tuple(
        field for field in REQUIRED_SCENARIO_INPUTS if getattr(scenario, field, None) is None
    )


def assess(scenario: Scenario) -> ScenarioReadiness:
    """A scenario with any missing input is never presented as ready for approval (F-01).

    Readiness is a function of completeness alone — the scenario's *status* does not enter into
    it. An `approved` row with a missing input is reported as not ready and keeps its gap list:
    that combination should not exist, and hiding it behind the status would turn a data defect
    into a silent "ready" claim, which is exactly what F-01 forbids. Whether such a row may be
    approved at all is ADR-0004's question, not this read path's.
    """
    gaps = missing_inputs(scenario)
    return ScenarioReadiness(missing_inputs=gaps, ready_for_approval=not gaps)


def assess_all(scenarios: Sequence[Scenario]) -> dict[str, ScenarioReadiness]:
    """Readiness per scenario id — computed per row, never once for the whole project."""
    return {str(scenario.id): assess(scenario) for scenario in scenarios}
