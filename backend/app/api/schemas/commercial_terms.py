"""Request and response schemas for a scenario's commercial rule and revenue (F-06.1, SC-4-01).

Two boundary decisions are visible in the shapes below.

**No field carries a cost** — not `default_cost_rate`, not a cost, a profit or a margin (ADR-0005,
addendum 2026-09-23 SC-4-01, point 3). Not "removed for callers without the permission": absent from
the schema, which is what lets the revenue go out without the SC-1-08 conjunction. Criterion K-11
asserts the whole field set by *equality*, so a cost field added here later fails a test on the day
it is added rather than leaking quietly. The first task adding profit or margin to this payload
reinstates the conjunction (same point).

**Money crosses the boundary as a fixed-point string** (`DecimalString`), never a JSON float
(ADR-0002). A revenue that cannot be stated is `"n/a"` with a named `state`, never `0` and never
`null` (ADR-0003, point 9; criterion K-10).
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.schemas.common import DecimalString, Iso4217Code
from app.api.schemas.project import ScenarioStatusLabel
from app.core.money import NOT_APPLICABLE
from app.models.commercial_terms import (
    OUTCOME_AMOUNT_PRECISION,
    OUTCOME_AMOUNT_SCALE,
    OUTCOME_UNITS_PRECISION,
    OUTCOME_UNITS_SCALE,
    PROBABILITY_PRECISION,
    PROBABILITY_SCALE,
)

OutcomeCategory = Literal["not_achieved", "partial", "achieved", "exceeded"]
"""Cztery stałe kategorie wyniku (ADR-0003, aneks 2026-09-25 SC-4-03, pkt 3) —
`app.models.commercial_terms.OUTCOME_CATEGORIES` w pisowni API."""

ExpectedRevenueState = Literal["calculated", "no_probabilities", "not_applicable"]
"""Stan przychodu oczekiwanego (pkt 5b-c): `no_probabilities` to nazwany stan reguły bez
prawdopodobieństw; `not_applicable` — model bez przychodu oczekiwanego albo przychód w ogóle
niepodany (wtedy `state` mówi dlaczego)."""

SourceNotApplicable = Literal["not_applicable"]
"""Źródło, którego wyliczenie nie czyta — model bez katalogu stawek (pkt 8, 10a)."""

RevenueState = Literal[
    "calculated",
    "no_commercial_terms",
    "incomplete_commercial_terms",
    "unsupported_model_type",
    "no_rate",
    "currency_mismatch",
    "no_revenue_currency",
]

StoredModelType = str
"""A model as a **response** carries it: whatever the stored row says, not a closed `Literal`.

The request side stays closed — `CommercialTermsCreateRequest` below is a union discriminated by
`model_type`, each variant with its own one-value `Literal`, so a model this version cannot price is
a `422` naming the field; the database CHECK is the rule behind it. This version creates only what
it can price. The
response side is open on purpose (R-02, SC-4-01 gate 2): in the mixed-version window of ADR-0001 a
row of a later model can exist while this code runs, and a closed `Literal` here would turn the
named `unsupported_model_type` state into a response-validation `500`."""


class TimeAndMaterialTermsCreateRequest(BaseModel):
    """`POST …/commercial-terms` for T&M — the one thing a T&M rule says: which model prices it.

    `extra="forbid"`: there is no rate, override, cap or day length to send (ADR-0003, points 4 and
    7, "Odłożone"), and a field the server silently ignored would be a promise it does not keep.
    """

    model_config = ConfigDict(extra="forbid")

    model_type: Literal["time_and_material"]


OutcomeAmount = Annotated[
    DecimalString,
    Field(ge=0, max_digits=OUTCOME_AMOUNT_PRECISION, decimal_places=OUTCOME_AMOUNT_SCALE),
]
"""Kwota reguły na wejściu: nieujemna i dokładnie tak precyzyjna jak `NUMERIC(14,4)` — piąte miejsce
po przecinku to `422`, nie zaokrąglenie przy zapisie (ADR-0002)."""

OutcomeUnits = Annotated[
    DecimalString,
    Field(ge=0, max_digits=OUTCOME_UNITS_PRECISION, decimal_places=OUTCOME_UNITS_SCALE),
]

Probability = Annotated[
    DecimalString,
    Field(ge=0, le=100, max_digits=PROBABILITY_PRECISION, decimal_places=PROBABILITY_SCALE),
]
"""Procent z co najwyżej dwoma miejscami po przecinku. `33.333` to `422` — nigdy zaokrąglenie po
cichu, które zmieniłoby sumę sprawdzoną przez użytkownika (ADR-0003, aneks SC-4-03, pkt 4)."""


class OutcomeCategoryRequest(BaseModel):
    """Jedna kategoria wyniku: liczba osiągniętych jednostek (wpis ręczny) i opcjonalne
    prawdopodobieństwo.

    `units` opcjonalne — obowiązkowe tylko wtedy, gdy reguła ma stawkę za jednostkę (walidator
    `OutcomeBasedTermsCreateRequest`, ten sam warunek co
    `ck_outcome_terms_units_given_with_unit_rate`). Pominięte zostaje `null`, nigdy `0`."""

    model_config = ConfigDict(extra="forbid")

    units: OutcomeUnits | None = None
    probability: Probability | None = None


class OutcomeCategoriesRequest(BaseModel):
    """Dokładnie cztery stałe kategorie — każda obowiązkowa, żadna dodatkowa (pkt 3)."""

    model_config = ConfigDict(extra="forbid")

    not_achieved: OutcomeCategoryRequest
    partial: OutcomeCategoryRequest
    achieved: OutcomeCategoryRequest
    exceeded: OutcomeCategoryRequest


class OutcomeBasedTermsCreateRequest(BaseModel):
    """`POST …/commercial-terms` dla Outcome-based (F-06.3; ADR-0003, aneks 2026-09-25 SC-4-03).

    **Każda reguła tutaj jest też regułą bazy** (`app.models.commercial_terms.OutcomeTerms`):
    schemat zamienia błąd klienta na `422` wskazujący pole; gwarancją jest `CHECK`, bo fixture ani
    import nie przechodzą przez ten moduł. Składnik opcjonalny pominięty albo `null` to brak
    składnika — nigdy `0` (pkt 2).
    """

    model_config = ConfigDict(extra="forbid")

    model_type: Literal["outcome_based"]
    currency: Iso4217Code
    """Waluta reguły, bez przeliczenia (pkt 7): inna niż waluta scenariusza →
    `currency_mismatch`."""
    fixed_fee: OutcomeAmount
    success_bonus: OutcomeAmount | None = None
    """Premia binarna dla kategorii "osiągnięty" i "przekroczony" — nie dla "częściowy" (D-1)."""
    unit_rate: OutcomeAmount | None = None
    revenue_min: OutcomeAmount | None = None
    revenue_max: OutcomeAmount | None = None
    categories: OutcomeCategoriesRequest

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        """Te same reguły co `ck_outcome_terms_revenue_bounds_ordered`,
        `ck_outcome_terms_probabilities_sum_to_100` i `ck_outcome_terms_units_given_with_unit_rate`,
        jako `422` przed jakimkolwiek zapisem."""
        if (
            self.revenue_min is not None
            and self.revenue_max is not None
            and self.revenue_min > self.revenue_max
        ):
            raise ValueError("revenue_min must not be greater than revenue_max")
        categories = (
            self.categories.not_achieved,
            self.categories.partial,
            self.categories.achieved,
            self.categories.exceeded,
        )
        if self.unit_rate is not None and any(category.units is None for category in categories):
            raise ValueError("units must be given for all four categories when unit_rate is given")
        probabilities = [category.probability for category in categories]
        given = [probability for probability in probabilities if probability is not None]
        if given and len(given) != len(probabilities):
            raise ValueError(
                "probabilities must be given for all four categories or for none of them"
            )
        if given and sum(given) != 100:
            raise ValueError("probabilities must sum to exactly 100.00")
        return self


CommercialTermsCreateRequest = Annotated[
    TimeAndMaterialTermsCreateRequest | OutcomeBasedTermsCreateRequest,
    Field(discriminator="model_type"),
]
"""Ciało `POST` wybierane po `model_type` — nigdy po kształcie danych (ADR-0003, pkt 9)."""


class OutcomeCategoryRead(BaseModel):
    """Jedna kategoria reguły Outcome-based tak, jak ją zapisano — `null` za brak, nigdy `0`."""

    units: DecimalString | None
    probability: DecimalString | None


class OutcomeCategoriesRead(BaseModel):
    """Cztery stałe kategorie — ten sam kształt co `OutcomeCategoriesRequest`."""

    not_achieved: OutcomeCategoryRead
    partial: OutcomeCategoryRead
    achieved: OutcomeCategoryRead
    exceeded: OutcomeCategoryRead


class OutcomeTermsRead(BaseModel):
    """Parametry reguły Outcome-based do odczytu (runda 2 weryfikacji SC-4-03, R-04) — ten sam
    kształt co `OutcomeBasedTermsCreateRequest` bez `model_type`, żeby klient mógł wyświetlić
    dokładnie to, co zapisał. Kwoty jako napisy stałoprzecinkowe przepisane z wiersza, bez
    zaokrąglenia; składnik nieobecny to `null`. Żadnego kosztu — to parametry przychodu."""

    currency: str
    fixed_fee: DecimalString
    success_bonus: DecimalString | None
    unit_rate: DecimalString | None
    revenue_min: DecimalString | None
    revenue_max: DecimalString | None
    categories: OutcomeCategoriesRead


class CommercialTermsRead(BaseModel):
    """The rule itself: its id, its model and ADR-0007's marker (covering its details row too)."""

    id: uuid.UUID
    model_type: StoredModelType
    updated_at: datetime
    outcome_terms: OutcomeTermsRead | None
    """Parametry reguły Outcome-based (R-04). `null` dla każdego innego modelu — T&M nie ma
    parametrów (ADR-0003, pkt 4) — i dla reguły Outcome-based bez wiersza szczegółów (wtedy
    `revenue.state` to `incomplete_commercial_terms`). Jedno pole addytywne, jedyna zmiana kształtu
    odpowiedzi T&M."""


class RateWindowRead(BaseModel):
    """One catalogue window the revenue used (F-06.5, `assumptions_used`). The selling rate only."""

    source_rate_id: uuid.UUID
    effective_from: date
    effective_to: date | None
    """Inclusive, `null` when open-ended — passed through as stored (ADR-0008, point 3)."""
    default_selling_rate: DecimalString
    currency: str


class UnresolvedMonthRead(BaseModel):
    """One (position, month) that caused a named state."""

    position_id: uuid.UUID
    period_month: date


class RevenueAssumptionsRead(BaseModel):
    """What the revenue depends on — present on a calculated revenue and on a named state alike."""

    model_type: StoredModelType | None
    hours_source: Literal["billable_hours"] | SourceNotApplicable
    vendor_axis: Literal["internal"] | SourceNotApplicable
    rate_source: Literal["live_catalog", "approved_snapshot"] | SourceNotApplicable
    """`not_applicable` w trzech polach źródła dla modelu bez katalogu stawek (Outcome-based;
    ADR-0003, aneks 2026-09-25 SC-4-03, pkt 8) — założenia nazywają tylko to, co wyliczenie
    czyta."""
    rate_windows: list[RateWindowRead]
    unresolved_months: list[UnresolvedMonthRead]
    currencies: list[str]


class CategoryRevenueRead(BaseModel):
    """Przychód jednej kategorii wyniku Outcome-based, po ograniczeniu min/max (pkt 5b, 6)."""

    category: OutcomeCategory
    units: DecimalString | None
    """`null`, gdy reguła nie ma stawki za jednostkę i jednostek nie podano — nigdy `0`."""
    probability: DecimalString | None
    """`null`, gdy reguła nie ma prawdopodobieństw — nigdy `0`."""
    amount: DecimalString


class RevenueRead(BaseModel):
    """The revenue of one scenario, or the named state that withholds it."""

    state: RevenueState
    amount: DecimalString | Literal[NOT_APPLICABLE]
    """A fixed-point string when `state` is `"calculated"`, `"n/a"` otherwise — never `0`.
    Dla Outcome-based: przychód **gwarantowany** (ADR-0003, aneks 2026-09-25 SC-4-03, pkt 5a)."""
    currency: str | None
    assumptions_used: RevenueAssumptionsRead
    expected_state: ExpectedRevenueState
    """Pole addytywne SC-4-03 (pkt 5b-d; bramka 1, D-6)."""
    expected_amount: DecimalString | Literal[NOT_APPLICABLE]
    """Przychód oczekiwany — kwota tylko przy `expected_state == "calculated"`, inaczej `"n/a"`."""
    category_revenues: list[CategoryRevenueRead]
    """Przychody per kategoria — puste dla modelu bez kategorii i dla nazwanego stanu przychodu."""


class ScenarioCommercialTerms(BaseModel):
    """`GET`/`POST …/scenarios/{id}/commercial-terms` — the rule and the revenue derived from it."""

    scenario_id: uuid.UUID
    scenario_status: ScenarioStatusLabel
    commercial_terms: CommercialTermsRead | None
    """`null` when the scenario has no rule — and then `revenue.state` is
    `"no_commercial_terms"`."""
    revenue: RevenueRead
