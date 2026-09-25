"""Przychód Outcome-based: opłata stała + premia binarna + stawka za jednostkę, ograniczone min/max
(F-06.3; ADR-0003, aneks 2026-09-25 SC-4-03).

Czysta funkcja tego, co przeczytała warstwa danych — bez `Session`, bez zegara, bez katalogu. **Nie
importuje niczego z innego modelu komercyjnego ani z żadnego wyliczenia kosztu** (F-06: niezależne
wyliczenie per model) i nie dostaje niczego z obsady, alokacji ani katalogu: typ wejściowy
`OutcomeTermsInput` nie ma na to pola.

Formuła (pkt 2, 5, 6):

    składnik_k   = premia · [k ∈ {osiągnięty, przekroczony}] + stawka_za_jednostkę · jednostki_k
    r_k          = ogranicz(opłata_stała + składnik_k, min, max)      — bez zaokrąglenia
    gwarantowany = ogranicz(opłata_stała, min, max)                    — zaokrąglony raz
    oczekiwany   = Σ_k (p_k / 100) · r_k                               — zaokrąglony raz, na końcu

Składnik nieobecny (`None`) nie jest `0` wpisanym przez użytkownika, ale w sumie znaczy "brak
składnika"; dla min/max `None` znaczy "bez ograniczenia", nigdy "ogranicz do zera".
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from app.core.money import round_money
from app.domain.revenue import (
    CURRENCY_MISMATCH,
    EXPECTED_CALCULATED,
    INCOMPLETE_COMMERCIAL_TERMS,
    NO_PROBABILITIES,
    NOT_APPLICABLE_SOURCE,
    AssumptionsUsed,
    CategoryRevenue,
    RevenueAnswer,
    RevenueResult,
    RevenueUnavailable,
)
from app.models.commercial_terms import MODEL_TYPE_OUTCOME_BASED

BONUS_CATEGORIES: Final = frozenset({"achieved", "exceeded"})
"""Kategorie, dla których wypłacana jest premia binarna — "częściowy" jej **nie** dostaje (D-1)."""

_HUNDRED: Final = Decimal("100")


@dataclass(frozen=True)
class OutcomeCategoryInput:
    """Jedna kategoria wyniku: ile jednostek osiągnięto (wpis ręczny) i z jakim prawdopodobieństwem
    (procent, opcjonalny). `units` jest `None`, gdy reguła nie ma stawki za jednostkę i użytkownik
    jednostek nie podał — nigdy `0` za brak."""

    category: str
    units: Decimal | None
    probability: Decimal | None


@dataclass(frozen=True)
class OutcomeTermsInput:
    """Reguła Outcome-based dokładnie tak, jak zapisał ją użytkownik — nic spoza scenariusza."""

    currency: str
    fixed_fee: Decimal
    success_bonus: Decimal | None
    unit_rate: Decimal | None
    revenue_min: Decimal | None
    revenue_max: Decimal | None
    categories: Sequence[OutcomeCategoryInput]


def outcome_assumptions(currencies: tuple[str, ...] = ()) -> AssumptionsUsed:
    """Założenia przychodu Outcome-based: żadnego źródła stawek, godzin ani osi poddostawcy.

    `assumptions_used` nazywa wyłącznie to, co wyliczenie faktycznie czyta (F-06.5; ADR-0003, aneks
    SC-4-03, pkt 8) — stąd `not_applicable` w trzech polach źródła, zamiast udawania katalogu.
    """
    return AssumptionsUsed(
        model_type=MODEL_TYPE_OUTCOME_BASED,
        rate_source=NOT_APPLICABLE_SOURCE,
        hours_source=NOT_APPLICABLE_SOURCE,
        vendor_axis=NOT_APPLICABLE_SOURCE,
        currencies=currencies,
    )


def _bounded(value: Decimal, terms: OutcomeTermsInput) -> Decimal:
    """Ogranicz **cały** przychód do [min, max] (D-5); `None` to brak ograniczenia z tej strony."""
    if terms.revenue_min is not None and value < terms.revenue_min:
        value = terms.revenue_min
    if terms.revenue_max is not None and value > terms.revenue_max:
        value = terms.revenue_max
    return value


def _category_revenue(terms: OutcomeTermsInput, category: OutcomeCategoryInput) -> Decimal:
    """Niezaokrąglony przychód jednej kategorii po ograniczeniu min/max."""
    variable = Decimal("0")
    if terms.success_bonus is not None and category.category in BONUS_CATEGORIES:
        variable += terms.success_bonus
    if terms.unit_rate is not None and category.units is not None:
        variable += terms.unit_rate * category.units
    return _bounded(terms.fixed_fee + variable, terms)


def outcome_based_revenue(
    terms: OutcomeTermsInput, *, scenario_currency: str | None
) -> RevenueAnswer:
    """Przychód gwarantowany (jako `revenue`), oczekiwany i per kategoria — albo nazwany stan.

    1. **Waluta reguły inna niż waluta scenariusza** (gdy ta jest ustawiona) → `currency_mismatch`,
       bez kwoty w żadnym polu i bez przeliczenia (pkt 7). Porównanie z walutą kosztów, gdy
       scenariusz waluty nie ma, należy do `app.domain.scenario_results` (R-01).
    1a. **Stawka za jednostkę bez liczby jednostek którejkolwiek kategorii** →
       `incomplete_commercial_terms`, nigdy mnożenie przez `0`. Baza tego nie dopuszcza
       (`ck_outcome_terms_units_given_with_unit_rate`); ta gałąź chroni czystą funkcję przed
       wejściem spoza bazy.
    2. Przychód gwarantowany: opłata stała po ograniczeniu min/max, zaokrąglona raz (pkt 5a, 6).
    3. Przychód oczekiwany: z **niezaokrąglonych** r_k, zaokrąglony raz, na końcu (pkt 5b). Brak
       prawdopodobieństw (baza gwarantuje "wszystkie albo żadne") → nazwany stan `no_probabilities`,
       nigdy `0` i nigdy kopia gwarantowanego (pkt 5c).
    """
    assumptions = outcome_assumptions((terms.currency,))
    if scenario_currency is not None and terms.currency != scenario_currency:
        return RevenueUnavailable(reason=CURRENCY_MISMATCH, assumptions_used=assumptions)
    if terms.unit_rate is not None and any(
        category.units is None for category in terms.categories
    ):
        return RevenueUnavailable(
            reason=INCOMPLETE_COMMERCIAL_TERMS, assumptions_used=assumptions
        )

    unrounded = [(category, _category_revenue(terms, category)) for category in terms.categories]
    guaranteed = round_money(_bounded(terms.fixed_fee, terms))
    category_revenues = tuple(
        CategoryRevenue(
            category=category.category,
            units=category.units,
            probability=category.probability,
            revenue=round_money(revenue),
        )
        for category, revenue in unrounded
    )
    if any(category.probability is None for category in terms.categories):
        return RevenueResult(
            revenue=guaranteed,
            currency=terms.currency,
            assumptions_used=assumptions,
            expected_state=NO_PROBABILITIES,
            category_revenues=category_revenues,
        )
    expected = sum(
        (
            category.probability / _HUNDRED * revenue
            for category, revenue in unrounded
            if category.probability is not None  # zawsze prawda tutaj; zawęża typ
        ),
        Decimal("0"),
    )
    return RevenueResult(
        revenue=guaranteed,
        currency=terms.currency,
        assumptions_used=assumptions,
        expected_revenue=round_money(expected),
        expected_state=EXPECTED_CALCULATED,
        category_revenues=category_revenues,
    )
