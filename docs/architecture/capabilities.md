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
| Kolor, typografia i odstępy ekranu listy projektów pochodzą z jednego źródła tokenów — literał koloru, nieznany token, nazwany kolor CSS albo inline style poza `tokens.css` wywraca zestaw testów | Wymagania/UI (paleta marki), PR-template invariant "New color — through a theme token" | mutation-checked test | `frontend/src/styles/tokens.test.ts` |
| Pasek listy (Search/Filters/Add project) widoczny i osiągalny z klawiatury mimo braku implementacji; nie filtruje listy po stronie klienta i nie wywołuje żadnego żądania | F-13, Issue #3 out of scope 2 | mutation-checked test | `frontend/src/features/projects/ProjectListScreen.test.tsx::shows search, filters and add-project as reachable controls that are wired to nothing` |
| Kolory tekstu i tła zadeklarowane w arkuszach ekranu listy spełniają WCAG AA (4.5:1) — współczynniki liczone z wartości tokenów przez własny parser testowy, nie opisane w komentarzu | Reviewer R-01 | mutation-checked test | `frontend/src/styles/tokens.test.ts::colour contrast` |
| Zaznaczony wiersz listy jest obrysowany z czterech stron — reguła stanu wygrywa w kaskadzie CSS, nie tylko istnieje w arkuszu | Reviewer R-04 | mutation-checked test | `frontend/src/styles/tokens.test.ts::outlines the selected row on all four sides, not only where the cascade happens to allow` |
| Nazwa projektu/klienta/scenariusza (do 200 znaków, bez wymogu spacji) dociera do DOM w całości — skracanie należy wyłącznie do CSS, nie do komponentu | Reviewer R-02 | mutation-checked test | `frontend/src/features/projects/ProjectListScreen.test.tsx::renders a long name with no break opportunities whole, leaving the breaking to the stylesheet` |
| Kontrolka niezaimplementowana (`aria-disabled`) wygląda tak samo niezależnie od wariantu przycisku — brak koloru marki pod opacity | F-13, Reviewer R-05 | mutation-checked test | `frontend/src/styles/tokens.test.ts::gives every not-yet-implemented control the same quietened appearance` |
| Edycja Projektu (`PATCH /projects/{id}`): strażnik współbieżności ADR-0007 liczony przez bazę w tej samej instrukcji `UPDATE`, nie porównaniem w Pythonie na wierszu przed chwilą odczytanym | ADR-0007, NF-05 | mutation-checked test | `backend/tests/test_project_edit.py`, `backend/tests/test_project_write_actions_guards.py::test_the_concurrency_guard_is_evaluated_by_the_database_not_against_the_row_just_read` |
| Zamrożenie pól grupy 2 (ADR-0004) sprawdzane w tej samej instrukcji `UPDATE` co zapis, nie check-then-act — zatwierdzenie scenariusza między sprawdzeniem a zapisem nie przecieka | ADR-0004 aneks | mutation-checked test | `backend/tests/test_project_edit.py`, `backend/tests/test_project_write_actions_guards.py::test_the_frozen_field_guard_is_evaluated_in_the_same_statement_as_the_write` |
| Edycja projektu spoza zasięgu wołającego → `404`, nieodróżnialne od nieistniejącego, z precedencją 404 nad 409 | F-13, NF-04, ADR-0005, ADR-0007 | mutation-checked test | `backend/tests/test_project_edit.py` |
| `PATCH /projects/{id}` wymaga osobnego uprawnienia `PROJECT_EDIT` — odmowa nie zapisuje żadnej zmiany | ADR-0005 aneks | mutation-checked test | `backend/tests/test_project_edit.py`, `backend/tests/test_access_control.py` |
| Kopiowanie Projektu (`POST /projects/{id}/copy`): deep-copy wszystkich scenariuszy źródła jako `draft`, niezależnie od statusu źródła, bez współdzielonej mutowalnej referencji na poziomie wiersza scenariusza | ADR-0004 aneks, invariant-guardian reguła 17 | mutation-checked test | `backend/tests/test_project_copy.py` |
| Kaskada kopiowania (`SCENARIO_CHILD_COPIERS`) jest żywym szwem, nie deklaracją — uruchamia się raz per skopiowany scenariusz, po jego zapisie | ADR-0004 aneks | mutation-checked test | `backend/tests/test_project_copy.py` |
| `project_access` po kopiowaniu istnieje wyłącznie dla kopiującego — grant źródła nie jest replikowany | ADR-0005 aneks | mutation-checked test | `backend/tests/test_project_copy.py` |
| Kopiowanie projektu spoza zasięgu wołającego → `404`; `POST /projects/{id}/copy` wymaga `PROJECT_COPY` | F-13, NF-04, ADR-0005 | mutation-checked test | `backend/tests/test_project_copy.py`, `backend/tests/test_access_control.py` |
| Archiwizacja Projektu (`POST /projects/{id}/archive`): zmiana `status`, zero instrukcji zapisu wobec tabeli `scenarios` — dowód na poziomie SQL, nie tylko porównania wiersza | ADR-0004 aneks | mutation-checked test | `backend/tests/test_project_archive.py`, `backend/tests/test_project_write_actions_guards.py::test_archiving_issues_no_write_statement_against_the_scenarios_table` |
| Powtórna archiwizacja tego samego projektu nie zapisuje niczego (jednokierunkowa, idempotentna) | ADR-0004 aneks (p. 4) | mutation-checked test | `backend/tests/test_project_write_actions_guards.py::test_archiving_an_already_archived_project_writes_nothing_at_all` |
| Archiwizacja projektu spoza zasięgu wołającego → `404`; `POST /projects/{id}/archive` wymaga `PROJECT_ARCHIVE` | F-13, NF-04, ADR-0005 | mutation-checked test | `backend/tests/test_project_archive.py`, `backend/tests/test_access_control.py` |
| `PATCH /projects/{id}` jest osiągalny z przeglądarki (przeglądarka blokuje metody niesimple bez poprawnego CORS preflight) | SC-1-02, F-13 | mutation-checked test | `backend/tests/test_project_write_actions_guards.py::test_cors_preflight_allows_the_patch_method_the_edit_endpoint_needs` |
| Kolejność refuzji `update_project` gdy nieaktualny token i zatwierdzony scenariusz zachodzą naraz (`_diagnose_refusal`) | ADR-0007, ADR-0004 | no evidence | mutacja nieuruchomiona (QA, 2026-09-19) — brak dostarczonego testu na tę kombinację |
| `EDITABLE_FIELDS` jako zamknięta lista dopuszczalnych pól, nie otwarta na dowolną kolumnę | ADR-0004 aneks | no evidence | mutacja nieuruchomiona (QA, 2026-09-19) — istniejący test pokrywa tylko `status`, `id` i pustą mapę zmian |

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
| 2026-09-18 | SC-1-07 | `test.css: true` usunięte z `vite.config.ts` | Killed — 3 testy (całe `tokens.test.ts`). Bez przetworzonego CSS asercje-strażnicy (`>10` literałów, `>20` tokenów, obecność `--sc-font-family`) nie dają się spełnić przez pustkę — pułapka pustego dowodu zamknięta. |
| 2026-09-18 | SC-1-07 | `#ff5500` + `font-family: Arial` + `text-transform: uppercase` wstawione naraz do `ProjectListScreen.css` | Killed — 2 z 3 defektów zaraportowane naraz (asercja `uppercase` kończy test przed sprawdzeniem fontu); po rozbiciu na osobne mutacje wszystkie trzy killed pojedynczo. |
| 2026-09-18 | SC-1-07 | `var(--sc-color-text-muted)` → literówka `var(--sc-color-text-mutedd)` w `app.css` | Killed — 1 test, z nazwą pliku w komunikacie. Żaden test DOM-owy tego nie widzi (nierozpoznany token renderuje się jako przezroczysty). |
| 2026-09-18 | SC-1-07 | `family=Manrope` → `family=Inter` w `index.html` | Killed — 1 test. |
| 2026-09-18 | SC-1-07 | `data-project-status` → `data-mutant` na odznace statusu | Killed — 1 test. |
| 2026-09-18 | SC-1-07 | `data-project-status={project.status}` → stała `"Active"` | Killed — 1 test, na wierszu Archived. Dwa statusy nie mogą zlać się w jeden wygląd (NF-08). |
| 2026-09-18 | SC-1-07 | `<ListToolbar />` wyniesiony poza guard `state.kind === "ready"` | Killed — 2 testy, w tym jeden sprzed restyle'u (403 → zero przycisków). |
| 2026-09-18 | SC-1-07 | Guard zawężony do `!== "loading" && !== "denied"` — toolbar wycieka na ekran timeoutu/błędu serwera | SURVIVED pierwszy raz — nieobecność toolbara dowiedziona tylko na jednej ścieżce. QA rozszerzył kontrast na 500/timeout; killed — 2 testy. |
| 2026-09-18 | SC-1-07 | Guard zawężony do `projects.length > 0` — brak toolbara nad pustą ale dozwoloną listą | SURVIVED pierwszy raz — brakowało dodatniej strony kontrastu. QA dopisał asercję; killed — 1 test. |
| 2026-09-18 | SC-1-07 | `toFailureState` fallback `{kind:"failed"}` → `{kind:"denied"}` | SURVIVED — każdy błąd 500 mówił "brak uprawnień" zamiast błędu ogólnego; gałąź `failed` nie miała testu. QA dopisał test na 500; killed — 1 test. Kod produkcyjny był poprawny — luka była wyłącznie w pokryciu testami. |
| 2026-09-18 | SC-1-07 | `readOnly` usunięte z pola wyszukiwania | SURVIVED — nieszkodliwość pisania wynika z braku handlera/stanu (dowiedzione niezależnie), nie z atrybutu. Atrybut to afordancja, nie mechanizm; przypięty osobną asercją, killed — 1 test. |
| 2026-09-18 | SC-1-07 | Biała etykieta przywrócona na `.button--primary` (pomarańcz `#FF5500`) | Killed — 1 test kontrastu WCAG (3.21:1 < wymagane 4.5:1). |
| 2026-09-18 | SC-1-07 | Reguły bocznych krawędzi zaznaczonego wiersza usunięte | Killed — 1 test; komunikat wskazuje dokładnie regułę wygrywającą specyficznością (reprodukuje finding Reviewera). |
| 2026-09-18 | SC-1-07 | `position: sticky` usunięte z panelu szczegółów | Killed — 1 test. |
| 2026-09-18 | SC-1-07 | `overflow-wrap: anywhere` usunięte z komórek tabeli | Killed — 1 test. |
| 2026-09-18 | SC-1-07 | `opacity: 0.65` + osobna reguła per-wariant przywrócone dla `aria-disabled` | Killed — 1 test. |
| 2026-09-18 | SC-1-07 | `color: white` (nazwany kolor CSS) zamiast tokenu w arkuszu funkcji | Killed — 1 test. |
| 2026-09-18 | SC-1-07 | `style={{ color: "#333" }}` (inline style) w `App.tsx` | Killed — 1 test. |
| 2026-09-18 | SC-1-07 | `{project.name.slice(0, 40)}` wstawione w komponencie | Killed — 1 test. Skracanie należy do CSS, nie do komponentu. |
| 2026-09-19 | SC-1-02 | `Project.updated_at == expected_updated_at` usunięte z `WHERE` w `update_project` | Killed — 2 testy. Przegrywający wyścig nadpisuje cudzy zapis bez ostrzeżenia. |
| 2026-09-19 | SC-1-02 | Strażnik współbieżności przeniesiony z `UPDATE` do porównania w Pythonie (`project.updated_at != expected`) | SURVIVED — cały zestaw zielony, łącznie z testem o nazwie `..._is_the_update_statement_not_a_python_comparison`: token, którego nikt nie wydał, nie zgadza się w obu implementacjach. Killed po dopisaniu testu z commitem konkurenta w okno check-then-act (`test_project_write_actions_guards.py`). |
| 2026-09-19 | SC-1-02 | `sa.not_(_approved_scenario_exists(...))` usunięte z `WHERE` | Killed — 2 testy (warstwa danych + 409 w API). |
| 2026-09-19 | SC-1-02 | Zamrożenie pól grupy 2 sprawdzane w Pythonie przed `UPDATE` (check-then-act) | SURVIVED — żaden dostarczony test nie umieszcza zatwierdzenia scenariusza w oknie między sprawdzeniem a zapisem. Killed po dopisaniu testu wyścigu. |
| 2026-09-19 | SC-1-02 | `project_for_caller` → `session.get(Project, ...)` w `update_project` | Killed — 2 testy. Projekt spoza zasięgu staje się edytowalny. |
| 2026-09-19 | SC-1-02 | Token sprawdzany na nieprzefiltrowanym odczycie przed ustaleniem zasięgu (409 przed 404) | Killed — 2 testy, na asercji `denied_with_stale_token`. Precedencja 404 nad 409 z ADR-0007 jest naprawdę pokryta. |
| 2026-09-19 | SC-1-03 | Grant do kopii replikowany z `project_access` źródła zamiast `caller.user_id` | Killed — 1 test (kontrast z kolegą z zespołu). Test persystencji tego nie widzi: jego źródło ma jednego uprawnionego. |
| 2026-09-19 | SC-1-03 | Pętla kopiująca scenariusze pominięta | Killed — 3 testy. |
| 2026-09-19 | SC-1-03 | Pętla `SCENARIO_CHILD_COPIERS` usunięta (dziś no-op w produkcji — rejestr jest pusty) | Killed — 1 test. Szew kaskady jest żywy, nie deklaratywny. |
| 2026-09-19 | SC-1-03 | `copy_scenario`: `status=DRAFT` → `status=source.status` | Killed — 2 testy. |
| 2026-09-19 | SC-1-03 | Kopia dziedziczy `status` projektu źródłowego | Killed — 1 test (źródło Archived → kopia Archived). |
| 2026-09-19 | SC-1-03 | Źródło w endpointcie kopii ustalane przez `session.get(Project, ...)` | Killed — 1 test, na porównaniu ciał obu odmów. |
| 2026-09-19 | SC-1-04 | Kaskada: każdy scenariusz projektu ustawiany na `approved` | Killed — 1 test. |
| 2026-09-19 | SC-1-04 | `UPDATE scenarios SET name = name` (zapis nie zmieniający wartości) | SURVIVED — wewnątrz jednej transakcji `now()` to czas jej początku, więc `onupdate` wstawia tę samą wartość i porównanie wiersza przed/po jest na to niewidome. Killed po dopisaniu testu na poziomie instrukcji SQL. |
| 2026-09-19 | SC-1-04 | Przejście jednokierunkowe zamienione na przełącznik | Killed — 1 test. |
| 2026-09-19 | SC-1-04 | `status` zapisywany zawsze (Core `UPDATE`), więc powtórna archiwizacja przepisuje wiersz | SURVIVED — „idempotentne" było dowiedzione tylko jako 200 + `Archived` w ciele. Powtórny zapis rusza `projects.updated_at`, czyli token ADR-0007 każdego edytującego. Killed po dopisaniu testu na poziomie instrukcji SQL. |
| 2026-09-19 | SC-1-04 | `project_for_caller` → `session.get(Project, ...)` w `archive_project` | Killed — 1 test. |
| 2026-09-19 | SC-1-02..04 | `PLACEHOLDER_PERMISSIONS` rozszerzone o `PERSONNEL_COSTS_READ` | Killed — 3 testy. Kanarek równości zbiorów (ADR-0005) działa po przezbrojeniu na 5 uprawnień. |
| 2026-09-19 | SC-1-02 | `allow_methods` bez `PATCH` w CORS (stan gałęzi w commicie) | SURVIVED — w całym zestawie nie było żadnego testu CORS. Killed po dopisaniu testu preflight. Defekt produkcyjny, naprawiony w `backend/app/main.py`. |
