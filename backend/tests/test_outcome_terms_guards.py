"""SC-4-03, K-05 — reguły Outcome-based nie da się zapisać do scenariusza `approved`, a odmowa
należy
do instrukcji zapisu (ADR-0004, aneks 2026-09-25 SC-4-03, pkt 2).

Ta sama konstrukcja co K-06 SC-4-01 (`tests/test_commercial_terms_guards.py`), zastosowana do
ścieżki zapisu outcome, która niesie teraz kolumny dziedzinowe w tej samej instrukcji. Wszystko na
**zatwierdzonych** transakcjach i liczone z osobnego połączenia:

1. odmowa wprost na `approved`, kontrast na `draft` tego samego projektu (`201`, oba wiersze);
2. zatwierdzenie zatwierdzone tuż przed instrukcją `INSERT` (hak `before_cursor_execute` na innym
   połączeniu) — odczyt statusu w Pythonie przed wstawieniem by to przepuścił;
3. wyścig dwóch połączeń z prawdziwym endpointem zatwierdzenia, który trzyma blokadę wiersza
   scenariusza: zapis musi czekać (`blocked`), a para "scenariusz zatwierdzony" i "reguła istnieje"
   nigdy nie zachodzi razem. Mutacja: usunięcie `FOR UPDATE` z
   `app.data.scenario_guard.unapproved_scenario`.
"""

import threading
import uuid
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.models import ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    commercial_terms_path,
    make_project,
    make_scenario,
    outcome_payload,
    wait_until_a_lock_request_is_pending,
)

OUTCOME = outcome_payload(probabilities=("70", "0", "30", "0"))


def _committed_project(engine: Engine) -> dict[str, uuid.UUID]:
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Outcome guard", accessible_to=(IN_SCOPE_USER,))
        draft = make_scenario(setup, project, name="Baseline", currency="PLN")
        approved = make_scenario(
            setup, project, name="Approved v1", status=ScenarioStatus.APPROVED, currency="PLN"
        )
        state = {"project_id": project.id, "draft_id": draft.id, "approved_id": approved.id}
        setup.commit()
    return state


def _rows_of(engine: Engine, scenario_id: uuid.UUID) -> tuple[int, int]:
    """(reguły, wiersze `outcome_terms`) jednego scenariusza — tylko zatwierdzone wiersze."""
    with engine.connect() as connection:
        rules = connection.execute(
            sa.text("SELECT count(*) FROM commercial_terms WHERE scenario_id = :id"),
            {"id": scenario_id},
        ).scalar_one()
        details = connection.execute(
            sa.text(
                "SELECT count(*) FROM outcome_terms o JOIN commercial_terms c"
                " ON c.id = o.commercial_terms_id WHERE c.scenario_id = :id"
            ),
            {"id": scenario_id},
        ).scalar_one()
    return rules, details


def _is_approved(engine: Engine, scenario_id: uuid.UUID) -> bool:
    with engine.connect() as connection:
        return (
            connection.execute(
                sa.text("SELECT status FROM scenarios WHERE id = :id"), {"id": scenario_id}
            ).scalar_one()
            == "approved"
        )


def test_k_05_writing_an_outcome_rule_into_an_approved_scenario_is_refused_and_writes_nothing(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, pierwszy przebieg — `409` z "approved", zero reguł i zero wierszy `outcome_terms`.

    Kontrast: to samo ciało do scenariusza `draft` tego samego projektu → `201` i **oba** wiersze,
    z kolumnami dziedzinowymi (oczekiwany 23000 dowodzi, że prawdopodobieństwa zapisały się w tej
    samej instrukcji)."""
    state = _committed_project(engine)

    refused = committing_client.post(
        commercial_terms_path(state["project_id"], state["approved_id"]),
        json=OUTCOME,
        headers=as_caller(IN_SCOPE_USER),
    )

    assert refused.status_code == 409, refused.text
    assert "approved" in refused.json()["detail"]
    assert _rows_of(engine, state["approved_id"]) == (0, 0)

    accepted = committing_client.post(
        commercial_terms_path(state["project_id"], state["draft_id"]),
        json=OUTCOME,
        headers=as_caller(IN_SCOPE_USER),
    )

    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["revenue"]["expected_amount"] == "23000.00"
    assert _rows_of(engine, state["draft_id"]) == (1, 1)


def test_k_05_an_approval_committed_just_before_the_insert_still_refuses_the_outcome_rule(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, drugi przebieg — strażnikiem jest instrukcja, nie odczyt statusu przed wstawieniem.

    Zatwierdzenie commituje się na innym połączeniu po tym, jak zapis rozstrzygnął zasięg (widział
    `draft`), a tuż przed instrukcją strzeżoną. Mutacja: `if scenario.status == APPROVED: raise` w
    Pythonie + zwykły `INSERT` — przeczytał `draft`, więc wstawia regułę do zatwierdzonej
    kalkulacji.
    """
    state = _committed_project(engine)
    fired: list[str] = []

    def approve_first(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if fired or "insert into commercial_terms" not in statement.lower():
            return
        fired.append(statement)
        with engine.begin() as competitor:
            competitor.execute(
                sa.text("UPDATE scenarios SET status = 'approved' WHERE id = :id"),
                {"id": state["draft_id"]},
            )

    event.listen(Engine, "before_cursor_execute", approve_first)
    try:
        response = committing_client.post(
            commercial_terms_path(state["project_id"], state["draft_id"]),
            json=OUTCOME,
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", approve_first)

    assert fired, "the approval never landed inside the window — nothing here is about the race"
    assert "insert into outcome_terms" in fired[0].lower(), (
        "the rule and its outcome details are not one statement — the guard may miss the second"
    )
    assert response.status_code == 409, response.text
    assert "approved" in response.json()["detail"]
    assert _rows_of(engine, state["draft_id"]) == (0, 0)


def test_k_05_an_approval_racing_the_outcome_rule_write_on_two_connections_leaves_no_rule_under_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, trzeci przebieg — dwa połączenia, prawdziwy endpoint zatwierdzenia, warunek parą.

    Zatwierdzenie zatrzymane tuż przed zmianą statusu (po migawce, z blokadą wiersza scenariusza);
    wtedy startuje zapis reguły outcome w wątku, a test czeka, aż PostgreSQL sam zgłosi oczekującą
    prośbę o blokadę. Zapis musi czekać, a po zatwierdzeniu dostać `409` i zostawić zero wierszy.
    """
    state = _committed_project(engine)
    outcome: dict[str, Any] = {}
    fired: list[str] = []

    def start_the_writer(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if fired or "update scenarios set status" not in statement.lower():
            return
        fired.append(statement)

        def writer() -> None:
            try:
                outcome["response"] = committing_client.post(
                    commercial_terms_path(state["project_id"], state["draft_id"]),
                    json=OUTCOME,
                    headers=as_caller(IN_SCOPE_USER),
                )
            except BaseException as error:  # noqa: BLE001 — reported, never swallowed
                outcome["error"] = error

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        outcome["thread"] = thread
        outcome["blocked"] = wait_until_a_lock_request_is_pending(engine)

    event.listen(Engine, "before_cursor_execute", start_the_writer)
    try:
        approval = committing_client.post(
            approve_path(state["project_id"], state["draft_id"]),
            headers=as_caller(IN_SCOPE_USER),
        )
    finally:
        event.remove(Engine, "before_cursor_execute", start_the_writer)

    assert fired, "the approval never reached its status update"
    thread = outcome["thread"]
    thread.join(timeout=30)
    assert not thread.is_alive(), "the rule write never finished — it is still holding a lock"
    assert "error" not in outcome, outcome.get("error")

    assert approval.status_code == 200, approval.text
    assert outcome["blocked"], (
        "the outcome rule write never waited for a lock — nothing serialises it against the "
        "approval, and this run says nothing about the race"
    )
    approved = _is_approved(engine, state["draft_id"])
    rules, details = _rows_of(engine, state["draft_id"])
    assert not (approved and (rules or details)), (
        f"approved={approved} with {rules} rule(s) and {details} outcome row(s)"
    )
    assert approved, "the approval itself failed — the pair above is satisfied vacuously"
    assert outcome["response"].status_code == 409, outcome["response"].text
    assert "approved" in outcome["response"].json()["detail"]


def test_k_05_contrast_an_outcome_rule_written_before_the_approval_is_approved_with_it(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-05, kontrast wyścigu — zapis zatwierdzony pierwszy należy do zatwierdzanej kalkulacji: bez
    tego wyścig wyżej spełniłby strażnik odrzucający każdy zapis niezależnie od kolejności. Po
    zatwierdzeniu reguła zostaje, a jej przychód jest ten sam (brak migawki — ADR-0004, aneks
    SC-4-03, pkt 4)."""
    state = _committed_project(engine)
    path = commercial_terms_path(state["project_id"], state["draft_id"])

    written = committing_client.post(path, json=OUTCOME, headers=as_caller(IN_SCOPE_USER))
    assert written.status_code == 201, written.text
    approval = committing_client.post(
        approve_path(state["project_id"], state["draft_id"]), headers=as_caller(IN_SCOPE_USER)
    )

    assert approval.status_code == 200, approval.text
    assert _rows_of(engine, state["draft_id"]) == (1, 1)
    read = committing_client.get(path, headers=as_caller(IN_SCOPE_USER)).json()
    assert read["scenario_status"] == "Approved"
    assert read["revenue"] == written.json()["revenue"]
