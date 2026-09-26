"""QA SC-4-03, runda 3 (po merge z SC-4-04 i SC-1-11) — dowody, których brakowało drzewu po merge.

- **Migracja `b9e3c7a1f264` w obie strony z zapisaną regułą Story Points.** Dotychczasowe próby
  `downgrade`/`upgrade` tej migracji miały w bazie wyłącznie regułę T&M
  (`tests/test_outcome_terms_schema.py`), a próba `d2f6a91c4b58` — żadnej
  (`tests/test_story_points_terms.py`). `downgrade` odtwarzający listę `IN` bez `story_points`
  przechodził więc po stronie bazy (na regule T&M) i był łapany wyłącznie porównaniem tekstu
  `pg_get_constraintdef`. Tu reguła Story Points ze szczegółami musi przejść `downgrade` do
  `b7e3f19a6c52` (lista `IN` z `d2f6a91c4b58`) i ponowny `upgrade` bez zmiany wartości — baza sama
  odrzuca zawężoną listę.
- **Kontrast: ta sama próba z zapisaną regułą Outcome-based jest odrzucona i nic nie ginie.**
  Docstring `downgrade` obiecuje, że pozostała reguła `outcome_based` sprawi, iż odtworzenie CHECK
  zostanie odrzucone — "celowo: downgrade nie usuwa po cichu reguł scenariuszy". Dotąd nie
  sprawdzone. Tu: `downgrade` podnosi wyjątek, rewizja zostaje na `head`, reguła i jej wiersz
  `outcome_terms` są nietknięte (transakcyjny DDL PostgreSQL cofa także `DROP TABLE`).
- **Kopia projektu z trzema modelami naraz** (T&M, dwie reguły Story Points o różnych wartościach,
  Outcome-based): każda skopiowana reguła ma szczegóły własnego modelu, wartości **swojego**
  źródła i ten sam przychód. Dotychczasowe testy kopii mają jedną regułę jednego modelu w
  transakcji, więc kopiujący, który szuka wiersza szczegółów bez warunku na
  `commercial_terms_id`, znajdował jedyny wiersz tabeli — właściwy przypadkiem.

Prawdziwy PostgreSQL; migracje na `engine` współdzielonym przez sesję, `upgrade head` w `finally`.
"""

import os
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.data.commercial_terms import TM_TERMS_COLUMNS_NOT_COPIED
from app.models import CommercialTerms, OutcomeTerms, Scenario, StoryPointsTerms, TmTerms
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    as_caller,
    commercial_terms_path,
    make_commercial_terms,
    make_outcome_terms,
    make_project,
    make_scenario,
    make_story_points_terms,
)
from tests.test_outcome_revenue_copy import FULL_DETAILS

OUTCOME_REVISION = "b9e3c7a1f264"
PREVIOUS_REVISION = "b7e3f19a6c52"
"""`down_revision` migracji `b9e3c7a1f264` po drugiej linearyzacji (merge SC-4-05); `b7e3f19a6c52`
nie dotyka CHECK dyskryminatora — lista po `downgrade` to nadal lista z `d2f6a91c4b58`.

**`OUTCOME_REVISION` przestał być `head` po trzeciej linearyzacji** (merge SC-5-02, 2026-09-25):
`9b3f6a1d0c47` dopięta na `b9e3c7a1f264` zamiast obok niej, więc `head` jest teraz o jeden krok
dalej. Oba testy migracyjne poniżej badają zachowanie MIGRACJI `b9e3c7a1f264`, nie definicję
`head` — każdy zaczyna teraz jawnym `downgrade(alembic_config, OUTCOME_REVISION)`, który odtwarza
dokładnie ten punkt startowy, jaki miały przed tą linearyzacją. Treść żadnej asercji się nie
zmienia."""

DETAILS_BY_MODEL = {
    "time_and_material": TmTerms,
    "story_points": StoryPointsTerms,
    "outcome_based": OutcomeTerms,
}


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _one(engine: Engine, sql: str, **params: object) -> object:
    with engine.connect() as connection:
        return connection.execute(sa.text(sql), params).scalar_one()


def _committed_rule(engine: Engine, make, name: str, **details: object) -> tuple[uuid.UUID, ...]:
    """Reguła zatwierdzona w bazie (nie w wycofywanej transakcji `db_session`): `alembic` otwiera
    własne połączenie i nie widzi niezatwierdzonych wierszy."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name=name, accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Baseline")
        rule = make(setup, scenario, **details)
        ids = (rule.id, scenario.id, project.id)
        setup.commit()
    return ids


def _delete_committed(engine: Engine, rule_id, scenario_id, project_id) -> None:
    with engine.begin() as connection:
        for table in ("tm_terms", "story_points_terms"):
            connection.execute(
                sa.text(f"DELETE FROM {table} WHERE commercial_terms_id = :id"), {"id": rule_id}
            )
        if connection.execute(sa.text("SELECT to_regclass('outcome_terms')")).scalar_one():
            connection.execute(
                sa.text("DELETE FROM outcome_terms WHERE commercial_terms_id = :id"),
                {"id": rule_id},
            )
        connection.execute(sa.text("DELETE FROM commercial_terms WHERE id = :id"), {"id": rule_id})
        connection.execute(
            sa.text("DELETE FROM project_access WHERE project_id = :id"), {"id": project_id}
        )
        connection.execute(sa.text("DELETE FROM scenarios WHERE id = :id"), {"id": scenario_id})
        connection.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": project_id})


# --- migracja w obie strony z regułą Story Points -------------------------------------------------


def test_merge_a_story_points_rule_survives_the_downgrade_and_the_upgrade_of_the_outcome_migration(
    engine: Engine, alembic_config: Config
) -> None:
    """Reguła Story Points ze szczegółami (1000 × 25 PLN) przechodzi `downgrade` do `b7e3f19a6c52`
    i ponowny `upgrade` bez zmiany. Mutacja "`downgrade` odtwarza listę `IN` bez `story_points`"
    (albo `upgrade` z listą bez `story_points`): baza odrzuca odtworzenie CHECK na tej regule —
    niezależnie od tego, czy stała w migracji i jej strażnik dryfu się zgadzają."""
    ids = _committed_rule(
        engine, make_story_points_terms, "SP downgrade",
        price_per_point=Decimal("1000.0000"), accepted_points=25, currency="PLN",
    )
    rule_id = ids[0]
    details_sql = (
        "SELECT price_per_point::text || '|' || accepted_points::text || '|' || currency"
        " FROM story_points_terms WHERE commercial_terms_id = :id"
    )
    # `OUTCOME_REVISION` docstring: no longer `head` after the SC-5-02 linearization — an explicit
    # `downgrade` here reproduces the exact starting point this test had before that.
    command.downgrade(alembic_config, OUTCOME_REVISION)
    before = _one(engine, "SELECT version_num FROM alembic_version")
    assert before == OUTCOME_REVISION
    try:
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        assert _one(engine, "SELECT version_num FROM alembic_version") == PREVIOUS_REVISION
        assert _one(engine, "SELECT to_regclass('outcome_terms') IS NULL") is True
        assert _one(
            engine, "SELECT model_type FROM commercial_terms WHERE id = :id", id=rule_id
        ) == "story_points"
        assert _one(engine, details_sql, id=rule_id) == "1000.0000|25|PLN"

        # `OUTCOME_REVISION`, not `"head"`: `head` now points one migration further (SC-5-02) than
        # the point this test is about — going all the way to `head` here would still prove nothing
        # wrong, but `assert … == before` would then compare `OUTCOME_REVISION` against the real
        # `head` and fail for a reason unrelated to what this test checks.
        command.upgrade(alembic_config, OUTCOME_REVISION)
        assert _one(engine, "SELECT version_num FROM alembic_version") == before
        assert _one(
            engine, "SELECT model_type FROM commercial_terms WHERE id = :id", id=rule_id
        ) == "story_points"
        assert _one(engine, details_sql, id=rule_id) == "1000.0000|25|PLN"
    finally:
        command.upgrade(alembic_config, "head")
        _delete_committed(engine, *ids)


def test_merge_contrast_an_outcome_rule_makes_the_downgrade_refuse_and_loses_nothing(
    engine: Engine, alembic_config: Config
) -> None:
    """Kontrast testu wyżej — jedna zmiana: reguła Outcome-based zamiast Story Points. `downgrade`
    jest odrzucony (odtworzenie CHECK bez `outcome_based` na istniejącej regule), rewizja zostaje na
    `b9e3c7a1f264`, a reguła i jej wiersz `outcome_terms` są nietknięte — `DROP TABLE` cofnięty
    razem z resztą transakcji. Mutacja "downgrade najpierw usuwa reguły `outcome_based`, żeby
    przejść": `downgrade` przechodzi i reguła znika po cichu."""
    ids = _committed_rule(engine, make_outcome_terms, "Outcome downgrade", **FULL_DETAILS)
    rule_id = ids[0]
    # `OUTCOME_REVISION` docstring: no longer `head` after the SC-5-02 linearization — an explicit
    # `downgrade` here reproduces the exact starting point this test had before that.
    command.downgrade(alembic_config, OUTCOME_REVISION)
    before = _one(engine, "SELECT version_num FROM alembic_version")
    assert before == OUTCOME_REVISION
    try:
        with pytest.raises(sa.exc.IntegrityError):
            command.downgrade(alembic_config, PREVIOUS_REVISION)
        assert _one(engine, "SELECT version_num FROM alembic_version") == OUTCOME_REVISION
        assert _one(engine, "SELECT to_regclass('outcome_terms') IS NOT NULL") is True
        assert _one(
            engine, "SELECT model_type FROM commercial_terms WHERE id = :id", id=rule_id
        ) == "outcome_based"
        assert _one(
            engine,
            "SELECT fixed_fee::text FROM outcome_terms WHERE commercial_terms_id = :id",
            id=rule_id,
        ) == "20000.0000"
    finally:
        command.upgrade(alembic_config, "head")
        _delete_committed(engine, *ids)


# --- kopia projektu z regułami trzech modeli naraz ------------------------------------------------


def _details(session: Session, model_type: str, rule_id: uuid.UUID) -> dict[str, object]:
    table = DETAILS_BY_MODEL[model_type]
    row = session.execute(
        sa.select(table).where(table.commercial_terms_id == rule_id)
    ).scalar_one_or_none()
    assert row is not None, f"the {model_type} rule has no details row — an incomplete rule"
    return {
        attribute.key: getattr(row, attribute.key)
        for attribute in sa.inspect(table).column_attrs
        if attribute.key not in TM_TERMS_COLUMNS_NOT_COPIED
    }


def test_merge_one_project_copy_copies_every_models_rule_with_its_own_sources_details(
    client: TestClient, db_session: Session
) -> None:
    """Jedna kopia projektu z czterema scenariuszami: T&M, Story Points 1000 × 25, Story Points
    500 × 10 (EUR) i Outcome-based. Każda reguła kopii: nowy identyfikator, model źródła, wiersz
    szczegółów tabeli **swojego** modelu z wartościami **swojego** źródła, ten sam przychód.

    Dwie reguły tego samego modelu z różnymi wartościami są tu celowo: kopiujący, który czyta
    wiersz szczegółów bez warunku na regułę źródła, w teście jednej reguły trafia w jedyny wiersz
    tabeli; tu trafia w cudzy albo w dwa."""
    project = make_project(db_session, name="Mixed copy", accessible_to=(IN_SCOPE_USER,))
    sources = {
        "T&M": make_commercial_terms(db_session, make_scenario(db_session, project, name="T&M")),
        "SP-A": make_story_points_terms(
            db_session, make_scenario(db_session, project, name="SP-A"),
            price_per_point=Decimal("1000.0000"), accepted_points=25, currency="PLN",
        ),
        "SP-B": make_story_points_terms(
            db_session, make_scenario(db_session, project, name="SP-B"),
            price_per_point=Decimal("500.0000"), accepted_points=10, currency="EUR",
        ),
        "Outcome": make_outcome_terms(
            db_session, make_scenario(db_session, project, name="Outcome"), **FULL_DETAILS
        ),
    }
    source_revenue = {
        name: client.get(
            commercial_terms_path(project.id, rule.scenario_id), headers=as_caller(IN_SCOPE_USER)
        ).json()["revenue"]
        for name, rule in sources.items()
    }
    assert source_revenue["SP-A"]["amount"] == "25000.00"
    assert source_revenue["SP-B"]["amount"] == "5000.00"

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    db_session.expire_all()
    copy_project_id = uuid.UUID(response.json()["id"])
    copies = {
        scenario.name: scenario
        for scenario in db_session.execute(
            sa.select(Scenario).where(Scenario.project_id == copy_project_id)
        ).scalars()
    }
    assert set(copies) == set(sources)

    for name, source_rule in sources.items():
        copied_rule = db_session.execute(
            sa.select(CommercialTerms).where(CommercialTerms.scenario_id == copies[name].id)
        ).scalar_one()
        assert copied_rule.id != source_rule.id
        assert copied_rule.model_type == source_rule.model_type
        assert _details(db_session, copied_rule.model_type, copied_rule.id) == _details(
            db_session, source_rule.model_type, source_rule.id
        ), f"{name}: the copy's details are not its own source's"
        copy_read = client.get(
            commercial_terms_path(copy_project_id, copies[name].id),
            headers=as_caller(IN_SCOPE_USER),
        ).json()
        assert copy_read["commercial_terms"]["id"] == str(copied_rule.id)
        assert copy_read["revenue"] == source_revenue[name], name
