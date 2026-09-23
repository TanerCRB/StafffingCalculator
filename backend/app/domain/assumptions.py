"""Resolving a scenario's configurable assumptions: scenario → project → organisation (F-02,
ADR-0012).

One rule, spelled once, for every assumption that follows it — today the target margin and the
overload threshold (gate 1, Q-2: two fields, so that "a mechanism" is falsifiable rather than an
implementation shaped around one column):

    the nearest level that holds a value wins, and the answer names that level;
    no level holds one → the named state `NO_VALUE`, never `0` and never an exception.

**"Holds a value" means `is not None`, and nothing else.** `0` is a value — a 0 % margin is a
decision somebody took — so the rule must not be written as `scenario or project or organisation`:
truthiness coalescing turns every `Decimal("0")` into "not set here" and silently hands the answer
to the next level up (criterion K-02's mutation). The loop below compares against `None` explicitly.

**The source is derived, never stored** (ADR-0012, point 2; gate 1, P-A). Which level answered is
decided here, by which input was present — there is no `source` column anywhere in the schema, so
there is no stored label that could disagree with the values it describes.

**Pure.** Nothing here queries or reads a clock: the three levels arrive as values. Whether the
organisation level is the live row or the one frozen at approval is decided by the caller
(`app.data.assumptions`), which is what lets one rule serve a draft (live, gate 1 Q-1) and an
approved scenario (frozen, AC-04) without a second copy of it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.core.money import NOT_APPLICABLE

RESOLVABLE_ASSUMPTIONS: tuple[str, ...] = ("target_margin_percent", "overload_threshold_percent")
"""Every assumption resolved through the chain, by the column name it has on **all three** levels.

One name per assumption on `scenarios`, `projects` and `organization_defaults` alike, so the rule
reads the same attribute on each level and cannot pair the margin of one level with the threshold of
another. An assumption joining the chain later is one entry here plus a column on each level —
and the drift guard in `tests/test_assumption_resolution.py` fails until all three exist."""

SCENARIO: Literal["scenario"] = "scenario"
PROJECT: Literal["project"] = "project"
ORGANIZATION: Literal["organization"] = "organization"
"""The three levels, nearest first. Also the vocabulary of the `source` a resolved value reports
(ADR-0012, point 2) — a second vocabulary next to ADR-0006's `rate_source`, named as such in the
ADR rather than merged into it."""

LEVELS: tuple[Literal["scenario", "project", "organization"], ...] = (
    SCENARIO,
    PROJECT,
    ORGANIZATION,
)
"""The order of precedence, as data. Reordering it is criterion K-02's second mutation."""

RESOLVED = "resolved"
NO_VALUE = "no_value"
"""The two named states of a resolved assumption — the pattern of `app.domain.capacity.NO_CALENDAR`
(ADR-0008, addendum 2026-09-22, point 7): an omission is a state, not a hole to fill with a
guess."""

Source = Literal["scenario", "project", "organization"]
State = Literal["resolved", "no_value"]


@dataclass(frozen=True)
class ResolvedAssumption:
    """One assumption of one scenario: the value, the state, and the level it came from.

    `value` is a `Decimal` when `state` is `RESOLVED`, and `app.core.money.NOT_APPLICABLE` (`"n/a"`)
    when it is `NO_VALUE` — never `0`, which is a number every later formula would happily use, and
    never `None`, which a caller could mistake for "not computed yet". `source` is `None` exactly
    when there is no value: there is no level to name.
    """

    value: Decimal | str
    state: State
    source: Source | None

    @property
    def is_present(self) -> bool:
        """Whether some level supplied a value — what readiness asks (gate 1, Q-5)."""
        return self.state == RESOLVED


def resolve(
    scenario_value: Decimal | None,
    project_value: Decimal | None,
    organization_value: Decimal | None,
) -> ResolvedAssumption:
    """The nearest present value and its level, or the named `NO_VALUE` state."""
    candidates: Mapping[Source, Decimal | None] = {
        SCENARIO: scenario_value,
        PROJECT: project_value,
        ORGANIZATION: organization_value,
    }
    for level in LEVELS:
        value = candidates[level]
        # `is not None`, never truthiness: a `Decimal("0")` is a value and stops the search here.
        if value is not None:
            return ResolvedAssumption(value=value, state=RESOLVED, source=level)
    return ResolvedAssumption(value=NOT_APPLICABLE, state=NO_VALUE, source=None)


@dataclass(frozen=True)
class OrganizationDefaultValues:
    """The organisation level as the rule needs it — from the live row or from the snapshot.

    A value object rather than an ORM row for the reason `app.domain.capacity.CalendarBasis` is one:
    the live table and the frozen copy produce the same shape, so the rule cannot tell (and must not
    care) which one it was handed. The *absence* of the organisation level — no defaults row, live
    or frozen — is `None` where one of these is expected, not an instance full of `None`s; the two
    resolve identically, and keeping them distinct is what lets a reader of the snapshot say "no
    row was frozen" (criterion K-06).
    """

    target_margin_percent: Decimal | None
    overload_threshold_percent: Decimal | None


def resolve_all(
    scenario: object,
    project: object,
    organization: OrganizationDefaultValues | None,
) -> dict[str, ResolvedAssumption]:
    """Every assumption in `RESOLVABLE_ASSUMPTIONS`, resolved for one scenario.

    `scenario` and `project` are read by attribute name — an ORM row or any object carrying the same
    names. `getattr` without a default: a level missing the column is a schema defect to surface as
    an `AttributeError`, not a quiet `None` that would resolve to the next level up.
    """
    return {
        name: resolve(
            getattr(scenario, name),
            getattr(project, name),
            None if organization is None else getattr(organization, name),
        )
        for name in RESOLVABLE_ASSUMPTIONS
    }
