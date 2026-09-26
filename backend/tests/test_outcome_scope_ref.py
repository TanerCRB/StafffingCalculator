"""SC-4-03 × SC-4-05 — `scope_ref` (F-06.5, Issue #69) dla reguły Outcome-based (F-06.3, Issue #67).

SC-4-05 dowodzi `scope_ref` na regułach T&M i Story Points (`tests/test_commercial_terms_scope.py`);
ten plik powtarza jego kluczowe przypadki dla trzeciego modelu, który wszedł do `main` równolegle:
zapis jedną strzeżoną instrukcją (reguła, `scope_ref` i wiersz `outcome_terms` jako literały),
rozłączność zasięgów w bazie (K-02), odczyt i przychód reguły segmentu (K-01 — Outcome-based czyta
wyłącznie własny wiersz `outcome_terms`, więc dwie reguły tego modelu **nie** są jedną odpowiedzią),
strażnik `approved` (K-03), złożony klucz obcy (K-04) i przemapowanie `scope_ref` przy kopii (D-4=A)
razem z pełnym wierszem szczegółów.

**Czego ten plik nie dowodzi:** ochrony przed podwójnym rozliczeniem na poziomie KWOTY przychodu
(wymaga F-04 — powiązania pozycji obsady z segmentem, poza zakresem SC-4-05); ścieżki API zapisu
`scope_ref` (żaden schemat żądania go nie niesie — ADR-0016, pkt 8).

Prawdziwy PostgreSQL — ograniczenia i strażnik są twierdzeniami o bazie.
"""

import importlib.util
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.commercial_terms import (
    CommercialTermsFrozen,
    CommercialTermsWriteRefused,
    MultipleRulesOfOneModelNotSupported,
    create_commercial_terms,
    revenue_by_model_type,
    rules_of_scenario,
)
from app.domain.revenue import RevenueResult
from app.models import (
    CommercialTerms,
    OutcomeTerms,
    Scenario,
    ScenarioDeliverySegment,
    ScenarioStatus,
)
from app.models.commercial_terms import SCENARIO_ID_SCOPE_UNIQUE, SCOPE_REF_FOREIGN_KEY
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    commercial_terms_path,
    make_allocation,
    make_commercial_terms,
    make_dimension_tuple,
    make_outcome_terms,
    make_project,
    make_rate,
    make_scenario,
    make_scenario_delivery_segment,
    make_staffing_position,
    make_story_points_terms,
)
from tests.test_outcome_revenue_copy import FULL_DETAILS

MAR = date(2026, 3, 1)
CALLER = CallerIdentity(user_id=IN_SCOPE_USER)

FULL_GUARANTEED = Decimal("22000.00")
"""`FULL_DETAILS`: opłata 20000 z zerowym składnikiem zmiennym, podniesiona do `revenue_min`
22000 (ADR-0003, aneks SC-4-03, pkt 6)."""
FULL_EXPECTED = Decimal("34400.00")
"""0.1·22000 (nieosiągnięty, min) + 0.2·25000 (częściowy, bez premii) + 0.4·38000 (osiągnięty) +
0.3·40000 (przekroczony 45000, obcięty do max)."""
DEFAULT_GUARANTEED = Decimal("20000.00")
"""Domyślne szczegóły `make_outcome_terms`: sama opłata 20000, bez min/max."""


def _scenario(session: Session, name: str = "Baseline") -> Scenario:
    project = make_project(session, name="Aurora outcome", accessible_to=(IN_SCOPE_USER,))
    return make_scenario(session, project, name=name)


def _priced_scenario(session: Session) -> tuple[Scenario, ScenarioDeliverySegment]:
    """Scenariusz z jedną wycenioną pozycją T&M (100 h × 200 PLN = 20000.00) i jednym segmentem —
    to samo przygotowanie co `_priced_plan` SC-4-05, żeby zatwierdzenie i reguła T&M miały dane."""
    scenario = _scenario(session)
    dimensions = make_dimension_tuple(session)
    position = make_staffing_position(session, scenario, dimensions, headcount=2, start_date=MAR)
    make_allocation(session, position, period_month=MAR)
    make_rate(
        session,
        dimensions,
        effective_from=date(2026, 1, 1),
        default_selling_rate=Decimal("200.0000"),
        currency="PLN",
    )
    return scenario, make_scenario_delivery_segment(session, scenario, name="Phase 1")


def _count(session: Session, model, scenario_id: uuid.UUID) -> int:
    session.expire_all()
    query = sa.select(sa.func.count()).select_from(CommercialTerms)
    if model is OutcomeTerms:
        query = query.join(OutcomeTerms, OutcomeTerms.commercial_terms_id == CommercialTerms.id)
    return session.execute(query.where(CommercialTerms.scenario_id == scenario_id)).scalar_one()


def _sqlstate_and_constraint(error: IntegrityError) -> tuple[str | None, str | None]:
    diagnostics = error.orig.diag  # type: ignore[union-attr]
    return diagnostics.sqlstate, diagnostics.constraint_name


def _details_row(session: Session, rule_id: uuid.UUID) -> dict[str, object]:
    row = session.execute(
        sa.select(OutcomeTerms.__table__).where(OutcomeTerms.commercial_terms_id == rule_id)
    ).mappings().one()
    return {column: row[column] for column in FULL_DETAILS}


# --- zapis: jedna strzeżona instrukcja niesie `scope_ref` i szczegóły -----------------------------


def test_k_02_outcome_a_segment_scoped_rule_is_written_with_its_details_in_one_guarded_statement(
    db_session: Session,
) -> None:
    """Ścieżka produkcyjna `create_commercial_terms` zapisuje regułę `outcome_based` z `scope_ref`
    segmentu **i** jej wiersz `outcome_terms` ze wszystkimi kolumnami dziedzinowymi — `scope_ref`
    to jeszcze jeden literał tej samej instrukcji, nie drugi zapis obok szczegółów."""
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")

    view = create_commercial_terms(
        db_session,
        CALLER,
        scenario.project_id,
        scenario.id,
        model_type="outcome_based",
        scope_ref=segment.id,
        domain_values=FULL_DETAILS,
    )

    assert view is not None
    assert view.terms is not None
    assert (view.terms.model_type, view.terms.scope_ref) == ("outcome_based", segment.id)
    assert view.outcome_terms is not None
    assert _details_row(db_session, view.terms.id) == FULL_DETAILS
    assert isinstance(view.revenue, RevenueResult), view.revenue
    assert (view.revenue.revenue, view.revenue.expected_revenue) == (
        FULL_GUARANTEED,
        FULL_EXPECTED,
    )


def test_k_02_outcome_a_second_outcome_rule_of_the_same_segment_is_refused_by_the_database(
    db_session: Session,
) -> None:
    """K-02 dla Outcome-based — drugi wiersz tego samego segmentu odrzuca częściowy indeks
    `uq_commercial_terms_scenario_id_scope_ref`, niezależnie od modelu reguły."""
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario)
    make_outcome_terms(db_session, scenario, scope_ref=segment.id)

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            make_outcome_terms(db_session, scenario, scope_ref=segment.id)

    assert _sqlstate_and_constraint(refused.value) == ("23505", SCENARIO_ID_SCOPE_UNIQUE)
    assert _count(db_session, CommercialTerms, scenario.id) == 1


def test_k_02_outcome_the_guarded_create_path_refuses_a_second_rule_of_a_segment_and_writes_nothing(
    db_session: Session,
) -> None:
    """Ta sama odmowa przez ścieżkę produkcyjną: `409`-owy `CommercialTermsWriteRefused`, a po
    odmowie nie ma ani drugiej reguły, ani osieroconego wiersza `outcome_terms` — oba wiersze są w
    jednej instrukcji, więc odmowa jednego cofa oba."""
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario)
    make_outcome_terms(db_session, scenario, scope_ref=segment.id)
    db_session.commit()  # do SAVEPOINT-u testu: `rollback` ścieżki zapisu nie cofnie przygotowania

    with pytest.raises(CommercialTermsWriteRefused):
        create_commercial_terms(
            db_session,
            CALLER,
            scenario.project_id,
            scenario.id,
            model_type="outcome_based",
            scope_ref=segment.id,
            domain_values=FULL_DETAILS,
        )

    assert _count(db_session, CommercialTerms, scenario.id) == 1
    assert _count(db_session, OutcomeTerms, scenario.id) == 1


def test_k_02_outcome_a_whole_scenario_rule_and_segment_rules_of_three_models_coexist(
    db_session: Session,
) -> None:
    """Kontrast: reguła Outcome-based całego scenariusza, reguła Outcome-based segmentu i reguły
    innych modeli na innych segmentach współistnieją — rozłączność dotyczy zasięgu, nie modelu."""
    scenario = _scenario(db_session)
    phase_1 = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    phase_2 = make_scenario_delivery_segment(db_session, scenario, name="Phase 2")
    phase_3 = make_scenario_delivery_segment(db_session, scenario, name="Phase 3")

    make_outcome_terms(db_session, scenario)
    make_outcome_terms(db_session, scenario, scope_ref=phase_1.id)
    make_commercial_terms(db_session, scenario, scope_ref=phase_2.id)
    make_story_points_terms(db_session, scenario, scope_ref=phase_3.id)

    assert _count(db_session, CommercialTerms, scenario.id) == 4
    assert _count(db_session, OutcomeTerms, scenario.id) == 2


# --- odczyt i przychód (K-01) --------------------------------------------------------------------


def test_k_01_outcome_a_segment_rule_is_priced_from_its_own_row_next_to_a_tm_rule(
    db_session: Session,
) -> None:
    """Reguła T&M całego scenariusza i reguła Outcome-based segmentu: dwie niezależne odpowiedzi,
    po jednej na model, każda z własnych danych — T&M z alokacji (20000.00), Outcome-based
    wyłącznie z własnego `outcome_terms` (22000.00 gwarantowany, 34400.00 oczekiwany)."""
    scenario, segment = _priced_scenario(db_session)
    make_commercial_terms(db_session, scenario)
    make_outcome_terms(db_session, scenario, scope_ref=segment.id, **FULL_DETAILS)

    answers = revenue_by_model_type(
        db_session, scenario, rules_of_scenario(db_session, scenario.id)
    )

    assert set(answers) == {"time_and_material", "outcome_based"}
    assert answers["time_and_material"].revenue == Decimal("20000.00")
    outcome = answers["outcome_based"]
    assert isinstance(outcome, RevenueResult), outcome
    assert (outcome.revenue, outcome.expected_revenue) == (FULL_GUARANTEED, FULL_EXPECTED)
    assert outcome.assumptions_used.rate_source == "not_applicable"


def test_k_01_outcome_two_outcome_rules_of_one_scenario_refuse_to_guess_instead_of_dropping_one(
    db_session: Session,
) -> None:
    """Jak Story Points (reviewer R-01 SC-4-05): Outcome-based czyta parametry z **własnego**
    wiersza reguły, więc reguła całego scenariusza (20000.00) i reguła segmentu (22000.00) mogą się
    różnić — `revenue_by_model_type` odmawia zamiast po cichu wybrać jedną.

    Mutacja: `outcome_based` dopisany do `_MODEL_TYPES_WITH_SHARED_SCENARIO_REVENUE` — wtedy wynik
    `calculated` dla jednej reguły, bez śladu drugiej."""
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    make_outcome_terms(db_session, scenario)
    make_outcome_terms(db_session, scenario, scope_ref=segment.id, **FULL_DETAILS)

    rules = rules_of_scenario(db_session, scenario.id)
    assert [rule.terms.scope_ref for rule in rules] == [None, segment.id]
    assert all(rule.has_details for rule in rules)

    with pytest.raises(MultipleRulesOfOneModelNotSupported):
        revenue_by_model_type(db_session, scenario, rules)


def test_k_01_outcome_the_read_of_a_scenario_whose_only_rule_is_segment_scoped_carries_its_params(
    client: TestClient, db_session: Session
) -> None:
    """`GET` reguły scenariusza, którego jedyna reguła Outcome-based ma `scope_ref` segmentu:
    przychód wyliczony z tej reguły i jej parametry w odczycie (R-04) — zasięg niczego nie
    zmienia w wycenie modelu."""
    scenario = _scenario(db_session)
    segment = make_scenario_delivery_segment(db_session, scenario)
    make_outcome_terms(db_session, scenario, scope_ref=segment.id, **FULL_DETAILS)

    response = client.get(
        commercial_terms_path(scenario.project_id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["commercial_terms"]["model_type"] == "outcome_based"
    assert body["commercial_terms"]["outcome_terms"]["revenue_min"] == "22000.0000"
    assert (body["revenue"]["state"], body["revenue"]["amount"]) == ("calculated", "22000.00")
    assert body["revenue"]["expected_amount"] == "34400.00"


# --- strażnik `approved` (K-03) -------------------------------------------------------------------


def test_k_03_outcome_a_segment_scoped_outcome_rule_is_refused_by_the_same_write_guard(
    db_session: Session,
) -> None:
    """K-03 dla Outcome-based (wzorem K-04(b) SC-4-04 i K-03 SC-4-05): reguła z `scope_ref` na
    zatwierdzonym scenariuszu odrzucona tym samym strażnikiem w instrukcji, która pisze — ani
    reguły, ani wiersza `outcome_terms`."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    approved = make_scenario(db_session, project, name="Approved", status=ScenarioStatus.APPROVED)
    segment = make_scenario_delivery_segment(db_session, approved, name="Phase 1")

    with pytest.raises(CommercialTermsFrozen):
        create_commercial_terms(
            db_session,
            CALLER,
            project.id,
            approved.id,
            model_type="outcome_based",
            scope_ref=segment.id,
            domain_values=FULL_DETAILS,
        )

    assert _count(db_session, CommercialTerms, approved.id) == 0
    assert _count(db_session, OutcomeTerms, approved.id) == 0


def test_k_03_outcome_an_approved_scenario_with_a_segment_scoped_outcome_rule_reads_its_own_row(
    client: TestClient, db_session: Session
) -> None:
    """Zatwierdzenie scenariusza z regułą Outcome-based segmentu: odczyt nadal `calculated` z
    własnego wiersza reguły, `rate_source = not_applicable` (bez migawki — ADR-0004, aneks
    SC-4-03, pkt 4), bez `409` strażnika wyścigu."""
    scenario, segment = _priced_scenario(db_session)
    make_outcome_terms(db_session, scenario, scope_ref=segment.id, **FULL_DETAILS)

    approval = client.post(
        approve_path(scenario.project_id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert approval.status_code == 200, approval.text

    response = client.get(
        commercial_terms_path(scenario.project_id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text
    revenue = response.json()["revenue"]
    assert (revenue["state"], revenue["amount"]) == ("calculated", "22000.00")
    assert revenue["assumptions_used"]["rate_source"] == "not_applicable"


# --- złożony klucz obcy (K-04) --------------------------------------------------------------------


def test_k_04_outcome_a_rule_cannot_point_at_a_segment_of_another_scenario(
    db_session: Session,
) -> None:
    """K-04 dla Outcome-based — złożony klucz obcy odrzuca segment innego scenariusza."""
    scenario = _scenario(db_session)
    other = make_scenario(db_session, scenario.project, name="Other")
    other_segment = make_scenario_delivery_segment(db_session, other, name="Phase 1")

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            make_outcome_terms(db_session, scenario, scope_ref=other_segment.id)

    assert _sqlstate_and_constraint(refused.value) == ("23503", SCOPE_REF_FOREIGN_KEY)
    assert _count(db_session, CommercialTerms, scenario.id) == 0


def test_k_04_outcome_the_guarded_create_path_refuses_another_scenarios_segment_and_writes_nothing(
    db_session: Session,
) -> None:
    """Ta sama odmowa przez ścieżkę produkcyjną: klucz obcy odrzuca instrukcję, która niesie
    regułę i szczegóły naraz — nie zostaje ani reguła, ani wiersz `outcome_terms`."""
    scenario = _scenario(db_session)
    other = make_scenario(db_session, scenario.project, name="Other")
    other_segment = make_scenario_delivery_segment(db_session, other, name="Phase 1")
    db_session.commit()

    with pytest.raises(CommercialTermsWriteRefused):
        create_commercial_terms(
            db_session,
            CALLER,
            scenario.project_id,
            scenario.id,
            model_type="outcome_based",
            scope_ref=other_segment.id,
            domain_values=FULL_DETAILS,
        )

    assert _count(db_session, CommercialTerms, scenario.id) == 0
    assert _count(db_session, OutcomeTerms, scenario.id) == 0


# --- kopia (D-4=A) --------------------------------------------------------------------------------


def test_d4_outcome_the_copy_remaps_an_outcome_rules_scope_ref_and_copies_its_details(
    client: TestClient, db_session: Session
) -> None:
    """D-4=A dla Outcome-based: trzy reguły trzech modeli — Story Points całego scenariusza,
    Outcome-based na "Phase 1", T&M na "Phase 2". Kopia wskazuje własne segmenty kopii o tych samych
    nazwach (nie zamienione), wiersz `outcome_terms` kopii ma każdą kolumnę dziedzinową źródła, a
    przychód reguły Outcome-based kopii jest ten sam. Źródło nietknięte."""
    project = make_project(db_session, name="Aurora copy", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    phase_1 = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    phase_2 = make_scenario_delivery_segment(db_session, scenario, name="Phase 2")
    make_story_points_terms(db_session, scenario)
    outcome_rule = make_outcome_terms(db_session, scenario, scope_ref=phase_1.id, **FULL_DETAILS)
    tm_rule = make_commercial_terms(db_session, scenario, scope_ref=phase_2.id)

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    db_session.expire_all()
    copy = db_session.execute(
        sa.select(Scenario).where(Scenario.project_id == uuid.UUID(response.json()["id"]))
    ).scalar_one()

    copy_segments = {
        segment.name: segment.id
        for segment in db_session.execute(
            sa.select(ScenarioDeliverySegment).where(ScenarioDeliverySegment.scenario_id == copy.id)
        ).scalars()
    }
    copied = {
        rule.model_type: rule
        for rule in db_session.execute(
            sa.select(CommercialTerms).where(CommercialTerms.scenario_id == copy.id)
        ).scalars()
    }
    assert set(copied) == {"story_points", "outcome_based", "time_and_material"}
    assert copied["story_points"].scope_ref is None
    assert copied["outcome_based"].scope_ref == copy_segments["Phase 1"]
    assert copied["time_and_material"].scope_ref == copy_segments["Phase 2"]
    assert copied["outcome_based"].id != outcome_rule.id
    assert _details_row(db_session, copied["outcome_based"].id) == FULL_DETAILS

    outcome_copy = next(
        rule
        for rule in rules_of_scenario(db_session, copy.id)
        if rule.terms.model_type == "outcome_based"
    )
    answers = revenue_by_model_type(db_session, copy, [outcome_copy])
    assert (answers["outcome_based"].revenue, answers["outcome_based"].expected_revenue) == (
        FULL_GUARANTEED,
        FULL_EXPECTED,
    )

    assert db_session.get(CommercialTerms, outcome_rule.id).scope_ref == phase_1.id
    assert db_session.get(CommercialTerms, tm_rule.id).scope_ref == phase_2.id
    assert _details_row(db_session, outcome_rule.id) == FULL_DETAILS


# --- linearyzacja migracji po merge z SC-4-05 -----------------------------------------------------


_VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def test_merge_the_outcome_migration_follows_the_scope_ref_migration_which_leaves_the_check_alone(
) -> None:
    """`b9e3c7a1f264` stoi bezpośrednio po `b7e3f19a6c52` (jedna głowa), a `b7e3f19a6c52` nie
    odtwarza `ck_commercial_terms_model_type_known` — dlatego lista, którą `downgrade` outcome
    odtwarza, to nadal lista z `d2f6a91c4b58` (dwie wartości). Gdyby kiedyś `b7e3f19a6c52`
    zaczęła dotykać tego CHECK, ten test każe przepiąć `PREVIOUS_CHECK_MIGRATION_PATH`."""
    spec = importlib.util.spec_from_file_location(
        "sc_4_03_migration_for_scope_ref",
        _VERSIONS / "b9e3c7a1f264_create_outcome_terms_and_widen_the_model_type_check.py",
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.down_revision == "b7e3f19a6c52"
    scope_ref_source = (_VERSIONS / "b7e3f19a6c52_add_scope_ref_to_commercial_terms.py").read_text(
        encoding="utf-8"
    )
    assert "model_type_known" not in scope_ref_source
    assert "model_type IN" not in scope_ref_source
