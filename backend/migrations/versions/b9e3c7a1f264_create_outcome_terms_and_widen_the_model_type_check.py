"""create outcome_terms and widen the commercial model discriminator

SC-4-03 (F-06.3, Issue #67; ADR-0003 aneks 2026-09-25 SC-4-03, ADR-0004 aneks 2026-09-25 SC-4-03).
Tylko expand (ADR-0001, expand → deploy → contract): jedna nowa tabela i jedno poszerzone
ograniczenie. Żadna kolumna istniejącej tabeli nie dochodzi, nie znika ani się nie zmienia, żaden
wiersz nie jest zapisywany — kod wdrożony przed tą migracją działa dalej na nowym schemacie. W oknie
mieszanych wersji instancja starszego kodu, która spotka regułę `outcome_based`, odpowiada nazwanym
stanem `unsupported_model_type` (odczyt) i `409` (kopia), nigdy połową agregatu (SC-4-01,
R-02/R-03).

**Jedyna instrukcja dotykająca istniejącej tabeli**: `ck_commercial_terms_model_type_known` jest
usuwane i odtwarzane z **pełną listą `IN`** — `time_and_material` i `outcome_based` (ADR-0003, aneks
SC-4-03, pkt 10c). Migracja niosąca tylko własną wartość po cichu unieważniłaby zapisane reguły T&M
przy walidacji ograniczenia; `downgrade` odtwarza listę sprzed migracji (`time_and_material`), nie
listę pustą ani jednoelementową z nową wartością. Odtworzenie waliduje istniejące wiersze pod
blokadą `ACCESS EXCLUSIVE` na `commercial_terms` — tabela jest mała (jedna reguła na scenariusz), a
`lock_timeout` niżej ogranicza czekanie.

**Co baza egzekwuje w `outcome_terms`** (fixture, skrypt ani import nie przechodzą przez Pydantic):

1. zgodność typu złożonym kluczem obcym `(commercial_terms_id, model_type) → commercial_terms (id,
   model_type)` i `CHECK (model_type = 'outcome_based')` — wzorzec `tm_terms` bez zmian (pkt 1);
2. opłata stała `NOT NULL`, składniki opcjonalne `NULL` — nigdy `0` za brak (pkt 2); wszystkie
   kwoty, stawka i liczby jednostek nieujemne; `revenue_min <= revenue_max`, gdy oba ustawione;
3. cztery kategorie jako kolumny, prawdopodobieństwa `NUMERIC(5,2)`, "wszystkie `NULL` albo suma
   dokładnie 100" jako `CHECK` jednego wiersza (pkt 3-4); liczby jednostek `NULL` dozwolone tylko
   bez stawki za jednostkę — ze stawką wszystkie cztery `NOT NULL` (`CHECK` jednego wiersza);
4. waluta reguły: te same dwa `CHECK` co w katalogu (pkt 7).

**Czego tu świadomie nie ma:** `ON DELETE` na kluczu obcym (kaskada byłaby drugą, niestrzeżoną drogą
zniknięcia wiersza zatwierdzonego scenariusza), `updated_at` (znacznik należy do reguły), tabeli
migawki (ADR-0004, aneks SC-4-03, pkt 4 — nic spoza scenariusza).

Revision ID: b9e3c7a1f264
Revises: a3d9e6f20c71
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9e3c7a1f264"
down_revision: str | None = "a3d9e6f20c71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "3s"
"""To samo ograniczenie co w każdej migracji od `d5e94a1c6b73` (R-06): odtworzenie CHECK na
`commercial_terms` i klucz obcy do niej czekają na blokady tabeli czytanej przez inne sesje."""

# Zapisane tu, a nie importowane z modelu: migracja musi dalej opisywać schemat, który wytworzyła,
# także gdy model pójdzie dalej (zasada `f3a1d0c58b27`). `tests/test_outcome_terms_schema.py`
# pilnuje zgodności kopii.
_MODEL_TYPE_OUTCOME_BASED = "outcome_based"

_MODEL_TYPE_KNOWN_EXPRESSION = "model_type IN ('time_and_material', 'outcome_based')"
"""Pełna lista `IN` — wartości wcześniejszych modeli razem z nową (ADR-0003, aneks SC-4-03,
pkt 10c)."""

_PREVIOUS_MODEL_TYPE_KNOWN_EXPRESSION = "model_type IN ('time_and_material')"
"""Lista sprzed tej migracji — dokładnie wyrażenie z `e7b41c9d2a58`; odtwarza ją `downgrade`."""

_MODEL_TYPE_KNOWN = "ck_commercial_terms_model_type_known"

_OUTCOME_MODEL_TYPE_EXPRESSION = "model_type = 'outcome_based'"

_CATEGORIES = ("not_achieved", "partial", "achieved", "exceeded")

_PROBABILITIES_EXPRESSION = (
    "(not_achieved_probability IS NULL AND partial_probability IS NULL"
    " AND achieved_probability IS NULL AND exceeded_probability IS NULL)"
    " OR (not_achieved_probability IS NOT NULL AND partial_probability IS NOT NULL"
    " AND achieved_probability IS NOT NULL AND exceeded_probability IS NOT NULL"
    " AND not_achieved_probability + partial_probability + achieved_probability"
    " + exceeded_probability = 100)"
)

_UNITS_WITH_UNIT_RATE_EXPRESSION = (
    "unit_rate IS NULL OR (not_achieved_units IS NOT NULL AND partial_units IS NOT NULL"
    " AND achieved_units IS NOT NULL AND exceeded_units IS NOT NULL)"
)

_BOUNDS_ORDERED_EXPRESSION = (
    "revenue_min IS NULL OR revenue_max IS NULL OR revenue_min <= revenue_max"
)
_CURRENCY_ISO4217_EXPRESSION = "char_length(currency) = 3"
_CURRENCY_UPPER_EXPRESSION = "currency = upper(currency)"

_AMOUNT = sa.Numeric(precision=14, scale=4)
_UNITS = sa.Numeric(precision=14, scale=4)
_PROBABILITY = sa.Numeric(precision=5, scale=2)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    # --- dyskryminator: pełna lista `IN` (ADR-0003, aneks SC-4-03, pkt 1 i 10c) ------------------
    op.drop_constraint(op.f(_MODEL_TYPE_KNOWN), "commercial_terms", type_="check")
    op.create_check_constraint(
        op.f(_MODEL_TYPE_KNOWN), "commercial_terms", sa.text(_MODEL_TYPE_KNOWN_EXPRESSION)
    )

    # --- szczegóły reguły Outcome-based, 1:1, zgodność typu w bazie ------------------------------
    category_columns: list[sa.Column] = []
    category_checks: list[sa.CheckConstraint] = []
    for category in _CATEGORIES:
        units, probability = f"{category}_units", f"{category}_probability"
        category_columns += [
            sa.Column(units, _UNITS, nullable=True),
            sa.Column(probability, _PROBABILITY, nullable=True),
        ]
        category_checks += [
            sa.CheckConstraint(
                f"{units} >= 0", name=op.f(f"ck_outcome_terms_{units}_not_negative")
            ),
            sa.CheckConstraint(
                f"{probability} >= 0", name=op.f(f"ck_outcome_terms_{probability}_not_negative")
            ),
        ]

    op.create_table(
        "outcome_terms",
        sa.Column("commercial_terms_id", sa.UUID(), nullable=False),
        sa.Column(
            "model_type",
            sa.String(length=40),
            server_default=sa.text(f"'{_MODEL_TYPE_OUTCOME_BASED}'"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("fixed_fee", _AMOUNT, nullable=False),
        sa.Column("success_bonus", _AMOUNT, nullable=True),
        sa.Column("unit_rate", _AMOUNT, nullable=True),
        sa.Column("revenue_min", _AMOUNT, nullable=True),
        sa.Column("revenue_max", _AMOUNT, nullable=True),
        *category_columns,
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _OUTCOME_MODEL_TYPE_EXPRESSION, name=op.f("ck_outcome_terms_model_type_is_outcome")
        ),
        sa.CheckConstraint(
            _CURRENCY_ISO4217_EXPRESSION, name=op.f("ck_outcome_terms_currency_iso4217")
        ),
        sa.CheckConstraint(
            _CURRENCY_UPPER_EXPRESSION, name=op.f("ck_outcome_terms_currency_is_upper")
        ),
        sa.CheckConstraint("fixed_fee >= 0", name=op.f("ck_outcome_terms_fixed_fee_not_negative")),
        sa.CheckConstraint(
            "success_bonus >= 0", name=op.f("ck_outcome_terms_success_bonus_not_negative")
        ),
        sa.CheckConstraint("unit_rate >= 0", name=op.f("ck_outcome_terms_unit_rate_not_negative")),
        sa.CheckConstraint(
            "revenue_min >= 0", name=op.f("ck_outcome_terms_revenue_min_not_negative")
        ),
        sa.CheckConstraint(
            "revenue_max >= 0", name=op.f("ck_outcome_terms_revenue_max_not_negative")
        ),
        sa.CheckConstraint(
            _BOUNDS_ORDERED_EXPRESSION, name=op.f("ck_outcome_terms_revenue_bounds_ordered")
        ),
        *category_checks,
        sa.CheckConstraint(
            _PROBABILITIES_EXPRESSION, name=op.f("ck_outcome_terms_probabilities_sum_to_100")
        ),
        sa.CheckConstraint(
            _UNITS_WITH_UNIT_RATE_EXPRESSION,
            name=op.f("ck_outcome_terms_units_given_with_unit_rate"),
        ),
        sa.ForeignKeyConstraint(
            ["commercial_terms_id", "model_type"],
            ["commercial_terms.id", "commercial_terms.model_type"],
            name=op.f("fk_outcome_terms_commercial_terms_model_type"),
        ),
        sa.PrimaryKeyConstraint("commercial_terms_id", name=op.f("pk_outcome_terms")),
    )

    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    """Odwrotność `upgrade`: tabela szczegółów, potem lista `IN` sprzed migracji.

    Utrata danych z definicji, jak w każdym downgrade migracji expand — istnieje, żeby migrację
    dało się przetestować w obie strony. Reguły T&M przechodzą bez zmian. Pozostała reguła
    `outcome_based` (bez szczegółów po usunięciu tabeli) sprawi, że odtworzenie CHECK zostanie
    odrzucone przez bazę — celowo: downgrade nie usuwa po cichu reguł scenariuszy.
    """
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.drop_table("outcome_terms")
    op.drop_constraint(op.f(_MODEL_TYPE_KNOWN), "commercial_terms", type_="check")
    op.create_check_constraint(
        op.f(_MODEL_TYPE_KNOWN), "commercial_terms", sa.text(_PREVIOUS_MODEL_TYPE_KNOWN_EXPRESSION)
    )
    op.execute("SET LOCAL lock_timeout = DEFAULT")
