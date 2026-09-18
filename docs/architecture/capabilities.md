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
| Utworzony projekt jest odczytywalny z kompletem pól F-01 i utrwalony — sprawdzone z osobnego połączenia DB | F-01 | mutation-checked test | `backend/tests/test_project_create_read.py::test_sc_1_01_01_created_project_is_retrievable_with_all_its_fields`, `::test_sc_1_01_01_created_project_is_committed_and_readable_from_another_connection` |
| Projekt spoza zasięgu wołającego jest nieodróżnialny od nieistniejącego — ten sam status, ciało, długość; nigdy 403 | F-13, NF-04, ADR-0005 | mutation-checked test | `backend/tests/test_project_create_read.py::test_sc_1_01_02_out_of_scope_project_is_indistinguishable_from_one_that_does_not_exist`, `::test_sc_1_01_02_unknown_project_id_is_not_found` |
| Utworzenie projektu nadaje dostęp wyłącznie twórcy (z tożsamości żądania), nigdy podmiotowi nazwanemu w ciele | F-13, ADR-0005 | mutation-checked test | `backend/tests/test_project_create_read.py::test_sc_1_01_creating_a_project_grants_access_to_its_creator_and_to_nobody_else`, `::test_project_create_refuses_a_body_that_tries_to_name_the_access_holder` |
| `POST /projects` wymaga osobnego uprawnienia `PROJECT_CREATE` — odmowa nie zapisuje żadnego wiersza | F-13, ADR-0005 | mutation-checked test | `backend/tests/test_access_control.py::test_project_create_denies_caller_holding_only_project_read`, `::test_project_create_denies_caller_without_identity` |
| Obowiązkowe pola F-01 (name/client/owner) nie mogą być puste ani samo-białe — odrzucone na granicy API, zero zapisu | F-01 | mutation-checked test | `backend/tests/test_project_create_required_fields.py` |
| Baza odrzuca pusty/whitespace name/client/owner niezależnie od walidacji API (CHECK constraint) | F-01, ADR-0001 | mutation-checked test | `backend/tests/test_project_schema_constraints.py` (migracja `4f0a9c1b7d62`) |
| Nieznany klucz przemycony w zagnieżdżonym `delivery_period` jest odrzucany, nie po cichu pomijany | F-13, NF-04 | mutation-checked test | `backend/tests/test_project_create_read.py::test_project_create_refuses_an_unknown_key_smuggled_into_the_nested_delivery_period` |
| Nieudany zapis projektu nie wynosi wartości pól do logu — ani echo parametrów SQLAlchemy, ani `DETAIL: Failing row contains` z PostgreSQL | NF-11, ADR-0001 | mutation-checked test | `backend/tests/test_statement_errors_hide_parameters.py` |
| Zestaw uprawnień placeholdera jest zamknięty i sprawdzany przez równość zbiorów, nie samo `in` | ADR-0005 aneks | mutation-checked test | `backend/tests/test_access_control.py::test_personnel_cost_permission_is_not_granted_by_the_placeholder_identity` |
| Bramka pól kosztów osobowych (`can_view_personnel_costs`) jest konsultowana na obu ścieżkach kształtowania (lista i detail) | F-13, AC-06 | mutation-checked test, z zastrzeżeniem | `backend/tests/test_project_detail_personnel_costs.py` — dowodzi że bramka jest wywoływana (pole zastępcze podstawione w teście); SAME POLA KOSZTOWE NADAL NIE ISTNIEJĄ w schemacie. Known gap (Reviewer R-03): kształt per-caller zamiast per-project-assignment, pozostaje otwarty |

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
| 2026-09-18 | SC-1-01 | `project_for_caller`: `accessible_projects(caller)` → `select(Project)` | Killed — 2 testy. Projekt spoza zasięgu odpowiada 200 zamiast 404. |
| 2026-09-18 | SC-1-01 | `read_project` z fallbackiem bez filtra: 403 gdy wiersz istnieje | Killed — 2 testy. Nie ma gałęzi "istnieje, ale nie twój" — 403 potwierdzałby istnienie projektu. |
| 2026-09-18 | SC-1-01 | `read_project` z fallbackiem: nadal 404, ale inna treść `detail` dla wiersza spoza zasięgu | Killed — 1 test, na porównaniu ciała i `content-length` obu odmów, nie na statusie. |
| 2026-09-18 | SC-1-01 | Usunięty insert `ProjectAccess` przy tworzeniu projektu | Killed — 7 testów. Projekt bez grantu nieodróżnialny od nigdy niezapisanego. |
| 2026-09-18 | SC-1-01 | Grant `project_access` dla stałego user id zamiast `caller.user_id` | Killed — 7 testów, na przeciwnej asercji (twórca traci dostęp). |
| 2026-09-18 | SC-1-01 | Grant dla twórcy ORAZ dodatkowo dla drugiego, stałego użytkownika | Killed — 2 testy. "I dla nikogo więcej" dowiedzione osobno od "dla twórcy". |
| 2026-09-18 | SC-1-01 | `accessible_projects()` zachowuje join, traci predykat `user_id == caller.user_id` | Killed — 5 testów (3 z SC-1-05). Degradacja filtra też umiera. |
| 2026-09-18 | SC-1-01 | `session.commit()` → `session.flush()` w `create_project()` | Killed — dokładnie 1 test (`…_committed_and_readable_from_another_connection`), 87 pozostałych przeżywa. Trwałość dowodzi wyłącznie odczyt z osobnego połączenia. |
| 2026-09-18 | SC-1-01 | `require_permission(PROJECT_CREATE)` → `PROJECT_READ` na `POST /projects` | Killed — 1 test. Test odmowy nadpisuje tożsamość na wyłącznie `PROJECT_READ` (nie pełny placeholder), więc nie jest pusty. |
| 2026-09-18 | SC-1-01 | `ConfigDict(extra="forbid")` → `"ignore"` w `ProjectCreateRequest` | Killed — 1 test. Ciało nazywające beneficjenta grantu jest odrzucane. |
| 2026-09-18 | SC-1-01 | `get_caller_identity()` cache'uje tożsamość w module zamiast liczyć per request | Killed — 3 testy. |
| 2026-09-18 | SC-1-01 | Walidator `_delivery_period_is_ordered` usunięty (zaufanie do check constraintu w bazie) | Killed — 1 test. Baza broni integralności, granica API broni kodu odpowiedzi (422 nie 500). |
| 2026-09-18 | SC-1-01 | `_without_personnel_costs` pominięte w `shape_project_detail` i `_shape_project` | SURVIVED oba razy pierwotnie — `PERSONNEL_COST_FIELDS` puste, bramka bezwładna. QA dopisał testy z polem zastępczym (monkeypatch); po poprawce killed. Kod nietknięty — luka była w dowodzie. |
| 2026-09-18 | SC-1-01 | `NonEmptyName.min_length`: `1` → `0` | SURVIVED pierwotnie — baza nie miała check constraintu na niepustość, schemat był jedynym mechanizmem. QA dopisał 12 testów; killed. Naprawione też w kodzie (runda 2: migracja `4f0a9c1b7d62`). |
| 2026-09-18 | SC-1-01 | `description` traci domyślne `""` (staje się polem wymaganym) | SURVIVED — żaden test nie wysyłał body bez `description`. QA dopisał test; killed. |
| 2026-09-18 | SC-1-01 | `hide_parameters=True` usunięte z `_build_engine()` (runda 2) | Killed — 1 test. |
| 2026-09-18 | SC-1-01 | `raise ProjectWriteFailed(...) from None` → gołe `raise` (runda 2) | Killed — 2 testy. Surowy komunikat psycopg z `Failing row contains` wraca do traceback. |
| 2026-09-18 | SC-1-01 | `model_config = ConfigDict(extra="forbid")` usunięte z `DeliveryPeriod` (runda 2) | Killed — 1 test. |
| 2026-09-18 | SC-1-01 | `_NOT_BLANK_COLUMNS = ()` w migracji `4f0a9c1b7d62` (runda 2) | Killed — 15 testów. |
