"""The two shapes a revenue calculation answers with, shared by every commercial model (ADR-0003).

ADR-0003, point 9: the dispatcher returns **either** a result — `(revenue: Decimal, currency,
assumptions_used)` — **or** a named state — `(reason, assumptions_used)`. Two types rather than one
with optional fields, so "a revenue of zero" and "no revenue can be stated" cannot be the same
value: there is no `revenue` attribute on `RevenueUnavailable` to read a `0` from, and no `reason`
on `RevenueResult` to forget to check.

This module is the model-independent vocabulary only. The formula of each model lives in its own
module (`app.domain.revenue_time_and_material`, `app.domain.revenue_story_points` since SC-4-04,
`app.domain.revenue_outcome_based` since SC-4-03), which imports from here and from nothing of
another model's, and never from any cost calculation (F-06: independent calculation per model;
backend checklist; rule 10 of the Invariant Guardian).
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from app.core.money import NOT_APPLICABLE

# --- the named states (ADR-0003, point 9) --------------------------------------------------------

NO_COMMERCIAL_TERMS: Final = "no_commercial_terms"
"""The scenario has no rule. Never a revenue of `0` (criterion K-10)."""

INCOMPLETE_COMMERCIAL_TERMS: Final = "incomplete_commercial_terms"
"""A rule exists but its details row does not (ADR-0003, point 3): the database enforces the *type*
of a details row, not its *existence*, so this state is reachable and has to be named."""

UNSUPPORTED_MODEL_TYPE: Final = "unsupported_model_type"
"""The rule names a model **this version of the code** has no formula for (R-02, gate 2 of SC-4-01).

Unreachable in a single-version deployment (the CHECK admits only models this code knows). It
exists for the
mixed-version window of ADR-0001's expand → deploy → contract: the migration of a later model widens
the CHECK and a row of that model can be written while an instance still runs this code. Such an
instance answers with this state — never an unhandled `KeyError`, never a price computed by the
wrong formula, never `0`."""

NO_REVENUE_CURRENCY: Final = "no_revenue_currency"
"""A T&M rule over a plan with **no allocation rows at all** in a scenario with **no currency**
(R-04, gate 2 of SC-4-01 — a decision of the implementer, named here and in the report).

With no priced month there is no rate to take a currency from, and the scenario declares none, so a
revenue of `0.00` would have to be `0.00` of nothing — which is what ADR-0003's result shape
(`currency: str`) forbids. When the scenario *does* declare a currency, the same empty plan is the
true sum `0.00` in that currency (`calculated`)."""

NO_RATE: Final = "no_rate"
"""At least one (position, month) is not priced by **one selling rate over the whole month** — a gap
in the catalogue, or a change of the *selling* rate or of its currency inside the month (ADR-0003,
point 5, with the resolution rule corrected at gate 2 of SC-4-01, R-01: a window boundary that
changes only the cost rate does not unprice the month — see `app.data.commercial_terms`). The
whole revenue is withheld, never the sum of the months that did resolve (point 9: a partial revenue
is a silently understated one)."""

CURRENCY_MISMATCH: Final = "currency_mismatch"
"""The resolved rates carry more than one currency, or one other than the scenario's (point 8). No
conversion: `exchange_rates` (ADR-0006) does not exist and `1:1` would be invented."""

CALCULATED: Final = "calculated"
"""Not a named state of point 9 — the label the API gives a `RevenueResult`, so a client reads one
`state` field whichever of the two shapes it got."""

# --- where the rates came from (ADR-0003, point 9: `assumptions_used` names the source) ----------

LIVE_CATALOG: Final = "live_catalog"
APPROVED_SNAPSHOT: Final = "approved_snapshot"

RATE_SOURCE_STORY_POINTS_TERMS: Final = "story_points_terms"
"""Where a Story Points revenue's price came from (SC-4-04): the rule's own, write-guarded row —
never the catalogue and never a snapshot of it (ADR-0003 addendum 2026-09-25: `story_points_terms`
is "an own datum of the scenario", not a rate the approval snapshot mechanism ever touches). Naming
`live_catalog`/`approved_snapshot` here instead would claim a source this model never reads."""

RATE_SOURCE_NOT_APPLICABLE: Final = "not_applicable"
"""`rate_source` modelu, który nie czyta żadnej stawki (Outcome-based; ADR-0003, aneks 2026-09-25
SC-4-03, pkt 8 i 10a) — ta sama pisownia co `HOURS_SOURCE_NOT_APPLICABLE` i
`VENDOR_AXIS_NOT_APPLICABLE` niżej.

Nie udaje `live_catalog`/`approved_snapshot`: przychód Outcome-based czyta wyłącznie własne wiersze
scenariusza, więc nie zależy od statusu scenariusza."""

STATUS_DEPENDENT_SOURCES: Final = frozenset({LIVE_CATALOG, APPROVED_SNAPSHOT})
"""Wartości `rate_source` wybierane ze statusu scenariusza — jedyne, których porównanie mówi coś o
zmianie statusu między dwoma odczytami (ADR-0003, aneks SC-4-03, pkt 8).

Każda inna wartość (`RATE_SOURCE_NOT_APPLICABLE`, `RATE_SOURCE_STORY_POINTS_TERMS`) nazywa daną
własną scenariusza, niezależną od statusu — strażnik wyścigu `/results`
(`app.data.scenario_results.refuse_a_status_race`) nie traktuje jej ani jako dowodu wyścigu, ani
jako dowodu jego braku (decyzja człowieka 2026-09-25, merge SC-4-03 z SC-4-04)."""

HOURS_SOURCE_BILLABLE: Final = "billable_hours"
"""The only source of hours a T&M revenue has (ADR-0003, point 6) — named in `assumptions_used` so a
reader of the result is told, not left to assume, that neither the plan nor the availability was
used."""

HOURS_SOURCE_NOT_APPLICABLE: Final = "not_applicable"
"""Also the hours source of an Outcome-based revenue (SC-4-03, pkt 8). A Story Points revenue has
no hours at all (SC-4-04, criterion K-02): `accepted_points` is not an
hour figure and nothing here converts one into the other. Naming `billable_hours` for this model
would claim an hours source it never reads."""

VENDOR_AXIS_INTERNAL: Final = "internal"
"""The vendor axis of every rate read (ADR-0003, point 4): `vendor_id IS NULL`, the organisation's
own price — never "any vendor"."""

VENDOR_AXIS_NOT_APPLICABLE: Final = "not_applicable"
"""Also the vendor axis of an Outcome-based revenue (SC-4-03, pkt 8). A Story Points rule prices no
rate row, so it has no vendor axis to name (SC-4-04): there is no
catalogue lookup here for `vendor_id IS NULL` to be true or false of."""


@dataclass(frozen=True)
class RateWindow:
    """One catalogue window a calculation used — enough to name it, and nothing about cost.

    `default_cost_rate` is deliberately not a field: the revenue path reads the selling-rate column
    only (ADR-0003, point 4), and a cost field on this type would be one attribute away from a
    response that ADR-0005's addendum of 2026-09-23 (point 3) forbids.
    """

    source_rate_id: uuid.UUID
    effective_from: date
    effective_to: date | None
    selling_rate: Decimal
    currency: str


@dataclass(frozen=True)
class MonthPrice:
    """The selling rate one (position, month) is priced at, and every window it came from.

    More than one window when catalogue boundaries fall inside the month but none of them changes
    the selling rate or its currency — e.g. a mid-month change of the *cost* rate alone (R-01). The
    windows then share `selling_rate` and `currency` by construction: that equality is part of the
    SQL predicate that produced this value, not something checked here.
    """

    selling_rate: Decimal
    currency: str
    windows: tuple[RateWindow, ...]


@dataclass(frozen=True)
class UnresolvedMonth:
    """A (position, month) the named state is about — what `assumptions_used` points at (point
    9)."""

    position_id: uuid.UUID
    period_month: date


@dataclass(frozen=True)
class AssumptionsUsed:
    """What a revenue figure — or its absence — depends on (F-06.5; ADR-0003, point 9).

    Every field is present on both shapes of the answer. A named state still says which model, which
    source and which months caused it; a result still says which windows priced it.
    """

    model_type: str | None
    rate_source: str
    hours_source: str = HOURS_SOURCE_BILLABLE
    vendor_axis: str = VENDOR_AXIS_INTERNAL
    rate_windows: tuple[RateWindow, ...] = ()
    unresolved_months: tuple[UnresolvedMonth, ...] = ()
    currencies: tuple[str, ...] = ()


EXPECTED_CALCULATED: Final = "calculated"
"""Przychód oczekiwany policzony z prawdopodobieństw kategorii (F-06.3)."""

NO_PROBABILITIES: Final = "no_probabilities"
"""Reguła Outcome-based bez prawdopodobieństw — nazwany stan przychodu **oczekiwanego**, nigdy `0` i
nigdy kopia gwarantowanego (ADR-0003, aneks 2026-09-25 SC-4-03, pkt 5c). Przychód gwarantowany i per
kategoria są wtedy nadal podawane."""

EXPECTED_NOT_APPLICABLE: Final = "not_applicable"
"""Model bez przychodu oczekiwanego (T&M) — albo przychód w ogóle niepodany (nazwany stan w
`reason`/`state`, który mówi dlaczego)."""


@dataclass(frozen=True)
class CategoryRevenue:
    """Przychód jednej kategorii wyniku Outcome-based, po ograniczeniu min/max (pkt 5b, 6).

    `revenue` jest zaokrąglony raz, przez `round_money`, **do prezentacji** — przychód oczekiwany
    liczony jest z wartości niezaokrąglonych, nigdy z tego pola (pkt 5b).
    """

    category: str
    units: Decimal | None
    """`None` — jednostek nie podano (dozwolone tylko bez stawki za jednostkę); nigdy `0`."""
    probability: Decimal | None
    revenue: Decimal


@dataclass(frozen=True)
class RevenueResult:
    """A stated revenue: rounded once, at the end, through `app.core.money.round_money`.

    `revenue` to przychód, na który scenariusz może liczyć — dla Outcome-based **przychód
    gwarantowany** (ADR-0003, aneks 2026-09-25 SC-4-03, pkt 5a); od niego liczą zysk `/results` i
    porównanie scenariuszy. Trzy pola niżej są addytywne (pkt 5b/5d): model bez przychodu
    oczekiwanego (T&M) zostawia wartości domyślne.
    """

    revenue: Decimal
    currency: str
    assumptions_used: AssumptionsUsed
    expected_revenue: Decimal | str = NOT_APPLICABLE
    """Kwota tylko przy `expected_state == EXPECTED_CALCULATED`; w każdym innym stanie
    `NOT_APPLICABLE` (`"n/a"`) — nigdy `0` i nigdy `None`."""
    expected_state: str = EXPECTED_NOT_APPLICABLE
    category_revenues: tuple[CategoryRevenue, ...] = ()


@dataclass(frozen=True)
class RevenueUnavailable:
    """A named state: no revenue can be stated, and `reason` says why. There is no amount on it."""

    reason: str
    assumptions_used: AssumptionsUsed


RevenueAnswer = RevenueResult | RevenueUnavailable
