# Verified capabilities registry

Answers exactly one question: **what does the system actually do, and what proves it** — kept
separate from `decisions/`, because an accepted architectural decision means only an approved
direction, not working code (see `FrameworkDoc.md`, section 7). Rows are added only in a
documentation commit made by the human (gate 3) — the Developer and QA roles **propose** rows in
their reports, ready to paste; they do not write here directly.

Evidence column uses exactly one of four values:

- **mutation-checked test** — a test exists, and a mutation was run against the mechanism it
  guards, and recorded in the mutation log below.
- **test, no mutation** — a test exists and is green, but no mutation has been run against it yet.
  This is weaker proof than it looks — see `FrameworkDoc.md`, section 6.
- **manual verification, `<date>`** — checked by hand once; will go stale silently if nothing
  re-checks it.
- **no evidence** — documented and possibly implemented, but nothing here proves it works. This is
  the honest default for a new capability row, not a flaw in the registry.

## Capabilities

| Capability | Requirement ref | Evidence | Reference |
|---|---|---|---|
| Project list restricted to the caller's `project_access` scope — out-of-scope project absent, not marked unavailable | F-13, NF-04, ADR-0001 aneks, ADR-0005 | mutation-checked test | `backend/tests/test_project_list.py::test_project_list_omits_projects_outside_caller_access` |
| A caller with no `project_access` rows gets an empty list, not an error | F-13 | mutation-checked test | `backend/tests/test_project_list.py::test_caller_with_no_project_access_rows_gets_an_empty_list_not_an_error` |
| A project holds ≥2 independent scenarios, proven at the data layer | F-01, F-02 | mutation-checked test | `backend/tests/test_project_list.py::test_sc_1_05_02_project_supports_two_independent_scenarios` |
| A scenario reports its own missing inputs and is never presented as ready when incomplete, regardless of status | F-01 | mutation-checked test | `backend/tests/test_project_list.py::test_sc_1_05_03_drafts_report_their_own_missing_inputs_and_are_not_ready`, `::test_sc_1_05_03_approved_scenario_with_a_gap_is_still_reported_as_not_ready` |
| Archived projects remain on the default list, marked `Archived` | F-01 (gate-1 decyzja 8) | mutation-checked test | `backend/tests/test_project_list.py::test_sc_1_05_04_archived_project_stays_on_the_default_list_marked_archived` |
| `GET /projects` denies by default (brak tożsamości → 401, brak uprawnienia → 403) | F-13, NF-04, ADR-0005 | mutation-checked test | `backend/tests/test_access_control.py` |
| Placeholder tożsamości odmawia startu bez jawnego opt-in poza dev/test | ADR-0005 aneks | mutation-checked test | `backend/tests/test_access_control.py::test_placeholder_identity_refuses_to_run_without_an_explicit_opt_in`, `::test_application_import_fails_when_nothing_is_configured_at_all` |
| Decimal przekracza granicę API jako fixed-point string, nigdy jako JSON float | NF-01, ADR-0002 | mutation-checked test | `backend/tests/test_project_list.py::test_decimals_cross_the_api_boundary_as_fixed_point_strings` |
| Ekran listy renderuje dokładnie odpowiedź API, łącznie z zarchiwizowanymi, bez filtrowania po stronie klienta | F-01, NF-04, ADR-0005 | mutation-checked test | `frontend/src/features/projects/ProjectListScreen.test.tsx` |
| Procent przekraczający granicę jako string dziesiętny jest zaokrąglany ROUND_HALF_UP na stringu, zgodnie z regułą backendu | ADR-0002, NF-01 | mutation-checked test | `frontend/src/lib/money.test.ts` |
| Odczyt bez odpowiedzi (nagłówki lub ciało) w 12s kończy się jawnym błędem, nie wiecznym stanem ładowania | NF-08 | mutation-checked test | `frontend/src/features/projects/ProjectListScreen.test.tsx` |
| Kontrolki akcji wiersza (View/Edit/Copy/Archive/Add scenario) widoczne i dostępne z klawiatury mimo braku implementacji, nie wywołują żadnego żądania | F-01 | test, no mutation — martwy kod (`preventDefault` na przycisku bez formularza) usunięty, bo nie do udowodnienia testem | `frontend/src/features/projects/ProjectListScreen.test.tsx` |
| Uwierzytelnianie wołającego | NF-04 | no evidence | placeholder header, ADR-0005 aneks 2026-09-18; zamknięcie: osobny ADR uwierzytelniania |
| Usunięcie uprawnienia (`project_access`) działa natychmiast, bez cache po stronie serwera | ADR-0005 | no evidence | zweryfikowane przez Invariant Guardian przez czytanie kodu (rebuild identity per request, brak cache), brak dedykowanego testu — luka odnotowana w raporcie QA |
| Bramka pól kosztów osobowych (`can_view_personnel_costs`) | F-13, AC-06 | no evidence | seam istnieje (`response_shaping.py`), pole i tak puste — żadne pole kosztowe jeszcze nie istnieje; known gap (Reviewer R-03): kształt per-caller zamiast per-project-assignment |

## Mutation log

| Date | Task | Removed mechanism | Result |
|---|---|---|---|
| 2026-09-18 | SC-1-05 | `project_access` join usunięty z `accessible_projects()` | Killed — 3 testy. Projekt spoza zasięgu pojawia się na liście. |
| 2026-09-18 | SC-1-05 | `list_projects_for_caller()` zwraca zawsze pustą listę | Killed — 5 testów, na PRZECIWNEJ asercji niż wyżej (brak własnego projektu). |
| 2026-09-18 | SC-1-05 | Wyliczanie scenariuszy aliasuje pierwszy scenariusz na wszystkie wiersze | Killed — 2 testy. Dwa scenariusze dowiedzione jako niezależne wiersze, nie powtórzony jeden. |
| 2026-09-18 | SC-1-05 | `missing_inputs()` zwraca stałą listę | Killed — 2 testy, na DRUGIM draft. Lista braków liczona per wiersz, nie raz dla projektu. |
| 2026-09-18 | SC-1-05 | `.where(status == ACTIVE)` dodane do zapytania listy | Killed — 1 test. Zarchiwizowany projekt w zasięgu zostaje na liście, oznaczony, zamiast zniknąć. |
| 2026-09-18 | SC-1-05 | `DecimalString` zamieniony na zwykły `Decimal` | SURVIVED pierwszy raz — asercja `"18.250"` spełniona przez domyślne zachowanie Pydantic, nie przez serializer projektu. Test zastąpiony sprawdzeniem na poziomie schematu (`1E+2` → `"100"`); po poprawce mutacja killed. |
| 2026-09-18 | SC-1-05 | `assess()` zachowuje `or status is APPROVED` (usunięte w rundzie 2) | Killed — nowy test na approved+braki. Status już nie zwalnia z kompletności. |
| 2026-09-18 | SC-1-05 | `allow_placeholder_identity` domyślnie `True` (poprawka rundy 3 na R-01) | Killed — 2 testy, w tym subprocess startowany z pustym środowiskiem. |
| 2026-09-18 | SC-1-06 | Filtr po stronie klienta `status !== "Archived"` dodany przed renderem | Killed — 4 testy, w tym kryterium 1. Ekran nie podejmuje własnej decyzji o widoczności (NF-04). |
| 2026-09-18 | SC-1-06 | Etykiety brakujących pól zamienione na stałą listę | Killed — 1 test. Dwa drafty z różnymi brakami renderują różny tekst. |
| 2026-09-18 | SC-1-06 | `aria-disabled` zamieniony na `disabled` na kontrolkach wiersza | Killed — 1 test, na focusability. |
| 2026-09-18 | SC-1-06 | `formatPercentString()` ominięty na rzecz `String(value)` | Killed — 1 test. Procenty renderowane przez wspólny formatter. |
| 2026-09-18 | SC-1-06 | `onClick preventDefault` usunięty z kontrolek wiersza (samo, `aria-disabled` zostaje) | SURVIVED — mutacja równoważna. `type="button"` bez formularza czyni `preventDefault` no-opem; bezczynność kontrolki nie jest mechanizmem i nie da się jej udowodnić testem (Reviewer R-06). Kod usunięty jako martwy, nie naprawiony. |
| 2026-09-18 | SC-1-06 | Gałąź pustych scenariuszy usunięta z `ScenarioDetails` | Killed — 1 test (dopisany przez QA). |
| 2026-09-18 | SC-1-06 | `handle(response)` przeniesiony poza wyścig z timerem (deadline tylko na fazę nagłówków, poprawka rundy 3 na R-02) | Killed — 1 test, po naprawie testu który za pierwszym razem był pusty (zatrzymywał zegar zanim ciało zaczęło się czytać). |
