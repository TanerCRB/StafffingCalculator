"""SC-4-03 × SC-4-05 — QA, runda 4 (po drugim merge `origin/main`): luki w dowodzie
`tests/test_outcome_scope_ref.py` znalezione mutacjami.

1. **Kopia (D-4=A) — mutacja przeżywała twierdzenie, ginęła na innym mechanizmie.** Mutacja
   "kopia reguły `outcome_based` gubi `scope_ref` (zapisuje `NULL`)" czerwieniła
   `test_d4_outcome_the_copy_remaps_…`, ale nie na asercji o `scope_ref`: źródło niosło też regułę
   Story Points całego scenariusza, więc druga reguła `scope_ref IS NULL` kopii wpadała na
   `uq_commercial_terms_scenario_id` (500 przy kopii) — test mierzył indeks bazy, nie przemapowanie.
   Tu źródło ma **wyłącznie** regułę Outcome-based segmentu: zgubiony `scope_ref` nie koliduje z
   niczym, więc zabija go tylko asercja o zasięgu. Kontrast (jedna zmiana — reguła bez `scope_ref`)
   pokazuje, że `NULL` w kopii jest poprawny wtedy i tylko wtedy, gdy był w źródle.
2. **Jedna instrukcja zapisu także z `scope_ref`.** Nazwa
   `test_k_02_outcome_a_segment_scoped_rule_is_written_with_its_details_in_one_guarded_statement`
   obiecuje jedną instrukcję, ale test sprawdza tylko wynik. Mutacja "z `scope_ref` reguła strzeżoną
   instrukcją, szczegóły drugą" przeżyła cały zestaw (902 zielone). Tu liczone są instrukcje
   `INSERT` ścieżki produkcyjnej — z `scope_ref` i bez niego (kontrast), ten sam kształt.

Prawdziwy PostgreSQL.
"""

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.commercial_terms import create_commercial_terms
from app.models import CommercialTerms, OutcomeTerms, Scenario, ScenarioDeliverySegment
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    make_outcome_terms,
    make_project,
    make_scenario,
    make_scenario_delivery_segment,
)
from tests.test_outcome_revenue_copy import FULL_DETAILS

CALLER = CallerIdentity(user_id=IN_SCOPE_USER)


def _details_row(session: Session, rule_id: uuid.UUID) -> dict[str, object]:
    row = session.execute(
        sa.select(OutcomeTerms.__table__).where(OutcomeTerms.commercial_terms_id == rule_id)
    ).mappings().one()
    return {column: row[column] for column in FULL_DETAILS}


@pytest.mark.parametrize("scoped", [True, False], ids=["segment_rule", "whole_scenario_rule"])
def test_qa_d4_outcome_the_copy_keeps_the_scope_of_a_lone_outcome_rule(
    client: TestClient, db_session: Session, scoped: bool
) -> None:
    """Jedyna reguła źródła to Outcome-based — na segmencie "Phase 1" (`segment_rule`) albo na całym
    scenariuszu (`whole_scenario_rule`, kontrast: jedna zmiana). Segment istnieje w obu wariantach,
    więc kopia ma swój "Phase 1" w obu. Kopia reguły wskazuje segment **kopii** o tej samej nazwie
    wtedy i tylko wtedy, gdy źródło wskazywało segment; bez `scope_ref` w źródle — bez niego w
    kopii. Żadna inna reguła kopii nie może tu zabić mutacji zamiast asercji o zasięgu."""
    project = make_project(db_session, name="Aurora lone", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    phase_1 = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    source_rule = make_outcome_terms(
        db_session, scenario, scope_ref=phase_1.id if scoped else None, **FULL_DETAILS
    )

    response = client.post(f"/projects/{project.id}/copy", headers=as_caller(IN_SCOPE_USER))
    assert response.status_code == 201, response.text
    db_session.expire_all()
    copy = db_session.execute(
        sa.select(Scenario).where(Scenario.project_id == uuid.UUID(response.json()["id"]))
    ).scalar_one()
    copy_phase_1 = db_session.execute(
        sa.select(ScenarioDeliverySegment.id).where(
            ScenarioDeliverySegment.scenario_id == copy.id,
            ScenarioDeliverySegment.name == "Phase 1",
        )
    ).scalar_one()
    copied = db_session.execute(
        sa.select(CommercialTerms).where(CommercialTerms.scenario_id == copy.id)
    ).scalar_one()

    assert copied.model_type == "outcome_based"
    if scoped:
        assert copied.scope_ref == copy_phase_1
        assert copied.scope_ref != phase_1.id
    else:
        assert copied.scope_ref is None
    assert _details_row(db_session, copied.id) == FULL_DETAILS
    assert db_session.get(CommercialTerms, source_rule.id).scope_ref == (
        phase_1.id if scoped else None
    )


@pytest.mark.parametrize("scoped", [True, False], ids=["segment_rule", "whole_scenario_rule"])
def test_qa_k_02_outcome_the_rule_scope_ref_and_details_are_one_insert_statement(
    db_session: Session, scoped: bool
) -> None:
    """Ścieżka produkcyjna `create_commercial_terms` dla `outcome_based` wykonuje **jedną**
    instrukcję `INSERT`, i ta instrukcja pisze zarówno `commercial_terms` (z `scope_ref`), jak i
    `outcome_terms` — z `scope_ref` segmentu i bez niego (kontrast), ten sam kształt. Druga
    instrukcja dla szczegółów byłaby zapisem poza strażnikiem `approved` wbudowanym w pierwszą
    (ADR-0003, pkt 3; ADR-0004, aneks SC-4-03, pkt 2)."""
    project = make_project(db_session, name="Aurora one statement", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    segment = make_scenario_delivery_segment(db_session, scenario, name="Phase 1")
    db_session.flush()
    inserts: list[str] = []

    def record(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if "insert into" in statement.lower():
            inserts.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        view = create_commercial_terms(
            db_session,
            CALLER,
            project.id,
            scenario.id,
            model_type="outcome_based",
            scope_ref=segment.id if scoped else None,
            domain_values=FULL_DETAILS,
        )
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert view is not None and view.terms is not None
    assert view.terms.scope_ref == (segment.id if scoped else None)
    assert len(inserts) == 1, (
        "the outcome rule and its details were written by more than one INSERT — the approved "
        f"guard embedded in the first does not cover the others: {inserts}"
    )
    written = inserts[0].lower()
    assert written.strip().startswith("with")
    assert "insert into commercial_terms" in written
    assert "scope_ref" in written
    assert "insert into outcome_terms" in written
