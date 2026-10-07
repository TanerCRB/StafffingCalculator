# Task plan register

One row per task, one task identifier per row: `SC-<block>-<seq>`. A task is checked off only with
a `**Done <date>:**` line pointing at a specific test or artifact — never on the strength of code
existing (see `FrameworkDoc.md`, section 7). Checking off a row is gate 3 and is done by the human,
never by an agent.

Process rollout tasks (adapting `agents/`, `process/`, this bootstrap) are **not** listed here —
per `FrameworkDoc.md`, section 3, they have no Issue and live only in the repository's commit
history / this file's own change log, not as tracked product work.

## Blocks

| Block | Area | Requirements |
|---|---|---|
| 1 | Projects, scenarios, access control | F-01, F-02, F-13 |
| 2 | Roles, resources, rates | F-03 |
| 3 | Staffing planning, calendars, absences | F-04, F-05 |
| 4 | Commercial models & revenue | F-06 |
| 5 | Personnel & additional costs | F-07, F-08 |
| 6 | Scenario comparison & sensitivity | F-09 |
| 7 | Results, metrics, visualization, export | F-10, F-11 |
| 8 | History, reproducibility, versioning | F-12 |

## Tasks

- [x] **SC-1-01** — Persist a Project: create/read a Project with name, client, owner, delivery
  period, reporting currency, description; server-side access restricted to the project's
  assigned users.
  *Done when:* `backend/tests` prove: (1) a created Project is retrievable with the fields above,
  and (2) a user without access to a Project gets a response indistinguishable from
  "does not exist" — not a 403 that confirms the Project's existence.
  **Out of scope (explicit):** editing, archiving, copying (F-01) — separate tasks. Scenario
  creation (needs the Project to exist first).
  **Done 2026-09-18:** `backend/tests/test_project_create_read.py::test_sc_1_01_01_created_project_is_retrievable_with_all_its_fields`
  + `::test_sc_1_01_01_created_project_is_committed_and_readable_from_another_connection` (kryt. 1),
  `::test_sc_1_01_02_out_of_scope_project_is_indistinguishable_from_one_that_does_not_exist`
  (kryt. 2, z kontrastem: ten sam id, wołający z dostępem → 200) + `::test_sc_1_01_02_unknown_project_id_is_not_found` —
  88 testów backendowych zielono na prawdziwym PostgreSQL (testcontainers); migracja
  `backend/migrations/versions/4f0a9c1b7d62_require_non_blank_project_name_client_owner.py`.
  PR #20.

- [x] **SC-1-05** — Zwróć listę projektów wołającego użytkownika (odczyt, bez akcji zapisu).
  *Done when:* `backend/tests` prove: (1) `test_project_list_omits_projects_outside_caller_access`
  passes — an in-scope project is present, an out-of-scope project is absent (not marked
  unavailable), both asserted in the same response; (2) a project supports 2 independent
  scenarios, proven at the data layer (fixture-level writes, no edit endpoint required); (3) a
  draft with missing inputs is returned with its own missing-field list and a not-ready flag,
  two drafts with different gaps produce different lists; (4) an already-archived project (state
  set by fixture, not by an archive action) still appears on the default list, marked Archived,
  while an out-of-scope project stays absent regardless of archive state.
  **Out of scope (explicit):** search/filter/pagination (separate Story once the list exceeds
  ~20 projects); Commercial Model column (data doesn't exist until plan block 4); the archive/
  edit/copy *actions* themselves (SC-1-02..04) — this task only reads state, never writes it.
  Real authentication — caller identity is a dated placeholder deviation recorded in
  `docs/architecture/decisions/ADR-0005-model-dostepu.md`, addendum 2026-09-18.
  **Done 2026-09-18:** `backend/tests/test_project_list.py::test_project_list_omits_projects_outside_caller_access`
  (kryt. 1), `::test_sc_1_05_02_project_supports_two_independent_scenarios` (kryt. 2),
  `::test_sc_1_05_03_drafts_report_their_own_missing_inputs_and_are_not_ready` +
  `::test_sc_1_05_03_approved_scenario_with_a_gap_is_still_reported_as_not_ready` (kryt. 3),
  `::test_sc_1_05_04_archived_project_stays_on_the_default_list_marked_archived` (kryt. 4) —
  30 testów backendowych zielono na prawdziwym PostgreSQL (testcontainers) po `alembic upgrade
  head`; migracja `backend/migrations/versions/09191236aba0_create_projects_scenarios_project_access.py`.
  PR #18.

- [x] **SC-1-06** — Pokaż PM-owi ekran listy projektów ze scenariuszami wybranego projektu (odczyt).
  Blocked by SC-1-05.
  *Done when:* `frontend/src` vitest tests pass under the names: `renders one row per project
  returned by the API, with name, client, delivery period and status`; `renders an empty-state
  message and no project rows when the API returns an empty list`; `exposes view, edit, copy,
  archive and add-scenario controls for every project row as named controls reachable by
  keyboard`; `renders a prompt instead of scenario details until a project is selected`; `lists
  the scenarios of the selected project with their status as text, not colour alone` (two
  projects with different scenario sets must render differently); `marks a draft scenario as not
  ready for approval and names its missing inputs` (two drafts with different gaps render
  differently).
  **Out of scope (explicit):** pixel fidelity to `Wymagania/UI/Project List.jpeg` (reference, not
  spec — no visual-regression tests); the screens the row controls point to (F-02, SC-1-02..04)
  — controls are rendered and reachable, not wired to any write; role-based control hiding
  (F-13); mobile/responsive layout (NF-09); the list taking any access/visibility decision of its
  own — screen content is a pure function of the API response (NF-04, ADR-0005).
  **Done 2026-09-18:** `frontend/src/features/projects/ProjectListScreen.test.tsx` +
  `frontend/src/lib/money.test.ts` — 25 testów frontendowych zielono, w tym wszystkich 6
  wymaganych nazw kryteriów, dwa projekty z różnymi zestawami scenariuszy, dwa drafty z różnymi
  brakami, zaokrąglanie ADR-0002 na stringu dziesiętnym ("1.005" → "1.01%"), timeout obejmujący
  fazę nagłówków i ciała odpowiedzi. PR #18.

- [x] **SC-1-07** — Wprowadź warstwę tokenów projektowych (kolor/typografia/odstępy) i zastosuj ją
  do ekranu listy projektów (SC-1-06), zgodnie z układem `Wymagania/UI/Project List.jpeg` i paletą
  marki wyciągniętą z `Wymagania/UI/globallogic_style_guide-v3.docx` (dokument to podręcznik do
  prezentacji PowerPoint, nie web-owy design system — wzięto z niego tylko font Manrope, hex
  kolorów i regułę "orange nigdy na tle Light Steel"; reszta decyzji wizualnych — spacing, stany
  hover/focus, layout tabeli — dobrana samodzielnie, spójnie).
  *Done when:* `frontend/src/styles/tokens.test.ts` dowodzi: jedno źródło kolorów (żaden literał
  poza `tokens.css`, żaden nazwany kolor CSS, żaden inline style), font Manrope faktycznie
  zamawiany, zakaz ALL CAPS, kontrast WCAG AA (4.5:1) dla wszystkich par tekst/tło zadeklarowanych
  w arkuszach; `ProjectListScreen.test.tsx` dowodzi że pasek Search/Filters/Add project jest
  widoczny i dostępny z klawiatury ale nieaktywny (bez filtrowania, bez wywołań API), że długie
  nazwy (do 200 znaków, bez wymogu spacji) docierają do DOM w całości.
  **Out of scope (explicit):** funkcjonalność wyszukiwania/filtrów/dodawania projektu (SC-1-05
  wyklucza search/filter; "Add project" mimo że `POST /projects` istnieje — osobne zadanie, nie
  mieszane ze zmianą wizualną); przyciski Edit/Delete w panelu scenariusza (poza zakresem F-01/
  ADR-0004, patrz Issue #3 out of scope 4-5); test regresji wizualnej (brak w tym repo); kolor
  marki na wyciszonych kontrolkach Filters/Add project — zostają wyciszone (`--sc-color-text-
  disabled`) tak samo jak kontrolki wiersza, decyzja świadoma, nie błąd.
  **Done 2026-09-18:** `frontend/src/styles/tokens.test.ts` + `frontend/src/features/projects/ProjectListScreen.test.tsx`
  — 40 testów frontendowych zielono, w tym mutation-checked kontrast WCAG, specyficzność CSS
  zaznaczonego wiersza, sticky panel, jednolite wyciszenie nieaktywnych kontrolek.

- [x] **SC-1-02** — Edytuj Projekt (name/client/owner/description zawsze; reporting_currency/
  delivery_period zamrożone gdy istnieje scenariusz `approved`), z ochroną przed zgubioną
  aktualizacją.
  *Done when:* `backend/tests` prove: (1) pola opisowe edytowalne niezależnie od statusu
  scenariuszy; (2) edycja `reporting_currency`/`delivery_period` odrzucana w warstwie dostępu do
  danych (nie tylko w API) gdy istnieje `approved` scenariusz, przyjmowana gdy nie istnieje —
  kontrast na tym samym projekcie przed/po zatwierdzeniu; (3) żądanie ze starym znacznikiem
  współbieżności (`updated_at`) → `409`, nie ciche nadpisanie — kontrast: żądanie z aktualnym
  znacznikiem → `200`; (4) edycja projektu spoza zasięgu wołającego → `404`, nieodróżnialne od
  nieistniejącego (nie `403`); (5) wołający wyłącznie z `PROJECT_READ` (bez `PROJECT_EDIT`) → `403`,
  zero zapisanych zmian (obowiązkowy test odmowy dla nowego uprawnienia, ADR-0005).
  **Out of scope (explicit):** `audit_log`/historia zmian — odłożone do bloku 8, jawne odstępstwo
  zapisane w aneksie ADR-0004 z 2026-09-18. Autosave i obsługa `409` po stronie UI — osobne
  zadanie frontendowe. Tworzenie/usuwanie scenariuszy. Zmiana statusu projektu (archiwizacja to
  SC-1-04). **Uwaga dla tego przyszłego zadania (reviewer, weryfikacja gate 2 z 2026-09-19):**
  `updated_at` ma precyzję mikrosekundy i backend porównuje go bit-do-bitu; klient, który
  przepuści go przez typ daty z precyzją milisekundy (`new Date()`, `.toISOString()` i podobne)
  zamiast trzymać go jako nieprzezroczysty string, dostanie trwały `409` przy każdej edycji.
  Zadanie frontendowe musi przechować i odesłać surowy string, nigdy go nie parsując.
  Podstawa: `docs/architecture/decisions/ADR-0004-wersjonowanie-kalkulacji.md` (aneks
  "zakres migawki wobec pól Projektu"), `docs/architecture/decisions/ADR-0007-wspolbiezna-edycja.md`,
  `docs/architecture/decisions/ADR-0005-model-dostepu.md` (aneks "uprawnienia akcji zapisu").
  **Done 2026-09-19:** PR #25 (scalone `81f077d`). Dowód: `backend/tests/test_project_edit.py`
  (14 testów) + mutation-checked `test_project_write_actions_guards.py::test_the_concurrency_guard_is_evaluated_by_the_database_not_against_the_row_just_read`
  i `::test_the_frozen_field_guard_is_evaluated_in_the_same_statement_as_the_write`. Zob.
  `docs/architecture/capabilities.md`.

- [x] **SC-1-03** — Kopiuj Projekt (deep-copy wszystkich scenariuszy, `approved` → `draft` na
  kopii, bez migawki, dostęp wyłącznie dla kopiującego).
  *Done when:* `backend/tests` prove: (1) kopia to nowy, niezależny wiersz Projektu; (2) każdy
  scenariusz źródła jest skopiowany do kopii jako `draft` (także jeśli źródło było `approved`),
  bez współdzielonej mutowalnej referencji do danych źródła — dowód na poziomie wiersza
  scenariusza (tabele staffing/koszty/stawki nie istnieją jeszcze, więc to NIE jest pełny dowód
  AC-02, wprost odnotowane jako ograniczenie dowodu, nie ukryte); (3) `project_access` po
  kopiowaniu istnieje wyłącznie dla kopiującego — grant źródła NIE jest replikowany (kontrast:
  użytkownik z dostępem do źródła, bez własnej kopii, nie widzi kopii); (4) kopiowanie projektu
  spoza zasięgu wołającego → `404`; (5) wołający bez `PROJECT_COPY` → `403`, zero zapisanych
  wierszy.
  **Out of scope (explicit):** kaskada kopiowania dla przyszłych tabel-dzieci scenariusza
  (staffing, koszty, stawki, reguły komercyjne z ADR-0003) — każde z tych zadań MUSI rozszerzyć
  mechanizm kopiowania o swoją tabelę w tym samym zadaniu, w którym ta tabela powstaje (jawne
  zobowiązanie naprzód, zapisane w aneksie ADR-0004). `audit_log` — jak w SC-1-02. Podstawa:
  `docs/architecture/decisions/ADR-0004-wersjonowanie-kalkulacji.md` (aneks "kopiowanie Projektu
  jako trzeci punkt wejścia"), `agents/invariant-guardian.md` reguła 17.
  **Done 2026-09-19:** PR #25 (scalone `81f077d`). Dowód: `backend/tests/test_project_copy.py`
  (11 testów) + mutation-checked test na żywym szwie `SCENARIO_CHILD_COPIERS` i na grancie
  `project_access` ograniczonym do wołającego. Zaakceptowane, nienaprawione: kopiowanie
  nieidempotentne, kopia bez powiązania ze źródłem — ADR-0004 aneks 2026-09-19. Zob.
  `docs/architecture/capabilities.md`.

- [x] **SC-1-04** — Archiwizuj Projekt (zmiana stanu widoczności, bez wpływu na niezmienność
  scenariuszy ani na F-12).
  *Done when:* `backend/tests` prove: (1) archiwizacja zmienia `status` Active→Archived, projekt
  zostaje widoczny na liście (rozszerza już dowiedzione dla odczytu w SC-1-05 o samą akcję
  zapisu); (2) archiwizacja nie zmienia żadnego wiersza scenariusza projektu (dane scenariuszy
  identyczne przed/po — dowód że to wyłącznie flaga widoczności, nie mechanizm zamrożenia zapisu);
  (3) archiwizacja projektu spoza zasięgu wołającego → `404`; (4) wołający bez `PROJECT_ARCHIVE`
  → `403`, zero zmian.
  **Out of scope (explicit):** odarchiwizowanie — F-01 wymienia wyłącznie "archive", stan jest
  jednokierunkowy w tym zadaniu; osobne zadanie jeśli potrzebne. Blokowanie zapisu na
  zarchiwizowanym projekcie — świadomie NIE wprowadzone (decyzja: archiwizacja to widoczność, nie
  niezmienność — zob. aneks ADR-0004). `audit_log` — jak w SC-1-02. Podstawa:
  `docs/architecture/decisions/ADR-0004-wersjonowanie-kalkulacji.md` (aneks "archiwizacja Projektu
  a niezmienność i odtwarzalność").
  **Done 2026-09-19:** PR #25 (scalone `81f077d`). Dowód: `backend/tests/test_project_archive.py`
  (7 testów) + mutation-checked `test_project_write_actions_guards.py::test_archiving_issues_no_write_statement_against_the_scenarios_table`
  i `::test_archiving_an_already_archived_project_writes_nothing_at_all`. Zaakceptowane,
  nienaprawione: archiwizacja unieważnia token współbieżności ADR-0007 każdego równoległego
  edytora — ADR-0004 aneks 2026-09-19. Zob. `docs/architecture/capabilities.md`.

- [x] **SC-1-08** — Egzekwuj widoczność kosztów osobowych per przypisanie do projektu
  (`project_access.can_view_personnel_costs`), w koniunkcji z globalnym `PERSONNEL_COSTS_READ`,
  nie per wołający samodzielnie — domknięcie Known gap R-03.
  *Done when:* `backend/tests` dowodzą (kryteria K-01..K-06, analyst 2026-09-19):
  1. (K-01) Jedna odpowiedź `GET /projects` dla tego samego wołającego, dwa projekty różniące się
     wyłącznie `can_view_personnel_costs` — pole kosztowe obecne na jednym, `None` na drugim, w tej
     samej odpowiedzi. Mutacja: rozstrzyganie flagi aliasowane na pierwszy wiersz `project_access`
     wołającego zamiast per projekt — musi zabić.
  2. (K-02) Wołający z flagą `true` na przypisaniu, ale bez globalnego `PERSONNEL_COSTS_READ` →
     pole `None`; kontrast: ten sam wołający z uprawnieniem → pole obecne. Mutacja: usunięcie
     koniunktu `caller.has(PERSONNEL_COSTS_READ)` z bramki.
  3. (K-03) Wołający z `PERSONNEL_COSTS_READ`, ale flaga przypisania `false` → pole `None` (status
     `200`, nie `403`/`404` — to odmowa pola, nie projektu); kontrast: flaga `true` → pole obecne.
     Mutacja: usunięcie koniunktu `can_view_personnel_costs` z bramki (stan dzisiejszy).
  4. (K-04) Bramka wpięta na WSZYSTKICH ścieżkach zwracających reprezentację projektu — list,
     detail, `PATCH`, `POST .../copy`, `POST .../archive` — nie tylko list+detail. Dla kopii: pole
     kosztowe kopii rozstrzyga flaga NOWEGO przypisania kopiującego, nie źródła. Dwie mutacje
     (pominięcie bramki w ścieżce list vs. detail) muszą zabić niezależnie.
  5. (K-05) Nowe przypisanie (`POST /projects` → grant twórcy) ma `can_view_personnel_costs=false`
     domyślnie — sprawdzone w bazie, nie tylko w payloadzie; kontrast: flaga ustawiona po fakcie →
     pole widoczne przy kolejnym odczycie. Mutacja: `can_view_personnel_costs=True` przy wstawianiu
     grantu w `create_project`.
  6. (K-06) Żaden z testów 1–5 nie jest pustym dowodem: uruchomienie z podstawianym
     `PERSONNEL_COST_FIELDS = frozenset()` (stan dzisiejszy) wywraca każdy test odmowy — inaczej
     `_without_personnel_costs`'s `or not PERSONNEL_COST_FIELDS` przepuszcza wszystko bez różnicy.

  Pole kosztowe jest polem zastępczym po stronie testu (wzorzec `test_project_detail_personnel_costs.py`)
  — kolumny kosztów osobowych nadal nie istnieją. **To zadanie nie dowodzi AC-06 w całości.**

  **Decyzje bramki 1 (2026-09-19):** koniunkcja, nie zamiennik (ADR-0005 aneks "bramka kosztów
  osobowych jako koniunkcja dwóch mechanizmów"); flaga przychodzi z wierszem z `app.data.project_reads`,
  jednym zapytaniem na żądanie — `response_shaping` nie dostaje `Session`, żadna relacja ORM
  niezawężona do `caller.user_id`; flaga nie dostaje ścieżki nadawania w tym zadaniu (gałąź
  pozytywna nieosiągalna w produkcji do czasu zadania zarządzania uprawnieniami — nazwane, nie
  odkryte później); dowód gałęzi pozytywnej przez `dependency_overrides` w teście, `PLACEHOLDER_PERMISSIONS`
  bez zmian.

  **Out of scope (explicit):** wymiar roli `admin`/`author`/`viewer` (brak ADR uwierzytelniania —
  warunek zamknięcia: ten ADR, koduje też czy `PERSONNEL_COSTS_READ` zostaje wyprowadzone z
  `project_access` — aneks pkt 3); realne pola kosztowe w `PERSONNEL_COST_FIELDS` (F-07, blok 5 —
  zadanie F-07 dopisuje je w tym samym zadaniu, w którym tworzy kolumny); egzekwowanie w eksporcie
  (F-11, blok 7 — zadanie F-11 dowodzi go własnym kryterium); endpoint/ekran nadawania flagi (brak
  zarządzania użytkownikami); ukrywanie pól w UI (NF-04 — ekran jest funkcją odpowiedzi API); luka
  z ADR-0005 aneks 2026-09-19 dot. odczytu bez PROJECT_READ (własny warunek zamknięcia, koniunkcja
  ją tylko zawęża — patrz nowy aneks pkt 7); audyt wglądu (blok 8). Podstawa:
  `docs/architecture/decisions/ADR-0005-model-dostepu.md` (aneks 2026-09-19), `docs/architecture/decisions/ADR-0001-trwalosc-danych.md`
  (aneks — jedna ścieżka odczytu), `docs/architecture/capabilities.md` (Known gap R-03), Issue #15.
  **Done 2026-09-19:** PR #29 (scalone `980c06c`). Dowód: `backend/tests/test_project_personnel_cost_visibility.py`
  (K-01..K-06, 8 testów) + zaktualizowany `test_project_detail_personnel_costs.py` — 133 testy
  backendowe zielono. Bramka to koniunkcja `caller.has(PERSONNEL_COSTS_READ)` i
  `project_access.can_view_personnel_costs` dla pary (wołający, projekt); flaga przychodzi jednym
  zapytaniem z `app.data.project_reads` jako `CallerProjectView`, z jawną asercją podmiotu
  (`AssertionError`, przetrwa `python -O`) przed policzeniem koniunkcji. Known gap R-03 ZAMKNIĘTY.
  Zaakceptowane, nienaprawione: gałąź pozytywna nieosiągalna w działającym systemie — brak ścieżki
  nadawania flagi (ADR-0005 aneks pkt 4). Nie dowodzi AC-06 w całości: kolumny kosztów osobowych
  nadal nie istnieją. Zob. `docs/architecture/capabilities.md`.

- [x] **SC-1-09** — Uodpornij ekran listy projektów na wadliwą odpowiedź `GET /projects` (obrona
  przed wybuchem w renderze) i anuluj jego odczyt przy odejściu z ekranu. Wynik weryfikacji SC-2-02
  (ustalenie R-07, „zaakceptowane, nienaprawione"). Kryteria (K-01..K-07) i pełny zapis decyzji
  bramki 1 w Issue #43. Zadanie wyłącznie frontendowe — `backend/` bez zmian.
  *Done when:* `frontend/src` (vitest) dowodzi K-01..K-07, każde z zarejestrowanym przebiegiem
  mutacyjnym (rozstrzygnięcie Q-3 — nie „test, no mutation" jak precedens katalogu):
  1. (K-01) Wiersz niezgodny z kontraktem zatrzymany na granicy sieci (predykat kształtu w
     `getProjects`), nie na granicy renderu: brak `delivery_period`; `target_margin_percent` liczbą
     zamiast stringiem dziesiętnym; `status` spoza enumu `"Active"|"Archived"`. Każdy przebieg →
     istniejący nazwany stan `Projects could not be loaded.` i zero wierszy; odrzucana CAŁA
     odpowiedź, nie pojedynczy wiersz (lista po cichu skrócona jest gorsza niż biała strona).
     Kontrast w tym samym pliku: ta sama odpowiedź z poprawionym wierszem renderuje wiersze.
     Mutacja: usunięty predykat wiersza (samo `Array.isArray`, stan dzisiejszy).
  2. (K-02) Wyjątek w renderze, którego walidacja kształtu złapać nie mogła, zatrzymany w gnieździe
     ekranu powłoki — chrome żyje (topbar, breadcrumb, rail z obydwoma wpisami, osiągalny z
     klawiatury), fallback nazwany. Dowiedzione dla OBU wartości `activeScreen`: granica chroni
     każdy ekran montowany przez powłokę, nie tylko listę projektów. Mutacje: granica usunięta z
     `AppShell`; osobno: granica przeniesiona do `ProjectListScreen` zamiast do gniazda ekranu.
  3. (K-03, NF-11) Fallback nie niesie żadnej wartości z payloadu — ani w tekście DOM, ani w
     atrybucie DOM, ani w żadnym argumencie wywołania `console.*` wykonanego przez **aplikację**
     (własne logowanie Reacta w trybie dev jest poza mechanizmem — nazwane ograniczenie dowodu,
     rozstrzygnięcie bramki 1, luka 2). Kontrola pozytywna w tym samym teście dowodzi, że detektor
     widzi podstawioną wartość. Mutacja: fallback renderuje `error.message`.
  4. (K-04) Złapana awaria nie przeżywa ekranu: nawigacja railem na zdrowy ekran montuje go
     naprawdę (jego własne odczyty ruszają), fallback znika. Kontrast: przycisk „spróbuj ponownie"
     w fallbacku daje ten sam efekt z tego samego miejsca (rozstrzygnięcie bramki 1, luka 3).
     Drugi kontrast: re-render, który nie jest nawigacją, nie kasuje fallbacku. Mutacja: usunięty
     reset granicy przy zmianie ekranu.
  5. (K-05) Odczyt w locie naprawdę anulowany przy odmontowaniu — `AbortSignal` przekazany do
     `fetch` jest `aborted` po odejściu i NIE jest `aborted` przed nim, nie tylko zignorowany flagą
     `cancelled`. Druga asercja: `getProjects` faktycznie przekazuje sygnał do `requestWithDeadline`
     (nie przyjmuje-i-gubi). Trzecia: przerwany odczyt nigdy nie renderuje się jako nazwana awaria.
     Mutacja (historycznie prawdziwa, SC-2-04 R-02): kontroler zbudowany, `.abort()` nigdy nie
     wołane.
  6. (K-06) `lib/money.ts` bez `try`/`catch` — błędna kwota nadal rzuca, ekran nie renderuje kwoty
     zastępczej (`0.00%`, `NaN`, pusta komórka). Kontrast: `"1.005"` → `"1.01%"` nadal działa,
     jawny `null` nadal renderuje `Not provided`. Mutacja: `throw` w `roundDecimalString`
     opakowany w fallback.
  7. (K-07) Zdanie awarii renderu różne od `denied`/`timed-out`/`failed` na obu ekranach, żadne nie
     jest podciągiem innego w żadną stronę (precedens SC-2-02/SC-2-03, ADR-0009 pkt 5). Mutacja:
     fallback jako nadciąg istniejącego zdania.

  **Decyzje bramki 1 (2026-09-22, Issue #43):** Q-1 jedna granica błędu w powłoce (`AppShell`),
  wokół gniazda ekranu — nie wokół całej powłoki (zabrałaby rail, czyli jedyne wyjście), nie tylko
  wokół `ProjectListScreen` (ta sama klasa awarii żyje dziś w `CatalogScreen`); ADR-0010 przyjęty
  jako `Draft — pending approval`; Q-2 walidacja kształtu **i** granica błędu jako dwa niezależnie
  zabijalne mechanizmy, w tej kolejności (granica jest ostatnią instancją, nie zamiennikiem);
  Q-3 mutation-checked dla obu mechanizmów; Q-4 osobny, nazwany komunikat awarii renderu. Cztery
  domknięcia luk analityka: ADR-0010 nazywa wprost, czego granica NIE łapie (handlery zdarzeń,
  callbacki async, efekty po commit); K-03 ograniczone do własnych wywołań aplikacji; fallback
  dostaje przycisk „spróbuj ponownie" (szerzej niż rekomendacja analityka); predykat kształtu
  waliduje też enum `status` (szerzej niż rekomendacja analityka).

  **Out of scope (explicit):** audyt pozostałych ekranów i retrofit dowodu mutacyjnego dla katalogu
  — wariant A z Q-1 domyka go dla każdego ekranu montowanego railem, ale nie dokłada dowodu dla
  samego katalogu (warunek domknięcia: pierwsza kolejna Story frontendowa dotykająca tego ekranu);
  zmiana walidacji backendu — obrona klienta zostaje niezależnie od niej (**trwałe**);
  raportowanie złapanego błędu poza ekran (telemetria, log zdalny) — brak decyzji o odbiorniku
  diagnostyki, NF-11 (warunek domknięcia: ADR o telemetrii frontendu); `AbortSignal` dla zapisów —
  świadomie nie, przerwana mutacja ma nieznany wynik (ADR-0009/ADR-0007), i dla `getHealth`
  (warunek domknięcia: pierwszy ekran czytający go na montażu); paginacja `GET /projects` —
  warunki SC-1-05 bez zmian; walidacja *formatu* stringa dziesiętnego w predykacie kształtu —
  gramatyka dziesiętna zostaje w jednym miejscu (`lib/money.ts`, ADR-0002), a jej naruszenie jest
  właśnie tym, co dowodzi kolejności obu mechanizmów.

  **Ryzyko dla bramki 3 (architekt):** uzasadnienie istniejących wpisów rejestru o
  `isCatalogRateShape`/`isDimensionEntryShape` („apka nie ma error boundary") przestaje być
  prawdziwe co do przesłanki — wymaga poprawki przy najbliższej bramce 3.

  **Done 2026-09-22:** PR #52 (scalone `36df400`). Dowód: `frontend/src/shell/
  ScreenErrorBoundary.test.tsx` (12 testów: K-02, K-04, K-07), `frontend/src/shell/
  screenCrashContainment.test.tsx` (7, na działającym `<App/>`: K-02, K-03, K-06),
  `frontend/src/features/projects/ProjectListScreen.test.tsx` (28: K-01, K-05),
  `frontend/src/api/client.test.ts` (4: K-05, przekazanie sygnału), `frontend/src/lib/
  money.test.ts` (+6: K-06) — 153 testy frontendowe zielono (było 114), lint i build czyste.
  Runda weryfikacji (QA, Invariant Guardian, reviewer, security-auditor) + poprawki: QA domknęło
  dwie luki dowodu — detektor wycieku NF-11 (K-03) był oparty na argumencie (`instanceof Error`)
  i wybaczał każde wywołanie konsoli niosące błąd (przeżył `componentDidCatch` logujący złapany
  błąd, 142/142 zielono); przepisany na klasyfikację po miejscu wywołania (stos). Strażnik
  porzuconego odczytu (K-05) dowiedziony tylko na ścieżce błędu — ścieżka sukcesu (wyścig
  StrictMode double-mount, spóźniona odpowiedź porzuconego odczytu nadpisująca świeższe wiersze
  bez żadnego komunikatu) przeżyła 143/143, domknięta osobnym testem. Invariant Guardian: PASS.
  Reviewer: STOP, jedno High i trzy Medium/Low — **R-01 (High, potwierdzone bezpośrednio w
  zainstalowanym `react-dom`)**: produkcyjny build Reacta 18.3.1 zawiera `console.error(b.value)`
  wołane bezwarunkowo dla każdej granicy błędu klasowej (`logCapturedError`), wbrew komentarzowi w
  kodzie i pierwotnej treści ADR-0010 twierdzącym, że produkcja tego nie robi — wyjątek formattera
  niósł surową, źle sformatowaną stawkę w treści komunikatu, więc trafiała na konsolę produkcyjną
  mimo że sama granica błędu nic nie loguje; naprawione u źródła (`lib/money.ts` rzuca stałą bez
  interpolacji wartości — jedyne egzekwowalne miejsce, bo tego co loguje sam framework żaden
  komponent nie stłumi), ADR-0010 skorygowana. R-02 (Medium): fokus po „spróbuj ponownie" gubiony
  na `document.body` — naprawione (fokus na nagłówek przemontowanego ekranu, ten sam mechanizm co
  fokus po nawigacji railem; przy ponownej awarii wraca na przycisk). R-03 (Medium, poza
  pierwotnym zakresem Issue #43): `CatalogScreen.afterSave` łamał ADR-0010 pkt 7 — zapis w locie
  + odejście z ekranu zostawiało 6 nieanulowanych odczytów katalogu (~19.7 MB) — naprawione
  (strażnik przed startem re-readu, nie tylko po). R-04 (Low): brak limitu powtórzeń „spróbuj
  ponownie" przy awarii reprodukowalnej od razu — naprawione (licznik, zmiana treści po dwóch
  kolejnych nieudanych próbach, zerowany po udanym renderze i po nawigacji). Security-auditor:
  PASS, zero znalezisk (audyt poprzedzał odkrycie R-01 przez reviewera — traktował ryzyko
  produkcyjnego logowania Reacta jako przyszłe, wersjo-specyficzne dla React 19; reviewer
  zweryfikował bezpośrednio w zainstalowanym pakiecie i pokazał że dotyczy już React 18.3.1).
  **Zaakceptowane, nienaprawione:** brak realnego dowodu granicy błędu na payloadzie katalogu
  (tylko syntetyczny throwing child dla `CatalogScreen` — różnica między „objęty mechanizmem" a
  „dowiedziony”, warunek zamknięcia: pierwsza kolejna Story dotykająca tego ekranu); fokus po
  nawigacji na już-złapaną awarię (osobne od naprawionego R-02, nazwane w ADR-0010 jako otwarte
  ryzyko dostępności); zachowanie pod przyszłym majorem Reacta (React 19 zmienia domyślne
  logowanie na `onCaughtError` — odnotowane jako trigger do ponownej weryfikacji przy upgrade,
  nie zamknięte teraz, bo naprawa R-01 usuwa wartość u źródła niezależnie od wersji Reacta).
  Zob. `docs/architecture/capabilities.md`.

- [x] **SC-2-01** — Wprowadź katalog wymiarów roli (rola/senioritet/lokalizacja/typ zaangażowania)
  jako dane, ze stawką domyślną kosztową i sprzedażową obowiązującą w rozłącznym przedziale dat.
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-07 (analyst 2026-09-19):
  1. (K-01) Katalog jest organizacyjny — wołający z zerowym `project_access` widzi te same
     wiersze co każdy inny (kontrast: lista projektów tego wołającego pusta, katalog nie).
     Mutacja: odczyt zawężony filtrem podmiotowym musi zabić; osobno: stała pusta lista musi zabić.
  2. (K-02) Katalog odmawia domyślnie — wołający bez `CATALOG_READ`/`CATALOG_WRITE` → `403`,
     zero zapisanych wierszy. Mutacja: podmiana uprawnienia w `require_permission`; rozszerzenie
     `PLACEHOLDER_PERMISSIONS` o nowe uprawnienia musi zabić kanarek równości zbiorów.
  3. (K-03) Stawka kosztowa jest polem odmawianym, nie wierszem ukrywanym — wołający z
     `CATALOG_READ` bez `PERSONNEL_COSTS_READ` → `200`, wiersz obecny, stawka kosztowa `None`,
     sprzedażowa obecna; kontrast: z `PERSONNEL_COSTS_READ` → wartość dokładna. Dowód niepusty:
     pole usunięte ze zbioru bramkowanych musi zabić (na realnej kolumnie, nie polu zastępczym).
  4. (K-04) Stawka na dzień z wcześniejszego okienka to stawka wcześniejsza, nie najnowszy wiersz.
     Trzy stawki, rozłączne okienka; data w środkowym → stawka środkowego. Mutacja: `ORDER BY
     effective_from DESC LIMIT 1` musi zabić.
  5. (K-05) Nakładanie się okienek dla tej samej krotki odrzucone przez bazę, także w wyścigu
     dwóch połączeń. Mutacja: `EXCLUDE` usunięty z migracji musi zabić; strażnik przeniesiony do
     Pythona musi zabić TYLKO w wersji z wyścigiem na dwóch połączeniach.
  6. (K-06) Kluczem jest pełna krotka wymiarów — wiersz różniący się dokładnie jednym z czterech
     wymiarów w tym samym okienku → przyjęty (4 przebiegi); wiersz nieróżniący się niczym →
     odrzucony. Mutacja: jedna kolumna usunięta z klucza, cztery razy — każda musi zabić swój
     przebieg.
  7. (K-07) Jednostka i kwota jednoznaczne — zapis z jednostką inną niż `hour` (ścieżka poza
     schematem żądania) odrzucony; kwota jako fixed-point string dla wartości, której domyślna
     serializacja różniłaby się (`1.85E+2` → `"185.00"`).

  **Decyzje bramki 1 (2026-09-19):** katalog bez `project_access` (pierwszy zbiór danych
  organizacyjnych, ADR-0005 aneks); stawka kosztowa poza projektem strzeżona samym globalnym
  `PERSONNEL_COSTS_READ` (wyjątek kierunkowy od koniunkcji SC-1-08, ADR-0005 aneks pkt 3);
  pełna krotka wymiarów jako klucz; jednostka `hour` na razie; przedział `effective_from`/
  `effective_to` (nullowalne = bezterminowa) + kolumna generowana `valid_period daterange` +
  `EXCLUDE` z `btree_gist` (ADR-0008, Draft — pierwsze użycie wzorca); koszt i sprzedaż w jednym
  wierszu; nowe uprawnienia `CATALOG_READ`/`CATALOG_WRITE`; `NUMERIC` ze skalą większą niż minor
  unit waluty, zaokrąglanie tylko u konsumenta; waluta nie w kluczu `EXCLUDE`;
  `CREATE EXTENSION btree_gist` w migracji + dokumentacja uprawnienia w `backend/README.md`.

  **Out of scope (explicit):** model pozycji staffingowej (F-04, Issue #6 — rozszerza
  `SCENARIO_CHILD_COPIERS` w swoim zadaniu); katalog osób nazwanych (Issue #31, zablokowane na
  ADR uwierzytelniania); łańcuch nadpisań organizacja→projekt→scenariusz (F-02, Issue #4); AC-04
  (zobowiązanie naprzód — pierwszy konsument stawki dowodzi go); nadgodziny/dyżury/jednostki
  dzienna/miesięczna (F-07); przewalutowanie (ADR-0006); ekran katalogu; nadawanie uprawnień
  (jak SC-1-08); usuwanie pozycji słownika używanej przez stawkę (F-02); czy wgląd w koszty
  jednego projektu odblokowuje cały katalog (przekazane ADR-owi uwierzytelniania, ADR-0005
  aneks pkt 7); **paginacja `GET /catalog/rates`/list słowników** — zmierzone przez reviewera
  (weryfikacja gate 2, 2026-09-19): 48k wierszy / 19.7 MB / 1.6 s dla realistycznego katalogu
  (40 ról × 5 senioritetów × 20 lokalizacji × 4 typy zaangażowania × 3 okna). Zaakceptowane na
  tych samych warunkach co lista projektów (SC-1-05): osobna Story, gdy katalog realnie przekroczy
  rozmiar, przy którym jedna odpowiedź jest problemem — nie teraz, na wyrost.
  **Fundament nieudowodniony, przyjęty świadomie:** `EXCLUDE`/`btree_gist` — pierwsze użycie w
  repo, precedens dla `exchange_rates`/`commercial_terms`; dowód w CI (testcontainers, rola
  nadrzędna) nie dowodzi uprawnień na środowisku docelowym. Podstawa: Issue #5,
  `Wymagania/Requirements_EN.md` §4 F-03, `docs/architecture/decisions/ADR-0001-trwalosc-danych.md`
  (aneks), `ADR-0002-obsluga-pieniedzy.md`, `ADR-0004-wersjonowanie-kalkulacji.md` (aneks),
  `ADR-0005-model-dostepu.md` (aneks), `ADR-0006-waluty-i-kursy.md` (aneks),
  `ADR-0008-przedzialy-obowiazywania.md` (nowa, Draft).
  **Done 2026-09-19:** PR #34 (scalone `8390f4c`). Dowód: `backend/tests/test_catalog_access.py`
  (K-01, K-02), `test_catalog_personnel_cost_visibility.py` (K-03, na realnej kolumnie),
  `test_catalog_rate_resolution.py` (K-04, K-07 kwota), `test_catalog_schema_constraints.py`
  (K-05 w wyścigu dwóch połączeń, K-06 ×4 wymiary, K-07 jednostka) — 208 testów backendowych
  zielono (było 133). Runda weryfikacji + poprawki: mapowanie SQLSTATE→status zamiast zgadywania
  przyczyny 409; test chroniący przed rozjazdem `valid_period` między modelem a migracją;
  normalizacja nazw słownika (indeks funkcyjny, nie walidator); odmowa precyzji stawki >4 miejsca
  jako 422. Zaakceptowane, nienaprawione: jednoczynnikowa bramka kosztowa poza kontekstem projektu
  (ADR-0005 aneks); paginacja katalogu (wyżej); `EXCLUDE`/`btree_gist` na środowisku docelowym
  nieudowodnione. Zob. `docs/architecture/capabilities.md`.

- [x] **SC-2-02** — Pokaż katalog ról i stawek domyślnych jako ekran (odczyt, bez akcji zapisu).
  Blocked by SC-2-01. Kryteria i decyzje bramki 1 w Issue #39.
  **Done 2026-09-21:** `frontend/src/features/catalog/CatalogScreen.test.tsx` (K-01..K-08, 20
  testów) + `frontend/src/App.test.tsx::makes the catalogue screen reachable from the running
  application, not only from its own test` (K-09); rozszerzone `frontend/src/lib/money.test.ts`
  i `frontend/src/styles/tokens.test.ts` (kontrast WCAG AA dla nowego arkusza) — 79 testów
  frontendowych zielono, lint i build czyste. PR #44.
  Runda weryfikacji (QA, Invariant Guardian, reviewer) + poprawki: R-01/R-06 (anulowanie
  wszystkich 5 odczytów katalogu przy odmontowaniu ekranu i przy porażce dowolnego z nich —
  bez tego odbicie się w railu marnowało transfer zmierzonego katalogu, 48k wierszy/19.7 MB);
  R-02 (walidacja kształtu wiersza w kliencie — zły payload trafia w istniejący stan awarii
  zamiast wybuchać `TypeError` w renderze); R-03 (fokus przenoszony na nagłówek nowego ekranu po
  nawigacji railem, `role="status"` na stanie ładowania). QA znalazł i zamknął dwie dziury w
  dowodzie K-03 (etykieta odmowy kosztu nieprzypięta do treści ekranu, kontrast koloru sprawdzany
  między tokenami zamiast czytany z arkusza), 11 mutacji, zob. `docs/architecture/capabilities.md`.
  **Zaakceptowane, nienaprawione:** brak error boundary w aplikacji i `getProjects` bez
  `AbortSignal` (ta sama klasa co R-01/R-02, ale na `ProjectListScreen`, sprzed tego zadania) —
  wydzielone do Issue #43.

- [x] **SC-2-03** — Rozszerz katalog stawek domyślnych o stawki poddostawców (z podziałem na
  lokalizacje), rozszerz ekran katalogu o wyświetlanie poddostawcy.
  Blocked by SC-2-01, SC-2-02. Kryteria i decyzje bramki 1 w Issue #46.
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-08 i `frontend/src` (vitest) dowodzi
  K-09..K-10 (analyst, 2026-09-21):
  1. (K-01) Poddostawca jako piąta kolumna klucza: stawka wewnętrzna i stawki dwóch poddostawców
     współistnieją dla jednej krotki i jednego okna. Mutacja: piąta kolumna usunięta z
     `ExcludeConstraint`.
  2. (K-02) Dopisanie nullowalnej kolumny do klucza NIE wyłącza ochrony dla wierszy wewnętrznych
     (pułapka `NULL = NULL`). Mutacja: `COALESCE(vendor_id, sentinel)` zamieniony na goły
     `vendor_id`.
  3. (K-03) Pominięcie `vendor_id` przy rozstrzyganiu znaczy „wewnętrzna”, nigdy wybór. Mutacja:
     predykat na `vendor_id` usunięty z `WHERE`.
  4. (K-04) Brak stawki wewnętrznej to brak stawki, nigdy cichy fallback do stawki poddostawcy.
     Mutacja: `ORDER BY vendor_id NULLS FIRST LIMIT 1` dodane.
  5. (K-05) Poddostawca jest piątym słownikiem, nie piątym mechanizmem — te same bramki
     `CATALOG_READ`/`CATALOG_WRITE`. Mutacja: wpis `"vendors"` usunięty z `DIMENSION_MODELS`.
  6. (K-06) Stawka kosztowa wiersza z poddostawcą bramkowana dokładnie jak wewnętrzna. Mutacja:
     gałąź `vendor_id` pominięta w `_without_catalog_personnel_costs`.
  7. (K-07) `vendor_id` jest referencją albo niczym — nieznany `vendor_id` odrzucony przez FK,
     brak `vendor_id` zapisuje `NULL`, nie sentinel. Mutacja: FK usunięty; druga mutacja: sentinel
     podstawiony zamiast `NULL`.
  8. (K-08) Przebudowane `EXCLUDE` istnieje w zmigrowanej bazie w jednym egzemplarzu, model zgodny
     z migracją (asercja równością). Mutacja: rozjazd modelu z migracją.
  9. (K-09) Ekran katalogu rozróżnia stawkę wewnętrzną od stawki poddostawcy bez decyzji o
     widoczności — nazwany stan „Internal”, nigdy pusta komórka. Mutacja: `?? ""` zamiast
     nazwanego stanu.
  10. (K-10) Ekran nie podejmuje żadnej decyzji o widoczności cennika poddostawcy — dokładnie 6
      żądań GET, zero żądań o tożsamość. Mutacja: warunkowe pominięcie odczytu `vendors`.

  **Decyzje bramki 1 (2026-09-21, Issue #46):** `vendor_id UUID NULL` jako piąta kolumna klucza
  `EXCLUDE`, `COALESCE(vendor_id, uuid_nil())` z `CHECK` zabraniającym wiersza `catalog_vendors`
  o `id = uuid_nil()`; `catalog_vendors` jako piąty słownik `_CatalogDimension`; jedna migracja
  przebudowująca `EXCLUDE` (nie expand/contract — środowisko docelowe nie istnieje, open decision
  #5); zero nowego uprawnienia — `CATALOG_READ`/`CATALOG_WRITE` obejmują cennik poddostawców,
  decyzja biznesowa przyjęta świadomie mimo że to zwykle dane pod NDA; `default_cost_rate` wiersza
  z poddostawcą nadal bramkowany przez `PERSONNEL_COSTS_READ`, nazwana ochrona nadpłacona; ADR-0008
  podniesiony z Draft do Accepted; ekran (SC-2-02) rozszerzony w tym samym PR, bez zapisu z UI.

  **Decyzja bramki 2 (2026-09-21, komentarz na Issue #46):** paginacja `GET /catalog/rates` —
  pierwotnie poza zakresem (Out of scope pkt 10, jak SC-2-01) — pozostaje w zakresie tego zadania
  zamiast osobnej Story, pod warunkiem zamknięcia trzech zastrzeżeń reviewera (R-01/R-02/R-03,
  patrz niżej). Wyłącznie syntetyczne dane w bazach dev/test dla cenników poddostawców do czasu
  ADR uwierzytelniania (ADR-0005 aneks pkt 6).

  **Out of scope (explicit):** zapis z UI (SC-2-04); osoby nazwane po stronie poddostawcy, dane
  kontaktowe (Issue #31 + ADR uwierzytelniania); poddostawca jako kategoria kosztu dodatkowego
  F-08 (Issue #10); warunki handlowe per poddostawca (Issue #8); przewalutowanie (ADR-0006);
  nadpisanie stawki projekt/scenariusz (Issue #4); wskazanie dostawcy na pozycji obsady (Issue #9);
  kalendarz roboczy per poddostawca (Issue #7); usuwanie wpisu słownika używanego przez stawkę;
  migawka AC-04 (Issue #9); wymiar kraju/hierarchia lokalizacji — odrzucone na bramce 1, nieotwarte.

  **Fundament nieudowodniony, przyjęty świadomie:** rola aplikacyjna do `CREATE EXTENSION
  btree_gist`/przebudowy `EXCLUDE` na środowisku docelowym (open decision #5); wydajność indeksu
  gist na pięcioelementowym kluczu w produkcyjnej skali; `PERSONNEL_COSTS_READ` nadal przez nic nie
  nadawane — gałąź autoryzowana dowodliwa wyłącznie testem.

  **Done 2026-09-21:** PR #47 (scalone `73de5d2`). Dowód: `backend/tests/
  test_catalog_schema_constraints.py` (K-01, K-02, K-07, K-08), `test_catalog_rate_resolution.py`
  (K-03, K-04), `test_catalog_access.py` (K-05), `test_catalog_personnel_cost_visibility.py`
  (K-06), `test_catalog_migration_reversibility.py` (odwracalność migracji `c1a4f7b92e05`),
  `frontend/src/features/catalog/CatalogScreen.test.tsx` (K-09, K-10) — 310 testów backendowych
  zielono (było 307), 84 frontendowych (było 83). Runda weryfikacji (QA, Invariant Guardian,
  reviewer, security-auditor) + poprawki: QA domknęło lukę w kierunku usuwania FK `vendor_id`
  (`ON DELETE` musi być `NO ACTION`, nie `SET NULL`/`CASCADE` — usunięcie poddostawcy inaczej po
  cichu przemianowywało jego stawki na wewnętrzne). Invariant Guardian: PASS, jedna uwaga poza
  listą reguł (paginacja poza jawnym zakresem — zamknięta aneksem bramki 2, patrz wyżej).
  Reviewer R-01 (medium): `count(*) OVER()` bez indeksu ograniczał tylko rozmiar odpowiedzi, nie
  pracę serwera (pełny sort całej tabeli na każdą stronę) — poprawione: indeks
  `ix_catalog_default_rates_effective_from_id` (migracja `e2c7b04d9a31`), strona czytana jako
  ograniczone top-N, `total` jako podzapytanie skalarne w tym samym zdaniu (gwarancja jednego
  snapshotu bez zmiany). R-02 (low): `offset` bez górnej granicy kończył się nieobsłużonym `500`
  zamiast `422` — dodana górna granica (`MAX_RATE_LIST_OFFSET`), wzorem `limit`. R-03 (low): pusta
  strona odpowiedzi (`rates: [], total > 0`) renderowana jako pusty katalog — rozróżnione
  (`NoRatesMessage`/`emptyPageLabel`). Security-auditor: PASS WITH RESERVATIONS — komentarz
  „vendor = firma, więc nigdy dane osobowe” doprecyzowany w `backend/app/models/catalog.py`
  (sole trader = dane osobowe, nic tego nie pilnuje — nie klasyfikować tabeli jako wolnej od
  danych osobowych z automatu w przyszłym audycie).
  **Zaakceptowane, nienaprawione:** widoczność cennika per poddostawca (Out of scope wyżej);
  wydajność planu zapytania nie zmierzona na realnym rozmiarze (test dowodzi kształtu planu,
  `enable_seqscan`/`enable_sort` wyłączone), tylko że ograniczona ścieżka istnieje i jest o nią
  proszone; koszt `total` pozostaje liniowy względem przefiltrowanego zbioru (świadomie — patrz
  R-01 wyżej); paginacja słowników (w tym `vendors`) pozostaje nieograniczona — sprawdzone
  świadomie jako nieszkodliwe przy dzisiejszej skali poddostawców, zmienia się jeśli liczba
  poddostawców urośnie do dziesiątek tysięcy. Zob. `docs/architecture/capabilities.md`.

- [x] **SC-2-04** — Dodaj zapis z ekranu katalogu: dodawanie **i edycja** wpisu słownika oraz okna
  stawki domyślnej (backend: znacznik współbieżności + dwa `PATCH`-e; frontend: dwa formularze).
  Blocked by SC-2-01, SC-2-02, SC-2-03. Kryteria (K-13..K-23, dwie rundy analityka) i pełny zapis
  decyzji bramki 1 w Issue #49.
  *Done when:* `frontend/src` (vitest) dowodzi K-13..K-22, a `backend/tests` dowodzą K-23 i decyzji
  Q-1/Q-2 bramki 1:
  1. (K-23, backend) Dwa zapisy tego samego wiersza z jednego odczytu: jeden wygrywa, drugi
     odmówiony — nigdy dwa „sukcesy”, nigdy ciche nadpisanie. Dowiedzione sekwencyjnie **i w
     wyścigu dwóch połączeń** (konkurent zatwierdza zmianę między odczytem a `UPDATE`-em).
     Mutacja: porównanie znacznika w Pythonie zamiast w `WHERE` instrukcji `UPDATE`.
  2. (Q-1) Znacznik `updated_at` na **wszystkich sześciu** tabelach katalogu w zmigrowanej bazie,
     `NOT NULL` z `server_default` (migracja wstecznie zgodna, bez drugiego kroku). Mutacja:
     znacznik tylko na `catalog_default_rates` (odrzucony wariant C).
  3. (Q-2) `PATCH` częściowy: pominięty `default_cost_rate` zostawia zapisany koszt nietknięty —
     to jest to, co w ogóle pozwala edytować wiersz wołającemu bez `PERSONNEL_COSTS_READ`.
     Mutacja: `UPDATE` budowany ze wszystkich pól modelu zamiast z `model_fields_set`.
  4. Dwa `409` na jednej ścieżce zapisu są rozróżnialne (nieaktualny znacznik vs. nakładanie okien
     /duplikat nazwy) i żaden nie cytuje wartości wiersza (NF-11). Mutacja: obie gałęzie zlane w
     jeden komunikat.
  5. `404` ma pierwszeństwo przed `409` dla wiersza, którego nie ma (precedens SC-3-01 R-01).
  6. Odpowiedź `PATCH` przechodzi przez tę samą bramkę kosztową co odczyt — wołający bez
     `PERSONNEL_COSTS_READ` nie dostaje `default_cost_rate` także po własnym zapisie.

  **Decyzje bramki 1 (2026-09-21, Issue #49):** P-1 zakres = dodawanie + edycja; P-2 „zapisujesz
  koszt, nie odczytasz go” przyjęte świadomie; P-3 ADR-0009 (zapis z UI) zakładany teraz; P-3a stan
  po zapisie = ponowny odczyt listy; P-4 wszystkie pięć słowników + stawki; P-5 waluta jako pole
  tekstowe ISO-4217 walidowane wyłącznie przez backend; G-3 (konflikt testu SC-2-02) zaakceptowany
  jako zamierzony; G-6 „zapisano, ale odświeżenie listy się nie udało” jako osobny nazwany stan
  (K-19); Q-1 znacznik `updated_at` na sześciu tabelach, wzorzec ADR-0007 reużyty, warunek liczony
  przez bazę w tej samej instrukcji `UPDATE`; Q-2 `PATCH` częściowy (pominięte pole = bez zmian).

  **Out of scope (explicit):** usuwanie wpisu słownika i okna stawki (bez zmian wobec SC-2-03);
  przekluczowanie istniejącego okna na inną krotkę wymiarów albo innego poddostawcę
  (`EDITABLE_RATE_FIELDS` to lista dozwolonych, nie „cały wiersz”); kolumna audytu „kto zmienił”
  — wygasiłaby wyjątek ADR-0001/ADR-0005 dla katalogu, wymaga własnej datowanej decyzji; autosave
  (NF-05, druga połowa) — odłożone po raz trzeci; `unit` jako pole edytowalne (baza wymusza
  `hour`).

  **Fundament nieudowodniony:** `PERSONNEL_COSTS_READ` nadal przez nic nie nadawane, więc dodatnia
  gałąź bramki kosztowej na ścieżce `PATCH` dowodliwa wyłącznie testem; migracja niedowiedziona na
  jakimkolwiek trwałym środowisku (open decision #5); zapis z przeglądarki nadal bez ani jednego
  wiersza w rejestrze możliwości do czasu części frontendowej.

  **Done 2026-09-22:** PR #50 (scalone `4699072`). Dowód: `backend/tests/test_catalog_edit.py`
  (18 testów: K-23, Q-1, Q-2, rozróżnialność 409, 404>409),
  `backend/tests/test_catalog_migration_lock_timeout.py` (R-06),
  `frontend/src/features/catalog/CatalogWrite.test.tsx` (27 testów: K-13..K-23),
  `frontend/src/api/contracts/writeRefusals.test.ts` (spięcie warunku odmowy backend↔frontend) —
  331 testów backendowych zielono (było 307 przed tym zadaniem), 114 frontendowych (było 84).
  Runda weryfikacji (QA, Invariant Guardian, reviewer, security-auditor) + poprawki: QA domknęło
  dwie luki w dowodzie — znacznik pięciu słowników nie był dowiedziony jako ruchomy przy zmianie
  nazwy (`onupdate` przeżył cały pierwotny zestaw), string warunku odmowy nie był spięty między
  backendem a frontendem (rename backendu nie zostałby wykryty przez żaden test). Invariant
  Guardian: PASS, jedna uwaga poza listą reguł — ryzyko AC-04 (edycja stawki jako zdarzenie
  zmieniające domyślną organizacji) zgubione przy rozszerzeniu zakresu z dodawania na
  dodawanie+edycję, dopisane do ADR-0009. Reviewer: STOP, 3 medium + 3 low — naprawione R-01
  (komunikat o nieaktualnym znaczniku fałszywie wskazywał „kogoś innego" na ścieżce samo-konfliktu
  z własnym wcześniejszym zapisem), R-02 (ponowny odczyt po zapisie bez anulowania przy opuszczeniu
  ekranu, brak blokady otwarcia formularza w oknie odczytu — mogło seedować formularz z nieaktualną
  wartością), R-03 (udany zapis 2xx o nieczytelnym ciele — np. przy rolling deploy — raportowany
  jako awaria serwera zamiast stanu nierozstrzygniętego, zachęcając do powtórki i duplikatu),
  R-06 (migracja bez `lock_timeout` — ryzyko zablokowania całego katalogu przez konkurencyjną
  transakcję). Security-auditor: PASS, zero znalezisk; dwa nazwane, nienaprawione ryzyka (poniżej).
  **Zaakceptowane, nienaprawione:** R-04/R-05 (martwa rada „reload katalogu" w komunikatach zapisu
  bez kontrolki odświeżenia; ostrzeżenie o paginacji w komunikacie sukcesu obecne nawet gdy
  nieadekwatne) — kosmetyka UX, świadomie odłożona; `updated_at` jako niebramkowany kanał
  czasowy dla bramkowanego pola kosztowego (ujawnia *kiedy* zmieniono koszt, nie wartość — bez
  populacji dziś, bo nikt nie ma `PERSONNEL_COSTS_READ`); nieodwracalność edycji — katalog był
  dotąd insert-only, teraz pojedynczy `PATCH` może nadpisać wrażliwą stawkę bez cofnięcia i bez
  audytu „kto zmienił" (F-12 nadal odłożone, świadomie). Zob. `docs/architecture/capabilities.md`.

- [x] **SC-3-01** — Utrwal pozycje obsady scenariusza (krotka wymiarów katalogu, headcount, okres)
  z alokacją miesięczną w godzinach, trzema niezależnymi wartościami (dostępność / planowana
  alokacja / czas rozliczalny) i rejestracją w kaskadzie kopiowania.
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-08 (analyst + architect, 2026-09-19):
  (1) pozycja wraca z czterema FK katalogu i headcount, obcy wymiar odrzucony przez FK w bazie;
  (2) scenariusz rozstrzygany przez zasięg `project_access`, nie `session.get` — `404` nie `403`,
  precedencja przed `409`; (3) wołający bez `STAFFING_READ`/`STAFFING_WRITE` → `403`, zero wierszy;
  (4) kształt wiersza miesiąca w bazie: `UNIQUE(position_id, period_month)` w wyścigu dwóch
  połączeń, CHECK na 1. dzień miesiąca, CHECK na nieujemność godzin; (5) trzy wartości godzin
  niezależne, mutacja "billable := planned" i "availability := planned" zabijają osobno;
  (6) INSERT nowej pozycji do scenariusza `approved` odrzucony (`WHERE status <> 'approved'` w
  podzapytaniu) — wyścig zatwierdzenia-kontra-INSERT-a NIE domykany (nic dziś nie ustawia
  `approved`), warunek zamknięcia: pierwsza prawdziwa ścieżka zatwierdzenia; (7) UPDATE istniejącej
  alokacji pod `approved` odrzucony w tej samej instrukcji, w wyścigu dwóch połączeń — osobne
  kryterium od (6), różne ścieżki dojścia do statusu, żadna mutacja nie zabija obu; plus znacznik
  współbieżności (ADR-0007) na pozycji; (8) AC-02 w całości: kopia niesie te same wiersze na
  nowych id, zmiana na kopii nie rusza źródła, kontrast źródło-`approved`-odrzuca/kopia-`draft`-
  przyjmuje — jeden kopiujący na agregat pozycja+alokacja w `SCENARIO_CHILD_COPIERS`.
  **Decyzje bramki 1 (2026-09-19):** alokacja to suma pozycji, nie na głowę (niefalsyfikowalne bez
  konsumenta — przeniesione); okres jako `period_month DATE` + `UNIQUE(position_id, period_month)`,
  NIE wzorzec ADR-0008; pozycja/alokacja NIE wchodzą do migawki `approved` (aneks ADR-0004 — dane
  własne scenariusza, chronione strażnikiem zapisu, nie migawką); nowa para uprawnień
  `STAFFING_READ`/`STAFFING_WRITE` (aneks ADR-0005); dostępność na wierszu miesiąca; jeden
  kopiujący na agregat, kontrakt `SCENARIO_CHILD_COPIERS` bez zmian (aneks ADR-0004); strażnik
  INSERT-a jako podzapytanie w `WHERE`, wyścig zatwierdzenia odłożony jawnie i datowany (aneks
  ADR-0004); odpowiedź nie niesie żadnej stawki (aneks ADR-0005); znacznik współbieżności na
  pozycji (aneks ADR-0007); adres zagnieżdżony `/projects/{id}/scenarios/{id}/...`, zasięg przez
  `project_for_caller` bez nowej funkcji-strażnika (aneks ADR-0001).
  **Out of scope (explicit):** FTE jako jednostka (F-05, Issue #7 — brak podstawy konwersji);
  kalendarze/nieobecności jako źródło dostępności (F-05 — tu wartość ręczna, ryzyko nazwane: do
  F-05 system nie wie o ani jednym dniu wolnym); faza dostawy jako wymiar (encji nie ma, F-02,
  Issue #4, ADR-0003 w Draft); onboarding/handover jako nazwane typy (F-06/F-07/F-10); ostrzeżenie
  o przeciążeniu (brak progu z F-02, osobne zadanie SC-3-0x — nie `CHECK`, przeciążenie jest
  stanem legalnym); koszt nierozliczalnego wysiłku (F-07, Issue #9); rozwiązana stawka na pozycji
  (F-07, Issue #9); osoba nazwana (Issue #31); ekran (osobne zadanie FE); endpoint tworzenia
  scenariusza (F-02, Issue #4 — gałąź pozytywna zapisu nieosiągalna w produkcji do tego czasu, jak
  SC-1-08); usuwanie pozycji/wiersza miesiąca (DELETE — nie istnieje, byłoby trzecią operacją
  zapisu wymagającą własnego strażnika `approved`); `audit_log` (blok 8, już objęte odstępstwem
  "bloki 1-7"); wydajność NF-03 (7200 wierszy/scenariusz, kaskada kopiowania mnoży per scenariusz —
  mierzone na bramce 2 jak SC-1-05/SC-2-01); **korekta pozycji po utworzeniu** — zmierzone przez
  reviewera (weryfikacja gate 2, 2026-09-19): brak `PATCH` na nagłówku pozycji (`headcount`,
  `start_date`/`end_date`) i brak sposobu dodania miesiąca do już istniejącej pozycji; jedyna
  publikowana rada przy błędzie ("skopiuj scenariusz") kopiuje błędną pozycję dosłownie.
  Zaakceptowane jawnie: cała ścieżka zapisu tego zadania jest i tak nieosiągalna w produkcji (brak
  tworzenia scenariusza), więc rozszerzenie teraz nie odblokowuje realnego użycia szybciej niż
  osobne zadanie po tym. Wzorzec do reużycia gotowy (CTE `guarded_position` z `update_allocation`
  strzeże już zapisu do nagłówka pozycji i dodania wiersza miesiąca zerem nowych strażników).
  *Warunek zamknięcia:* osobne zadanie SC-3-0x, przed albo razem z zadaniem tworzącym scenariusze
  (F-02, Issue #4) — inaczej pierwszy prawdziwy scenariusz w produkcji dziedziczy tę samą
  niekorygowalność.
  **Fundament nieudowodniony:** K-06 to pierwsza implementacja w repo strażnika zapisu do
  `approved` scenariusza dla INSERT-a — ustanawia wzorzec dla wszystkich następnych tabel-dzieci;
  wyścig zatwierdzenia-kontra-zapisu pozostaje otwarty. Brak endpointu tworzenia scenariusza —
  cała ścieżka zapisu nieosiągalna w produkcji, dowodzona wyłącznie przez fixture. Decyzja 1
  (suma, nie na głowę) niefalsyfikowalna testem. Podstawa: Issue #6,
  `Wymagania/Requirements_EN.md` §4 F-04,
  `docs/architecture/decisions/ADR-0004-wersjonowanie-kalkulacji.md` (2 aneksy),
  `ADR-0005-model-dostepu.md` (aneks), `ADR-0001-trwalosc-danych.md` (aneks),
  `ADR-0007-wspolbiezna-edycja.md` (aneks), `ADR-0002-obsluga-pieniedzy.md`.
  **Done 2026-09-19:** PR #38 (scalone `1fa4b6b`). Dowód: `backend/tests/test_staffing_positions.py`,
  `test_staffing_approved_guards.py`, `test_staffing_schema_constraints.py`, `test_staffing_copy.py`,
  `test_staffing_hours_precision.py`, `test_staffing_data_layer_guards.py` — 284 testy backendowe
  zielono (było 208). Runda weryfikacji gate 2 (QA, Invariant Guardian, reviewer, security-auditor)
  + poprawki: R-01 (precedencja 404 przed 409 w diagnozie odmowy alokacji — miesiąc-nie-istnieje
  przed approved, stara rada "skopiuj scenariusz" była myląca); R-04 (granice wejścia API — headcount
  ≤ 10 000, siatka miesięcy ≤ 60, sprawdzenie duplikatów O(n) zamiast O(n²)); S-01 (dowód dla granicy
  precyzji godzin `NUMERIC(10,2)`, 20 testów); S-02 (strażniki warstwy danych bez testu — kolejność
  scalania `id`/`position_id` przy INSERT-cie, `EDITABLE_ALLOCATION_FIELDS`/`AllocationFieldNotEditable`
  przy UPDATE, 6 testów); R-05 (usunięty nadmiarowy indeks `position_id`, pokryty przez `UNIQUE`);
  B-01 security-auditor (porządek scalania słownika przy wstawianiu alokacji — computed fields
  ostatnie). Zaakceptowane, nienaprawione: R-02 (rozstrzygnięte jako equivalent mutant — "brak commit
  ⇒ implicit rollback" chroni niezależnie od predykatu `month_row_exists`, predykat przywrócony bo
  dokumentuje intencję); R-03 (korekta pozycji po utworzeniu — odłożone do SC-3-0x, PR #37); wyścig
  zatwierdzenia-kontra-zapisu (K-06) — niewykonalny do przetestowania, nic dziś nie ustawia
  `approved`. Zob. `docs/architecture/capabilities.md`.

- [x] **SC-3-02** — Utrwal kalendarze robocze (podstawa godzinowa, wzorzec tygodnia, dni
  wyjątkowe), słownik typów nieobecności i instancje nieobecności pozycji obsady; wylicz pojemność
  miesięczną z kalendarza lokalizacji i nieobecności; zbuduj endpoint zatwierdzenia scenariusza z
  pierwszą migawką `approved_snapshot_*` (F-05, F-12; Issue #7).
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-23 (analyst, 2026-09-22, runda 2 finalna):
  (1) podstawa godzinowa pozycji pochodzi z kalendarza jej lokalizacji, wzorzec tygodnia i dni
  wyjątkowe są danymi, nie stałą w Pythonie (NF-10); (2) jedna nieobecność zabiera jeden ekwiwalent
  osoby za każdy dzień roboczy — nie `headcount ×`, nie dni kalendarzowe — nakładające się instancje
  sumują się bez deduplikacji, wynik podłogowany zerem, `availability_hours` nigdy nie nadpisywane;
  (3) słowniki kalendarza i typów nieobecności organizacyjne (`CATALOG_READ`/`CATALOG_WRITE`, zero
  nowych uprawnień), instancje nieobecności projektowe (`STAFFING_READ`/`STAFFING_WRITE`, `404`
  nigdy `403`); (4) każda z dwóch nowych ścieżek zapisu ma test odmowy i test wyścigu dwóch
  połączeń — znacznik ADR-0007 i strażnik `approved` liczone przez bazę w instrukcji zapisu; (5)
  nieobecności wchodzą do istniejącej kaskady kopiowania, tabele organizacyjne nie dostają wpisu w
  `SCENARIO_CHILD_COPIERS`; (6) zatwierdzenie zapisuje `approved_snapshot_*` jako wartości (nigdy FK
  do źródła), migawka przed przestawieniem statusu w jednej transakcji, powtórne zatwierdzenie nie
  zapisuje niczego; (7) wyścig zatwierdzenia-kontra-zapisu-dziecka domknięty jednym mechanizmem
  (`scenario_guard.py`) dla wszystkich czterech ścieżek zapisu naraz (pozycja, alokacja, instancja
  nieobecności ×2); (8) `staffing_position_absence` nie ma kolumny na osobę ani na notatkę —
  dowiedzione równością zbioru kolumn; (9) K-23 (rozstrzygnięcie G-2): pozycja bez kalendarza
  lokalizacji zwraca nazwany stan "brak kalendarza", nigdy zero po cichu, nigdy błąd.
  **Decyzje bramki 1 (2026-09-22):** absencja liczona w godzinach, nie dniach (przeciw
  rekomendacji, przyjęte świadomie); pełne instancje nieobecności, nie tylko słownik typów (przeciw
  rekomendacji); endpoint zatwierdzenia jako realna ścieżka zapisu, nie fixture (P-2, przeciw
  rekomendacji, ryzyko przyjęte: brak kontroli roli, zamknięcie warunkowe na ADR
  uwierzytelniania); kalendarz na poziomie lokalizacji (P-1); `calendar_id` nullowalne, nazwany
  stan "brak kalendarza" (G-2); migawka jako pierwsza implementacja mechanizmu ADR-0004 (Q-1);
  parametr długości dnia z kalendarza, nie z pola scenariusza (nowy).
  **Out of scope (explicit):** formularze zapisu kalendarza/typów nieobecności/przypisania
  kalendarza do lokalizacji (brak ścieżki HTTP — ADR-0007 aneks pkt 3, każdy kalendarz dziś
  wymaga fixture/seeda); kontrola roli i audyt na endpoincie zatwierdzenia (ADR-0005 aneks pkt 9,
  ADR-0004 aneks pkt 5); odczyt migawki w raporcie (blok 8); FTE, dzień częściowo roboczy,
  kalendarz per poddostawca, próg przeciążenia; konwersja
  `scenarios.working_calendar`/`full_time_hours_per_week` na kalendarz (G-1, nazwane, nieblokujące
  — dwa źródła prawdy o tygodniu pracy współistnieją); zakres migawki ograniczony do kalendarzy
  lokalizacji pozycji scenariusza i typów faktycznie użytych przez jego nieobecności, nie całego
  katalogu (decyzja developera, uzasadniona w kodzie, niepodważona przy weryfikacji).
  **Fundament nieudowodniony:** K-20 (wyścig zatwierdzenia-kontra-zapisu) był `NIEDOWIEDZIONY,
  DOTĄD NIETESTOWALNY` na etapie analizy — domknięty przy implementacji, dowiedziony czterema
  przebiegami + kontrastem, zweryfikowany niezależnie dwukrotnie (invariant-guardian, reviewer) na
  żywym PostgreSQL włącznie z analizą planu zapytania. Nikt nie czyta migawki — dowiedzione
  wyłącznie, że powstaje i się nie rusza. Podstawa: Issue #7,
  `Wymagania/Requirements_EN.md` §4 F-05, F-12,
  `docs/architecture/decisions/ADR-0004-wersjonowanie-kalkulacji.md` (aneks),
  `ADR-0005-model-dostepu.md` (aneks + 2 nazwane ryzyka danych osobowych),
  `ADR-0001-trwalosc-danych.md` (aneks), `ADR-0007-wspolbiezna-edycja.md` (aneks),
  `ADR-0008-przedzialy-obowiazywania.md` (aneks).
  **Done 2026-09-22:** PR #55 (scalone `d6659f3`). Dowód: `backend/tests/test_working_calendar.py`,
  `test_working_calendar_schema_constraints.py`, `test_staffing_absence_capacity.py`,
  `test_catalog_access.py`, `test_staffing_absences.py`, `test_catalog_absence_types.py`,
  `test_staffing_absence_guards.py`, `test_staffing_copy.py`, `test_project_copy.py`,
  `test_scenario_approval_snapshot.py`, `test_scenario_approval.py`,
  `test_staffing_approved_guards.py`, `test_staffing_schema_constraints.py` — 407 testów backendowe
  zielono (było 331). Dwie rundy weryfikacji gate 2 (QA, Invariant Guardian, reviewer,
  security-auditor) + poprawki: **S-01/R-01** (bug znaleziony niezależnie przez invariant-guardian
  i reviewer — `DISTINCT` w zapisie migawki grupował po `gen_random_uuid()`, VOLATILE i liczony
  przed węzłem `Unique`, więc nigdy nic nie deduplikował; scenariusz z ≥2 pozycjami w jednej
  lokalizacji dawał migawkę zwielokrotnioną, nieodwracalnie — naprawione przeniesieniem `DISTINCT`
  do podzapytania, 2 nowe testy); **R-02** (docstring `scenario_guard.draft_scenario` mylnie
  sugerował redundancję blokady; zmierzone empirycznie na PostgreSQL: to jedyna blokada kontraktowa
  transakcji zatwierdzenia, jej usunięcie zamienia legalne równoległe zatwierdzenie z `409` na
  nieobsłużony `500` — naprawione poprawką docstringu + 2 nowe testy współbieżności); **R-03**
  (martwy kod `_open_scenario` zbudowany na `uuid.UUID(int=0)`, mina na przyszłość — usunięty);
  **R-05** (niska, dwa zdania w docstringu twierdziły własność silniejszą niż zmierzoną —
  odnotowane, niekrytyczne). **Zaakceptowane, nie naprawiane:** B-01/B-02 security-auditor (nazwa
  `absence_type` jako wolny tekst trafiający do niemodyfikowalnej migawki; `headcount=1`
  reidentyfikuje osobę przez nieobecność) — zamknięte jako nazwane ryzyko z warunkiem ponownego
  otwarcia, ADR-0005 aneks 2026-09-22 pkt 10-11. Zob. `docs/architecture/capabilities.md`.

- [x] **SC-3-03** — Wprowadź roczny budżet urlopowy jako konfigurowalną własność pary (kalendarz,
  typ zaangażowania); zbieg z ręcznymi wpisami nieobecności typu ustawowego regułą `max`; zamroź
  budżet razem z kalendarzem w migawce zatwierdzenia (wydzielone z Issue #7/SC-3-02 na polecenie
  właściciela produktu — Issue #54).
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-09 (analyst, 2026-09-22, rewizja 2,
  finalna): (1) budżet w pełnych dniach, przedział obowiązywania zawsze domknięty (`CHECK
  effective_to IS NOT NULL`, odstępstwo nazwane od ADR-0008), kluczem `(calendar_id,
  engagement_type_id)`, nakładające się okna odrzuca baza, także w wyścigu dwóch połączeń; (2) brak
  wiersza dla krotki jest nazwanym stanem, nigdy `0`, nigdy wyjątek; (3) budżet redukuje
  `derived_capacity_hours` przez równomierną prorację, jeden punkt zaokrąglenia, okno wyrównane do
  granic miesiąca; (4) zbieg z wpisami ręcznymi typu flagowanego `is_statutory_leave` (dokładnie
  jeden taki wiersz, indeks częściowy) daje `max(budżet, suma ręcznych)` na całym oknie, z
  zachowaniem rozkładu miesięcznego; (5) okno przyszłe nie rusza roku bieżącego, luka między oknami
  = brak budżetu; (6) zatwierdzenie zamraża surowy budżet w dniach + okno + flagę typu RAZEM z
  kalendarzem (nie osobno) jako wartości; (7) odmowa dostępu jest odmową (`403` bez `CATALOG_READ`,
  pełna liczba bez `PERSONNEL_COSTS_READ` — budżet NIE jest daną kosztową, `404` nigdy `403` dla
  scenariusza spoza zasięgu).
  **Decyzje bramki 1 (2026-09-22):** Story rozdzielona na dwie (Q-1: budżet urlopowy jako zakres
  bez podstawy w `Requirements_EN.md`, pochodzący z bezpośredniego polecenia PO — datowany aneks do
  wymagań, nie odkrycie w zbieraniu wymagań); klucz budżetu `(kalendarz/lokalizacja, typ
  zaangażowania)`, oba `NOT NULL`, bez pułapki `NULL = NULL` i bez aparatu sentinela z SC-2-03
  (Q-2); brak wiersza budżetu = nazwany stan, nigdy cichy `0` (Q-3); budżet NIE jest daną kosztową
  w sensie ADR-0005 — bramkowany zwykłym `CATALOG_READ`, nie `PERSONNEL_COSTS_READ` (Q-4); zbieg
  budżetu z ręcznym wpisem tego samego typu w tym samym okresie: `max(budżet, suma ręcznych)`,
  ciągłe, ręczny wpis uszczegóławia budżet zamiast się do niego dokładać (P-4); okno budżetu zawsze
  domknięte, `CHECK effective_to IS NOT NULL` jako wąskie, nazwane odstępstwo od wzorca ADR-0008
  (pytanie architekta, aneks pkt 10b); `adr-deviation` zdjęta — wariant przyjęty nie dotyka wymiaru
  kraju.
  **Twarda zależność:** SC-3-03 zablokowane na SC-3-02 do czasu jego scalenia (kryteria K-03/K-04/
  K-07/K-08 konsumują mechanizmy kalendarza i migawki, których wcześniej nie było) — zdjęta po
  merge PR #55/#56.
  **Out of scope (explicit):** poprawność liczby budżetu wobec prawa jakiegokolwiek konkretnego
  kraju — zaimplementowano jedną regułę (roczny budżet w dniach, proracja miesięczna), nie że jest
  jedyna słuszna; jakakolwiek kwota (brak kalkulacji kosztu/przychodu w repo); osiągalność ścieżki
  zapisu w produkcji (brak endpointu tworzenia scenariusza, F-02, Issue #4, i zapisu
  `absence_type`); ekran (osobne zadanie frontend); współbieżna edycja tego samego budżetu przez
  dwóch administratorów katalogu jednocześnie poza parą kalendarz/typ (tę chroni S-01); pozycja
  krótsza niż okno budżetu (proracja per miesiąc obsługuje to poprawnie z konstrukcji, nikt tego
  nie testuje wprost).
  **Fundament nieudowodniony:** K-05 był najsłabszym fundamentem w zestawie — mechanizm `max()` nie
  istniał wcześniej nigdzie w repo, spełnienie tego kryterium go tworzy. K-03/K-04/K-07/K-08 stoją
  na fundamencie SC-3-02, w chwili analizy scalonym lecz jeszcze niezarejestrowanym (bramka 3
  tamtego zadania otwarta) — ryzyko przeniesione, zamknięte merge PR #56. Podstawa: Issue #54,
  `Wymagania/Requirements_EN.md` §4 F-05 (aneks datowany 2026-09-22 dla wymiaru budżetu/kraju),
  `docs/architecture/decisions/ADR-0004-wersjonowanie-kalkulacji.md` (aneks SC-3-03),
  `ADR-0005-model-dostepu.md` (aneks SC-3-03), `ADR-0008-przedzialy-obowiazywania.md` (aneks
  SC-3-03, pkt 10b — okno bezterminowe).
  **Done 2026-09-23:** PR #57 (scalone `45731b3`). Dowód: `backend/tests/test_absence_budget_schema_constraints.py`,
  `test_absence_budget_capacity.py`, `test_catalog_absence_budgets.py`, `test_absence_budget_access.py`,
  `test_scenario_approval_snapshot.py`, `test_scenario_approval.py` — 473 testy backendowe zielono
  (było 469), 153 frontendowe bez zmian. Cztery rundy weryfikacji (Invariant Guardian, reviewer) +
  poprawki: **NOWY R-01** (reviewer, Wysoka — typ ustawowy w migawce kopiowany tylko przez
  zabookowane nieobecności, scenariusz z zastosowanym budżetem ale bez żadnej zabookowanej
  nieobecności dawał migawkę bit-w-bit identyczną z brakiem flagowanego typu w katalogu — naprawione
  drugą gałęzią złączenia z zamrożonym budżetem, niezależnie od zabookowania); **S-01** (invariant-
  guardian, Średnia — cztery osobne instrukcje zapisu migawki czytały katalog w różnych momentach
  pod `READ COMMITTED`, zapis do katalogu między nimi mógł zamrozić niespójną parę kalendarz↔budżety
  albo budżet↔typ ustawowy; wariant `REPEATABLE READ` rozważony i **odrzucony** po pomiarze
  empirycznym na żywym PostgreSQL — gorszy defekt, migawka RR powstaje przed blokadą wiersza
  scenariusza; naprawione złożeniem czterech kopiarek w jedną instrukcję SQL pod blokadą, R-01
  reviewera na tej poprawce (luka w teście dla nietypowego porządku rozbicia) domknięta wyczerpującym
  dowodem na wszystkich 75 możliwych podziałach). **Zaakceptowane, nie naprawiane:** R-03 reviewera
  (Niska — predykat złączenia z budżetem zduplikowany w dwóch miejscach, bez testu na rozjazd między
  nimi); S-02/S-03 invariant-guardiana (Niskie — dryf dokumentacji ADR-0004 pkt 5, dowód mutacyjny
  75-podziałowy zadeklarowany w komentarzu testu zamiast w logu mutacji, uzupełniony teraz w
  `docs/architecture/capabilities.md`). Zob. `docs/architecture/capabilities.md`.

- [x] **SC-3-04** — Ekran Staffing plan: siatka obsady scenariusza z miesięczną alokacją, wyliczoną
  pojemnością i budżetem urlopowym (F-04, F-05, część, frontend, odczyt) — konsument API
  dostarczonego przez SC-3-01/SC-3-02/SC-3-03 (`GET .../staffing-positions`), Issue #135.
  *Done when:* `frontend/src` (vitest) dowodzi kryteriów K-01..K-07 (analyst, 2026-09-26), każde z
  zarejestrowanym i wykonanym przebiegiem mutacyjnym: headcount/okres/trzy godziny wejściowe przez
  wspólny formatter (nowy `lib/hours.ts`, osobny od `money.ts` — ADR-0002 aneks 2026-09-26);
  `derived_capacity`/`absence_budget` jako liczba+nazwany stan, nigdy ciche `0.00`; nieobecności (4
  pola) z `absence_type_id` rozwiązanym przez katalog; pięć identyfikatorów katalogowych
  rozwiązanych do nazw niezależnym odczytem `CATALOG_READ`; `403`/`404` scalone w jeden stan
  "niedostępne"; zero elementów interaktywnych (ekran czysto odczytowy); zamontowane w karcie
  scenariusza w `ProjectListScreen`, obok `ScenarioCommercialTermsSection`/`ScenarioResultsSection`.
  **Decyzje bramki 1 (2026-09-26):** godziny w osobnym module `lib/hours.ts`, nie rozszerzeniu
  `money.ts`; `403`/`404` (siatki i katalogu) scalone w jeden stan; ryzyko pseudonimizacji
  (`headcount=1` + daty urlopu identyfikują jedną osobę, pierwszy raz widoczne człowiekowi na
  ekranie) przyjęte świadomie bez countermeasure — ADR-0005 aneks 2026-09-26, warunek ponownego
  otwarcia z aneksów SC-3-02 pkt 11/SC-3-03 pkt 7 pozostaje NIE domknięty; brak pełnego rozkładu
  źródła (`derived_capacity_source`/`absence_budget_source`) w MVP.
  **Out of scope (explicit):** edycja godzin, dodawanie pozycji, edycja `cost_basis`, zapis
  nieobecności — osobne zadania frontendowe (inna warstwa ryzyka: token współbieżności ADR-0007,
  strażnik `approved` ADR-0004, koniunkcja SC-1-08); pełny rozkład źródła pojemności/budżetu;
  router/globalny stan wybranego projektu-scenariusza (odłożone jak przy SC-4-06/SC-7-02);
  mobile/responsive (NF-09).
  **Done 2026-09-27:** PR #138 (scalone). Dowód:
  `frontend/src/features/projects/StaffingPlanSection.test.tsx` (K-01..K-07 + R-01),
  `frontend/src/lib/hours.test.ts`, `frontend/src/styles/tokens.test.ts` (R-02) — 350 testów
  frontendowych zielono. Dwie rundy weryfikacji (Invariant Guardian, reviewer) + poprawki: **R-01**
  (reviewer, Medium — wachlarz 5 identycznych odczytów katalogu per karta scenariusza, bez cache,
  N scenariuszy × 5 żądań do zaledwie 5 adresów; naprawione request-coalescing cache w `client.ts`,
  deduplikacja współbieżnych żądań, nie długożyjący cache danych — obejmuje też istniejącego
  konsumenta `CatalogScreen`); **R-02** (reviewer, Low/Medium — brak traktowania skali NF-03, do
  ~7200 wierszy DOM; częściowo złagodzone przez `content-visibility:auto` — adresuje koszt
  layout/paint, NIE koszt React mount/reconciliation, nazwane wprost w kodzie). **Recorded
  exception (human, 2026-09-27):** pełne rozwiązanie R-02 wymaga paginacji API (zbudowanej jako
  SC-3-05, PR #137) + osobnego, przyszłego zadania frontendowego konsumującego strony — numer do
  nadania po zapriorytetyzowaniu; do tego czasu ekran konsumuje pełną listę (bez parametrów
  paginacji, zachowanie zachowane przez SC-3-05 jako "jak dziś"), co jest dziś wystarczające (brak
  ścieżki tworzenia pozycji w produkcji poza fixture — Issue #4). Zob.
  `docs/architecture/capabilities.md`.

- [x] **SC-3-05** — Paginuj `GET /projects/{project_id}/scenarios/{scenario_id}/staffing-positions`
  (`limit`/`offset`/`total`, F-04, backend), dodaj cap na nieobecności per pozycja (Issue #136).
  Pierwsze zadanie formalizujące wzorzec paginacji dla całego API — nowy `ADR-0017`.
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-08 (analyst, 2026-09-26) przeciw
  prawdziwemu PostgreSQL, każde z zarejestrowanym i wykonanym przebiegiem mutacyjnym: brak
  parametrów = cała lista (K-01); jawny rozmiar strony ogranicza + niesie `total` (K-02);
  sortowanie `(start_date, id)` to porządek totalny, remis nie gubi/dubluje wiersza (K-03); suma
  stron = pełna lista (K-04); paginacja nie staje się nowym kanałem potwierdzającym zasięg (K-05);
  parametr poza granicą → `422`, nigdy ciche przycinanie (K-06); cap absences
  (`MAX_ABSENCES_PER_POSITION=60`) odrzucony w tej samej strażonej instrukcji co `INSERT`, nie
  cichy (K-07); `404` (zasięg) zawsze przed `422` (paginacja), strukturalnie — `limit`/`offset`
  przyjmowane jako string, parsowane ręcznie po sprawdzeniu zasięgu (K-08).
  **Decyzje bramki 1 (2026-09-26):** nowy `ADR-0017` (wzorzec prospektywny, nie retroaktywny —
  `/catalog/rates` zostaje niezmieniony, `GET /projects`/SC-1-05 dziedziczy wzorzec gdy zostanie
  podjęte); `limit`/`offset`/`total` (nie kursor); stałe nazwane per zasób
  (`DEFAULT_STAFFING_POSITION_LIST_LIMIT` itd.); `absences` też w zakresie (cap zapisu, nie
  paginacja odczytu drugiego poziomu).
  **Out of scope (explicit):** konsumpcja stron przez frontend SC-3-04 — osobne, przyszłe zadanie;
  paginacja `allocations` (drugi poziom zagnieżdżenia) — `MAX_ALLOCATION_MONTHS=60` już ogranicza
  jedno żądanie tworzące, brak ścieżki dodawania kolejnych miesięcy poza tworzeniem pozycji;
  zastosowanie wzorca ADR-0017 do innych list API — osobne zadania, każde z własnym numerem;
  wydajność/czas odpowiedzi (NF-03) — niezmierzone.
  **Done 2026-09-27:** PR #137 (scalone). Dowód: `backend/tests/test_staffing_pagination.py`
  (K-01..K-06, K-08), `backend/tests/test_staffing_absence_cap.py` (K-07),
  `backend/tests/test_staffing_schema_constraints.py::test_the_model_and_the_migration_agree_on_the_staffing_position_page_index`
  — 1015 testów backendowych zielono (było 1010), migracja
  `backend/migrations/versions/f1a2c4b6d8e0_index_staffing_position_for_paging.py` (upgrade/
  downgrade zweryfikowane). Dwie rundy weryfikacji (Invariant Guardian, reviewer, security-auditor)
  + poprawki: **S-01** (invariant-guardian, STOP — `limit`/`offset` niesparsowalne jako `int` (np.
  `"abc"`) omijały `scenario_in_scope`, dając `422` przed `404`, wbrew własnej deklaracji ADR-0017
  pkt 8; naprawione przyjmowaniem parametrów jako `str`, parsowaniem ręcznym po sprawdzeniu
  zasięgu; potwierdzone empirycznie przez `TestClient`, w tym seria przypadków brzegowych); **R-01**
  (reviewer, Medium — kształt błędu `422` jako string, łamiący istniejący kontrakt reszty API
  (lista `{type,loc,msg,input}`); naprawione, potwierdzone bit-identycznym porównaniem z natywną
  odpowiedzią Pydantic); **R-02** (reviewer, Low — docstring obiecywał plan zapytania z indeksem,
  którego nie było; naprawione migracją z indeksem złożonym `(scenario_id, start_date, id)`,
  potwierdzone testem planu zapytania (`EXPLAIN`) i testem dryfu model/migracja/baza). Zob.
  `docs/architecture/capabilities.md`.

- [x] **SC-3-06** — Ekran Working calendars: kalendarze robocze (wzorzec tygodnia, dni wyjątkowe) i
  budżet urlopowy (odczyt + formularz dodania) (F-05, frontend), Issue #140 — konsument API
  SC-3-02/SC-3-03 (`GET /catalog/working-calendars`, `GET`/`POST /catalog/absence-budgets`).
  Odwrotna strona monety SC-3-04: tam liczba+stan per pozycja obsady, tu organizacyjne dane
  źródłowe (sam kalendarz, sam budżet) niezależnie od żadnego scenariusza.
  *Done when:* `frontend/src` (vitest) dowodzi kryteriów K-01..K-06 (analyst, 2026-09-27), każde z
  zarejestrowanym i wykonanym przebiegiem mutacyjnym: `week_pattern` interpretowany po dniach
  tygodnia (poniedziałek pierwszy, nigdy surowy string); `days[].kind` rozróżnialne tekstowo;
  `statutory_leave_state`/`generates_cost`/`generates_revenue` trójwartościowe (bool/"n/a", nigdy
  ciche false); formularz budżetu — jeden `POST`, świeży odczyt po zapisie (nie echo), brak
  klienckiej walidacji okna/nakładania duplikującej serwer; pole `source` nigdy nie sugeruje
  autora. Zamontowane jako nowy `ScreenKey` `"working-calendars"` na `RAIL_WORKSPACE`
  (`AppShell.tsx`), osiągalne z działającej aplikacji.
  **Decyzje bramki 1 (2026-09-27):** zakres — Opcja B (odczyt + zapis budżetu, nie tylko odczyt —
  `POST` nie ma warstwy ryzyka tokenu współbieżności ani strażnika `approved`, identyczny wzorzec
  co "add dictionary entry" z `CatalogScreen`); montaż — nowy `ScreenKey` (mechanicznie jak
  `roles-and-rates`), nie sekcja istniejącego `CatalogScreen`; render wzorca tygodnia — biblioteka
  kalendarza (`react-day-picker@10.0.1`, nie lista/tabela — koszt: nowa zależność, wymagany
  przegląd security-auditora); `budget_days` jako czwarta nazwana klasa wartości dziesiętnej —
  nowy osobny moduł `lib/days.ts`, nigdy `formatHoursString`/rozszerzenie `money.ts`.
  **Out of scope (explicit):** edycja/dodawanie/usuwanie kalendarza lub budżetu — brak endpointów
  `PATCH`/`DELETE`; osobna sekcja typów nieobecności — renderowane wyłącznie pośrednio przez
  `statutory_leave` osadzony w odpowiedzi budżetu; filtrowanie/paginacja — oba `GET` zwracają
  całość (endpointy sprzed `ADR-0017`, prospektywnego wzorca — nie naruszenie); mobile/responsive;
  router jako mechanizm ogólny — rozszerzenie unii `ScreenKey`, nie nowy mechanizm nawigacji.
  **Done 2026-09-27:** PR #141 (scalone). Dowód: `frontend/src/features/catalog/WorkingCalendarsScreen.test.tsx`,
  `WorkingCalendarsWrite.test.tsx`, `frontend/src/lib/days.test.ts`,
  `frontend/src/api/client.test.ts` (parowanie kształtu `isAbsenceBudgetShape`),
  `frontend/src/styles/tokens.test.ts` — 375 testów frontendowych zielono (było 368 po pierwszej
  implementacji, 350 przed nią). Trzy rundy weryfikacji (Invariant Guardian PASS, security-auditor
  PASS — przegląd supply chain `react-day-picker`/`date-fns`, `pnpm audit` czysty, reviewer PASS
  WITH RESERVATIONS) + poprawki: **QA** (luka realna — reguła parowania `statutory_leave_state`
  na granicy `client.ts` nie miała testu na tym poziomie, tylko pośrednio przez komponent z
  fixture'ami już poprawnie sparowanymi; domknięte nowym testem, mutacja killed, test komponentu
  pod tą samą mutacją pozostał zielony — potwierdza realność luki); **R-01** (reviewer, Medium —
  brak testu abort/unmount dla `readScreen` mimo deklarowanego mirror `CatalogScreen`; naprawione
  dwoma testami mirror `CatalogScreen` R-01/R-06, RESOLVED); **R-02** (reviewer, Low — N instancji
  `react-day-picker` bez `content-visibility`; naprawione mirror SC-3-04 R-02, RESOLVED). Zob.
  `docs/architecture/capabilities.md`.

- [x] **SC-3-07** — Convert an FTE allocation into hours from the working calendar of the position
  (F-04, F-05), Issue #163. Prerequisite for the FTE cost basis (SC-5-04, Issue #79) and
  for the day/month cost rates (Issue #80).
  *Done when:* `backend/tests` prove: 1 FTE -> hours in a calendar month comes from the position's
  calendar (`standard_hours_per_day` x working days, a calendar with a non-8 day; not a constant, not
  `scenarios.full_time_hours_per_week`); varies with working days at unchanged hours per day;
  unchanged by absences (gross, Q3 = A); a location without a calendar and a month without
  working days each yield a named state, never `0`; every figure names its basis; Decimal only with
  the single rounding rule of ADR-0002 (addendum 2026-09-29); existing hour-based allocation,
  `derived_capacity_hours` and SC-5-01 cost results are unchanged; an approved scenario reads the
  snapshot calendar. Each criterion with a registered, executed mutation run.
  **Gate 1 decisions (2026-09-29):** Q1 = A calendar source; Q2 = B FTE derived from hours only;
  Q3 = A gross of absences; Q4 = A `scenarios.full_time_hours_per_week` untouched (G-1 stays
  named); Q5 = A calendar month; Q6 = A this task first, #79 and #80 wait; Q7 = A the holiday import
  is a separate task (SC-3-08).
  **Out of scope (explicit):** FTE cost basis (SC-5-04, #79); daily/monthly cost rates (#80); FTE as
  a charge base for additional costs (F-08) and the planned-FTE report metric (F-10); FTE as an
  allocation input (named follow-up, F-04 point 1 stays partly open); partial working days;
  versioned day length; FTE screen; API field or endpoint (proposed: domain function only, see
  ADR-0008 addendum 2026-09-29).
  **Basis:** ADR-0008 addendum 2026-09-29 (SC-3-07) and 2026-09-22 (SC-3-02), ADR-0003 pt 6-7,
  ADR-0004, ADR-0002 addendum 2026-09-29; `docs/PLAN.md` SC-3-01, SC-3-02, SC-5-01.
  **Done 2026-09-29:** PR #168 (merged). Proof: `backend/tests/test_fte_hours.py` - hours of 1 FTE from the position's calendar
  (`test_a_k01_hours_of_one_fte_come_from_the_calendar_not_a_constant`), working days and leap February
  (`test_a_k02_*`), gross of absences and no absence/headcount/scenario input (`test_a_k03_*`), named states
  `no_calendar` / `no_working_days` with `"n/a"` (`test_a_k04_*`, `test_a_k04b_*`), the basis named on every figure
  (`test_a_k05_*`), one rounding rule and the exact `fte_exact` way back (`test_a_k06_*`), input validation, unit and
  state pairing, month normalisation (`test_r02_*`, `test_r03_*`, `test_r05_*`, `test_r06_*`), existing results
  unchanged and the module imported by no revenue or hours module (`test_a_k07_*`). 45 tests; full backend suite 1252
  green, `ruff` clean. Verification: QA PROOF HOLDS (two passes, ~110 mutation runs; 6 realistic mutants survived the
  first 12 tests and were killed by added ones), Invariant Guardian PASS, Reviewer PASS after R-01..R-07 were fixed;
  security-auditor not applicable. Domain function only: no endpoint, migration, model or dependency.
  **Not proven (deferred):** FTE-6 - an approved scenario reading the snapshot calendar - moves to the first consumer
  (#79 SC-5-04 or #80, which must call `frozen_basis_by_location` and carry that test); FTE-7 - revenue unmoved by
  `standard_hours_per_day` - has no honest fixture (no revenue function takes a calendar); independence from
  `scenarios.full_time_hours_per_week` holds by construction (signature), not by a test that could have read it; partial
  months, FTE as an allocation input and any API surface are not covered. See `docs/architecture/capabilities.md`.

- [x] **SC-3-08** — Import public holidays from Nager.Date into a working calendar's exceptional days,
  with provenance (F-05, F-02), Issue #165. A new instruction, not a documented requirement
  (F-05 addendum 2026-09-29).
  *Done when:* `backend/tests` prove: after an operator-run import for (calendar, country, year)
  working days reflect the imported holidays with the network disabled and no HTTP client on the
  calculation path; only `Public` and global entries become `NON_WORKING` days, the rest is counted
  in a partitioned import report; a manual day is never overwritten, deleted or duplicated; every
  imported day carries source, name, country and year and older rows read `manual` after upgrade;
  insertion is idempotent and race-safe in the database; any failure or invalid body writes zero
  rows; the client obeys the egress policy of ADR-0020 and CI never calls the live API; an import
  changes no value of an approved scenario or any `approved_snapshot_*` row; provenance is copied
  into the snapshot with no default on the snapshot table. Each criterion with a registered,
  executed mutation run.
  **Gate 1 decisions (2026-09-29):** Q7 = A separate task; Q8 = A persisted data, calculations read
  only the DB; Q9 = C operator-run script or service function; Q10 = B country stored on the day
  row only; Q11 = A regional holidays skipped and reported; Q12 = A only `Public`; Q13 = A
  insert-if-absent; Q14 = A strict validation; Q15 = A `httpx` to runtime dependencies; Q16 = A
  hosted API, on the condition of private, non-commercial use (the provider requires active
  sponsorship for commercial use); Q17 = A provenance on the day row and in the snapshot; Q18 = A
  one year per call.
  **Out of scope (explicit):** scheduled or automatic sync; HTTP trigger and UI; regional holidays
  and subdivisions; refresh or deletion of imported rows; bulk all-country import and location
  assignment; `NextPublicHolidays`, `IsTodayPublicHoliday`, `LongWeekend`, `CountryInfo`; in-lieu
  and half days; self-hosted Nager.Date.
  **Basis:** ADR-0020 (draft), ADR-0004 addendum 2026-09-29, ADR-0006, ADR-0008, F-05 addendum
  2026-09-29; Nager.Date Terms of Service (2023-09-15).
  **Done 2026-10-02:** [PR #214](https://github.com/TanerCRB/StafffingCalculator/pull/214) implements
  the import; [PR #218](https://github.com/TanerCRB/StafffingCalculator/pull/218) guards the
  calculation path against HTTP; [PR #224](https://github.com/TanerCRB/StafffingCalculator/pull/224)
  adds the legacy-migration, fixed-HTTPS, and no-HTTP-route regression tests. Proof: the test set
  and mutation records are listed in the [SC-3-08 capability and mutation registry](architecture/capabilities.md#capabilities)
  and [QA report on Issue #165](https://github.com/TanerCRB/StafffingCalculator/issues/165#issuecomment-5954378260).

- [x] **SC-3-09** — Edit staffing positions and monthly allocations through the UI (Issue #234).
  *Done when:* Backend and frontend tests prove authorized position create/edit and monthly allocation add/edit with read-after-write; callers without `STAFFING_WRITE` or project scope cannot change data; invalid supported hours or period, stale position and allocation markers, and approved-scenario writes are refused without changing persisted values; the new UI workflows and responses expose no personnel-cost fields.
  **Out of scope (explicit):** FTE-based inputs; new calculation formulas; catalogue maintenance; working-calendar editing and absence management; named-person assignments pending separate privacy assessment.
  **Basis:** F-04; `docs/PLAN.md` SC-3-01 and SC-3-04; Issue #234; ADR-0001, ADR-0004, ADR-0005, ADR-0007, and ADR-0009 (Draft — pending approval); docs/architecture/capabilities.md.
  **Done 2026-10-05:** [PR #252](https://github.com/TanerCRB/StafffingCalculator/pull/252) merged. Proof: `backend/tests/test_staffing_position_edit.py` (K-01–K-06), `frontend/src/features/projects/StaffingPlanSection.test.tsx` (position/allocation writes, marker handling, unknown catalogue dimensions, and cost/person/FTE-free requests); mutation runs are recorded in [PR #252](https://github.com/TanerCRB/StafffingCalculator/pull/252) and the SC-3-09 mutation rows in `docs/architecture/capabilities.md`.

- [x] **SC-2-05** — Dostosuj wygląd ekranu Roles & rates (katalog) do makiety UI-15 i rozbuduj rail
  nawigacji `AppShell` do pełnej listy 11 wpisów z tej samej makiety, bez zmiany zachowania ani
  kontraktów API (Issue #59).
  *Done when:* `frontend/src` (vitest) zielono, liczba testów ≥ punkt odgałęzienia, żaden `it`/
  `describe` usunięty, każda zmieniona asercja niesie to samo lub silniejsze twierdzenie (analyst,
  2026-09-23): (1) powierzchnia zmiany ograniczona do `features/catalog/` i arkuszy stylów, zero
  dotknięcia `backend/`, `api/`, `lib/`, `features/projects/`; (2) drzewo dostępności i kolejność
  czytania ekranu katalogu identyczne ze stanem sprzed zmiany, poza dwoma nazwanymi wyjątkami; (3)
  słowa stanu ("Restricted", "Internal", liczniki, komunikaty) identyczne co do znaku; (4) kontrast
  koloru ≥4.5:1 domknięty dla KAŻDEGO arkusza dotkniętego zmianą, nie tylko ekranu katalogu, oraz
  dla koloru ikon; (5) siedem właściwości wizualnych z makiety (siatka, wyrównanie, tło etykiety,
  przyciski/ikony, hierarchia rozmiarów, linie, kolejność akcji formularza) sprawdzone na
  elementach DOM po roli i nazwie; (6) rail nawigacji pokazuje 11 wpisów z makiety, 9 nieistniejących
  ekranów jako `aria-disabled` (konwencja F-13) bez cichego zamontowania złego ekranu — `ScreenKey`
  zamknięty do realnie zbudowanych ekranów.
  **Decyzje bramki 1 (2026-09-23):** zakres rozszerzony w trakcie z samego restyle'u katalogu na
  rozbudowę `AppShell` (na polecenie właściciela produktu); cały katalog `Wymagania/` (bez zip)
  dołączony do repozytorium jako referencja, nie specyfikacja; ikony przez `@tabler/icons-react`
  (ADR-0011), bez literału koloru; kolor przycisków primary zostaje pomarańczowy z produktu, nie
  niebieski z makiety (różnica świadomie zaakceptowana); kolejność Cancel/Save w DOM zmieniona na
  zgodną z makietą (jedyny nazwany wyjątek od "zero zmiany zachowania" poza nową zależnością);
  nagłówek grupy railu "Project" — NIE "Commerce platform" z makiety (to nazwa przykładowego
  projektu w prototypie, nie nazwa sekcji — kopiowanie dosłowne byłoby fałszywymi danymi w UI);
  topbar bez awatara/roli/waluty z makiety (brak pokrycia w danych — placeholder identity, brak
  przewalutowania); 3 istniejące testy przypinające dokładną listę railu przepisane za zgodą
  (twierdzenie wzmocnione, nie osłabione).
  **Out of scope (explicit):** router i URL dla ekranów sekcji "Project" z railu; kontekst
  wybranego projektu/scenariusza w topbarze/grupie railu; zgodność co do piksela z makietą i wygląd
  mobile (`@media` świadomie zablokowane testem-strażnikiem, precedens SC-2-02); kontrast stanów
  `:hover`/`:focus` poza regułami z samym tłem; reguła marki "pomarańczowy nigdy na light steel"
  (naruszenie przedistniejące, niepogorszone tym zadaniem); kolor ikony przekazany przez nazwaną
  stałą JS zamiast literału (resztkowa luka w skanie strażnika, ADR-0011).
  **Fundament:** restyle stoi na fundamencie SC-2-01..04 (katalog, w pełni scalonym i
  zarejestrowanym) i SC-1-09 (granica błędu, obejmuje też rozbudowany rail bez zmian). Podstawa:
  Issue #59, `Wymagania/prototyp/screens/15-catalog*.png`,
  `docs/architecture/decisions/ADR-0011-zasoby-wizualne-frontendu.md`.
  **Done 2026-09-23:** PR #60 (scalone `0b32a6a`). Dowód: `frontend/src/features/catalog/
  CatalogScreenStructure.test.tsx`, `CatalogScreenAppearance.test.tsx`, `frontend/src/styles/
  tokens.test.ts`, `frontend/src/shell/AppShell.test.tsx`, `ScreenErrorBoundary.test.tsx`,
  `screenCrashContainment.test.tsx` — 173 testy frontendowe zielono (było 153), lint i build
  czyste. Dwie rundy weryfikacji (Invariant Guardian, reviewer) + poprawki przed commitem: ADR-0011
  sprzeczny sam ze sobą (naprawiony), brak w indeksie ADR (dodany), 3 martwe wpisy kontrastu w
  `declaredPairs` (usunięte). **Zaakceptowane, nie naprawiane:** R-01 reviewera (Niska — silnik
  dopasowania CSS w teście K-30 nie widzi skrótów CSS/dziedziczenia/`!important`, potencjalna
  luka, dziś żaden arkusz tych konstrukcji nie używa); S-02 invariant-guardiana (Niska — skan koloru
  ikon nie łapie koloru przez nazwaną stałą JS, nazwany otwarty dług w ADR-0011); S-04
  invariant-guardiana (Niska — ~200 linii szumu w `pnpm-lock.yaml`, dryf metadanych rejestru npm,
  zweryfikowany jako nieszkodliwy — `--frozen-lockfile` przechodzi). Zob.
  `docs/architecture/capabilities.md`.

- [x] **SC-2-06** — Wprowadź rejestr osób nazwanych i opcjonalne przypisanie osoby do pozycji obsady
  (F-03 pkt 3, backend; bez stawki indywidualnej, bez ekranu). Osoba nazwana to osobny rekord, nie
  konto użytkownika (decyzja P-2 = a, 2026-09-27) — zadanie nie czeka na ADR uwierzytelniania.
  Blocked by: ocena wpływu na dane osobowe + aneks ADR-0005 (Architekt, przed bramką 1). Kryteria
  i decyzje bramki 1 w Issue #31.
  *Done when:* wiersz osoby ma dokładnie zatwierdzony zbiór kolumn (równość zbioru); rejestr odmawia
  domyślnie (`PEOPLE_READ`/`PEOPLE_WRITE`, placeholder ich nie nadaje); pozycja z osobą jest
  nieodróżnialna od anonimowej dla wołającego bez odczytu osób, na każdej ścieżce zwracającej
  pozycję; przypisanie nie zmienia kosztu, przychodu ani pojemności; przypisanie tylko do pozycji z
  `headcount = 1`; przypisanie w scenariuszu `approved` odmawiane w warstwie danych (odmowa +
  wyścig), spoza zasięgu → `404`; duplikat zachowuje odwołanie do tej samej osoby; sprostowanie
  imienia widoczne przy scenariuszu `approved` bez zmiany migawki; nieudany zapis nie loguje imienia
  (NF-11); żaden istniejący test niezmieniony z wyjątkiem ponownego uzbrojenia kanarków (K-10)
  wyłącznie w `test_catalog_access.py`, `test_absence_budget_access.py`,
  `test_additional_cost_access.py`, `test_staffing_copy.py`, `test_staffing_schema_constraints.py`
  — dopisanie elementu, równość zostaje równością. Kryteria K-xx ustala Analityk.
  **Out of scope (explicit):** stawka indywidualna sprzedażowa i kosztowa (osobna Story po ADR-0013
  Accepted; kosztowa wymaga aneksu do wymagań); własna lokalizacja osoby; ekran (osobna Story FE);
  zapis z UI (ADR-0009); usuwanie/anonimizacja i retencja (osobna Story, scalona przed pierwszymi
  rzeczywistymi danymi); ekspozycja nieobecności/kosztów dodatkowych pozycji z osobą (osobna Story,
  scalona przed ekranem osób); powiązanie z kontem użytkownika (SC-1-12); nieobecności na osobie
  (blok 3); nadalokacja między pozycjami/projektami (§6); osoby poddostawcy (F-03 aneks); integracja
  HR (§6).
  **Done 2026-09-28:** PR #153 (scalone `5aead82`). Dowód: `backend/tests/test_people_register.py`
  (K-01 `test_k_01_the_person_row_has_exactly_the_approved_column_set`, K-02
  `test_k_02_the_person_register_denies_a_caller_holding_every_other_permission`, K-08
  `test_k_08_correcting_a_persons_name_is_visible_on_an_approved_scenario_without_touching_its_snapshot`,
  K-09 `test_k_09_a_failed_person_save_does_not_log_the_person_name` z kontrastem
  `test_k_09_the_contrast_the_driver_error_itself_quotes_the_name`, PD-K8, PD-K9),
  `backend/tests/test_staffing_person_assignment.py` (K-03..K-07, A5-31-7, PD-K7, A7-31-1..3),
  `backend/tests/test_people_qa_contrast.py`; migracja
  `backend/migrations/versions/c4d7e2a9b1f6_create_person_register_and_person_on_staffing_position.py`;
  decyzje: ADR-0019 + aneksy 2026-09-27/2026-09-28 do ADR-0004/0005/0007/0019 (bramka 1 i decyzje
  D-1..D-5 po weryfikacji, Issue #31). 1089 testów backendu zielono po scaleniu z `main`, CI zielone.
  QA: dwie rundy, 48 mutacji (`SC-2-06-M01..M39`) — przeżyły tylko M18 (nieobserwowalna przez HTTP;
  K-09 dowiedzione na warstwie danych) i M26dr (równoważna po D-1); M26d z rundy 1 przeżyła zestaw
  developera i została domknięta testem QA. Invariant Guardian: PASS (obie rundy). Security-auditor:
  PASS (runda 2; B-01 z rundy 1 zamknięte decyzją D-4). Reviewer: zastrzeżenia rundy 1 zamknięte
  (D-1..D-3), runda 2 R-01/R-03 naprawione, **zaakceptowane, nienaprawione:** R-02 (Low — świeża
  pozycja ma `updated_at` = `person_assignment_updated_at`, więc klient mylący wartości tokenów nie
  zobaczy błędu na świeżych fixture'ach; kryterium przyszłej Story ekranu osób: test przypisania po
  edycji siatki). **Nie dowodzi:** gałąź pozytywna `PEOPLE_*` osiągalna tylko przez
  `dependency_overrides` (placeholder ich nie nadaje); dane rzeczywiste zakazane do spełnienia
  warunków (a)–(g) ADR-0019 pkt 8; `[[:space:]]` poza ASCII zależne od ctype bazy; `headcount > 1`
  przy osobie dowiedzione tylko w bazie. Zob. `docs/architecture/capabilities.md`.

- [x] **SC-1-10** — Rozstrzygaj założenia scenariusza z łańcucha organizacja → projekt →
  scenariusz ze wskazaniem źródła wartości (F-02), na dwóch reprezentatywnych polach: marża
  docelowa i próg przeciążenia alokacji; napraw wyścig współbieżności między zapisem pól grupy 2
  Projektu a zatwierdzeniem scenariusza (Issue #4).
  *Done when:* `backend/tests` dowodzą kryteriów K-01, K-02, K-04–K-09 (analyst, 2026-09-23, runda
  2 finalna): (1) scenariusz bez nadpisania dziedziczy wartość domyślną organizacji, źródło
  "organizacja"; brak wszędzie = nazwany stan "brak danej", nigdy `0`, nigdy wyjątek; (2)
  pierwszeństwo scenariusz→projekt→organizacja, `0` jest wartością rozstrzygniętą nie brakiem; (4)
  gotowość do zatwierdzenia (F-01) liczy wartość rozstrzygniętą, wartość dziedziczona liczy się
  jako dostarczona; (5) czytelnik migawki (pierwszy w repo) — zatwierdzony scenariusz czyta
  wyłącznie zamrożoną wartość organizacji, nigdy żywą tabelę, zmiana defaultu po zatwierdzeniu nic
  nie rusza (AC-04); (6) zatwierdzony w stanie "brak danej" zostaje w nim, nawet gdy wartość
  domyślna pojawi się później; (7) nadpisanie projektu przez istniejący `PATCH /projects`, pole
  grupy 2, `null` = powrót do dziedziczenia; (8) zapis dowolnego pola grupy 2 Projektu (istniejące
  `reporting_currency`/`delivery_period_*` ORAZ nowe) serializowany z zatwierdzeniem scenariusza
  tego projektu, dowiedzione testem wyścigu dwóch połączeń w obu kolejnościach; (9) próg jako %
  `derived_capacity_hours`, odmowa wartości `≤0` na wszystkich trzech poziomach, w bazie nie tylko
  w API.
  **Decyzje bramki 1 (2026-09-23):** zakres zawężony przez PO z całego F-02 (10 typów założeń) do
  mechanizmu na dwóch polach, reszta jako osobne, późniejsze zadania na tym samym wzorcu; P-A —
  migawka zamraża SUROWĄ wartość domyślną organizacji, nie wynik łańcucha z polem źródła (źródło
  wynika z obecności wiersza, wzorem stanu D z SC-3-03); P-B — SC-1-10 buduje pierwszego czytelnika
  migawki w całym repo (dotąd nikt jej nie czytał); P-C — naprawiony TERAZ wyścig
  projekt↔zatwierdzenie dla WSZYSTKICH pól grupy 2 (nie tylko nowych) jednym mechanizmem, zamiast
  nazwania jako ryzyko; P-D — migracja NIE zasiewa wiersza wartości domyślnych; Q-1 — "zapisana
  kalkulacja" (F-02) = "zatwierdzona" (AC-04), reguła 9 `agents/invariant-guardian.md`
  doprecyzowana; Q-2 — próg przeciążenia jako % `derived_capacity_hours` (świadomie pogłębia G-1 z
  SC-3-02, dwa źródła prawdy o tygodniu pracy); Q-3 — nadpisanie projektu przez ISTNIEJĄCY
  `PATCH /projects` (bezpieczne dopiero po naprawie P-C), nadpisanie scenariusza fixture-only; Q-4
  — poziom projektu zostaje w łańcuchu; Q-5 — wartość dziedziczona liczy się jako "dostarczona" dla
  gotowości (`test_project_list.py:142,207,239` — asercje BEZ zmian, bo brak zasiewu pod P-D
  zostawia rozstrzygniętą wartość jako "brak danej" w tych fixture'ach, zmienione tylko
  komentarze); G-1 — `null` w `PATCH` kasuje nadpisanie WYŁĄCZNIE dla tych dwóch pól; G-2 — dolna
  granica progu ściśle `>0`.
  **Nowa decyzja architektoniczna: ADR-0012** ("Założenia konfigurowalne: łańcuch organizacja →
  projekt → scenariusz i źródło wartości") — status Accepted, obejmuje wszystkie punkty P-A..P-D i
  Q-1..Q-5 powyżej jako trwały wzorzec dla każdego kolejnego założenia F-02.
  **Out of scope (explicit):** ścieżka zapisu nadpisania na poziomie SCENARIUSZA (fixture-only —
  brak endpointu tworzenia/edycji scenariusza, jak SC-3-01/SC-3-02); ścieżka zapisu wartości
  domyślnych ORGANIZACJI (fixture-only); jakikolwiek konsument progu przeciążenia (logika
  porównania z alokacją — świadomie odłożone, jak w SC-3-01); pozostałych 8 typów założeń F-02
  (kalendarz, waluta/kursy, fazy dostawy, eskalacja/rezerwy — każde osobna Story na tym samym
  wzorcu); poziom projektu NIE jest zamrożony w migawce (czytany na żywo, chroniony tylko
  strażnikiem zapisu K-08) — bezpośredni SQL z pominięciem `update_project` może go przesunąć,
  ryzyko nazwane w ADR-0012 pkt 7; ekran (osobne zadanie frontend).
  **Fundament:** dwa niezależne słowniki źródeł (`rate_source` z ADR-0006, nowy z ADR-0012)
  współistnieją świadomie, nieujednolicone. Wartości domyślne organizacji i nadpisanie scenariusza
  widoczne dla każdego wołającego z `PROJECT_READ` na jakikolwiek projekt-szkic — spójne z
  istniejącym zachowaniem `target_margin_percent`, przyjęte świadomie (security-auditor D-1).
  Podstawa: Issue #4, `Wymagania/Requirements_EN.md` §4 F-02, §7 AC-04,
  `docs/architecture/decisions/ADR-0012-zalozenia-lancuch-nadpisan.md`,
  `ADR-0004-wersjonowanie-kalkulacji.md`, `ADR-0006-waluty-i-kursy.md` (rozjazd słownika źródeł
  nazwany), `ADR-0001-trwalosc-danych.md` (tabela singleton bez zasięgu).
  **Done 2026-09-23:** PR #62 (scalone `8bfc2f5`). Dowód: `backend/tests/test_assumption_resolution.py`,
  `test_assumption_readiness.py`, `test_assumption_approval.py`, `test_project_assumption_overrides.py`,
  `test_project_group_two_race.py` — 533 testy backendowe zielono (było 473). Trzy równoległe rundy
  weryfikacji (Invariant Guardian, reviewer, security-auditor) + poprawki: **zatrzymanie** —
  istniejący test (`test_absence_budget_schema_constraints.py::test_the_absence_budget_migration_downgrades_and_upgrades_again`)
  miał zahardkodowaną wersję migracji sprzeczną z własnym docstringiem testu ("revision the
  database starts at is read, not hard-coded") — naprawione za zgodą człowieka, usunięta jedna
  asercja, reszta testu (round-trip downgrade/upgrade) nietknięta; **R-01** (reviewer, Niska) —
  nadpisanie projektu zapisywalne przez `PATCH`, ale nieodczytywalne żadnym endpointem — naprawione
  osobną rundą, `ProjectDetail` rozszerzony o surowe wartości nadpisań; **R-02** (reviewer, Niska)
  — nieaktualny docstring `draft_scenario` ("the only lock") — poprawiony po naprawie P-C; **S-01,
  S-02** (invariant-guardian, Niskie) — ADR-0012 prowadził własne śledzenie statusu dowodu wbrew
  regule 18 (rola wyłącznie `capabilities.md`) i miał martwe odesłanie — oba naprawione przed
  merge. **Zaakceptowane, nie naprawiane:** D-1/D-2/D-3 security-auditora (Niskie — widoczność
  wartości domyślnych, przyszłe ryzyko wnioskowania kosztu z marży+przychodu gdy przychód powstanie,
  brak `statement_timeout` na sesjach aplikacji — wszystkie pre-existing albo nazwane świadomie).
  Zob. `docs/architecture/capabilities.md`.

- [x] **SC-4-01** — Wylicz przychód Time & Material dla scenariusza (F-06.1): reguła komercyjna
  scenariusza, stawka sprzedażowa rozstrzygana z katalogu oknem obejmującym cały miesiąc alokacji,
  przychód = Σ (`billable_hours` × stawka), z migawką stawek dla zatwierdzonych scenariuszy
  (Issue #8, Story F-06 zawężona na bramce 1 do wyłącznie tego zadania — reszta modeli i reguł
  wspólnych to przyszłe, osobne Issues na tym samym wzorcu).
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-11: (1) AC-01, część przychodowa: 100 h
  fakturowalnych × 200 PLN/h = 20000 PLN; (2) przychód liczony z `billable_hours`, nie z
  `planned_allocation_hours`/dostępności — podniesienie planu przy stałych godzinach fakturowalnych
  zostawia przychód bez zmian; (3) stawka rozstrzygana oknem `valid_period` obejmującym CAŁY miesiąc
  alokacji; zmiana stawki w trakcie miesiąca daje nazwany stan "brak stawki" dla tego miesiąca,
  kontrast na zmianie przypadającej dokładnie na granicę miesiąca (przechodzi); (4) zgodność typu
  `commercial_terms`/`tm_terms` egzekwowana złożonym kluczem obcym w bazie, nie walidacją aplikacji
  — wiersz szczegółów niezgodnego `model_type` odrzucony przez bazę; (5) reguła i przychód
  scenariusza spoza zasięgu wołającego → `404`, nieodróżnialne od nieistniejącego, także dla zapisu;
  (6) zapis reguły do scenariusza `approved` odrzucony w tej samej instrukcji co zapis, dowiedziony
  testem wyścigu dwóch połączeń, kontrast na `draft`; (7) kopiowanie scenariusza kopiuje
  `commercial_terms` i `tm_terms` jednym wpisem agregatu w `SCENARIO_CHILD_COPIERS`, kopia dostaje
  nowe identyfikatory — brak wiersza szczegółów na kopii jest regresją, nie stanem legalnym; (8)
  zatwierdzenie zamraża TYLKO okna stawek czytane przez pozycje/miesiące scenariusza do nowej tabeli
  `approved_snapshot_catalog_default_rate` (grupa 1 ADR-0004); kopia zatwierdzonego scenariusza ma
  zero wierszy migawkowych (kanarek, obejmuje też tę nową tabelę); (9) AC-04/AC-10: edycja stawki w
  katalogu po zatwierdzeniu nie zmienia ani jednej wartości przychodu zatwierdzonego scenariusza —
  czytelnik migawki rozstrzyga per miesiąc tym samym predykatem co ścieżka żywa; (10) brak reguły
  komercyjnej i brak stawki dla miesiąca nigdy nie dają przychodu `0` — nazwany stan, przychód
  częściowy (suma z pominięciem miesięcy bez stawki) zakazany; (11) odpowiedź reguły/przychodu nie
  niesie żadnego pola kosztowego (`default_cost_rate`, koszt, zysk, marża) — dowód przez równość
  zbioru pól odpowiedzi; testy odmowy `COMMERCIAL_READ`/`COMMERCIAL_WRITE` (wołający z pozostałymi
  uprawnieniami, bez tego jednego).
  **Decyzje bramki 1 (2026-09-23, architect + analyst, zaakceptowane przez człowieka):** Issue #8
  (cała Story F-06) zawężona do wyłącznie T&M — Fixed Price/Outcome-based/Story Points/reguły
  wspólne F-06.5 odłożone jako przyszłe, osobne Issues na tym samym wzorcu (precedens: Issue #3 →
  SC-1-05/06, reszta jako późniejsze zadania); blok 4 dowodzi WYŁĄCZNIE przychodu — koszt/zysk/marża
  z AC-01 należą do bloków 5/7; stawka sprzedażowa WYŁĄCZNIE z katalogu (`catalog_default_rates`),
  bez nadpisania na poziomie scenariusza (brak potrzeby biznesowej w MVP, łańcuch ADR-0012 odłożony
  do zadania, które go zażąda); brak stawek dziennych w MVP (katalog wymusza `unit = 'hour'` w
  bazie) — "hours in a billable day" (F-06.1) nie dostaje pola, ani reużycia
  `working_calendar.standard_hours_per_day` (podstawa zdolności, nie warunek umowy); zmiana stawki w
  trakcie miesiąca = nazwany stan "brak stawki" dla tego miesiąca, NIE stawka z pierwszego dnia
  (unika cichego zastosowania starej stawki do całego miesiąca przejścia); brak `commercial_terms`
  NIE wchodzi do gotowości scenariusza (`assess()`, F-01) w tym zadaniu — zmieniłoby wynik dla
  każdego istniejącego scenariusza.
  **Nowa/zmieniona decyzja architektoniczna: ADR-0003** przepisany i zawężony do reguły scenariusza
  + T&M (poprzednia wersja nigdy nie miała statusu Accepted — to poprawka Draftu, nie aneks),
  status **Accepted**; aneksy tego samego dnia do **ADR-0004** (grupa 2 dla `commercial_terms`/
  `tm_terms`, nowa tabela migawkowa `approved_snapshot_catalog_default_rate` grupa 1), **ADR-0005**
  (nowe uprawnienia `COMMERCIAL_READ`/`COMMERCIAL_WRITE`, bez koniunkcji z `PERSONNEL_COSTS_READ`) i
  **ADR-0008** (`commercial_terms` wychodzi z listy tabel wzorca `EXCLUDE`/`valid_period` — jedna
  reguła na scenariusz, wersjonowanie przez mechanizm ADR-0004, nie przez okno dat).
  **Out of scope (explicit):** SC-4-02..05 (Fixed Price, Outcome-based, Story Points, reguły
  wspólne F-06.5) — przyszłe, osobne Issues; encja fazy/workstreamu i mieszane modele per
  faza/workstream (F-06 — encja nie istnieje); nadpisanie stawki na poziomie projektu/scenariusza;
  limity godzin/budżetu, stawki nadgodzinowe/dyżurowe/poza godzinami, proporcja czasu
  fakturowalnego (F-06.1 — pola `tm_terms` przy zadaniu, które ich zażąda); stawki per osoba (tylko
  per rola/senioritet/lokalizacja/typ zaangażowania); stawki dzienne i ich przeliczenie; wiele walut
  jednocześnie w jednej regule (nazwany stan "waluty niezgodne" zamiast konwersji); konsumpcja flag
  `absence_type.generates_revenue`; przypisanie przychodu do okresów i termin płatności (F-06.5);
  koszt, zysk, marża (blok 5/7); ekran (osobne zadanie frontend); `audit_log` (blok 8).
  **Fundament nieudowodniony:** pierwszy konsument `catalog_default_rates.default_selling_rate` w
  jakimkolwiek wyliczeniu i pierwsza migawka stawek w repozytorium — ustanawia wzorzec dla
  przyszłych modeli komercyjnych i dla migawki kursów walut (ADR-0006). Podstawa: Issue #8,
  `Wymagania/Requirements_EN.md` §4 F-06/F-06.1, §7 AC-01,
  `docs/architecture/decisions/ADR-0003-model-modeli-komercyjnych.md`,
  `ADR-0004-wersjonowanie-kalkulacji.md` (aneks SC-4-01), `ADR-0005-model-dostepu.md` (aneks
  SC-4-01), `ADR-0008-przedzialy-obowiazywania.md` (aneks SC-4-01), `ADR-0001-trwalosc-danych.md`,
  `ADR-0002-obsluga-pieniedzy.md`, `ADR-0007-wspolbiezna-edycja.md`.
  **Done 2026-09-23:** PR #64 (scalone `ffdb735`). Dowód: `backend/tests/test_commercial_revenue.py`,
  `test_commercial_revenue_gate_2.py`, `test_commercial_terms_access.py`, `test_commercial_terms_guards.py`,
  `test_commercial_terms_copy.py`, `test_commercial_terms_schema.py` — 581 testów backendowych zielono
  (było 533). Dwie rundy weryfikacji: QA (mutacje, proof holds po obu rundach — R-01..R-06 zamknięte),
  Invariant Guardian PASS (×2), reviewer STOP→PASS (R-01: zmiana stawki kosztowej w trakcie miesiąca
  blokowała przychód, naprawione; R-02/R-03: nieznany `model_type` dawał 500/kopię niekompletną,
  naprawione defensywnie; R-05: kopiowanie omijało `DETAIL_TABLE_BY_MODEL`, naprawione), security-auditor
  PASS WITH RESERVATIONS (B-01: zakres `COMMERCIAL_READ` doprecyzowany w ADR-0005). ADR-0003 poprawiony
  i przyjęty (Accepted), aneksy ADR-0004/0005/0008 tego samego dnia, aneks ADR-0003 2026-09-23 (R-01).
  Story F-06 (Issue #8) zawężona na bramce 1 do wyłącznie tego zadania — reszta jako Issues #65
  (encja fazy/workstreamu), #66 (Fixed Price), #67 (Outcome-based), #68 (Story Points), #69
  (reguły wspólne F-06.5).

- [x] **SC-4-06** — Ekran reguły T&M i przychodu scenariusza (F-06.1, frontend): konsument API
  dostarczonego przez SC-4-01 (`GET`/`POST /projects/{id}/scenarios/{id}/commercial-terms`) —
  ekran świadomie odłożony przy SC-4-01, wzorem każdego backend/frontend podziału w tym repo
  (Issue #71).
  *Done when:* `frontend/src` (vitest) dowodzi K-01..K-07, każde z zarejestrowanym przebiegiem
  mutacyjnym. Fragment reguły komercyjnej i przychodu scenariusza, zamontowany w działającej
  aplikacji (nie tylko we własnym teście), jako sekcja karty scenariusza w `ProjectListScreen`
  (bramka 1, D-2 = opcja A). Pokazuje regułę albo stan "brak reguły" z akcją ustawienia T&M.
  Kwotę i walutę pokazuje wyłącznie dla `revenue.state = "calculated"`, a każdy z sześciu
  pozostałych nazwanych stanów (`no_commercial_terms`, `incomplete_commercial_terms`,
  `unsupported_model_type`, `no_rate`, `currency_mismatch`, `no_revenue_currency`) oraz 403/404
  odczytu dostaje własny, odróżnialny komunikat, nigdy `0` ani pusty fragment. Pokazuje wybrane
  pola `assumptions_used` czytelnie — źródło stawki, okna, miesiące bez stawki (bramka 1, D-1 =
  opcja B), bez rozwijania `position_id` do nazwy pozycji. Zapis T&M (`POST`, dziś tylko
  `time_and_material`) renderuje regułę i przychód z ciała `201` (ADR-0009 aneks 2026-09-23
  SC-4-06), rozróżnia 201, 409 "approved", 409 "reguła już istnieje" — dopasowaniem treści
  odmowy, spiętym testem kontraktowym (bramka 1, D-3 = opcja b), 403, 404 i wynik nierozstrzygnięty
  (timeout). Akcja "ustaw T&M" ukryta/wyłączona wg `scenario_status = "approved"` z odczytu, ze
  ścieżką `409` zachowaną jako osobno dowiedziony wyścig (ADR-0009 aneks 2026-09-23 SC-4-06).
  **Out of scope (explicit):** edycja i usuwanie reguły (ADR-0003 pkt 2, brak ścieżki w API);
  modele inne niż T&M (Issues #66/#67/#68); rozwinięcie `position_id` z `unresolved_months` do
  nazwy pozycji (wymagałoby koniunkcji `STAFFING_READ`+`COMMERCIAL_READ`, poza zakresem aneksu
  ADR-0005 SC-4-01); koszt/zysk/marża (blok 5/7); router/stan wybranego projektu-scenariusza jako
  ogólny mechanizm (odłożone do zadania, które go faktycznie potrzebuje — bramka 1, D-2); backend
  (żadna zmiana w `backend/`); mobile/responsive (NF-09).
  Podstawa: Issue #71, `backend/app/api/schemas/commercial_terms.py`,
  `backend/app/api/commercial_terms.py`, `docs/PLAN.md` wpisy SC-1-06, SC-2-02, SC-2-04,
  `docs/architecture/decisions/ADR-0003-model-modeli-komercyjnych.md`,
  `ADR-0005-model-dostepu.md` (aneks SC-4-01), `ADR-0009-zapis-z-interfejsu.md` (aneks
  2026-09-23), `ADR-0010-awaria-renderu-frontendu.md`.
  **Decyzje bramki 1 (2026-09-23, architect + analyst, zaakceptowane przez człowieka):** ADR-0009
  i ADR-0010 do przyjęcia (Draft→Accepted) przez człowieka niezależnie od tego wpisu — kod tego
  zadania zakłada ich treść jak dla decyzji przyjętej. Aneksy do ADR-0009 (zawężenie pkt 3, wynik
  zapisu z ciała `201`; doprecyzowanie pkt 4, prezentacja `scenario_status` dozwolona, wyścig `409`
  zostaje) dopisane 2026-09-23.
  **Done 2026-09-23:** PR #73 (scalone `00798bd`). Dowód: `frontend/src/features/projects/ScenarioCommercialTerms.test.tsx`
  (K-01..K-07, 54 testy, w tym jeden przez zamontowane `<App/>`), `frontend/src/api/contracts/commercialTermsRefusals.test.ts`
  (5 testów kontraktowych D-3) — 234 testy frontendowe zielono (było 173). Backend niezmieniony,
  581 testów zielono. Cztery równoległe rundy weryfikacji: QA PASS WITH GAPS (luka M3 — brak
  resetu stanu zapisu po `readAgain` — domknięta nowym testem kontrastowym), Invariant Guardian
  PASS (3 uwagi niskie, wszystkie domknięte przed mergem), reviewer PASS z **naprawionym**
  znaleziskiem R-01 (średnia — kwota/stawka o złej gramatyce liczby dziesiętnej przechodziła
  walidację kształtu i wywalała cały `ProjectListScreen` w fazie renderu; naprawa: `isDecimalString`
  eksportowany z `lib/money.ts`, użyty w `isRevenueShape`/`isRateWindowShape`, 3 mutacje zabite),
  security-auditor PASS. **Zaakceptowane, nie naprawiane:** R-02 (reviewer, niska) — N równoległych
  odczytów kart na projekt może się otrzeć o limit gniazd przeglądarki i budżet 12 s przy bardzo
  dużych projektach; strażnik `mounted` przy zapisie jest nieobserwowalny w React 18 (dekoracyjny,
  udokumentowane w teście).

- [x] **SC-4-04** — Story Points — wyliczanie przychodu scenariusza (F-06.4), czwarty model
  komercyjny po T&M (SC-4-01): reguła "cena za zaakceptowany Story Point × liczba zaakceptowanych
  punktów", zero konwersji Story Points ↔ godziny (Issue #68).
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-05: (1) AC-09 — 25 zaakceptowanych punktów
  × 1000 PLN = 25000 PLN dokładnie; niezaakceptowane/częściowe punkty nie generują przychodu; (2)
  brak konwersji Story Points ↔ godziny — drastyczna zmiana `billable_hours` scenariusza nie rusza
  przychodu SP, kontrast na regule T&M w tym samym scenariuszu, która MUSI zareagować; (3)
  dyspozytor wielu modeli komercyjnych (`REVENUE_BY_MODEL`/`DETAIL_TABLE_BY_MODEL`) dowiedziony
  PIERWSZY RAZ na DWÓCH realnych modelach w bazie (T&M + SP), nie symulacją `monkeypatch`;
  `unsupported_model_type`/`CommercialTermsNotCopyable` nadal poprawne z realnym drugim wpisem; (4)
  powtórzenie dla nowej tabeli szczegółów `story_points_terms` trzech boundary już dowiedzionych dla
  T&M — zasięg projektu (404 nieodróżnialne, także dla zapisu), niemutowalność zapisu do
  scenariusza `approved` (strażnik w TEJ SAMEJ instrukcji zapisu SP, dowiedzione realnym wyścigiem
  dwóch połączeń), kaskada kopiowania (agregat reguła+szczegóły, jeden wpis
  `SCENARIO_CHILD_COPIERS`), zgodność typu przez złożony klucz obcy w bazie; (5) odpowiedź
  reguły/przychodu SP nie niesie żadnego pola kosztowego — dowód przez równość zbioru pól.
  **Decyzje bramki 1 (2026-09-25, analyst + architect, zaakceptowane przez człowieka):** D-1 —
  wyłącznie wariant "za zaakceptowany punkt" (Requirements §8, open decision #2 rozstrzygnięta dla
  MVP), wariant "sprint fee" poza zakresem; D-4 — limit budżetowy poza zakresem (AC-09 go nie
  wymaga); D-5 — `accepted_points` jako pojedyncza wartość wpisywana raz przy tworzeniu reguły, zero
  ścieżki edycji w MVP (zmiana wymaga kopii scenariusza); D-6 — kształt API jako unia dyskryminowana
  po `model_type`, wartości dziedzinowe SP do TEJ SAMEJ strażonej instrukcji zapisu co T&M. Prognoza
  przychodu z velocity per zespół i rozliczenie punktu przenoszonego między sprintami trwale poza
  zakresem MVP — brak encji "team" i brak tożsamości jednostkowej punktu w rejestrze.
  **Nowa/zmieniona decyzja architektoniczna:** aneks do **ADR-0003** (2026-09-25, sekcja "impact map
  i decyzje bramki 1 dla SC-4-04") — rozszerzenie dyskryminatora/dyspozytora, zasięgu i uprawnień,
  znacznika współbieżności i okien obowiązywania na drugi realny model, bez zmiany istniejących
  tabel (wzorzec ADR-0003 "Konsekwencje": "Nowy model komercyjny = nowa tabela szczegółów +
  rozszerzenie CHECK + gałąź dyspozytora"); aneks do **ADR-0004** wyłącznie potwierdzający
  (kopiowanie/strażnik zapisu obejmują każdą tabelę zarejestrowaną w `DETAIL_TABLE_BY_MODEL`, nie
  tylko `tm_terms`).
  **Out of scope (explicit):** wariant "sprint fee" (opłata za sprint ze zobowiązaniem punktowym),
  limit budżetowy, prognoza z velocity per zespół, rozliczenie punktu przenoszonego między
  sprintami, edycja `accepted_points` po utworzeniu reguły, reguły rework/re-estymacji, progi
  cenowe (pricing tiers), koszt/zysk/marża (blok 5/7), ekran (osobne przyszłe zadanie, wzorem
  SC-4-01/SC-4-06), wielowalutowość w jednej regule.
  Podstawa: Issue #68, `Wymagania/Requirements_EN.md` §2, §4 F-06.4, §7 AC-09, §8 open decision #2,
  `docs/architecture/decisions/ADR-0003-model-modeli-komercyjnych.md` (aneks 2026-09-25).
  **Done 2026-09-25:** PR #115 (scalone `4011f7c`). Dowód: `backend/tests/test_story_points_revenue.py`,
  `backend/tests/test_story_points_terms.py` — 796 testów backendowych zielono (było 772 przed
  mergem z main, 760 na moment otwarcia PR), `ruff check .` czysty. QA: PASS WITH GAPS — luka w
  teście wyścigu (nie łapał drugiego, niestrzeżonego statementu post-commit) znaleziona i zamknięta
  nowym testem strukturalnym w tej samej rundzie, 5/5 mutacji zabite mutacyjnie. Invariant Guardian
  PASS (zero naruszeń reguł 1–21). Reviewer: **R-01 (High)** — `story_points_terms.currency` nigdy
  nie było zestawiane ze `scenarios.currency`, ryzyko cichego mieszania walut w
  profit/margin/markup — naprawione tym samym wzorcem co pozostałe cztery komponenty przychodu/
  kosztu (T&M, personnel_cost, paid_absence_cost, additional_cost); ponowna recenzja potwierdziła
  **R-01 CLOSED**. Security-auditor PASS (migracja w zakresie): reużyty mechanizm
  uprawnień/zasięgu bez zmian, brak nowych zależności, brak wektora SQL injection, walidacja
  liczbowa zgodna z CHECK w bazie. Synchronizacja z `main` przed PR: konflikt mechaniczny w
  `backend/tests/conftest.py` (dwie niezależne funkcje fixture wstawione w tym samym miejscu przez
  SC-4-04 i SC-1-11 — rozwiązany, zachowano obie) i rozjazd łańcucha migracji Alembic z SC-1-11
  (dwie głowy z tego samego `down_revision` — zlinearyzowane bez zmiany treści żadnej migracji).

- [x] **SC-4-05** — Reguły wspólne modeli komercyjnych: `scope_ref` na `commercial_terms`, ochrona
  przed podwójnym rozliczeniem między regułą projektu a regułą segmentu, cross-scenario integrity
  (F-06.5, część), warunek wstępny SC-1-11 (encja segmentu, bramka 3 zamknięta) i SC-4-04 (drugi
  model komercyjny) (Issue #69).
  **Done 2026-09-25:** PR #119 (scalone `06296e8`). Developer: 814/814 testów backendu, `ruff`
  czyste, `backend/tests/test_commercial_terms_scope.py` (K-01..K-04, D-4). QA: PROOF HOLDS (4
  mutacje uruchomione i zabite, jeden dodatkowy test dwusegmentowej granicy D-4). Invariant
  Guardian i Reviewer: PASS WITH RESERVATIONS w pierwszej rundzie (odczyt niehartowany na >1 wiersz
  — surowy `MultipleResultsFound` w `_rule_of`, cicha utrata przychodu drugiej reguły Story Points
  w `revenue_by_model_type`), oba zastrzeżenia naprawione (jawne, nazwane wyjątki zamiast
  cichego/surowego zachowania) i zweryfikowane niezależnie przez tych samych audytorów, werdykt
  końcowy PASS. Security-auditor: PASS. Frontend: dodatkowa poprawka poza zakresem roli backend —
  `commercialTermsRefusals.test.ts` czytał `backend/app/models/commercial_terms.py` jako tekst i
  oczekiwał starego kształtu `UniqueConstraint(...)`; SC-4-05 zmienił go na częściowy `Index(...)`
  pod tą samą nazwą, regex testu zaktualizowany do nowego kształtu (262/262 testów frontendu,
  `eslint` czyste) — ta sama asercja treści, inny sposób jej wyciągnięcia ze źródła.
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-04 (analyst, runda 3, 2026-09-25):
  1. (K-01) Reguła projektu i reguła segmentu dzielące tę samą policzalną pracę (dwie reguły T&M,
     zasięgi zagnieżdżone, te same pozycje/miesiące obsady) — przychód liczony raz per
     (pozycja, miesiąc), nie sumą obu reguł. Mutacja: usunięcie predykatu rozłączności zasięgów.
     Para modeli musi faktycznie czytać `staffing_position_allocation` (np. dwie reguły T&M) — T&M
     + Story Points nie dzielą żadnej policzalnej jednostki (godziny vs. punkty), nie zabija tej
     mutacji.
  2. (K-02) Reguła "cały scenariusz" (`scope_ref IS NULL`) i reguła "jeden segment" nie mogą
     współistnieć bez jawnej reguły łączonej — zaimplementowanej jako rozłączność wartości
     `scope_ref` wymuszona ograniczeniem w bazie (D-3=A), zastępującym `UNIQUE (scenario_id)`
     (ADR-0003 pkt 1). Mutacja: usunięcie ograniczenia bez zastąpienia.
  3. (K-03) Zatwierdzony scenariusz z regułą na poziomie segmentu pozostaje odtwarzalny
     mechanizmem, który dany model faktycznie używa — migawka stawek dla T&M-kształtnych
     (rozszerzenie testu SC-4-01 K-09), strażnik zapisu dla Story-Points-kształtnych (rozszerzenie
     testu SC-4-04 K-04(b)). Mutacja: `scope_ref` jako kolumna zwalniająca z istniejącego
     strażnika/migawki.
  4. (K-04) Złożony FK `(segment_id, scenario_id) → scenario_delivery_segment (id, scenario_id)`
     odrzuca segment INNEGO scenariusza niż reguła (cross-scenario integrity, wzorzec
     `TYPE_AGREEMENT_FOREIGN_KEY` na `tm_terms`). Mutacja: FK zawężony do samego `segment_id`.

  **Decyzje bramki 1 (2026-09-25, analyst + architect, zaakceptowane przez człowieka bez
  zastrzeżeń):** D-1=A (przypisanie przychodu do okresów/termin płatności, F-06.5 pierwsza połowa,
  pozostaje odłożone — poza zakresem tego zadania); D-2=potwierdzone (porównanie modeli przez F-09/
  duplikację scenariusza i ujawnianie założeń przez `assumptions_used` już pokryte, bez nowego
  mechanizmu); D-3=A (reguła łączona = rozłączność `scope_ref` w bazie, nie nowa encja —
  rozłączność dowiedziona na poziomie SCHEMATU wierszy reguł, NIE na poziomie PRZYCHODU: żadna
  opcja nie liczy przychodu per segment bez F-04, poza zakresem, patrz Out of scope); D-4=A
  (`copy_scenario_delivery_segments` przestawione przed `copy_commercial_terms` w
  `SCENARIO_CHILD_COPIERS`, mapowanie `scope_ref` po `(scenario_id, name)`).

  **Out of scope (explicit):** przypisanie przychodu do okresów i termin płatności (F-06.5, D-1);
  porównanie modeli i ujawnianie założeń jako nowy mechanizm (D-2, już pokryte gdzie indziej);
  ochrona przed podwójnym rozliczeniem na poziomie KWOTY przychodu (dopiero po F-04, pozycja obsady
  ↔ segment — dziś nieistniejącej); zysk/marża/koszt (blok 5/7); historia wersji warunków poza
  migawką zatwierdzenia; API dla segmentu (ADR-0016 pkt 8, nadal bez nośnika).

  **Basis:** `Wymagania/Requirements_EN.md` §4 F-06.5; `ADR-0003-model-modeli-komercyjnych.md`
  (aneksy 2026-09-25, bramka 1 SC-4-05); `ADR-0016-segment-dostawy-scenariusza.md` (aneks
  2026-09-25); `ADR-0004-wersjonowanie-kalkulacji.md`; `docs/PLAN.md` SC-1-11, SC-4-01, SC-4-04.

- [x] **SC-5-01** — Wylicz bazowy koszt osobowy scenariusza z przepracowanego czasu (F-07, podstawa
  worked time): Σ (`planned_allocation_hours` × `default_cost_rate` rozstrzygnięta per miesiąc
  predykatem lustrzanym do ADR-0003/R-01), koszt jawnie bazowy (nie w pełni obciążony), bramka
  koniunkcji kosztowej (`STAFFING_READ` na endpoincie, `PERSONNEL_COSTS_READ` ∧
  `can_view_personnel_costs` na polu), czytelnik migawki dla zatwierdzonych scenariuszy (AC-04)
  (Issue #9, Story F-07 zawężona na bramce 1 do wyłącznie tego zadania — reszta jako SC-5-02..).
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-07 (analyst, 2026-09-23):
  1. (K-01) Koszt = Σ (`planned_allocation_hours × default_cost_rate`), jedno zaokrąglenie na
     końcu (ADR-0002), bez `× headcount` (alokacja to już suma pozycji). Cztery niezależne mutacje
     muszą zabić: `billable_hours` zamiast planu, `× headcount`, `default_selling_rate` zamiast
     `default_cost_rate`, zaokrąglanie per miesiąc zamiast raz na końcu.
  2. (K-02) Stawka kosztowa rozstrzygana per (pozycja, miesiąc) predykatem lustrzanym do
     ADR-0003/R-01, po parze (`default_cost_rate`, `currency`), niezależnie od predykatu
     sprzedażowego. Zmiana stawki kosztowej w trakcie miesiąca → "brak stawki kosztowej"; zmiana
     wyłącznie sprzedażowej nie blokuje kosztu (ADR-0013 pkt 1).
  3. (K-03) Wynik ma dokładnie dwa kształty: koszt bazowy albo stan nazwany (`no_cost_rate` /
     `currency_mismatch` / `no_cost_currency`) — nigdy `0`, nigdy suma częściowa (ADR-0013 pkt 2,
     doprecyzowane aneksem 2026-09-23 bramka 2).
  4. (K-04) Pole kosztu (kwota i stawki w `assumptions_used`) obecne tylko przy koniunkcji
     `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs` dla projektu tego scenariusza; odmowa =
     `200` bez pola, nie `403`. Sześć niezależnych mutacji, w tym: flaga z dowolnego przypisania
     wołającego zamiast przypisania projektu scenariusza; bramka usuwa kwotę ale zostawia stawkę w
     `assumptions_used`.
  5. (K-05) Scenariusz/projekt spoza zasięgu → `404`, nieodróżnialne od nieistniejącego, dowiedzione
     przy wołającym mającym wszystkie uprawnienia.
  6. (K-06) Odpowiedź przychodu SC-4-01 nadal bez pola kosztowego, także przy otwartej bramce
     kosztowej (nowy test, nie tylko istniejący `test_k_11_*` przy zamkniętej).
  7. (K-07) Koszt zatwierdzonego scenariusza identyczny tuż przed i po zatwierdzeniu, nie zmienia
     się po późniejszej edycji katalogu — dowiedzione dla miesiąca ze zmienną stawką sprzedażową w
     trakcie (ADR-0004 aneks 2026-09-23 SC-5-01) i dla miesiąca ze zmienną stawką kosztową.

  **Decyzje bramki 1 (2026-09-23, product-owner + analyst + architect, zaakceptowane przez
  człowieka):** Q-1 `default_cost_rate` = stawka bazowa, przed narzutami (SC-5-02 dokłada składniki
  na wierzchu; organizacje z już-obciążonymi stawkami dostają w SC-5-02 podwójny narzut do czasu
  poprawy danych, nazwane tam wprost); Q-2 "worked time" = `planned_allocation_hours` (obejmuje
  wysiłek nierozliczalny, domyka SC-3-01 Out of scope); Q-3 zakres migawki rozszerzony o predykat
  kosztowy jako alternatywę do predykatu sprzedażowego (ADR-0004 aneks 2026-09-23 SC-5-01, bez
  działania wstecz — scenariusze zatwierdzone wcześniej zostają z luką na zawsze, nazwane); Q-4
  gałąź pozytywna bramki nieosiągalna w produkcji, przyjęta jak SC-1-08, `PLACEHOLDER_PERMISSIONS`
  bez zmian; Q-5 pole kosztu w nowym `SCENARIO_COST_FIELDS`, trzecia funkcja kształtująca, endpoint
  pod `STAFFING_READ` (ADR-0005 aneks 2026-09-23 SC-5-01) — test
  `test_project_personnel_cost_visibility.py::test_k_06_…` zostaje zielony bez zmian, bo nadal
  dotyczy wyłącznie payloadu projektu, nie scenariusza; Q-6 bramka na sumie scenariusza i na
  rozkładzie jednakowo, nadpłacona ochrona świadomie (zobowiązanie naprzód: zysk/marża bloku 7
  muszą dziedziczyć tę koniunkcję); Q-7 nowe **ADR-0013** "Koszt osobowy (F-07)", Draft — pending
  approval, jako miejsce reguły kosztu i przyszłych SC-5-02..04.

  **Out of scope (explicit):** premie/benefity/narzuty pracodawcy i koszt w pełni obciążony, test
  na podwójny narzut (SC-5-02 — wymaga oceny skutków dla danych osobowych, `headcount=1` czyni
  narzut pozycji daną jednej osoby, ADR-0005 aneks SC-3-02 pkt 11); kwota stała jako podstawa i
  wybór podstawy per pozycja (SC-5-03 — nowe wejście, ścieżka zapisu, strażnik `approved`, wpis w
  `SCENARIO_CHILD_COPIERS`); podstawa FTE (SC-5-04 — brak podstawy konwersji FTE→godziny, odłożone
  w SC-3-01/SC-3-02); stawki kosztowe dzienne/miesięczne (katalog wymusza w bazie `unit='hour'`);
  koszt nieobecności płatnych (`absence_type.generates_cost`, budżet urlopowy — inna podstawa
  godzin, własna reguła bramki ADR-0005 aneks SC-3-03 pkt 4); stawki poddostawców na pozycji
  (pozycja nie ma dziś `vendor_id`); F-08 (Issue #10, osobna Story); zysk/marża/suma kosztów (blok
  7, F-10); eksport i ekran (F-11, osobne zadanie frontend); nadawanie `PERSONNEL_COSTS_READ` i
  flagi przypisania (ADR uwierzytelniania); scenariusze zatwierdzone przed wdrożeniem aneksu
  ADR-0004 2026-09-23 — świadome, nienaprawiane odstępstwo; `audit_log` (blok 8).

  **Fundament nieudowodniony, przyjęty świadomie:** pierwszy konsument `default_cost_rate` w
  jakimkolwiek wyliczeniu; pierwszy czytelnik kosztu z migawki
  `approved_snapshot_catalog_default_rate`; pierwsza realna aktywacja koniunkcji kosztowej z
  aneksu SC-1-08 na polu niezastępczym; gałąź pozytywna bramki nieosiągalna w produkcji; wyścig
  edycji katalogu z zatwierdzeniem dziedziczy status "no evidence" z SC-2-03 (rozszerzony zakres
  tej samej migawki). Podstawa: Issue #9, `Wymagania/Requirements_EN.md` §4 F-07 (pkt 1 i 3, tylko
  worked time), §7 AC-01/AC-04/AC-06, `docs/PLAN.md` SC-3-01/SC-1-08/SC-4-01,
  `docs/architecture/decisions/ADR-0002-obsluga-pieniedzy.md`,
  `ADR-0003-model-modeli-komercyjnych.md` (pkt 4, 5, aneks R-01),
  `ADR-0004-wersjonowanie-kalkulacji.md` (aneksy SC-4-01, 2026-09-23 SC-5-01),
  `ADR-0005-model-dostepu.md` (aneksy SC-1-08, SC-3-01, SC-3-02, SC-3-03, SC-4-01, 2026-09-23
  SC-5-01), `ADR-0008-przedzialy-obowiazywania.md`, `ADR-0013-koszt-osobowy.md` (nowa, Draft).
  **Done 2026-09-23:** PR #74 (scalone `31f0ac1`). Dowód: `backend/tests/test_personnel_cost.py`
  (K-01, K-02, K-03, K-07, test strukturalny C-5), `backend/tests/test_personnel_cost_access.py`
  (K-04, K-05, K-06) — 610 testów backendowych zielono (było 581). Runda weryfikacji (architect,
  QA, Invariant Guardian, reviewer, security-auditor) + poprawki: architect rozstrzygnął 4 pytania
  z implementacji — test strukturalny C-5 pilnujący separacji `app.data.rate_windows` od obu ścieżek
  (dodany), doprecyzowanie stanów walutowych ADR-0013 (nowy stan `no_cost_currency`, rozróżnienie
  `no_cost_rate`/`currency_mismatch`), doprecyzowanie ADR-0005 pkt 7 (co odmowa bramki zostawia
  widoczne: `state`/`cost_basis`/`currency`, nigdy kwotę). QA domknęła realną lukę dowodu: filtr
  `frozen.scenario_id == position.scenario_id` na ścieżce kosztowej migawki (wspólny moduł geometrii
  z przychodem) przeżył pierwszą mutację — żaden test kosztowy nie miał dwóch zatwierdzonych
  scenariuszy tej samej krotki naraz; zabity nowym testem kontrastowym, zero błędów w kodzie
  produkcyjnym. Invariant Guardian: PASS, reguła 10 (koszt niezależny od przychodu) zweryfikowana
  empirycznie przez graf importów. Reviewer: PASS, zero wad High/Medium. Security-auditor: PASS WITH
  RESERVATIONS — **B-01** (Medium, uśpione): koniunkcja `PERSONNEL_COSTS_READ` ∧
  `can_view_personnel_costs` da się dziś teoretycznie obejść przeliczeniem z osobnych odczytów
  katalogu (`CATALOG_READ`, bramka jednoczynnikowa) i obsady (`STAFFING_READ`) dla wołającego z
  `PERSONNEL_COSTS_READ` globalnie, ale bez flagi na projekcie; nieaktywne, bo `PLACEHOLDER_PERMISSIONS`
  nie zawiera `PERSONNEL_COSTS_READ` — udokumentowane w ADR-0005 aneks pkt 5, warunek ponownego
  otwarcia: ADR uwierzytelniania musi rozstrzygnąć to złożenie uprawnień, zanim ktokolwiek dostanie
  `PERSONNEL_COSTS_READ`. **Zaakceptowane, nienaprawione:** gałąź pozytywna bramki kosztowej
  nieosiągalna w produkcji (dowód wyłącznie przez `dependency_overrides` w teście, jak SC-1-08);
  wyścig edycji katalogu z zatwierdzeniem dziedziczy status "no evidence" ze SC-2-03 (ta sama szósta
  CTE, rozszerzony zakres); scenariusze zatwierdzone przed tą zmianą nie odzyskują kosztu miesięcy ze
  zmienną stawką sprzedażową (migawka bez ścieżki UPDATE, nazwane w aneksie ADR-0004 pkt 7). Zob.
  `docs/architecture/capabilities.md`.

- [x] **SC-5-02** — Rozdziel narzuty osobowe od stawki bazowej, policz koszt w pełni obciążony
  (F-07), rozszerzenie SC-5-01/SC-5-06 (Issue #77).
  **Done 2026-09-25:** PR #128 (scalone `e807349`). Developer: 918/918 testów backendu, `ruff`
  czyste, migracja `9b3f6a1d0c47` (zlinearyzowana po merge z SC-4-05 na `b9e3c7a1f264`),
  `backend/tests/test_personnel_cost_surcharge.py` (K-01..K-08 + regresja QA + mid-month
  uniformity). QA: znalazła realną regresję nienazwaną przy implementacji (zatwierdzenie
  scenariusza z narzutem cicho zerowało go — `costed_month_windows` czytał literał zamiast
  kolumny migawki), naprawiona przez developera, mutacje na 6 mechanizmach uruchomione i zabite
  (K-02, K-05, K-03, K-04, regresja migawki, K-07). Invariant Guardian (S-01) i Reviewer (R-01)
  znalazły niezależnie tę samą lukę — zmiana samego narzutu w środku miesiąca cicho psuła cały
  miesiąc — naprawiona rozszerzeniem `month_has_cost_rate` o uniformity check (mirror istniejącej
  reguły dla stawki bazowej), werdykt końcowy obu PASS. Security-auditor: PASS WITH RESERVATIONS
  (klasyfikacja surowego narzutu zaimplementowana zgodnie z bramką 1; dwa Medium dokumentacyjne —
  rozszerzony payload B-01, stale docstringi po fixie migawki — naprawione, wpis ADR-0005 poniżej).
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-07 (analyst + architect, bramka 1,
  2026-09-25):
  1. (K-01) Suma narzutów/premii/benefitów + stawka bazowa = koszt w pełni obciążony, pole odrębne
     od kosztu bazowego SC-5-01, który pozostaje nietknięty. Mutacja: funkcja narzutu ignoruje
     wejście i zwraca kopię kosztu bazowego.
  2. (K-02) Narzut nie dolicza się drugi raz, gdy stawka bazowa już go zawiera (flaga "stawka już
     zawiera narzuty" — ten sam wiersz/tabela co `default_cost_rate`, `CatalogDefaultRate`/
     `catalog_default_rates`, dziedziczy okno efektywności ADR-0008). Mutacja: usunięcie warunku
     flagi, narzut zawsze dokładany.
  3. (K-03) Bramka kosztowa (koniunkcja `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs`)
     rozciągnięta na nowe pole KWOTY narzutu, identycznie jak pola SC-5-01. Mutacja: nowe pole nie
     dodane do `SCENARIO_COST_FIELDS` / nie iterowane przez `_without_scenario_personnel_costs`.
  4. (K-04) Kwota narzutu nigdy nie wypuszczana ścieżką jednoczynnikową katalogu (ADR-0005 SC-3-03
     pkt 4); surowy procent narzutu w katalogu jest daną organizacyjną, bramkowaną samym
     `CATALOG_READ` (ADR-0005 aneks 2026-09-25, mirror budżetu urlopowego SC-3-03 pkt 3). Mutacja:
     kwota narzutu zaimplementowana przez `CATALOG_PERSONNEL_COST_FIELDS`/
     `_without_catalog_personnel_costs` zamiast ścieżki scenariusza.
  5. (K-05) Składowa nieobecności płatnych (SC-5-06) dostaje narzut tak samo jak koszt bazowy
     (`cost_basis = base`, ADR-0013 aneks SC-5-06 pkt 5). Mutacja: mnożnik narzutu zastosowany
     tylko wokół `base_personnel_cost`, nigdy wokół wkładu `paid_absence_cost`.
  6. (K-06) Narzut zamrożony w migawce `approved` w TYM zadaniu — nowa kolumna na istniejącej
     tabeli `approved_snapshot_catalog_default_rate` (ADR-0004 aneks 2026-09-25, precedens SC-4-01
     pkt 2b). Kanarek: kolumna narzutu nie zmienia się po edycji katalogu po zatwierdzeniu
     scenariusza (wzorem M-1/SC-3-02 pkt 2).
  7. (K-07) Formuła narzutu czyta ze wspólnego słownika stawek, którego substytucję wykonuje
     what-if (ADR-0015 pkt 3, aneks 2026-09-25) — podwyżka stawki bazowej przez what-if podnosi też
     kwotę narzutu proporcjonalnie, bez osobnej ścieżki kodu w `app.data.scenario_what_if`.
  8. Wiersze stawek poddostawcy (`catalog_default_rates.vendor_id NOT NULL`) — procent narzutu
     zapisywalny, bez znaczenia biznesowego (formuła kosztu bazowego czyta wyłącznie
     `vendor_id IS NULL`); brak ograniczenia w bazie wymuszającego `0`, test dokumentuje brak
     efektu.

  **Decyzje bramki 1 (2026-09-25, PO + analyst + architect + security-auditor, zaakceptowane przez
  człowieka bez zastrzeżeń):** Q1=B (`profit`/`margin`/`markup`/`included_cost`, F-10/SC-7-01,
  zostają liczone od kosztu bazowego, nie w pełni obciążonego — ograniczenie nazwane, nie
  przełączane w tym zadaniu); Q2=A (narzut zamrożony w migawce już teraz, nie odłożony); Q3
  rozstrzygnięte jako konsekwencja Q4 (what-if pokrywa narzut automatycznie); Q4=B (surowy procent
  narzutu = parametr organizacyjny, `CATALOG_READ` samo, nie dana kosztowa jednoczynnikowa); Q5=ten
  sam wiersz/tabela co `default_cost_rate`. Ocena skutków dla danych osobowych wykonana
  (security-auditor, PASS WITH RESERVATIONS, przed kodem) — koniunkcja kosztowa jako mechanizm dla
  kwoty narzutu potwierdzona.

  **Out of scope (explicit):** kwota stała jako podstawa (SC-5-03) — osobna decyzja zapisu,
  niepotrzebna tu; podstawa FTE (SC-5-04) — brak dziś konwersji FTE→godziny; stawki
  dzienne/miesięczne — katalog wymusza `unit='hour'`; przełączenie `profit`/`margin`/`markup`/
  `included_cost` (F-10, SC-7-01) na koszt w pełni obciążony — zostaje na bazowym (Q1), ograniczenie
  do wpisania w `docs/architecture/capabilities.md` przy bramce 3 tego zadania.

  **Basis:** `Wymagania/Requirements_EN.md` §4 F-07; `ADR-0013-koszt-osobowy.md` pkt 5/8, aneks
  SC-5-06 pkt 5, aneks 2026-09-25 (SC-5-02); `ADR-0005-model-dostepu.md` SC-2-01 pkt 3, SC-3-03 pkt
  3-4, aneks SC-3-02 pkt 11, aneks 2026-09-25 (SC-5-02); `ADR-0004-wersjonowanie-kalkulacji.md`
  aneks 2026-09-25 (SC-5-02); `ADR-0015-przeliczenie-bez-zapisu.md` aneks 2026-09-25 (SC-5-02);
  `docs/PLAN.md` SC-5-01, SC-5-06.

- [x] **SC-5-03** — Kwota stała jako podstawa kosztu, wybór podstawy per pozycja (F-07), rozszerzenie
  ADR-0013 (Issue #78).
  **Done 2026-09-26:** PR #133 (scalone `4876e8a`). Developer: 837 testów backendu (potem 995 po
  scaleniu z SC-5-02, które w trakcie tego zadania zostało w pełni zaimplementowane), `ruff` czyste,
  migracja `a8f18e00172b` (zlinearyzowana po merge na `9b3f6a1d0c47`), `backend/tests/test_fixed_amount_cost.py`
  + `test_staffing_cost_basis*.py` (K-01..K-06). QA: cztery mutacje uruchomione i zabite (CHECK waluty,
  filtr dyspozytora, kopiarka, `SCENARIO_COST_FIELDS`), trzy luki dowodu domknięte (what-if, 403 na PATCH,
  422 walidatorów). Invariant Guardian: PASS WITH RESERVATIONS → naprawione (osierocony `fixed_amount`
  na wierszu `worked_time` bez `cost_basis`, `CostBasisMismatch`). Reviewer: PASS WITH RESERVATIONS →
  R-01 migracji zaakceptowany jako nazwany wyjątek (właściciel nieprzypisany, wygasa przed pierwszym
  wdrożeniem produkcyjnym); R-02 (kolejność diagnozy: konflikt tokenu ADR-0007 musi mieć pierwszeństwo
  przed `CostBasisMismatch`, inaczej nieaktualny token dostaje mylącą podpowiedź prowadzącą do cichego
  nadpisania cudzej, świeższej zmiany) — naprawione, dowiedzione dwoma realnymi połączeniami. Security-Auditor:
  PASS WITH RESERVATIONS → naprawione (koniunkcja `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs`
  wymagana też na zapisie `fixed_amount`, `POST` i `PATCH`, nie tylko na odczycie). Merge z origin/main
  (SC-5-02 w międzyczasie w pełni zaimplementowane w tych samych modułach) zweryfikowany osobno przez
  Guardian i Reviewer, PASS.
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-06 (analyst + architect, bramka 1,
  2026-09-25):
  1. (K-01) Dwie formuły kosztu (`worked_time`/`fixed_amount`) — osobne ścieżki dispatchowane z tej
     samej pozycji przez `cost_basis`, żadna nie importuje modułu drugiej ani ścieżki przychodu
     (`app.data.commercial_terms`) — test strukturalny grafu importów (mirror C-5). Mutacja:
     wspólna funkcja czytająca oba źródła kosztu warunkiem `if`, bez rozdziału modułów.
  2. (K-02) `cost_basis` jest kolumną per pozycja (`staffing_position`), domyślnie `worked_time`
     (zgodność wsteczna z SC-5-01: pozycje istniejące przed tym zadaniem liczą się dokładnie jak
     dziś), trwałe pole zapisu, nie parametr żądania odczytu. Mutacja: domyślna wartość
     `fixed_amount` albo dispatch po parametrze żądania zamiast po kolumnie.
  3. (K-03) Strażnik zapisu do `approved` (`app.data.scenario_guard`) obejmuje `cost_basis` i
     `fixed_amount` w TEJ SAMEJ instrukcji zapisu co pozostałe kolumny `staffing_position` — żaden
     nowy kształt strażnika, żaden nowy token współbieżności (ADR-0007 aneks 2026-09-19 SC-3-01,
     potwierdzony ADR-0007 aneks tej daty SC-5-03 przez odniesienie w ADR-0004). Wyścig z
     zatwierdzeniem dowiedziony na dwóch połączeniach jak dla każdej pozostałej kolumny tej tabeli.
  4. (K-04) Kopiowanie przenosi `cost_basis`/`fixed_amount`(+waluta) jako niezależny wiersz/wartość —
     bez nowego wpisu w `SCENARIO_CHILD_COPIERS` (kolumny podróżują z kopią agregatu pozycji,
     ADR-0004 aneks tej daty pkt 3), na niezależnym wierszu kopii. Mutacja do zabicia: kopiujący
     zwraca `cost_basis` domyślne (`worked_time`) niezależnie od wartości źródła.
  5. (K-05) Bramka kosztowa (koniunkcja `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs`) obejmuje
     kwotę `fixed_amount` identycznie jak kwotę worked time (ADR-0005 aneks 2026-09-23 SC-5-01) —
     `cost_basis`/`fixed_amount` nigdy w schemacie `GET .../staffing-positions` (ADR-0005, aneks tej
     daty, Q4). Dowód strukturalny: zbiór pól tego endpointu identyczny przed/po zadaniu.
  6. (K-06) Brak kwoty przy `cost_basis = 'fixed_amount'` jest stanem nieosiągalnym w aplikacji —
     CHECK w migracji (`cost_basis = 'fixed_amount' → fixed_amount IS NOT NULL`), asercja na
     `pg_constraint` (wzór ADR-0014 D-10) — nigdy `0` jako substytut. Niezgodność waluty
     (`currency_mismatch`) i brak waluty scenariusza bez żadnej rozstrzygniętej kwoty
     (`no_cost_currency`) są jedynymi dwoma stanami nazwanymi obok `calculated` — zakaz sumy
     częściowej dziedziczony z ADR-0013 pkt 2.

  **Decyzje bramki 1 (2026-09-25, PO + analyst + architect, zaakceptowane przez człowieka bez
  zastrzeżeń):** Q1=A (własna waluta `fixed_amount`, wiązana do `scenarios.currency`, stany
  `currency_mismatch`/`no_cost_currency` wzorem ADR-0014 pkt 7); Q2=A (CHECK w migracji, nie
  walidacja aplikacyjna — pytanie o objęcie kolumny waluty tym samym CHECK pozostaje do rozstrzygnięcia
  we własnym zakresie zadania, ADR-0013 aneks tej daty pkt 2); Q3=A (kolumny `cost_basis`+
  `fixed_amount` bezpośrednio na `staffing_position`, nie tabela-wnuczka; kopiowanie przez istniejący
  `copy_staffing_positions`, bez nowego wpisu w `SCENARIO_CHILD_COPIERS`); Q4=a (`cost_basis`/
  `fixed_amount` nigdy w `GET .../staffing-positions`, widoczne wyłącznie przez bramkowaną ścieżkę
  kosztu, wzorem `default_cost_rate`). Pełne uzasadnienia: `ADR-0013-koszt-osobowy.md` aneks tej
  daty (SC-5-03), `ADR-0004-wersjonowanie-kalkulacji.md` aneks tej daty (SC-5-03),
  `ADR-0005-model-dostepu.md` aneks tej daty (SC-5-03).

  **Out of scope (explicit):** podstawa FTE (SC-5-04 — brak dziś konwersji FTE→godziny); narzuty na
  kwotę stałą (relacja narzutu SC-5-02 do `fixed_amount` nierozstrzygnięta — narzut SC-5-02 mnoży
  `default_cost_rate`, nie kwotę stałą; jeśli `fixed_amount` ma kiedyś dostawać narzut, to osobna
  decyzja); przeliczenie walut (ADR-0006, niezgodność jest stanem nazwanym, nie kursem); zmiana
  formuły kosztu nieobecności płatnych (ADR-0013 aneks SC-5-06 — pozostaje przy `cost_basis = base`,
  niezależnie od podstawy pozycji); zysk/marża/suma kosztów (blok 7, F-10) — dziedziczą koniunkcję
  bez zmian (ADR-0005 aneks SC-5-01 pkt 5); eksport i ekran (F-11); `audit_log` (blok 8); scenariusze
  zatwierdzone przed tym zadaniem (nie mają i nie odzyskają wiersza migawkowego dla `fixed_amount` —
  ale ta pozycja nie ma migawki w ogóle, ADR-0004 aneks tej daty pkt 2, więc brak działania
  wstecznego nie ma tu zastosowania w sensie, w jakim miał dla worked time).

  **Basis:** `Wymagania/Requirements_EN.md` §4 F-07; `ADR-0013-koszt-osobowy.md` pkt 8, aneks tej
  daty (SC-5-03); `ADR-0004-wersjonowanie-kalkulacji.md` aneks 2026-09-19 (SC-3-01), aneks tej daty
  (SC-5-03); `ADR-0005-model-dostepu.md` aneks 2026-09-19 (SC-3-01) pkt 5, aneks 2026-09-23 (SC-5-01)
  pkt 7, aneks tej daty (SC-5-03); `ADR-0007-wspolbiezna-edycja.md` aneks 2026-09-19 (SC-3-01);
  `ADR-0014-koszty-dodatkowe.md` pkt 7 (mirror stanów walutowych); `docs/PLAN.md` SC-5-01, SC-5-02.

- [x] **SC-5-04** — `assigned_fte` as a personnel cost basis (F-07), Issue #79, PR #176. A third
  `cost_basis` value with a stored per-position FTE fraction (`NUMERIC(10,4)`, four database CHECKs),
  costed as its own component `fte x working days x standard hours/day x rate` (exact product, one
  final `round_money`, by rate unit; live or frozen calendar and rate). Dispatch by `cost_basis` is
  exclusive at the formula input (also fixes `fixed_amount` positions with allocation rows being
  priced by the worked-time path too).
  *Done when:* `backend/tests` prove criteria K-01..K-07 / controls FA-1..FA-13 (analyst and
  architect, 2026-09-29): the FTE component is priced from stored FTE and the calendar, never from
  the grid hours, and never also by the worked-time path; an hour-rate fixture on a 7.5 h Mon-Sat
  calendar gives 6499.35, not the chained 6499.00; gross of absences; named states `no_calendar`,
  `no_working_days`, `no_planned_months`, never `0.00`, only this component withheld; an approved
  scenario prices from the frozen calendar, rate and unit (carries FTE-6 of SC-3-07); the stored FTE
  is refused by the database when non-positive, missing on its basis, present on another basis or
  together with `fixed_amount`; gated like `fixed_amount` (never in `GET .../staffing-positions`);
  copied to an independent row; the FTE formula imports no revenue module. Each criterion has an
  executed mutation run (capabilities.md, mutation log).
  **Done 2026-09-29:** `backend/tests/test_assigned_fte_cost.py`,
  `backend/tests/test_assigned_fte_cost_scenario.py`, `backend/tests/test_assigned_fte_schema.py`,
  `backend/tests/test_assigned_fte_api.py` (PR #176; 1373 passed, 6 xfailed).
  **Out of scope (explicit):** overheads on the FTE basis (SC-5-02 follow-up); F-08 FTE charge base;
  F-10 planned-FTE metric and summing the components; frontend; partial months; SC-3-08 holiday
  import; `scenarios.full_time_hours_per_week`.
  **Accepted, not repaired (exceptions, owner TanerCRB, decision 2026-09-29; expiry: the F-10
  sum-components task reaches gate 1, or 2026-12-31, whichever first):** R-01 no cap on
  `assigned_fte`, a percent typo (`50` for `0.5`) prices 100x too high with state `calculated`;
  R-02 `included_cost` excludes the FTE component and, after the dispatch fix, `fixed_amount`
  positions with allocation rows, so profit/margin/markup rise for them, including approved
  scenarios (results are live). Also accepted: paid absence still priced for FTE positions (gross
  FTE overcount); mixed-version deploy window (H-7) untested; grid hours and stored FTE may
  disagree; the ADR-0013 SC-5-04 addendum and the ADR-0008/ADR-0002 SC-3-07 addenda remain "Draft
  — pending approval"; bare JSON `NaN`/`Infinity` answers 500 (6 strict xfail tests);
  `POST /projects/{id}/copy` answers 500 on a refused write.

- [x] **SC-5-05** — Koszty dodatkowe (F-08), zawężone na bramce 1 (2026-09-23, ADR-0014, Accepted):
  kategorie kosztów o **kwocie stałej** (`CHECK amount > 0`, G-1), jednorazowych i cyklicznych,
  przypisanych do scenariusza (poziom "projektu") albo pozycji obsady, z atrybutem `funding_source`
  (`internal` / `rebilled_to_client`, bez wpływu na przychód w tym zadaniu) (Issue #10).
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-10 (analyst, 2026-09-23; krzyżowo
  odesłane do kontroli ADR-0014 D-1..D-10):
  1. (K-01, D-1) Koszt jednorazowy pojawia się w wyniku dokładnie raz, w swoim miesiącu, niezależnie
     od przypisania (scenariusz/pozycja); dowiedzione na dwóch scenariuszach tej samej krotki
     (izolacja) i dwóch pozycjach z wieloma miesiącami alokacji (brak fan-out). Trzy niezależne
     mutacje: złączenie z pozycjami/miesiącami alokacji zamiast wyłącznie z własnym wierszem kosztu;
     filtr po projekcie zamiast po scenariuszu; koszt pozycji liczony podwójnie (pozycja + scenariusz).
  2. (K-02, D-2) Koszt cykliczny: pełna kwota w każdym miesiącu domkniętego `[start, koniec]` i w
     żadnym spoza — asercja na ZBIORZE miesięcy, nie tylko sumie. Miesiąc poza okresem dostawy
     projektu i poza alokacją pozycji nadal liczony (bez obcinania). Cztery mutacje: koniec zakresu
     wyłączny; kwota dzielona przez liczbę miesięcy; przecięcie z alokacją pozycji; obcięcie do
     okresu dostawy.
  3. (K-03, D-2/D-7/D-10) Kształt wiersza egzekwowany przez bazę, nie aplikację: zakres cykliczny bez
     końca odrzucony; jednorazowy obejmujący >1 miesiąc odrzucony; koniec przed początkiem odrzucony;
     pozycja spoza scenariusza kosztu odrzucona (asercja na `pg_constraint`, wzór SC-4-01 K-04);
     `funding_source` poza `{internal, rebilled_to_client}` odrzucony; kwota `≤ 0` odrzucona (G-1);
     `DELETE` kategorii wskazywanej przez jakikolwiek koszt odrzucony (bezpośrednio przez bazę — API
     nie ma ścieżki DELETE słownika).
  4. (K-04, D-3) Wejście do 4 miejsc po przecinku bez zaokrąglenia przy zapisie, 5 miejsc → `422`,
     zero nowych wierszy; wynik zaokrąglony raz na końcu przez `round_money`. Cztery mutacje, każda
     inna wartość wyniku: zaokrąglenie per koszt; per miesiąc; zaokrąglenie przy zapisie; `float` na
     ścieżce.
  5. (K-05, D-4) Dwa kształty wyniku: `calculated` / `currency_mismatch` (koszty w >1 walucie albo w
     walucie innej niż `scenarios.currency`) / `no_cost_currency` (brak kosztów i brak waluty
     scenariusza); brak kosztów z zadeklarowaną walutą → `calculated`, `0.00`. Zakaz sumy częściowej,
     obie gałęzie `currency_mismatch` zabijane niezależnie.
  6. (K-06, D-5) Zapis (INSERT/UPDATE/DELETE) kosztu pod scenariuszem `approved` odrzucony w tej samej
     instrukcji co odczyt statusu; wyścig z zatwierdzeniem na dwóch połączeniach (obowiązkowy, nie
     opcjonalny) nie zostawia wiersza; stary `updated_at` → `409` odróżnialne od `409 approved`;
     znacznik per wiersz kosztu (edycja kosztu A nie unieważnia znacznika kosztu B tej samej
     pozycji); nieistniejące id pod `approved` → `404`, nie `409`.
  7. (K-07, D-6) Kopia scenariusza przenosi obie połowy kosztów (scenariusza i pozycji) z nowymi id;
     koszt pozycji wskazuje skopiowaną pozycję, nigdy źródłową ani inną pozycję kopii; wynik kopii
     równy wynikowi źródła; źródło niezmienione. Kanarek kompletności kaskady czerwony osobno dla
     obu połowy (pominięcie w `SCENARIO_CHILD_COPIERS` vs. pominięcie w kopierze agregatu pozycji).
  8. (K-08) Zasięg projektu: `404` nigdy `403` dla każdej operacji (odczyt/zapis/wynik), dowiedzione
     przy wołającym z KOMPLETEM uprawnień (precedens SC-5-01 K-05); osobno dowiedzione pomylenie
     ścieżki (id kosztu/pozycji obcego projektu na URL projektu w zasięgu → `404`/brak zapisu).
  9. (K-09, ADR-0005 aneks SC-5-05) Uprawnienia: koszty pod `STAFFING_READ`/`STAFFING_WRITE` (bez
     koniunkcji z `PERSONNEL_COSTS_READ`), kategoria pod `CATALOG_READ`/`CATALOG_WRITE`; wołający ze
     `STAFFING_READ` bez `PERSONNEL_COSTS_READ` widzi koszt pozycji z `headcount = 1` (test nazwanego,
     przyjętego ryzyka — nie luka). Kanarek równości `PLACEHOLDER_PERMISSIONS` zielony bez zmian.
  10. (K-10, D-8/D-9) Koszt `rebilled_to_client` wchodzi do sumy kosztów dodatkowych (rośnie o dokładną
      kwotę) i NIE zmienia odpowiedzi przychodu (bajt w bajt, na scenariuszu z przychodem policzonym
      i niezerowym); test strukturalny grafu importów — moduł kosztów dodatkowych i ścieżka przychodu/
      moduł kosztu osobowego się nie przecinają.

  **Decyzje bramki 1 (2026-09-23, architekt + analityk, zaakceptowane przez człowieka):** Q-1 = A
  (wyłącznie kwota stała); Q-2 = A (koszt "projektu" = koszt scenariusza bez pozycji, brak
  współdzielonej tabeli); Q-3 = A (kwota cykliczna = kwota na miesiąc, bez dzielenia; miesiąc spoza
  okresu dostawy/alokacji liczy się); Q-4 = A (kategoria to etykieta, bez migawki); Q-5 = A
  (`funding_source` to wyłącznie atrybut, bez wpływu na przychód, świadome niedoszacowanie zysku do
  bloku 7); Q-6 = A (koszt pozycji kopiowany w istniejącym kopierze agregatu pozycji, koszt
  scenariusza dostaje własny wpis `SCENARIO_CHILD_COPIERS`); Q-7 = B (reużycie `STAFFING_*`, bez
  nowego uprawnienia, ryzyko `headcount=1` nazwane); Q-8 = A (nowy **ADR-0014**, Accepted); G-1 = A
  (kwota ściśle dodatnia, `CHECK amount > 0`). Pełne uzasadnienia: `docs/architecture/decisions/ADR-0014-koszty-dodatkowe.md`
  + aneksy SC-5-05 w ADR-0004/0005/0007/0008.

  **Out of scope (explicit):** koszty osobowe (F-07, blok SC-5-01..04); mechanizm rezerw ryzyka
  (F-09); poziom fazy (encja nie istnieje, Issue #65); podstawa headcount/FTE/godziny/procent
  (F-08 pkt 3, następcze zadania SC-5-07+ — SC-5-06 zajęte przez F-07/Issue #81); F-08 pkt 7
  (nakładające się obciążenia/narzuty/rezerwy, F-09); wpływ `funding_source` na przychód/zysk/marżę
  (blok 7, F-10); migawka nazwy kategorii; zasiew ośmiu kategorii migracją; przeliczenie walut
  (ADR-0006); domyślne ceny kategorii; ekran i eksport (F-11); nowe uprawnienie lub koniunkcja z
  `PERSONNEL_COSTS_READ` (ryzyko nazwane w ADR-0005 aneks SC-5-05); usunięcie pozycji/scenariusza z
  przypisanymi kosztami (API nie ma dziś ścieżki DELETE dla żadnego z nich — nieosiągalne, nie
  rozstrzygnięte).

  **Fundament nieudowodniony, przyjęty świadomie:** pierwszy konsument tabeli kosztu dodatkowego i
  kategorii; wyścig `DELETE` kategorii z równoległym `INSERT` kosztu (API nie ma ścieżki DELETE —
  nieosiągalne w produkcji). Podstawa: Issue #10, `Wymagania/Requirements_EN.md` §4 F-08 (pkt 1–6),
  §7 AC-03, `docs/architecture/decisions/ADR-0014-koszty-dodatkowe.md`, ADR-0003 pkt 1, ADR-0004
  (aneks SC-5-05), ADR-0005 (aneksy SC-2-01, SC-3-01, SC-3-02, SC-5-05), ADR-0006, ADR-0007 (aneks
  SC-5-05), ADR-0008 (pkt 6, aneks SC-5-05), `docs/PLAN.md` SC-1-03/SC-3-01/SC-3-02/SC-4-01
  (fundament dowiedziony w `docs/architecture/capabilities.md`).
  **Done 2026-09-24:** PR #83 (scalone `bdc82b3`). Dowód: `backend/tests/test_additional_cost.py`
  (K-01, K-02, K-04, K-05, K-10, R-02), `test_additional_cost_schema.py` (K-03),
  `test_additional_cost_guards.py` (K-06, w tym 3 wyścigi na dwóch połączeniach),
  `test_additional_cost_copy.py` (K-07, R-01), `test_additional_cost_access.py` (K-08, K-09) — 689
  testów backendowych zielono (było 610), 234 frontendowych bez zmian, ruff i lint czyste. Runda
  weryfikacji (QA, Invariant Guardian, reviewer, security-auditor) + poprawki: QA domknęła realną
  lukę dowodu — test strukturalny K-10 czytał tylko bezpośrednie importy, dwie mutacje (import
  `app.domain.revenue` w `scenario_guard.py`, import `app.domain.additional_cost` w
  `write_errors.py`, oba łączące koszt dodatkowy z przychodem przechodnio przez `app.data.staffing`)
  przeżyły pierwotny test — zabite nowym `test_k_10_no_import_path_at_any_depth_...` (chodzi cały
  graf importów). Invariant Guardian: PASS, zero wysokich/średnich, dwie uwagi niskie (obie
  domknięte w rundzie). Reviewer: STOP, dwa Medium naprawione w tym PR — **R-01**: koszt dodatkowy
  jest pierwszą tabelą-dzieckiem rozdzieloną między dwa kopiery (pozycji i scenariusza) po
  edytowalnej kolumnie `position_id`; bez blokady źródła kopia mogła zdublować albo zgubić koszt
  przy równoczesnym `PATCH` w oknie między przebiegami — naprawione `copying_source_scenario`
  (`FOR SHARE` na źródłowym scenariuszu, trzymane do commitu kopii, mirror `unapproved_scenario`).
  **R-02**: brak limitu zakresu kosztu cyklicznego (jeden `POST` do ~36k miesięcy czynił każdy
  późniejszy odczyt/kopię scenariusza nieograniczenie dużym) — naprawione `MAX_RECURRING_MONTHS =
  MAX_ALLOCATION_MONTHS` (60) w schemacie, mirror istniejącego precedensu. Security-auditor: PASS
  WITH RESERVATIONS — **H-01** (Low, nowe): `category_name` dociera do każdego `STAFFING_READ` bez
  `CATALOG_READ`, szerzej niż pierwotnie nazwane ryzyko (dowolna kategoria, nie tylko
  rekrutacja/szkolenie) — zaakceptowane i nazwane wprost w ADR-0005 aneks SC-5-05 pkt 6 (wzorem
  `COMMERCIAL_READ`, SC-4-01 pkt 4), etykieta grupy 1, nie dana F-13. **Zaakceptowane, nienaprawione:**
  R-04 (Low, reviewer) — brak idempotencji `POST` (baza nie odrzuca duplikatu, ADR-0014 pkt 4 świadomie
  bez `EXCLUDE`), nazwane w ADR-0014, warunek zamknięcia: pierwsze zadanie frontendowe budujące
  formularz zapisu (F-11); `PATCH` niosący tylko jeden koniec zakresu cyklicznego omija limit 60
  miesięcy (uniknięcie check-then-act, ten sam wzorzec co precedens `MAX_ALLOCATION_MONTHS`); wyścig
  `DELETE` kategorii z równoległym `INSERT` kosztu nieosiągalny w produkcji (brak ścieżki DELETE w
  API). Zob. `docs/architecture/capabilities.md`.

- [x] **SC-5-06** — Koszt nieobecności płatnych (F-07, F-05): koszt nieobecności flagowanych
  `absence_type.generates_cost = true` jako osobna, nazwana składowa obok niezmienionego kosztu
  bazowego SC-5-01; budżet urlopowy (`absence_budget_hours`, SC-3-03) wchodzi do kosztu wyłącznie
  dopłatą ponad wpisy ręczne — budżet sam w sobie nie jest daną kosztową (ADR-0005 aneks SC-3-03
  pkt 4), ale koszt z niego wyliczony już jest (Issue #81).
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-07 (analyst, 2026-09-23):
  1. (K-01) Składowa liczy wyłącznie typy `generates_cost = true`, godziny dni roboczych, bez
     `× headcount`, stawką kosztową miesiąca; koszt bazowy SC-5-01 nietknięty. Sześć mutacji do
     zabicia (m.in. podstawa z `absence_day_equivalents` wszystkich typów, filtr po
     `generates_revenue`, `× headcount`, stawka sprzedażowa, wmieszanie do kwoty bazowej).
  2. (K-02) Urlop ustawowy w koszcie = wpisy ręczne typu ustawowego + dopłata budżetu
     (`max(budżet, suma ręcznych)` z SC-3-03 przeniesione na koszt) — nigdy pełny budżet + wpisy
     ręczne (błąd "46 zamiast 26" z SC-3-03 R-02).
  3. (K-03) Koszt z budżetu podąża za `generates_cost` typu ustawowego; stan "budżet nie
     zastosowany" jest nazwany (`no_budget`/`no_statutory_leave_type`), nigdy ciche `0`.
  4. (K-04) Składowa nieobecności niepoliczalna (`no_calendar`, brak stawki miesiąca) jest stanem
     nazwanym na poziomie składowej, nigdy `0`, nigdy suma częściowa całego kosztu pozycji.
  5. (K-05) Kwota składowej (w tym część budżetowa) pod koniunkcją `PERSONNEL_COSTS_READ` ∧
     `can_view_personnel_costs`; budżet jako liczba dni/godzin w odpowiedzi obsady/katalogu
     zostaje poza nią (dowodzi ADR-0005 aneks SC-3-03 pkt 4 i aneks SC-3-02 pkt 7).
  6. (K-06, M-1, M-3) Koszt nieobecności zatwierdzonego scenariusza identyczny przed/po
     zatwierdzeniu, nie zmienia się po edycji katalogu (flaga typu, dni budżetu, stawka) —
     pierwszy czytelnik migawki kalendarza/budżetu/typu nieobecności (ADR-0004 aneks 2026-09-23
     SC-5-06). Typ ustawowy nazwany-niekosztowy bez zamrożonego budżetu przeżywa zatwierdzenie
     (M-3, poprawka bramki 2 — zob. niżej).
  7. (K-07) Odpowiedź przychodu SC-4-01 bajt w bajt bez zmian po dodaniu nieobecności płatnych i
     niezależnie od `generates_revenue`.

  **Decyzje bramki 1 (2026-09-23, analyst + architect, zaakceptowane przez człowieka):** G-1
  poprawiono terminologię budżetu w Story (`absence_budget_hours`, nie `derived_capacity_hours`);
  Q-1 składowa w istniejącym `PersonnelCostRead` — trzecia funkcja kształtująca, bez nowego aneksu
  ADR-0005; istniejące testy porównujące `SCENARIO_COST_FIELDS`/K-03 SC-5-01 zbiorem pól
  przezbrojone (nie osłabione); Q-2 `amount` SC-5-01 nietknięty, składowa nieobecności ma własny
  stan nazwany, bez sumy łącznej kosztu osobowego w tym zadaniu (suma → blok 7); Q-3 koszt liczy
  się tylko w miesiącach z wierszem alokacji, zakres migawki bez zmian; Q-4 koszt nieobecności
  dolicza się zawsze, niezależnie od `planned_allocation_hours` — ryzyko podwójnego liczenia przy
  nieodjętym urlopie z planu nazwane, nienaprawiane tu; Q-5 gałąź pozytywna `generates_cost`
  nieosiągalna w produkcji przyjęta jak SC-5-01 (fixture/seed, `PLACEHOLDER_PERMISSIONS` bez
  zmian); Q-6 `generates_revenue` poza zakresem, F-06 rozdziela koszt od przychodu.

  **Poprawka bramki 2 (2026-09-23/24, invariant-guardian + reviewer, zaakceptowana przez
  człowieka).** Weryfikacja znalazła High/Medium: zatwierdzenie scenariusza mogło cicho i trwale
  zmienić składową z `calculated` na `no_budget`, gdy typ ustawowy nie generuje kosztu i scenariusz
  nie zamraża żadnego okna budżetu (żadna instancja zarezerwowana, żaden budżet w oknie planu).
  Naprawiono u źródła: zmieniono kontrakt S-02 migawki typu ustawowego (ADR-0004 aneks SC-5-06 pkt
  5) — typ jest teraz zamrażany zawsze, gdy scenariusz ma alokację w lokalizacji z kalendarzem,
  niezależnie od budżetu. Dwa istniejące testy przepisane na nowy kontrakt (nie osłabione — kontrakt
  się zmienił decyzją architektoniczną): `test_scenario_approval_snapshot.py::test_k_07_m_3_…`,
  `test_scenario_approval.py::test_s_01_a_location_given_a_calendar_during_the_approval_…`. Nowy
  test regresyjny: `test_paid_absence_cost.py::test_r_01_m_1_a_named_non_costing_statutory_type_without_a_frozen_budget_survives_approval`.
  Drugie znalezisko (R-02, Medium, transient): dwa niezależne odczyty siatki w jednym `GET` mogą
  przy współbieżnej edycji szkicu dać przejściowy fałszywy `no_cost_rate` — nie dotyczy zatwierdzonych
  scenariuszy, nazwane w docstringu `app/data/paid_absence_cost.py`, nienaprawiane (koszt naprawy
  wysoki, efekt przejściowy).

  **Out of scope (explicit):** narzuty (SC-5-02); kwota stała (SC-5-03); FTE (SC-5-04); stawki
  dzienne/miesięczne; wpływ `generates_revenue` na przychód (druga połowa F-05, osobne zadanie);
  suma łączna kosztu osobowego (blok 7); zapis/edycja `absence_type` (brak ścieżki HTTP, ADR-0005
  aneks SC-3-02 pkt 10).

  **Fundament nieudowodniony, przyjęty świadomie:** pierwszy czytelnik flag `generates_cost`/
  `generates_revenue` (SC-3-02: "żadne wyliczenie kosztu/przychodu ich dziś nie czyta"); pierwszy
  czytelnik migawki kalendarza, budżetu i typu nieobecności (SC-3-02: "nikt nie czyta migawki");
  gałąź pozytywna koniunkcji kosztowej nieosiągalna w produkcji (B-01 dziedziczone z SC-5-01,
  rozszerzone o tę składową — ADR-0005 nota B-01); wyścig edycji katalogu z zatwierdzeniem
  (dziedziczone z SC-2-03/SC-5-01); R-02 (odczyt siatki szkicu, przejściowy, opisany wyżej).
  Podstawa: Issue #81, `Wymagania/Requirements_EN.md` §4 F-05, F-07; `docs/PLAN.md` SC-3-02,
  SC-3-03, SC-5-01; `ADR-0004-wersjonowanie-kalkulacji.md` (aneks 2026-09-23 SC-5-06, nowy);
  `ADR-0005-model-dostepu.md` (aneksy SC-3-02 pkt 7, SC-3-03 pkt 3–4/8, SC-5-01 pkt 4/5/7, nota
  B-01 rozszerzona); `ADR-0008-przedzialy-obowiazywania.md` (aneks SC-3-03 pkt 7–10);
  `ADR-0013-koszt-osobowy.md` (aneks 2026-09-23 SC-5-06, nowy).
  **Done 2026-09-24:** PR #82 (scalone `a4c7c71`). Dowód: `backend/tests/test_paid_absence_cost.py`
  (K-01..K-07, N-1..N-4, M-1..M-3), `backend/tests/test_scenario_approval_snapshot.py::test_k_07_m_3_*`,
  `backend/tests/test_scenario_approval.py::test_s_01_a_location_given_a_calendar_*` — 631 testów
  backendowych zielono (było 629), 234 frontendowych bez zmian. Runda weryfikacji (QA, Invariant
  Guardian, reviewer, security-auditor) + poprawka: reviewer i Invariant Guardian niezależnie
  znaleźli, że zatwierdzenie mogło cicho i trwale zmienić składową z `calculated` na `no_budget`
  (typ ustawowy niekosztowy, brak zamrożonego budżetu) — naprawione zmianą kontraktu S-02 migawki
  typu ustawowego (ADR-0004 aneks SC-5-06 pkt 5, zaakceptowane przez człowieka), dwa istniejące
  testy przepisane na nowy kontrakt (nie osłabione). QA: dowód trzymał się, dopisany 1 test
  kontrastowy (`test_m_2_the_frozen_calendar_days_are_read_from_the_own_snapshot_only`). Invariant
  Guardian: PASS (druga runda po poprawce, zero regresji na 130 testach zakresu migawki/budżetu).
  Reviewer: PASS (STOP → poprawka → PASS). Security-auditor: PASS WITH RESERVATIONS — nota B-01
  rozszerzona o tę składową (ADR-0005), bez nowego ryzyka, uśpione z tego samego powodu co SC-5-01.
  **Zaakceptowane, nienaprawione:** brak proporcji kosztu nieobecności do `planned_allocation_hours`
  (ryzyko podwójnego liczenia przy nieodjętym urlopie z planu); brak sumy łącznej kosztu osobowego
  (blok 7); scenariusze zatwierdzone przed tym PR z typem nazwanym-niekosztowym i bez zamrożonego
  budżetu zostają z `no_budget` na zawsze (migawka bez ścieżki UPDATE); R-02 (dwa niezależne odczyty
  siatki w jednym `GET` szkicu, przejściowy rozjazd przy współbieżnej edycji, nie dotyczy
  zatwierdzonych scenariuszy). Zob. `docs/architecture/capabilities.md`.

- [x] **SC-5-08** — Daily and monthly cost rates (F-07): a catalogue default rate carries its cost
  rate unit (`cost_rate_unit` in `hour`/`day`/`month`), and the personnel cost of a scenario is
  computed from the planned allocation hours by that unit's rule (Issue #80). The selling-rate
  `unit` stays pinned to `hour` (capabilities.md, K-07 of SC-2-01); T&M revenue is untouched.
  *Done when:* `backend/tests` prove criteria K-01..K-07 (analyst, 2026-09-29):
  1. (K-01) The three units price the same hours by their own rule: hour = hours x rate; day =
     hours / `standard_hours_per_day` x rate; month = rate x hours / (`working_days_in_month` x
     `standard_hours_per_day`), one final `round_money`, no intermediate rounding. Full-capacity
     month costs exactly the monthly rate; overtime costs more (known, not repaired); a zero-hour
     month is a legal `0.00`, not `no_cost_rate`. Mutations: unit ignored, day/month formulas
     swapped, per-day figure rounded before multiplying, hours capped at capacity.
  2. (K-02) A day/month-rate position whose location has no calendar withholds the whole scenario
     cost as `no_calendar` (ADR-0013 pt 2, no partial sum, hourly positions included); a scenario
     with hourly positions only never needs a calendar. A zero-hour day/month position without a
     calendar is `no_calendar`, not `0.00`; a month-rate position in a month with zero working days
     is the named state `no_working_days` (day rate never); `currency_mismatch` takes precedence
     over `no_calendar`. Mutations: missing calendar yields `0`/skip; calendar required for hour
     rows; zero working days divides or yields `0`.
  3. (K-03) `cost_rate_unit` joins `month_has_cost_rate` in all three places (live query, snapshot
     copier, snapshot reader); a unit change inside a month is `no_cost_rate`; an approved scenario
     costs from its frozen unit, not the live catalogue. Mutations: unit dropped from the copier /
     from the reader only, each killed separately.
  4. (K-04) Fully loaded cost, paid-absence cost and the what-if raise all apply the unit.
     Mutations: hour hard-coded in the paid-absence path alone, then in the what-if path alone.
  5. (K-05) Revenue and the selling-rate `unit` are untouched: existing T&M tests unedited; a
     month-cost-rate position with an hourly selling rate yields the same revenue as with an hour
     cost rate; a structural test forbids revenue modules referencing `cost_rate_unit` and cost
     modules importing revenue modules.
  6. (K-06) The API accepts and returns `cost_rate_unit`, gated like every cost field (ADR-0005):
     absent from the payload without the cost permission, invalid value rejected, omitted value
     stores `hour`; a `PATCH` sending exactly one of `default_cost_rate` / `cost_rate_unit` is a
     `422` with no write (ADR-0005 addendum, pair rule). Mutations: permission check for the field
     removed from response shaping; pair rule removed.
  7. (K-07) The migration is expand-safe on real PostgreSQL: existing catalogue and snapshot rows
     get `hour`, CHECK admits only `hour`/`day`/`month`, and the downgrade refuses (no row values
     echoed) while a non-hour row exists, succeeds otherwise. Mutations: downgrade guard removed;
     `NOT NULL DEFAULT 'hour'` removed.

  **Out of scope (explicit):** frontend (`RateForm.tsx` "unit is not a choice" text and the cost
  cell reading `rate.unit` become stale; follow-up Issue); selling-rate units other than `hour` and
  any change to T&M/Fixed Price revenue; repairing month-rate overtime; changing the `unit` column
  or the capabilities row for it; the contract step of the expand/contract migration; overheads
  (SC-5-02), fixed amount (SC-5-03), FTE (SC-5-04, Issue #79).
  **Basis:** `Wymagania/Requirements_EN.md` §4 F-07; ADR-0013, ADR-0004 and ADR-0002 (addenda
  required), ADR-0008, ADR-0006; `docs/PLAN.md` SC-2-01, SC-5-01, SC-5-02, SC-5-06.
  **Done 2026-09-29:** PR #167 merged; 1207 backend tests green, `ruff` clean, CI green.
  K-01, K-02, K-04: `backend/tests/test_cost_rate_unit.py`; K-03: `test_cost_rate_unit.py::test_k_03_*`;
  K-05: `test_cost_rate_unit.py::test_k_05_*` (structural test and T&M contrast not mutated);
  K-06: `backend/tests/test_cost_rate_unit_catalog_api.py`; K-07:
  `backend/tests/test_cost_rate_unit_migration.py` (real PostgreSQL). Guardian PASS; QA: proof holds
  after four surviving mutants got killing tests, two equivalent mutants named (mutation log in
  `docs/architecture/capabilities.md`). Reviewer and Security-Auditor: PASS WITH RESERVATIONS, all
  fixed except three human-recorded exceptions (owner TanerCRB): the blind-writer unit oracle
  (expiry: authentication ADR, #150), downgrade lock order vs a concurrent approval (expiry: first
  persistent environment), `cost_rate_unit` absent from `assumptions_used` and the frontend
  (expiry: closure of #164; the frontend half was met by SC-5-09, the `assumptions_used` half is
  re-dated at gate 3 of SC-5-09 to Issue #172).
  **Accepted, not repaired:** overtime on a month rate costs more than one month; a location with no
  calendar at approval stays `no_calendar` on that approved scenario for ever; the addenda of
  ADR-0013, ADR-0004, ADR-0002, ADR-0005 and ADR-0015 (2026-09-29) remain "Draft — pending
  approval"; frontend stale (`RateForm.tsx` "unit is not a choice", cost cell reads `rate.unit`),
  tracked in Issue #164.

- [x] **SC-5-09** — Catalogue screen: cost rate unit selector and display (F-07, frontend),
  follow-up of SC-5-08 (Issue #164). The screen offers the cost rate unit as a choice
  (`hour`/`day`/`month`, taken from the API contract's closed set) and shows every cost rate with
  the unit the API returned for that row, never the selling-rate `unit`.
  *Done when:* `frontend` tests prove criteria K-01..K-07 (analyst, 2026-09-29):
  1. (K-01) The unit control offers exactly `COST_RATE_UNITS` from `contracts/catalog.ts`; the
     shape check refuses `"week"`, `""`, a number, or a unit without a rate (or a rate without a
     unit) on reads and on write responses, rendering nothing. Mutations: shape check widened to
     `typeof === "string"`; a private list or a fourth option in `RateForm`; shape check removed
     on write responses; pair check replaced by per-field checks.
  2. (K-02) The cost cell and the edit form read `cost_rate_unit`, never `unit` (fixture with
     `unit: "hour"`, `cost_rate_unit: "month"`; a second run with `"day"`); the edit form seeds its
     unit from the response, not from the rendered cell. Mutations: cell reverted to `rate.unit`;
     form seeded from the label or hard-coded `hour`.
  3. (K-03) A row with the pair withheld (both `null` or both absent) renders no unit and no
     default `hour` beside the cost, while the selling cell still reads `/ hour`. Mutation:
     `cost_rate_unit ?? "hour"` (or `?? rate.unit`) in the cell.
  4. (K-04) Create requires an explicit unit choice (no preselection; saving without one is
     refused with the form's "is required" wording, no request sent), sends `cost_rate_unit` and no
     `unit` key, and the form has exactly one unit control, named as belonging to the cost rate.
     Two runs with different choices give different bodies. Mutations: unit dropped from the body;
     a `unit` key or a selling-unit control added; option label sent instead of the contract value.
  5. (K-05) Edit sends the amount and the unit together or neither: only the selling rate changed
     sends neither; only the unit changed sends both, the amount as loaded at full precision
     (`150.0050`, not `150.01`); a withheld row sends neither and the body never contains
     `cost_rate_unit`. A blind writer is offered no cost-amount edit (Q-1). Mutations: unit sent
     alone; amount sent alone; unit sent for a withheld row; amount seeded from the rounded cell.
  6. (K-06) The stale text is gone for the cost rate ("Every catalogue rate is priced per hour. The
     unit is not a choice." and the screen description "Manage default hourly rates…", pins updated
     in `CatalogScreenStructure.test.tsx`); the selling-rate statement stays and is accurate.
     Mutation: the old `UNIT_NOTE` restored.
  7. (K-07) A `409` whose body carries `cost_rate_unit_precondition` gets its own named ending
     (no stored unit, no rate, no typed value), distinct from the stale-marker message and from
     "unstated"; a `409` carrying `updated_at_marker` keeps its message. Mutation: the cause match
     removed.

  **Existing tests changed by decision (adaptation, never weakened):** `CatalogWrite.test.tsx` K-14
  ("does not offer the unit as a choice") loses its cost-unit half, replaced by K-04, while its
  selling-unit half (no `unit` control, no `unit` key in the body) stays; the two pins in
  `CatalogScreenStructure.test.tsx` follow the reworded texts; fixtures with a cost rate gain
  `cost_rate_unit`.
  **Out of scope (explicit):** backend changes; the selling-rate unit; exposing the unit in the
  scenario cost response (`assumptions_used`, Issue #172); a blind-writer control to change a cost
  amount (would make the recorded unit oracle a one-click feature); any conversion or derived
  hourly figure on screen (ADR-0002 pt 5).
  **Basis:** `Wymagania/Requirements_EN.md` §4 F-07, NF-07; ADR-0002 addendum 2026-09-29, ADR-0005
  addenda 2026-09-21 (SC-2-04) and 2026-09-29, ADR-0009 and ADR-0007 (addenda required), ADR-0008
  addendum 2026-09-19 pt 2, ADR-0010; `docs/PLAN.md` SC-5-08.
  **Done 2026-09-29:** PR #173 merged; frontend 414 tests green, `pnpm lint` and `pnpm build`
  clean, CI green. K-01..K-07: `frontend/src/features/catalog/CatalogCostRateUnit.test.tsx`
  (`K-01: …` x3, `K-02: …` x2, `K-03: …`, `K-04: …` x3, `K-05: …` x7 incl. the unit-change warning
  and the edit/create options, `K-06: …`, `K-07: …`); the closed set and the condition identifier
  against the backend source: `frontend/src/api/contracts/writeRefusals.test.ts`. Guardian PASS,
  Security-Auditor PASS, Reviewer PASS after re-review, QA: proof holds after two tests killed five
  first-suite survivors (mutation log in `docs/architecture/capabilities.md`). Existing tests
  changed by decision: `CatalogWrite.test.tsx` K-14 (cost-unit half moved to K-04), two pins in
  `CatalogScreenStructure.test.tsx`, fixtures.
  **Recorded exception (owner TanerCRB):** the strict pair check refuses the whole catalogue read
  when a frontend with this change is served by a backend that does not return `cost_rate_unit`
  (rule: deploy the backend before the frontend, roll back the frontend first; expiry: first
  persistent environment). The `assumptions_used` half of the SC-5-08 exception (3) is re-dated to
  Issue #172.
  **Accepted, not repaired:** the positive branch of the cost gate is fixture-only (no
  `PERSONNEL_COSTS_READ` in the placeholder set); the blind-writer `409` ending is reachable only in
  a race or from a non-UI client and is proven by a stubbed `409`; a blind writer is offered no
  cost-amount edit; an empty `role="status"` element leaves a small layout gap; the ADR-0009 and
  ADR-0007 addenda of 2026-09-29 (third `409` cause) remain "Draft — pending approval".

- [x] **SC-5-11** — Show the cost-rate unit in scenario cost results (F-07). Authorized scenario results expose each resolved rate window's `cost_rate_unit` only in gated `assumptions_used.rate_windows` and display the unit beside its corresponding resolved rate. Callers missing either cost permission receive no unit; cost calculation and rate-window resolution are unchanged.
  *Done when:* Backend response tests prove per-window unit mapping, nested-only shape, and withholding for callers missing either conjunct; `frontend/src/features/projects/ScenarioResults.test.tsx` proves each window's unit is displayed with its corresponding rate and that an absent unit affects only its own display. Update the five exact-body tests by the gate-1 decision.
  **Out of scope (explicit):** Catalogue behavior (#164), selling-rate units, cost computation, and rate-window resolution.
  **Basis:** Issues #80, #164, #172; ADR-0005 addendum 2026-09-29; SC-5-08 and SC-5-09.
  **Done 2026-10-01:** PR #215 (merge commit `e592843`). Evidence: `backend/tests/test_scenario_results_access.py::test_k_01_results_keep_each_resolved_rate_window_unit` proves per-window mapping and nested-only response shape; caller access and personnel-cost permission cases prove withholding when either gate is closed. `frontend/src/features/projects/ScenarioResults.test.tsx` proves each unit is paired with its own rate and missing units affect only their own display. SC-5-11-M1 mutation evidence: [Issue #172 QA record](https://github.com/TanerCRB/StafffingCalculator/issues/172#issuecomment-5940142938). Both required CI jobs passed; local backend suite (1,536 passed, 6 xfailed), frontend suite (435 passed), Ruff, ESLint, and production build passed.
- [x] **SC-6-01** — Duplikuj scenariusz niezależnie od źródła (F-09 pkt 1, AC-02). Nowy entry point
  do istniejącego mechanizmu kopiowania (`copy_scenario`/`SCENARIO_CHILD_COPIERS`, ADR-0004) —
  `into_project=source.project` zamiast nowego projektu (SC-1-03 zawsze tworzył nowy).
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-06 (analyst, gate 1 zaakceptowane
  2026-09-24):
  1. (K-01) Duplikat trafia do TEGO SAMEGO projektu, nigdy nowego — kontrast z `POST /projects/{id}/copy`
     (SC-1-03), który zawsze zwiększa liczbę projektów.
  2. (K-02) Duplikat zawsze `draft`, niezależnie od statusu źródła (`draft`/`approved`); status
     źródła nietknięty.
  3. (K-03) AC-02 przez ten entry point dla wszystkich tabel w `SCENARIO_CHILD_COPIERS` (obsada +
     alokacje + nieobecności, reguła komercyjna + T&M, koszt dodatkowy obie połówki) — obie
     kierunki (edycja duplikatu nie rusza źródła i odwrotnie).
  4. (K-04) Scenariusz spoza zasięgu wołającego → `404`, nigdy `403`, zero zapisanych wierszy.
  5. (K-05) Brak uprawnienia `SCENARIO_COPY` (nowe, gate 1 — architect, nie reużycie `PROJECT_COPY`:
     ziarnistość per akcja-na-encji, nie po mechanizmie) → `403`, zero zapisanych wierszy, mimo
     zasięgu.
  6. (K-06) `scenario_id` istnieje pod INNYM `project_id` niż w ścieżce → `404`.

  **Decyzje bramki 1 (2026-09-24, analyst + architect, zaakceptowane przez człowieka):** nazwa
  duplikatu generowana przez backend (sufiks `(copy)`/`(copy N)`, retry na kolizję) zamiast
  przyjmowana w ciele żądania — endpoint zostaje bez ciała, zgodnie z konwencją siostrzanych akcji
  (`copy_project`/`archive`/`approve`); wynika z `UniqueConstraint(project_id, name)` na
  `scenarios`, którego `copy_scenario` nigdy dotąd nie zderzał (każde wcześniejsze wywołanie szło
  do nowego projektu). Nowe uprawnienie `SCENARIO_COPY`.

  **Runda weryfikacji (QA, Invariant Guardian, reviewer, security-auditor) + poprawka.** QA:
  proof holds — 1 lukę pokrycia (porządek 404-przed-409 dla kolizji nazwy w projekcie spoza
  zasięgu) domknęła własnym testem kontrastowym; 3 mutacje (regresja K-01, off-by-one sufiksu,
  "moved not copied" w kopierze stafingu) — wszystkie killed. Invariant Guardian: PASS (źródło
  tylko czytane `FOR SHARE`, nigdy zapisywane; deny-by-default; kaskada niezmieniona
  strukturalnie). Security-auditor: PASS (autoryzacja, brak nadmiarowej ekspozycji odpowiedzi,
  brak nowej zależności/migracji, dyscyplina NF-11). Reviewer: PASS WITH RESERVATIONS →
  poprawka → PASS. Reviewer znalazł R-01 (Medium): łańcuchowe duplikowanie duplikatu wydłuża
  nazwę bez kolizji (sufiks `(copy)` zawsze wolny), po ~27-28 skokach przekracza `String(200)` →
  `DataError` niezmapowany → surowy, permanentny `500`. Naprawione: `_bounded_base_name` obcina
  bazę do budżetu kolumny PRZED generowaniem kandydatów — łańcuch dowiedziony jako zbiegający do
  stałego punktu (fixed point), nie tylko obserwacyjnie nienaprawiony. Reviewer zweryfikował
  naprawę ponownie: PASS.

  **Out of scope (explicit):** porównanie ≥3 scenariuszy i analiza wrażliwości (F-09 pkt 2-3) —
  Issues #87, #88; widoczność rezerw ryzyka i podwójna reprezentacja (F-09 pkt 4-5, mechanizm
  rezerw nie istnieje jeszcze w F-08) — Issue #89; nowa wersja po zatwierdzeniu jako nazwana akcja
  (F-12, blok 8); `audit_log` (blok 8); idempotencja (zaakceptowany koszt jak SC-1-03); frontend
  (zadanie czysto backendowe); realny wyścig współbieżny na nazwie (ścieżka odmowy `409`
  okablowana, niećwiczona współbieżnie, ten sam zaakceptowany wzorzec co SC-1-03).

  **Fundament nieudowodniony, przyjęty świadomie:** K-03 dla `commercial_terms`/`tm_terms` dowodzi
  niezależność przez rozłączne identyfikatory, nie przez edycję — router nie ma ścieżki
  edycji/usuwania dla tej tabeli (nazwane w module docstring testu). Podstawa: Issue #11,
  `Wymagania/Requirements_EN.md` §4 F-09 pkt 1, §7 AC-02; `docs/PLAN.md` SC-1-03, SC-3-01, SC-3-02,
  SC-3-03, SC-4-01, SC-5-05; `ADR-0004-wersjonowanie-kalkulacji.md` (aneks SC-6-01);
  `ADR-0005-model-dostepu.md` (aneks SC-6-01, nowe uprawnienie `SCENARIO_COPY`).
  **Done 2026-09-24:** PR #90 (scalone `df3c174`). Dowód: `backend/tests/test_scenario_duplication.py`
  (K-01..K-06, R-01, testy algorytmu nazewnictwa) — 701 testów backendowych zielono, 234
  frontendowych bez zmian. Zob. `docs/architecture/capabilities.md`.

- [x] **SC-7-01** — Wylicz i udostępnij zysk, marżę i markup scenariusza jako sumę całościową
  (F-10, część). Zarezerwowane, kryteria (K-01..K-06) i decyzje bramki 1 w Issue #12.
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-06 (analyst 2026-09-24): (1) arytmetyka
  zysku/marży/markupu na żywym endpointzie zgodna z AC-01; (2) przychód rozstrzygnięty jako zero →
  zysk/strata liczbowe, marża `"n/a"` (AC-05), dowiedzione na żywym endpointzie, nie tylko na
  `ratio_percent`; (3) scenariusz spoza zasięgu wołającego nieodróżnialny od nieistniejącego;
  (4) trzy składowe `included_cost` (koszt bazowy SC-5-01, nieobecności płatne SC-5-06, koszt
  dodatkowy fixed-amount SC-5-05) addytywne, żadna nie liczona dwa razy ani po cichu pominięta;
  (5) składowa w stanie nazwanym niekalkulowalnym przenosi swoją własną nazwę stanu, nigdy nie
  udaje liczby; (6) nowe uprawnienie `RESULTS_READ` bramkuje dostęp do endpointu, część wyniku
  pochodząca z kosztu osobowego dodatkowo bramkowana koniunkcją `PERSONNEL_COSTS_READ` ∧
  `can_view_personnel_costs` (czwarta funkcja kształtująca, ADR-0005 aneks 2026-09-24), koszt
  dodatkowy wchodzi bez koniunkcji.
  **Out of scope (explicit):** rozbicie na okresy raportowania (miesiące) — źródła nie niosą dziś
  kwoty per miesiąc, warunek domknięcia: rozszerzenie źródeł jako osobne zadanie; planned hours/FTE
  i billable ratio — brak dziś powierzchni API, osobne przyszłe zadanie; deviation from target
  margin — wymaga marży z tego zadania, osobne zadanie zaraz potem; procentowe/headcount'owe
  kategorie kosztu dodatkowego (F-08 reszta, SC-5-07+) — zobowiązanie naprzód: rozszerzają
  `included_cost` w swoim PR; wykresy/eksport (F-11); ekran wyników (osobne zadanie frontendowe);
  agregacja wielu scenariuszy jednego Projektu w jedną liczbę — "complete project" w F-10 rozumiane
  tu jako pojedynczy scenariusz (ADR-0005 aneks 2026-09-24 pkt 6).
  Podstawa: `Wymagania/Requirements_EN.md` §3, §4 F-10/F-13, §7 AC-01/AC-05;
  `docs/architecture/decisions/ADR-0002-obsluga-pieniedzy.md` (aneks 2026-09-24);
  `docs/architecture/decisions/ADR-0005-model-dostepu.md` (aneksy SC-5-01 pkt 4/5, SC-5-05 pkt 5,
  aneks 2026-09-24); `docs/architecture/capabilities.md` (SC-4-01, SC-5-01, SC-5-05, SC-5-06).
  **Done 2026-09-24:** PR #91 (scalone `98d0bc5`). Dowód: `backend/tests/test_scenario_results.py`
  (K-01, K-02, K-04 ×4, K-05 ×4, test strukturalny reguły 10), `backend/tests/test_scenario_results_access.py`
  (K-03, K-06 ×5), `backend/tests/test_scenario_results_race.py` (R-01, 3 testy ze współbieżnością
  dwóch połączeń, wzorem `test_project_group_two_race.py`) — 21 nowych testów, 722 testy backendowe
  zielono na CI po scaleniu z SC-6-01 (main przesunął się w trakcie pracy nad tym zadaniem). Runda
  weryfikacji (QA, Invariant Guardian, reviewer, security-auditor) + poprawki: **R-01 (High,
  reviewer)** — `scenario_results_for_caller` składał trzy niezależne odczyty (`commercial_terms_for_caller`,
  `scenario_cost_for_caller`, `additional_costs_for_caller`), z których dwa gałęzią na
  `scenario.status`; przy zatwierdzeniu scenariusza commitującym MIĘDZY tymi dwoma odczytami
  (`READ COMMITTED`, brak podniesienia izolacji w stosie) przychód liczył się live sprzed
  zatwierdzenia a koszt z migawki po nim, w jedno `profit` bez żadnego oznaczenia — deweloper
  odtworzył defekt deterministycznie (cofnięcie strażnika → odpowiedź `200` z rozjechanymi
  `rate_source` i błędnym `scenario_status`), naprawione porównaniem `assumptions_used.rate_source`
  obu odczytów i `409` przy rozjeździe zamiast cichego zmieszania danych; **R-02 (Low, reviewer)**
  — treść błędu `409` ujawniała dosłowne `rate_source` niezależnie od `PERSONNEL_COSTS_READ` —
  naprawione generycznym komunikatem, oba mutation-checked (cofnięcie poprawki → czerwono na obu
  wariantach uprawnień → przywrócenie → zielono). QA: PROOF HOLDS, 6/7 mutacji zabitych czysto w
  runda 1 + mutacje R-01/R-02 w rundzie 2. Invariant Guardian: PASS (dwie rundy). Security-auditor:
  PASS (ryzyko B-01 rozszerzone jakościowo, nie szerzej niż nazwane — koszt osobowy widoczny wprost
  w tym samym payloadzie przy spełnionej koniunkcji, arytmetyczne odtworzenie nic nie dodaje).
  Reviewer: PASS WITH RESERVATIONS → poprawki → PASS. **Zaakceptowane, nienaprawione:** mutacja
  "wspólny sentinel" dla K-05 nie zabija w izolacji dla wariantu `additional_cost`/
  `currency_mismatch` (jedyny literał wspólny trzem typom `Literal` zbiega z oczekiwaną wartością
  tego wariantu) — cały parametrized test i tak czerwienieje przez pozostałe dwa warianty, CI to
  wykrywa; porównanie `rate_source` nie wykrywa rozjazdu dwóch odczytów "live" różniących się
  generacją cennika przy edycji aktywnego okna stawki w trakcie odczytu (węższe, rzadsze ryzyko niż
  R-01, nazwane wprost w docstringu `app/data/scenario_results.py`); trzy złożone odczyty pozostają
  trzema osobnymi round-tripami, nie jednym zapytaniem (zaakceptowany kompromis architekta, R-01
  ogranicza tylko konkretny, wykryty przypadek rozjazdu). Zob. `docs/architecture/capabilities.md`.

- [x] **SC-6-02** — Porównaj scenariusze tego samego projektu (F-09 pkt 2). `GET
  /projects/{project_id}/scenarios/compare?scenario_id=...` (powtórzony query param), składa N razy
  istniejący mechanizm `scenario_results_for_caller`/`shape_scenario_results` (SC-7-01), bez nowej
  kalkulacji.
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-05 (analyst, gate 1 zaakceptowane
  2026-09-24):
  1. (K-01) Metryki zestawienia N scenariuszy tożsame z niezależnym odczytem każdego z osobna, bez
     mieszania składowych między scenariuszami — kontrast: zmiana jednego wejścia zmienia tylko
     jeden wiersz.
  2. (K-02) Bramka kosztu osobowego (koniunkcja `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs`)
     aplikowana identycznie na KAŻDYM wierszu, nigdy raz dla całej odpowiedzi.
  3. (K-03) Scenariusz spoza zasięgu wołającego nazwany w żądaniu porównania → CAŁA odpowiedź
     `404`, nieodróżnialna od żądania z tym samym id jako jedynym argumentem (all-or-nothing, gate
     1 decyzja 2 — bez partial-success).
  4. (K-04) Scenariusze z dwóch różnych projektów w jednym żądaniu → odrzucone, wymuszone
     strukturalnie przez `Project.scenarios` (nie drugi, niezależny lookup) — dowiedzione WŁASNYM
     testem tego zadania, nie odziedziczone z K-03 (QA: mutacja przeżyła 21 testów pojedynczego
     endpointu, zabita wyłącznie przez K-04).
  5. (K-05) Stan nazwany niepoliczalny jednego scenariusza nie zawala całego zestawienia, nie
     zwija się do wspólnego sentinela — pozostałe wiersze niezależne.

  **Decyzje bramki 1 (2026-09-24, analyst + architect, zaakceptowane przez człowieka, aneksy
  ADR-0001/ADR-0005, commit 53932f9):** żądanie jako zbiór `scenario_id` (powtórzony query param,
  bez ciała, wzorem `GET /catalog/rates`); all-or-nothing `404`/`409` (żaden partial-success —
  jeden zły/rasujący id blanki całą odpowiedź, zaakceptowany kompromis); `RESULTS_READ` bez zmian
  (ta sama akcja N razy, nie nowa); **obsada POZA zakresem metryk tego zadania** — żadna wartość
  skalarna (peak headcount / suma osobo-miesięcy / FTE) nie istnieje dziś w kodzie ani nie ma
  jednoznacznej definicji w Requirements_EN.md (F-10 "Planned hours and FTE" niezbudowane, F-11
  sugeruje że to seria/timeline, nie liczba jak reszta czterech metryk) — odłożone do zadania po
  zbudowaniu F-10.

  **Runda weryfikacji (QA, Invariant Guardian, reviewer, security-auditor) + poprawka.** QA: proof
  holds — 5 mutacji (reorder wyścigu, 404→continue, default-to-all, cross-project scope, gate
  cached z pierwszego wiersza); 4 zabite oryginalnymi testami, 1 (gate cached) niewykrywalna
  black-box z powodu gate-1 decyzji 7 (jeden projekt na żądanie) — zamknięta nowym testem
  białoskrzynkowym (`test_k_02_the_gate_verdict_is_never_cached_from_the_first_row_onto_later_rows`,
  monkeypatch wymuszający rozbieżne werdykty). Invariant Guardian: PASS (brak nowej arytmetyki,
  deny-by-default, zasięg strukturalny, bramka per wiersz potwierdzona w kodzie nie tylko w
  testach). Security-auditor: PASS WITH RESERVATIONS → poprawka → PASS. Reviewer: PASS WITH
  RESERVATIONS → poprawka → PASS. Oboje niezależnie znaleźli R-01 (Medium): nielimitowany
  powtarzany `scenario_id` = nielimitowana liczba sekwencyjnych round-tripów DB na jednym
  połączeniu (N=1000 → 3000+ round-tripów), realne wyczerpanie poola, dziś bez uwierzytelniania więc
  dotyczy każdego wołającego. Naprawione: `MAX_COMPARE_SCENARIOS=50` (`Query(max_length=...)`,
  wzorem `MAX_ALLOCATION_MONTHS` ze `staffing.py`), odmowa `422` przed jakimkolwiek dostępem do
  bazy — dowiedzione spy'em, który zawaliłby test gdyby DB zostało dotknięte. Oboje zweryfikowali
  naprawę ponownie: PASS.

  **Out of scope (explicit):** obsada jako metryka (patrz decyzje bramki 1 wyżej); analiza
  wrażliwości (Issue #88), widoczność rezerw ryzyka (Issue #89); walidacja minimalnej liczby
  scenariuszy — Issue opisuje typowy workflow PM-a, nie wymóg API; duplikaty `scenario_id` w jednym
  żądaniu (nieszkodliwe, nietestowane osobno); real-concurrency dla wyścigu przez ten endpoint
  (dowiedzione monkeypatchem — podstawowy mechanizm już dowiedziony realną współbieżnością w
  SC-7-01); rate limiting/throttling per wołający (`MAX_COMPARE_SCENARIOS` ogranicza koszt JEDNEGO
  żądania, nie liczbę żądań — kontencja poola przy wielu równoległych wołających nazwana przez
  reviewera, osobna kwestia infrastrukturalna); frontend (zadanie czysto backendowe).

  **Fundament nieudowodniony, przyjęty świadomie:** precedencja między 404-powodującym a
  409-powodującym id w tym samym żądaniu przy różnych pozycjach — implementacja: pierwszy
  terminalny wynik w kolejności żądania wygrywa, niezdecydowane wprost na bramce 1, sprawdzone
  przez QA jako niepowodujące wycieku informacji o pozostałych id. Podstawa: Issue #87,
  `Wymagania/Requirements_EN.md` §4 F-09 pkt 2; `docs/PLAN.md` SC-7-01, SC-4-01, SC-5-01, SC-5-05,
  SC-1-03; `ADR-0001-trwalosc-danych.md` (aneks SC-6-02); `ADR-0005-model-dostepu.md` (aneks
  SC-6-02).
  **Done 2026-09-24:** PR #96 (scalone `fdc4653`). Dowód: `backend/tests/test_scenario_results_compare.py`
  (K-01..K-05, R-01 — 15 testów) — 737 testów backendowych zielono, 234 frontendowych bez zmian.
  Zob. `docs/architecture/capabilities.md`.

- [x] **SC-6-04** — Pokaż wpływ hipotetycznej podwyżki wynagrodzeń na koszt i zysk scenariusza
  (F-09 pkt 3, wariant 1/4 analizy wrażliwości). Pierwszy w repo mechanizm "przelicz bez zapisu"
  (compute-without-persist) — nowa decyzja architektoniczna ADR-0015 (Draft — pending approval) +
  aneks ADR-0013. `GET /projects/{project_id}/scenarios/{scenario_id}/what-if?salary_raise_percent=`.
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-06 (analyst, gate 1 zaakceptowane
  2026-09-24):
  1. (K-01) Podstawienie w istniejące funkcje (`base_personnel_cost`, `paid_absence_cost`,
     `_worked_months`, `paid_absence_months`), nigdy nowa, piąta ścieżka licząca — DWA nośniki
     razem: strukturalny (graf importów) + behawioralny (0%≡`GET .../results`) + kontrast na
     mieszanym miesiącu (odróżnia prawdziwe podstawienie per-miesiąc od naiwnego mnożenia
     agregatu).
  2. (K-02) Żaden wiersz danych scenariusza nie zmienia się po wywołaniu — dowód wzmocniony przez
     QA: `session.new`/`.dirty`/`.deleted` puste NIE wystarcza (autoflush czyści przed
     sprawdzeniem), domknięte raw SQL re-read + kanarek `updated_at`.
  3. (K-03) Scenariusz spoza zasięgu → `404` jak nieistniejący.
  4. (K-04) Koniunkcja kosztu osobowego dziedziczona w warstwie kształtującej odpowiedź, nigdy na
     bramce dostępu endpointu (dokładnie mutacja, która już raz złamała SC-7-01).
  5. (K-05) Przychód niewrażliwy na hipotezę kosztową (dwie magnitudy podwyżki, revenue identyczne,
     koszt różny).
  6. (K-06) Scenariusz `approved` → ten sam `404` co K-03, nigdy osobny kształt błędu ani cichy
     przelicz na migawce.

  **Decyzje bramki 1 (2026-09-24, analyst + architect, zaakceptowane przez człowieka, ADR-0015
  nowa + aneks ADR-0013, commit fa8cd68):** `rate_source` rozszerzony o trzecią wartość
  `WHAT_IF_HYPOTHETICAL` (opcja A) — zaudytowane, nigdy nie dociera do porównania strażnika
  wyścigu `ScenarioResultsRaceDetected` (what-if buduje własną kompozycję, nigdy nie woła
  `scenario_results_for_caller`); zakres wyłącznie `draft`, z odziedziczonym strażnikiem wyścigu na
  stronie odczytu (przychód czytany osobno od kosztu, jak SC-7-01); podwyżka procentowa, nie
  kwotowa (brak słownictwa na walutę podwyżki w ADR-0013); `GET` nie bezciałowy `POST` (to odczyt,
  nie akcja); `RESULTS_READ` bez zmian, żadnego nowego uprawnienia — rozszerza już nazwany, uśpiony
  gap B-01; podwyżka dotyka WSPÓLNEGO słownika stawek, więc `paid_absence_cost` też, nie tylko
  koszt bazowy.

  **Runda weryfikacji (QA, Invariant Guardian, reviewer, security-auditor) + poprawka.** QA: proof
  holds po naprawie realnej metodologicznej luki — `session.new/dirty/deleted` puste nie dowodzi
  braku zapisu (autoflush), zademonstrowane mutacją (`scenario.name = ...` przeżyło oryginalny
  test, naprawdę zapisało wiersz), domknięte nowym testem. Invariant Guardian: PASS WITH
  RESERVATIONS → poprawka → PASS. S-01 (Medium): zero testów (nawet monkeypatch) dla własnej
  ścieżki 409 what-if — naprawione testem monkeypatch na realnym call site, potwierdzone. Reviewer:
  PASS WITH RESERVATIONS → poprawka → PASS. R-01 (Medium): `salary_raise_percent` bez dolnego
  ograniczenia — podwyżka -500% daje ujemną stawkę, `profit`>`revenue`, marża >100%, fizycznie
  niemożliwe ale pewny `200` — naprawione `ge=-100` (rata staje się dokładnie 0, nigdy ujemna),
  dwa testy (odmowa poniżej granicy, dokładna granica nie jest pułapką off-by-one), potwierdzone.
  Security-auditor: PASS WITH RESERVATIONS → poprawka → PASS. Dwie notatki: (1) kolejność
  check-po-odczycie dla scenariusza `approved` (realne dane czytane w pamięci przed odmową, nigdy
  serwowane) — doprecyzowany docstring, nie zmiana zachowania; (2) `WHAT_IF_HYPOTHETICAL` jako
  przyszła pułapka słownikowa dla kodu, który jeszcze nie istnieje — zaakceptowane jako nazwane,
  nieblokujące ryzyko na przyszłość.

  **Out of scope (explicit):** pozostałe 3 warianty analizy wrażliwości (spadek utylizacji #100,
  opóźniony start #101, kurs walutowy #102) — odziedziczą ten sam wzorzec compute-without-persist;
  podwykonawcy/dostawcy (strukturalnie nieosiągalni); zapis wyniku what-if jako nowego scenariusza
  (już istnieje jako duplikacja, SC-6-01); frontend; real-concurrency (dwa realne połączenia) dla
  WŁASNEJ ścieżki 409 what-if — odziedziczony mechanizm dowiedziony realną współbieżnością na
  siostrzanym endpoincie (SC-7-01), dla what-if dowiedziony czytaniem kodu + testem monkeypatch,
  nie pełną współbieżnością (nazwane, zaakceptowane).

  **Fundament nieudowodniony, przyjęty świadomie:** sam mechanizm compute-without-persist nie miał
  precedensu w repo przed tym zadaniem — formuły (`base_personnel_cost`, `paid_absence_cost`,
  `scenario_profitability`) dowiedzione (SC-5-01/SC-5-06/SC-7-01), sam mechanizm podstawienia
  budowany od zera. Reviewer zanotował (niebllokująco): granica `ge=-100` żyje w warstwie
  schematu/API, nie wewnątrz `scenario_what_if_salary_raise_for_caller` — przyszły wariant
  (SC-6-05/06/07), jeśli reużyje mnożnika przez inny punkt wejścia niż ten `Query`, musiałby
  ponownie zastosować granicę, nie dziedziczy jej za darmo. Podstawa: Issue #88,
  `Wymagania/Requirements_EN.md` §4 F-09 pkt 3; `docs/PLAN.md` SC-5-01, SC-5-06, SC-7-01;
  `ADR-0015-przeliczenie-bez-zapisu.md` (nowa); `ADR-0013-koszt-osobowy.md` (aneks).
  **Done 2026-09-24:** PR #106 (scalone `9f3b94b`). Dowód: `backend/tests/test_scenario_what_if.py`
  (K-01..K-06, S-01, R-01 — 12 testów) — 749 testów backendowych zielono, 262 frontendowych bez
  zmian. Zob. `docs/architecture/capabilities.md`.

- [x] **SC-6-06** — Show the impact of delaying a project start on scenario profitability (F-09
  point 3, backend). Applies one forward calendar-month shift to every staffing allocation month
  and evaluates staffing-linked inputs at the destination month; it does not move independently
  dated additional-cost rows.
  *Done when:* `backend/tests` prove criteria K-01..K-06 (analyst, 2026-09-30; gate 1 decisions
  confirmed 2026-09-30):
  1. (K-01) Every staffing allocation is evaluated after the requested whole calendar-month shift;
     N=0 reproduces the unshifted result. Contrast N=0 with N=1, including a month-end case that
     distinguishes calendar-month from fixed-day arithmetic. Mutations: remove the shift, shift
     only one allocation, or use a fixed-day duration.
  2. (K-02) Under the currently supported one-commercial-rule-per-scenario model, T&M revenue uses
     staffing-linked inputs from the applicable destination month and profitability follows
     existing result semantics. Models whose revenue is independent of staffing retain their
     existing revenue semantics. Combining multiple commercial models in one scenario is out of
     scope until the data model supports it. Contrast destination months covered by different
     rate/calendar windows. Mutations: resolve inputs at saved months, choose the latest rate
     regardless of effective date, omit a result component, or count an applicable component more
     than once.
  3. (K-03) Saved absence dates remain absolute and affect shifted staffing months only where they
     overlap. Contrast an overlapping saved absence with an otherwise equivalent non-overlapping
     absence. Mutations: shift absence dates with staffing or skip overlap against the destination
     month.
  4. (K-04) Independently dated additional-cost rows keep their saved periods, and the what-if
     changes no persisted scenario data; prove via before/after reads and direct database reads
     outside the ORM identity map. Contrast a shifted result with unchanged cost rows and saved
     inputs. Mutations: shift additional-cost periods or persist a shifted allocation/result.
  5. (K-05) Missing destination-month rates or calendars preserve existing named component states
     and dependent-metric behavior without invented or partial numeric totals. Contrast a month
     outside coverage with one inside coverage. Mutations: substitute zero/default, skip the
     missing-input state, or emit a partial total.
  6. (K-06) Project access, personnel-cost field visibility, draft-only eligibility, and existing
     scenario status/source mismatch refusal retain their boundaries. Contrast authorized and
     out-of-scope callers, personnel-cost permission states, and consistent versus mismatched
     scenario reads. Mutations: remove the access check, draft/status check, field gate, or status/
     source race refusal. No stable live rate/calendar snapshot across the request is claimed.

  **Gate 1 decisions (2026-09-29 and 2026-09-30, Issue #101):** `draft` only; named unavailable
  states follow existing component/dependent-metric rules; shift staffing-linked calculations,
  keep saved absence dates absolute and apply them where they overlap shifted staffing months,
  and leave independently dated additional costs in their saved periods. K-06 covers existing
  status/source mismatch handling, not a stable live rate/calendar snapshot. K-02 is limited to
  the currently supported one-commercial-rule-per-scenario model; combined simultaneous models
  remain out of scope until the data model supports them. ADR-0015 addenda 2026-09-29 and 2026-09-30.
  **Unproven foundation, consciously accepted:** delayed-start substitution across rate/calendar
  windows and its result-state behavior; SC-6-04 proves only the salary-raise what-if path.
  **Out of scope (explicit):** backward shifts; shifting only selected staffing positions;
  independently changing project duration; moving additional-cost periods; the other sensitivity
  variables (utilization #100 and exchange rates #102); UI; persisting a what-if result as a new
  scenario.
  **Basis:** `Wymagania/Requirements_EN.md` §4 F-09 point 3; ADR-0015 (addendum 2026-09-29);
  ADR-0002, ADR-0004, ADR-0005, ADR-0008, ADR-0013; `docs/PLAN.md` SC-3-01, SC-3-03, SC-4-01,
  SC-5-01/05/06, SC-7-01; Issue #101.
  **Done 2026-09-30:** PR #187 (merge commit `0739597`). Evidence: `backend/tests/test_scenario_what_if_delayed_start.py` (K-01–K-06; 10 tests), full backend suite (1,465 passed, 6 xfailed), frontend suite (414 passed), Ruff, and CI. QA mutation results are recorded in the capability registry and Issue #101 comment https://github.com/TanerCRB/StafffingCalculator/issues/101#issuecomment-5907146121.

- [x] **SC-6-07** — Show how a hypothetical replacement exchange rate for one currency pair affects a draft scenario result (F-09 point 3, sensitivity analysis variant 4/4; Issue #102).
  *Done when:* `backend/tests` prove: (1) a replacement rate for one selected currency pair recalculates an affected draft scenario result using the existing exchange conversion and scenario-result calculation; the current rate reproduces the baseline; (2) no hypothetical input or result is persisted, proven by identical `GET .../results` responses before and after; (3) an out-of-scope or approved scenario is indistinguishable from a nonexistent one (`404`); and (4) a scenario with no affected foreign-currency amount returns an explicit `not_applicable` state, while an affected scenario returns a calculated result. QA records the named mutations and results.
  **Gate 1 decisions (2026-09-30, Issue #102):** draft scenarios only; request supplies a direct replacement rate; one currency pair per call; a scenario with no affected foreign-currency amount returns an explicit not-applicable state.
  **Unproven foundation, knowingly accepted:** hypothetical exchange-rate substitution and its not-applicable result state; existing currency conversion, scenario results, access controls, and the compute-without-persist pattern have prior evidence (ADR-0002, ADR-0006, SC-7-01, SC-6-04).
  **Out of scope (explicit):** automated exchange-rate feeds; changing multiple currency pairs in one call; UI; persisting the hypothetical result as a new scenario.
  **Basis:** `Wymagania/Requirements_EN.md` §4 F-09 point 3 and §6; ADR-0002, ADR-0006, ADR-0015; `docs/PLAN.md` SC-7-01 and SC-6-04; Issue #102.
  **Done 2026-10-01:** [PR #202](https://github.com/TanerCRB/StafffingCalculator/pull/202) (merge commit `87f4b0e`). Evidence: [`test_scenario_what_if.py`](../backend/tests/test_scenario_what_if.py) (`test_k_01_replacement_exchange_rate_recalculates_the_existing_scenario_result` — replacement and current-rate contrast; `test_k_02_exchange_rate_what_if_leaves_saved_result_identical`; `test_k_03_exchange_rate_what_if_hides_out_of_scope_and_approved_scenarios`; `test_k_04_unaffected_currency_pair_returns_not_applicable`; `test_exchange_rate_state_does_not_disclose_hidden_personnel_cost_currency`); [QA mutation record](https://github.com/TanerCRB/StafffingCalculator/issues/102#issuecomment-5918896242). Full backend: 1,519 passed, 6 xfailed; frontend: 424 passed; [backend CI](https://github.com/TanerCRB/StafffingCalculator/actions/runs/36773042674) and [frontend CI](https://github.com/TanerCRB/StafffingCalculator/actions/runs/36773042445) passed. Ruff, `git diff --check`, Invariant Guardian, Reviewer, and Security Auditor passed.

- [x] **SC-6-05** — Show the impact of a hypothetical reduction in billable utilization on scenario revenue and profit (F-09 pt. 3, sensitivity analysis variant 2/4; Issue #100).
  *Done when:* `backend/tests` prove K-01–K-07 for a draft T&M scenario: a percentage-point reduction is applied per position/month; zero-planned-allocation rows stay unchanged; a negative requested reduction or a reduction that would make any billable-hours value negative refuses the whole request with a generic `422`; a zero reduction succeeds as the baseline; revenue and profitability use the existing calculations with personnel and other costs at baseline; no scenario data is persisted; and out-of-scope or approved scenarios receive the specified indistinguishable `404`. QA records the named mutations and their results.
  **Out of scope (explicit):** Fixed Price, Outcome-based, and Story Points, whose existing revenue models do not use billable utilization; changing personnel, paid-absence, or additional costs, because the approved hypothesis changes revenue only; UI behavior or saving the hypothetical as a scenario, because this task proves the calculation path only; the remaining sensitivity variants, which have their own Issues (#101 and #102).
  **Gate 1 decisions (2026-09-29, approved by the human):** utilization is billable hours divided by planned allocation hours per position/month; the decrease is in percentage points; revenue changes while costs remain at baseline; a negative requested reduction or a negative hypothetical billable-hours result refuses the whole request with a generic `422`; a zero reduction is valid and reproduces the baseline; zero-planned-allocation rows retain their saved billable hours.
  **Foundation risk, knowingly accepted:** the utilization measure and its hypothetical transformation are new and unproven; T&M revenue, profitability, access control, and the salary-raise compute-without-persist precedent are already proven (SC-4-01, SC-7-01, SC-6-04). Basis: `Wymagania/Requirements_EN.md` §4 F-09/F-10; `docs/PLAN.md` SC-4-01, SC-6-04, SC-7-01; ADR-0003 and ADR-0015.
  **Done 2026-09-29:** PR #185 (merge commit `31f78dd`). Evidence: `backend/tests/test_scenario_what_if_billable_utilization.py` (K-01–K-07, 10 tests), full backend suite (1,455 passed, 6 xfailed), and frontend suite (414 passed). QA mutation results are recorded in the capability registry and Issue #100 comment https://github.com/TanerCRB/StafffingCalculator/issues/100#issuecomment-5898283687.
- [x] **SC-6-03** — Duplikuj scenariusz z interfejsu (F-09 pkt 1, frontend). Konsument API
  dostarczonego przez SC-6-01 (`POST .../scenarios/{id}/duplicate`) — ekran świadomie odłożony przy
  SC-6-01, wzorem podziału backend/frontend SC-4-01/SC-4-06 (Issue #95).
  *Done when:* `frontend/src` (vitest) dowodzi kryteriów K-01..K-06 (analyst, gate 1 zaakceptowane
  2026-09-24):
  1. (K-01) Udany zapis (`201`) dodaje nowy wiersz scenariusza w tym samym projekcie, zbudowany
     wyłącznie z ciała odpowiedzi, bez ponownego odczytu listy.
  2. (K-02) Kontrolka "Duplicate" dostępna na scenariuszu w OBU stanach, `draft` i `approved` — nie
     ukrywana na `approved`, w odróżnieniu od kontrolki "Set Time & Material" (SC-4-06):
     duplikowanie nie jest zapisem do źródła (ADR-0004 aneks SC-6-01).
  3. (K-03) Po duplikowaniu zatwierdzonego scenariusza: wiersz źródłowy pozostaje `Approved` bez
     zmian, nowy wiersz zawsze `Draft`, wzięty z ciała odpowiedzi, nigdy nie dziedziczony ze
     źródła po stronie klienta.
  4. (K-04) `403` i `404` kończą się dwoma osobnymi, nazwanymi, tekstowo rozróżnialnymi stanami
     odmowy, nigdy generycznym "failed" ani cichym brakiem reakcji.
  5. (K-05) `409` (brak wolnej nazwy/przegrany wyścig o nazwę) zostawia listę dokładnie taką, jaka
     była — zero widmowego wiersza, nazwany komunikat konfliktu odróżnialny od 403/404/failed.
  6. (K-06) Nowy wiersz trafia do tablicy scenariuszy WŁAŚCIWEGO projektu (po `project_id`), nigdy
     do nowego projektu ani po pozycji/indeksie w tablicy.

  **Decyzje bramki 1 (2026-09-24, analyst + architect, zaakceptowane przez człowieka):** kontrolka
  jako nowy przycisk na istniejącej karcie scenariusza (Q1 = opcja A, brak nowego
  routera/mechanizmu, wzorem D-2 SC-4-06); render nowego wiersza z ciała `201` natychmiast, bez
  refetchu (Q2, spójne z ADR-0009). Architect: FITS WITHIN THE ARCHITECTURE, jeden aneks do
  ADR-0009 (render-from-201 dla zapisu bez ciała żądania, wstawianie po `project_id`) — nie bramka.

  **Runda weryfikacji (QA, Invariant Guardian, reviewer) + poprawka.** QA: proof holds — 8 mutacji
  uruchomionych, wszystkie zabite za pierwszym razem (w tym własny wariant QA dla K-02: kontrolka
  zamontowana, ale `disabled` zamiast ukryta — realistyczny odpowiednik pomyłki z SC-4-06).
  Invariant Guardian: PASS (dwie rundy). Reviewer: PASS WITH RESERVATIONS → poprawka → PASS.
  Reviewer znalazł R-01 (Low): udany duplikat nie dawał żadnego odróżnialnego potwierdzenia ani
  przesunięcia fokusu, w odróżnieniu od każdego innego zapisu w tym repozytorium — naprawione:
  nowy stan `"duplicated"`, komunikat `role="status"`, fokus tym samym mechanizmem co `"saved"` w
  `ScenarioCommercialTermsSection`. Security-auditor: nieuruchomiony (brak nowej powierzchni
  auth/danych osobowych — czysta konsumpcja już zamkniętego, audytowanego endpointu).

  **Out of scope (explicit):** duplikowanie do innego projektu (`POST /projects/{id}/copy`,
  SC-1-03, osobny ekran); zmiana nazwy duplikatu przez PM (backend generuje sam, brak ciała
  żądania — decyzja bramki 1 SC-6-01); usuwanie/cofanie duplikatu (brak endpointu usuwania
  scenariusza w ogóle); nowy ogólny mechanizm routingu/stanu wybranego projektu-scenariusza (ta
  sama decyzja D-2 co SC-4-06); realny test współbieżnego wyścigu o nazwę (backend już zaakceptował
  jako okablowany-nieskwiczony, SC-6-01); podwójne kliknięcie = dwa duplikaty na poziomie backendu
  (odziedziczony, zaakceptowany koszt); porównanie ≥3 scenariuszy (Issue #87); wykresy/eksport
  (F-11); mobile/responsive (NF-09, wzorzec SC-4-06); backend (zero zmian, SC-6-01 już zamknięte).

  Podstawa: `Wymagania/Requirements_EN.md` §4 F-09 pkt 1, §7 AC-02; `docs/PLAN.md` SC-6-01 (Issue
  #11, PR #90); `ADR-0004-wersjonowanie-kalkulacji.md` (aneks SC-6-01);
  `ADR-0005-model-dostepu.md` (aneks SC-6-01, uprawnienie `SCENARIO_COPY`);
  `ADR-0009-zapis-z-interfejsu.md` (aneks 2026-09-24, Issue #95, SC-6-03);
  `docs/architecture/capabilities.md` (SC-6-01, dowiedzione).
  **Done 2026-09-24:** PR #98 (scalone `a107118`). Dowód:
  `frontend/src/features/projects/DuplicateScenario.test.tsx` (K-01..K-06, R-01 — 11 testów) — 245
  testów frontendowych zielono po tym zadaniu (było 234), 262 po scaleniu z SC-7-02 (PR #99). Zob.
  `docs/architecture/capabilities.md`.

- [x] **SC-6-08** — Risk representation and the double-representation signal (F-09 pt 4-5, backend),
  gate 1 approved 2026-09-29 (ADR-0021, Accepted) (Issue #89). A risk is a declared per-scenario
  entity; it may be represented by cost events (ADR-0014 rows carrying an optional risk link) and/or
  by reserves (own table); a risk linked to both is reported `both`.
  *Done when:* `backend/tests` prove criteria K-01..K-07 (analyst, 2026-09-29; cross-referenced to
  ADR-0021 controls R-01..R-09), each with its named contrast and mutation: a risk is reported
  `cost_event` / `reserve` / `both` / `none` from the declared link only, and `both` is the
  double-representation signal; the signal never changes any total; the reserve is a month-granular
  fixed amount with one rounding and no partial sum across currencies; the risk link is refused
  across scenarios and a referenced risk cannot be deleted, both by the database; writes under an
  `approved` scenario are refused in the same statement; a copied scenario remaps every link to its
  own risk; scope is a `404` even for a caller holding every permission.

  **Gate 1 decisions (2026-09-29, architect + analyst, accepted by the human):** Q-1 = A (separate
  `risk_reserve` table); Q-2 = A (declared risk entity); Q-3 = A now (reserve total reported beside
  `additional_cost`, not inside `included_cost`; folding it in is a follow-up decision); Q-4 = A
  (fixed amount, month-granular); Q-5 = A (risk endpoint + one additive field on the additional-cost
  read: the id of the linked risk, or null; kinds and counts only, no amounts, G-1/G-2); Q-6 = A
  (`STAFFING_*`, scenario-level only); Q-7 = A (risk copier before `copy_staffing_positions`, remap by
  unique name); Q-8 = A (deleting a referenced risk is refused).

  **Out of scope (explicit):** scenario duplication (#11); multi-scenario compare (#87); sensitivity
  analysis (SC-6-04); the additional-cost mechanism itself (SC-5-05); frontend/UI (F-11);
  probability x impact reserves; folding reserves into `included_cost`/profit/margin; phase-level
  risk; overlap of reserves with surcharges or the management category (F-08 pt 7); idempotent
  creation (ADR-0014 R-04); export.

  **Unproven foundation, accepted knowingly:** first consumer of the risk entity, the reserve table
  and the risk link on `additional_cost`; group 2 write guard and approval race on two new tables;
  a link column carried through the reflection copier `copy_staffing_positions`; the composite FK for
  the new link; reserve-vs-`additional_cost` currency comparison. Basis: Issue #89,
  `Wymagania/Requirements_EN.md` §4 F-09 pt 4-5, `ADR-0021-risk-representation-and-double-counting-signal.md`,
  ADR-0014, ADR-0004, ADR-0005, ADR-0007.
  **Done 2026-09-29:** PR #183 (merged `573f966`). Evidence: `backend/tests/test_risk_schema.py`,
  `test_risk_representation.py`, `test_risk_reserve.py`, `test_risk_guards.py`, `test_risk_copy.py`,
  `test_risk_access.py`, `test_risk_api.py`, `test_risk_qa.py` (K-01..K-07, R-01) - 1445 backend tests
  green after merging main (was 1252 before the task), ruff clean. Verification round: QA closed three
  real gaps the first suite left (link writes to a sibling scenario's risk, `remapped` on an unmapped
  link, what-if byte identity) and left two equivalent mutants (redundant behind the composite FK / the
  API-layer scope check). Invariant Guardian: PASS. Security-auditor: PASS. Reviewer: PASS WITH
  RESERVATIONS - R-01 (Medium, a `PATCH` naming one end of a recurring reserve bypassed the 60-month
  bound and made the reserve read expand ~12k months per reserve) fixed in the PR (guarded `UPDATE`,
  409); R-02 (Medium, the migration holds a lock while validating the FK / building the index on
  `additional_cost`; `lock_timeout` bounds only the wait), R-03 (Low, a retried `POST` of a reserve
  duplicates, ADR-0014 R-04), R-04/R-05 (Low, the reserve read loads all reserves; status and rows are
  read in separate statements) accepted as recorded exceptions, owner: the repository owner, expiry:
  the first frontend task that builds a reserve write form (F-11). See
  `docs/architecture/capabilities.md`.

- [x] **SC-7-02** — Pokaż zysk, marżę, markup i koszt scenariusza na ekranie (F-10, część,
  frontend). Konsument API dostarczonego przez SC-7-01 (`GET .../scenarios/{id}/results`) — ekran
  świadomie odłożony przy SC-7-01, wzorem podziału SC-4-01/SC-4-06 (Issue #94).
  *Done when:* `frontend/src` (vitest) dowodzi kryteriów K-01..K-06 (analyst, gate 1 zaakceptowane
  2026-09-24):
  1. (K-01) Kwoty i procenty renderują się wyłącznie przez `frontend/src/lib/money.ts`, nigdy
     `Number()`/`toFixed()` w miejscu wywołania.
  2. (K-02) `null` bramki kosztu osobowego i `"n/a"` niepoliczalnego źródła nigdy nie renderują się
     jako to samo, także w przypadku pierwszeństwa gdy oba nakładają się na jedno pole naraz
     (bramka zamknięta I źródło niepoliczalne → generyczne "niedostępne", nigdy "n/a").
  3. (K-03) Każde z trzech złożonych źródeł (`revenue`/`personnel_cost`/`additional_cost`) niesie
     własny nazwany stan niepoliczalny, bez wspólnego sentinela między źródłami.
  4. (K-04) `403` i `404` renderują się IDENTYCZNIE — świadoma rozbieżność względem SC-4-06,
     rozszerzenie SC-7-01 K-03 na warstwę renderu.
  5. (K-05) `409` (rozjazd stanu scenariusza) to osobny, nazwany, ponawialny stan błędu.
  6. (K-06) Sekcja montuje się bez akcji użytkownika; odmowa/awaria na niej nigdy nie usuwa nazwy
     ani statusu karty (izolacja renderu).

  **Decyzje bramki 1 (2026-09-24, analyst + architect, zaakceptowane przez człowieka):** sekcja
  karty scenariusza obok `ScenarioCommercialTermsSection`, brak nowego routera (Q1 = opcja A,
  wzorem D-2 SC-4-06); komunikat stanu `null` generyczny "niedostępne", bez ujawniania powodu (Q2
  = opcja b, spójne z ostrożnością security-auditor przy SC-7-01 wobec ryzyka B-01). Architect:
  FITS WITHIN THE ARCHITECTURE — brak bramki architektonicznej; wiersz
  `architecture-sensitive-paths.md` dla `frontend/src/features/projects/**` renderujących
  `RESULTS_READ`/dane bramkowane kosztem osobowym.

  **Runda weryfikacji (QA, Invariant Guardian, reviewer, security-auditor) + poprawki.** QA: proof
  holds — jeden test pierwszeństwa K-02 dewelopera nie wymuszał prawdziwego konfliktu (`state:
  "calculated"` nigdy nie trafiał w gałąź stanu niezależnie od kolejności sprawdzeń) — QA dopisała
  przypadek `state: "no_cost_rate", amount: null`, mutacja dopiero wtedy zabita; reszta mutacji
  (formatter, wspólny sentinel trzech źródeł, odwrócenie scalenia 403/404, scalenie 409 z
  `failed`, osłabienie sprawdzenia kształtu pola bramkowanego) zabita za pierwszym razem. Invariant
  Guardian: PASS (trzy przebiegi). Reviewer: PASS WITH RESERVATIONS → poprawki → PASS. Reviewer
  znalazł R-01 (Medium): brak walidacji krzyżowej `revenue.state` vs. pola zbiorcze — złamanie
  kontraktu backendu (pole zbiorcze realną liczbą przy `revenue` innym niż `"calculated"`)
  renderowałoby się jako kwota z pustą walutą zamiast błędu — naprawione: `isScenarioResultsShape`
  odrzuca taki payload jako nieczytelny, tym samym mechanizmem co każdy inny zniekształcony
  payload. R-02 (Low): brak zarezerwowanej wysokości sekcji podczas ładowania (skok layoutu) —
  naprawione częściowo i uczciwie nazwane jako częściowe (`min-height` na stan ładowania, nie na
  pełny stan `ready`). Security-auditor: PASS — bramka kosztu osobowego nie poszerzona (ryzyko
  B-01 z SC-7-01 nierozszerzone), gaszone pola faktycznie `null` na przewodzie (nie ukrywane po
  stronie klienta), zero side-channel dla rozróżnienia 403/404, zero wartości kosztu osobowego w
  logu/DOM/URL.

  **Out of scope (explicit):** rozbicie na okresy raportowania, planned hours/FTE, billable ratio,
  deviation from target margin — brak powierzchni API na backendzie (SC-7-01); wykresy/eksport
  (F-11); porównanie/agregacja wielu scenariuszy (Issue #87 — SC-6-02 dostarczyło później własny
  endpoint porównania, nieużywany przez ten ekran); nowy ogólny mechanizm routingu/stanu wybranego
  projektu-scenariusza (ta sama decyzja D-2 co SC-4-06); edycja/zapis z tego ekranu (API tylko do
  odczytu); mobile/responsive (NF-09, wzorzec SC-4-06); backend (zero zmian, SC-7-01 już
  zamknięte).

  Podstawa: `Wymagania/Requirements_EN.md` §3, §4 F-10/F-13, §7 AC-01/AC-05; `docs/PLAN.md`
  SC-7-01 (Issue #12, PR #91), SC-4-01/SC-4-06 (precedens podziału); `ADR-0002-obsluga-pieniedzy.md`
  (aneks 2026-09-24); `ADR-0005-model-dostepu.md` (aneksy SC-5-01 pkt 4/5, SC-5-05 pkt 5, aneks
  2026-09-24); `ADR-0010-awaria-renderu-frontendu.md`; `docs/architecture/capabilities.md`
  (SC-4-01, SC-5-01, SC-5-05, SC-5-06, SC-7-01, dowiedzione).
  **Done 2026-09-24:** PR #99 (scalone `ad7e4d1`, w tym scalenie konfliktu z SC-6-03 na tej samej
  karcie scenariusza, `25ebc95`). Dowód: `frontend/src/features/projects/ScenarioResults.test.tsx`
  (K-01..K-06, R-01 — 17 testów) — 262 testy frontendowe zielono łącznie z SC-6-03. Zob.
  `docs/architecture/capabilities.md`.

- [x] **SC-1-11** — Wprowadź encję fazy dostawy / workstreamu (F-02, F-06), warunek wstępny dla
  SC-4-05 (reguły wspólne mieszanych umów komercyjnych, F-06.5).
  *Done when:* `backend/` (pytest) dowodzi kryteriów K-01..K-09 (analyst + architect, gate 1
  zaakceptowane 2026-09-25), każde z zarejestrowanym przebiegiem mutacyjnym; K-03 ograniczone
  jawnie do kształtu klucza — jego siłę ochronną dowiedzie dopiero SC-4-05, pierwszy konsument
  `scope_ref`.

  **Decyzje bramki 1 (2026-09-25, analyst + architect, zaakceptowane przez człowieka bez
  zastrzeżeń):** tabela `scenario_delivery_segment`, jedna, bez dyskryminatora (Q3); dziecko
  scenariusza, `UNIQUE (id, scenario_id)` i `UNIQUE (scenario_id, name)`, kolumny wyłącznie
  `id, scenario_id, name, created_at, updated_at` (K-05); grupa 2 tabel-dzieci scenariusza,
  strażnik zapisu `approved`, jeden wpis `SCENARIO_CHILD_COPIERS`, w tym samym zadaniu (Q4); bez
  API HTTP (Q2); alokacja obsady per faza (F-04) jawnie poza zakresem (Q1). Architect: NEEDS A NEW
  DECISION — `ADR-0016-segment-dostawy-scenariusza.md` (Draft — pending approval) + aneks
  `ADR-0004-wersjonowanie-kalkulacji.md` (2026-09-25).

  **Out of scope (explicit):** `scope_ref` na `commercial_terms` i ochrona przed podwójnym
  rozliczeniem (SC-4-05); alokacja obsady per faza (F-04); UI; uprawnienia ADR-0005 (do pierwszego
  zadania z endpointem); przedział obowiązywania segmentu (ADR-0008).

  Podstawa: Issue #65; `Wymagania/Requirements_EN.md` §4 F-02, F-06, F-06.5;
  `ADR-0003-model-modeli-komercyjnych.md` ("Odłożone"); `ADR-0016-segment-dostawy-scenariusza.md`;
  `ADR-0004-wersjonowanie-kalkulacji.md` (aneks 2026-09-25).
  **Done 2026-09-25:** PR #114 (scalone `b6d3396`). Dowód: nowa tabela `scenario_delivery_segment`
  (migracja `b1f4e8a3c95d`), `backend/tests/test_scenario_delivery_segment_schema.py`,
  `test_scenario_delivery_segment_copy.py`, `test_scenario_delivery_segment_guards.py`,
  `test_scenario_delivery_segment_no_api.py` (K-01..K-09) — 773 testy backendowe zielono. QA: proof
  holds — cztery mutacje naprawdę wykonane i zabite (strażnik zapisu K-07, rejestr kopiowania
  `SCENARIO_CHILD_COPIERS` K-06, trasa surowego SQL K-08, dodatkowe ograniczenie migracji K-04);
  worktree przywrócony czysto po każdej. Invariant Guardian: PASS (reguły 7/13/14/17 sprawdzone
  wprost na kodzie, nie tylko na deklaracji). Security-auditor: PASS (parametryzacja SQLAlchemy
  wszędzie, brak wycieku szczegółów bazy w refusal, brak dziś żadnej ścieżki dostępu do tabeli poza
  testami i kopiującym). Reviewer: PASS WITH RESERVATIONS → poprawka → PASS — R-01 (Low): test
  strukturalny K-08 używał nierekursywnego `glob` i pomijał `app/api/schemas/**` (cztery istniejące
  pliki tam importują wprost z `app.models.*` — realna luka); naprawione (`glob` → `rglob`), 773
  testów nadal zielono po poprawce. **Zaakceptowane, nie naprawiane:** brak — bez ustaleń
  pozostawionych otwartych.
  Zob. `docs/architecture/capabilities.md`.

- [x] **SC-4-03** — Wylicz przychód Outcome-based dla scenariusza (F-06.3): opłata stała + premia
  binarna warunkowa + stawka za jednostkę, ograniczone min/max; cztery stałe kategorie wyniku
  (nieosiągnięty / częściowy / osiągnięty / przekroczony) z ręcznie wpisaną liczbą jednostek i
  opcjonalnym prawdopodobieństwem; przychód gwarantowany i przychód oczekiwany jako dwie osobne
  wartości (Issue #67).
  *Done when:* `backend/tests` dowodzą kryteriów K-01..K-07, każde z zarejestrowanym przebiegiem
  mutacyjnym: (1) AC-08: opłata stała 20000 PLN + premia 10000 PLN → 20000 PLN dla kategorii
  "nieosiągnięty" i "częściowy", 30000 PLN dla "osiągnięty"; reguła w walucie innej niż waluta
  scenariusza daje nazwany stan `currency_mismatch`, nigdy kwotę; (2) przychód gwarantowany i
  oczekiwany to dwie odrębne wartości — prawdopodobieństwa zmieniają wyłącznie oczekiwany (70/30 →
  20000/23000, 10/90 → 20000/29000); brak prawdopodobieństw daje nazwany stan oczekiwanego, nigdy
  `0` ani kopię gwarantowanego; oczekiwany zaokrąglany raz, na końcu; (3) zestaw prawdopodobieństw o
  sumie różnej od dokładnie 100.00, niepełny albo z trzecim miejscem po przecinku odrzucany (`422`)
  bez zapisu jakiegokolwiek wiersza — suma egzekwowana `CHECK` w bazie; kontrast 33.34/33.33/33.33
  → `201`; (4) stawka za jednostkę i min/max ograniczają cały przychód kategorii oraz przychód
  gwarantowany (opłata 20000, 100 PLN/j., min 22000, max 30000: 50 j. → 25000, 150 j. → 30000, 0 j.
  → 22000, gwarantowany 22000); min > max odrzucone; (5) zapis reguły outcome do scenariusza
  `approved` odrzucony w tej samej instrukcji co zapis (test wyścigu dwóch połączeń), zero wierszy
  w tabelach reguły; kontrast na `draft`; (6) kopia scenariusza kopiuje regułę outcome z kompletem
  szczegółów, z nowymi identyfikatorami i identycznym wynikiem — `unsupported_model_type`/`409`
  przy kopiowaniu dowiedzione na prawdziwym wierszu drugiego modelu, nie tylko symulacją; (7)
  `/results` (SC-7-01) dla zatwierdzonego scenariusza outcome odpowiada `200` z zyskiem liczonym od
  przychodu gwarantowanego, a prawdziwy wyścig zatwierdzenia scenariusza T&M nadal daje `409`.
  Istniejące testy SC-4-01/SC-4-06/SC-7-01/SC-6-02 zielone bez zmian, z jednym wyjątkiem
  zatwierdzonym na bramce 1: zbiór pól w teście K-11 SC-4-01 rozszerzony o nazwane nowe pola
  (równość zbioru zostaje).
  **Decyzje bramki 1 (2026-09-25, analyst + architect, zaakceptowane przez człowieka):** D-1 —
  wynagrodzenie zmienne w MVP: opłata stała, premia binarna, stawka za jednostkę, min/max;
  "częściowy" nie wypłaca premii; częściowe osiągnięcie i udział w korzyści poza MVP (brak formuły
  w wymaganiach). D-2 — prawdopodobieństwa w procentach, 2 miejsca po przecinku, suma dokładnie
  100.00, bez tolerancji i normalizacji, nadmiar precyzji → `422`. D-3/P-2 — cztery stałe kategorie
  jako kolumny `outcome_terms`, "wszystkie NULL albo suma = 100" jako `CHECK` w bazie. D-4/P-1 —
  `amount` / przychód w SC-7-01 i SC-6-02 = przychód gwarantowany; oczekiwany w nowym polu. D-5 —
  min/max ograniczają cały przychód. D-6 — jawne rozszerzenie zbioru pól testu K-11 SC-4-01. D-7/P-4
  — reguła niesie własną walutę (`currency`), bez konwersji. P-3 — `rate_source = not_applicable`
  dla modelu bez katalogu; strażnik wyścigu `/results` porównuje tylko źródła zależne od statusu.
  P-5 — edycja reguły osobnym zadaniem (dziś tylko tworzenie). D-9 — ekran osobnym Issue; do tego
  czasu SC-4-06 nie pokazuje przychodu oczekiwanego (tymczasowe odstępstwo od F-06.3 "displayed
  separately"). Koordynacja bloku 4: elementy wspólne dla kolejnych modeli ustala aneks ADR-0003
  (założenie "SC-4-03 pierwsze" nieaktualne — SC-4-04 scalone wcześniej, patrz decyzje po rundzie
  weryfikacji 2).
  **Decyzje po rundzie weryfikacji 1 (2026-09-25, decyzje człowieka):** (1) wyniki złożone
  (`/results`, what-if, porównanie) wymagają równości walut przychodu i każdego źródła kosztu,
  inaczej `profit`/`margin`/`markup` = `currency_mismatch` — obejmuje też istniejący przypadek koszt
  dodatkowy ≠ koszt osobowy; (2) testy migracji porównują z własną zamrożoną listą, "nieznany model"
  to wartownik, nie nazwa przyszłego modelu, `_details_of` odmawia nieznanego typu — test SC-4-01 z
  `'fixed_price'` przepina SC-4-02 na swojej bramce 1; (3) ograniczenie frontendu obejmuje też sekcję
  wyników SC-7-02 dla outcome; `hours_source`, `vendor_axis`, `rate_source` dostają
  `not_applicable` jako nazwany wyjątek od "żadne pole nie zmienia typu"; (4) `GET` reguły niesie
  parametry outcome, zbiór pól reguły K-11 rozszerzony jawnie (równość zostaje); (5) jednostki
  nullowalne przy `unit_rate = NULL`, `CHECK` `unit_rate NOT NULL` → wszystkie jednostki `NOT NULL`;
  (6) ograniczenie przyjęte: `NUMERIC(5,2)`/`NUMERIC(14,4)` po cichu zaokrąglają zapis z pominięciem
  API, `422` tylko na API — do ponownego otwarcia przy pierwszej ścieżce zapisu spoza API; (7)
  `category_revenues` i `expected_amount` widoczne pod `RESULTS_READ` bez `COMMERCIAL_READ` —
  przyjęte, do ponownego otwarcia przy zadaniu wprowadzającym role.
  **Decyzje po rundzie weryfikacji 2 (2026-09-25, decyzje człowieka):** (1) integracja z SC-4-04
  (Story Points, scalone do `main` przed SC-4-03): migracja `b9e3c7a1f264` zlinearyzowana na
  `d2f6a91c4b58`, `CHECK model_type_known` z pełną listą trzech wartości (`time_and_material`,
  `story_points`, `outcome_based`), downgrade odtwarza listę dwóch wartości; Story Points zachowuje
  własne `rate_source = story_points_terms`, outcome — `not_applicable`; konwencja ADR-0003 pkt 10a
  w nowym brzmieniu: model bez katalogu stawek używa wartości spoza źródeł zależnych od statusu;
  (2) strażnik wyścigu `/results` porównuje wyłącznie źródła zależne od statusu
  (`live_catalog`/`approved_snapshot`) — to naprawia również **istniejący na `main` defekt
  SC-4-04**: każdy scenariusz Story Points dostawał `409` na `/results`, what-if i porównaniu
  przez porównanie `rate_source` zwykłą równością; dowód — nowe testy
  `backend/tests/test_story_points_scenario_results.py`; (3) strażnik dryfu SC-4-04
  (`test_story_points_terms.py`, ok. w. 511) przepięty wzorem R-02: migracja `d2f6a91c4b58` vs jej
  własna zamrożona lista, stała modelu vs najnowsza migracja
  (`LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH` → `b9e3c7a1f264`); (4) ADR-0005 (B-01 audytu
  bezpieczeństwa): nazwany pełny zakres odtwarzalny pod `RESULTS_READ` bez `COMMERCIAL_READ` — z
  `amount` i `category_revenues` zwykle `fixed_fee`, `unit_rate`, `success_bonus`, `currency`, a
  przy zadziałaniu ograniczenia także `revenue_min`/`revenue_max`; akceptacja utrzymana,
  nieeksploatowalne dziś (`PLACEHOLDER_PERMISSIONS`), do ponownego otwarcia przy zadaniu ról.
  **Integracja z SC-4-05 (2026-09-25):** `scope_ref` (SC-4-05, PR #119) dotyczy reguł
  `outcome_based` tak samo jak reguł pozostałych modeli — kolumna żyje na `commercial_terms`, nie
  na `outcome_terms`; migracja `b9e3c7a1f264` zlinearyzowana na `b7e3f19a6c52` (lista `IN` i
  downgrade bez zmian); decyzje rundy weryfikacji 2 obowiązują bez zmian — ADR-0003, aneks SC-4-03,
  pkt 13.
  **Nowa/zmieniona decyzja architektoniczna:** aneksy 2026-09-25 do **ADR-0003** (tabela
  `outcome_terms`, kategorie, waluta, dwa przychody, `rate_source = not_applicable`; uzupełnienia
  rundy weryfikacji 1 w pkt 3, 4, 5d, 7, 8, 10c, nowy pkt 11, kontrole O-7..O-10; runda 2: pkt 10
  i 10a przeredagowane, nowy pkt 12, kontrole O-11..O-12), **ADR-0004** (grupa 2 dla
  `outcome_terms`; runda 2: pkt 5), **ADR-0002** (równość walut w wynikach złożonych) i
  **ADR-0005** (parametry outcome pod `RESULTS_READ`; runda 2: pkt 3, pełny zakres B-01).
  **Out of scope (explicit):** częściowe osiągnięcie, udział w korzyści, progi wielostopniowe, kary;
  edycja i usuwanie reguły (osobne zadanie); zapis faktycznie zmierzonego wyniku po realizacji i
  automatyczne źródła pomiaru; definicje wyników, poziom bazowy/docelowy, okresy pomiaru jako pola
  opisowe; zysk/marża oczekiwana (blok 7, osobne Issue); przypisanie przychodu do okresów (F-06.5,
  SC-4-05); ekran (osobne zadanie frontend).
  **Fundament nieudowodniony:** pierwsze rozszerzenie `CHECK model_type_known` i pierwsza tabela
  szczegółów z kolumnami dziedzinowymi; pierwszy model przychodu bez katalogu (`rate_source`);
  pierwsze prawdopodobieństwo jako wartość użytkownika. (Stan po merge z `main`, 2026-09-25:
  pierwsze rozszerzenie `CHECK`, pierwszą tabelę szczegółów z kolumnami dziedzinowymi i pierwszy
  model bez katalogu dostarczyło SC-4-04; SC-4-03 jest drugim, pierwszym z trzema wartościami
  `CHECK` i pierwszym z prawdopodobieństwem.) Podstawa: Issue #67,
  `Wymagania/Requirements_EN.md` §4 F-06.3, §7 AC-08, `ADR-0003-model-modeli-komercyjnych.md`,
  `ADR-0004-wersjonowanie-kalkulacji.md`, `ADR-0002-obsluga-pieniedzy.md`,
  `ADR-0005-model-dostepu.md`, `ADR-0006-waluty-i-kursy.md`, `ADR-0007-wspolbiezna-edycja.md`.
  **Done 2026-09-25:** PR #120 (scalone `3a46772`), zintegrowany z SC-1-11, SC-4-04 i SC-4-05
  (trzy merge `main`). Dowód: `backend/tests/test_outcome_revenue.py` (K-01..K-04),
  `test_outcome_terms_schema.py` (K-03/K-04 w bazie, O-1, O-6), `test_outcome_terms_guards.py`
  (K-05), `test_outcome_revenue_copy.py` (K-06), `test_outcome_scenario_results.py` (K-07),
  `test_profitability_currency.py` (waluty w wynikach złożonych), `test_outcome_terms_read_and_units.py`
  (parametry reguły w odczycie, jednostki `NULL`), `test_story_points_scenario_results.py` (naprawa
  `/results` Story Points), `test_outcome_scope_ref.py` (`scope_ref` dla outcome) oraz testy QA
  `test_outcome_revenue_qa.py`, `test_outcome_round2_qa.py`, `test_sc_4_03_merge_qa.py`,
  `test_outcome_scope_ref_qa.py` — 906 testów backendu i 262 frontendu zielono, CI zielone. QA: cztery
  rundy, dowód trzyma (mutacje, które przeżyły testy dewelopera — 4/5/3/2 w kolejnych rundach —
  zabite po testach QA; jedna mutacja równoważna nazwana). Invariant Guardian: PASS WITH
  RESERVATIONS (waluta w `/results`) → PASS ×3. Reviewer: STOP (R-01 waluta, High) → STOP
  (integracja z SC-4-04) → PASS ×2. Security-auditor: PASS → PASS WITH RESERVATIONS (B-01, zakres
  akceptacji w ADR-0005) → PASS ×2. Zaakceptowane przez człowieka, nienaprawione: parametry outcome
  odtwarzalne spod `RESULTS_READ` (ADR-0005 aneks SC-4-03, do zadania ról); ciche zaokrąglenie
  `NUMERIC` przy zapisie z pominięciem API (ADR-0003 aneks pkt 4); N reguł per segment a przychód
  gwarantowany w `/results` (ADR-0003 aneks pkt 13d, warunek wstępny `scope_ref` w API); frontend
  bez przychodu oczekiwanego (D-9). Zob. `docs/architecture/capabilities.md`.

- [x] **SC-7-03** — Strażnik wyścigu `/results`, `/compare` i what-if na statusach zamrożonych przez
  trzy odczyty odświeżające scenariusz, nie na porównaniu `rate_source` przychodu z kosztowym
  (Issue #118, PR #123).
  *Done when:* jedna funkcja `refuse_a_status_race`, wołana po trzecim odczycie, odmawia `409` ⇔
  (a) przychód zależny od statusu (`rate_source` ∈ `STATUS_DEPENDENT_SOURCES`) i status odczytu
  przychodu ≠ kosztu, albo (b) status odczytu kosztu ≠ kosztu dodatkowego (każdy model); Story Points
  bez wyścigu → `200` na `/results`, `/compare`, what-if; realne zatwierdzenie między odczytami →
  `409` dla T&M i bez reguły, spójny wynik zatwierdzony dla SP/Outcome; testy wyścigu z `main` bez
  osłabienia; odpowiedź T&M bez zmian; treść `409` generyczna.
  **Done 2026-09-25:** `backend/tests/test_scenario_results_status_guard.py` (K-01, K-02, K-04,
  K-05, K-06, A15-6..A15-9 — realna współbieżność dwóch połączeń); `test_scenario_results_race.py`,
  `test_story_points_scenario_results.py`, `test_outcome_scenario_results.py`,
  `test_scenario_results.py` bez zmian i zielone. Mutacje: trzy rundy QA, zabite wszystkie
  nieekwiwalentne (`docs/architecture/capabilities.md`, mutation log SC-7-03). Backend 952 passed,
  frontend 262 passed, CI zielone. Decyzje człowieka: bramka 1 Q1–Q5; bramka 2 — R-01 (luka koszt →
  koszt dodatkowy, obecna też na `main`) naprawione, R-02, kolizja z SC-4-03 → Q4 A→B (semantyka
  `main`), pkt 2b A, R-06 A (warunek ważności zwolnienia SP/Outcome zapisany w ADR-0015). Podstawa:
  ADR-0015 aneks SC-7-03, ADR-0003 aneks SC-7-03. **Zaakceptowane, nienaprawione:** nieaktualne
  docstringi `test_scenario_results_race.py` — wyjątek człowieka (TEAM-CONTRACT §3a, do #122,
  najpóźniej 2026-10-09). Brak testu realnej współbieżności dla samego `/compare` (ta sama funkcja co
  `/results`); wyścig "live–live" (edycja okna katalogu) — nazwana luka.
  Zob. `docs/architecture/capabilities.md`.

- [x] **SC-7-04** — Ekran Compare scenarios: wybór projektu + multi-select scenariuszy + tabela
  porównania metryk N scenariuszy (F-09, F-11, frontend), Issue #108 — konsument API SC-6-02
  (`GET /projects/{project_id}/scenarios/compare`).
  *Done when:* `frontend/src` (vitest) dowodzi kryteriów K-01..K-05 (analyst, 2026-09-27), każde z
  zarejestrowanym i wykonanym przebiegiem mutacyjnym: brak mieszania danych między wierszami;
  bramka kosztu osobowego czytana niezależnie per wiersz, nigdy raz dla całej tabeli; stan nazwany
  niepoliczalny jednego wiersza nie zawala pozostałych, nie zwija się do wspólnego sentinela;
  all-or-nothing `404` renderowane jako jeden stan całego widoku, nigdy fallback na N osobnych
  wywołań `getScenarioResults`; kolejność/liczność wierszy = dokładnie żądanie (backend gwarantuje
  strukturalnie, front nie sortuje). Zamontowane jako nowy `ScreenKey` `"compare-scenarios"` na
  `RAIL_WORKSPACE`.
  **Decyzje bramki 1 (2026-09-27):** mechanizm wyboru — nowy `ScreenKey` workspace-level z własnym
  dwupoziomowym stanem (wybór projektu, potem multi-select scenariuszy tego projektu), zgodnie z
  tym co rail już rezerwował; brak formalizacji odłożonej decyzji D-2 (routing/stan wyboru) jako
  osobnego ADR — punktowa decyzja bramki 1 jak w SC-4-06/SC-7-02/SC-3-04; brak limitu
  `MAX_COMPARE_SCENARIOS=50` w UI — `422` renderuje się jak każdy inny błąd (spójne z precedensem
  `MAX_ALLOCATION_MONTHS`, zero odpowiednika frontendowego).
  **Out of scope (explicit):** obsada/FTE jako metryka — F-10 niezbudowane; nowa kalkulacja
  backendowa — zero zmian w SC-6-02; analiza wrażliwości (Issue #88), widoczność rezerw ryzyka
  (Issue #89); eksport/druk zestawienia (F-11); test regresyjny na zmianę projektu w trakcie
  trwającego żądania porównania (reviewer, nieblokująca rekomendacja — mechanizm poprawny,
  prześledzony przez inspekcję kodu, bez dedykowanego testu).
  **Done 2026-09-27:** PR #143 (scalone). Dowód:
  `frontend/src/features/compare/CompareScenariosScreen.test.tsx` (15 testów, K-01..K-05 + abort/
  unmount dla obu niezależnych odczytów, dodane od razu nie jako poprawka po recenzji) —
  `pnpm test` 392/392 zielone (było 375 po SC-3-06), `pnpm lint`/`pnpm build` czyste. Guardian PASS,
  reviewer PASS (jedna nieblokująca rekomendacja). **QA** znalazł i domknął dwie realne luki:
  walidacja kształtu odpowiedzi porównania (`isScenarioResultsComparisonShape`) sprawdzała tylko
  pierwszy wiersz zamiast każdego — **PROOF IS EMPTY** w pierwszej wersji testów (żaden fixture nie
  miał złego kształtu poza pierwszym wierszem), domknięte nowym testem; brak resetu zaznaczenia
  scenariuszy przy zmianie wybranego projektu — realny błąd UX (przycisk "Compare selected"
  zostawał aktywny mimo braku widocznego zaznaczenia w nowym projekcie), naprawiony kodem i testem.
  Zob. `docs/architecture/capabilities.md`.

- [x] **SC-7-05** - Show a negative-profit indicator on the scenario results card (F-11).
  *Done when:* frontend tests prove the indicator appears when `profit < 0` and is absent when `profit >= 0`; otherwise identical result fixtures differing only in profit sign render different indicator states. Record a named mutation result for each criterion at verification.
  **Out of scope (explicit):** below-target-margin indicator - the API does not expose the required deviation; revisit in a separate task after that data is available. Compare view - separate scope in SC-7-04.
  **Done 2026-09-30:** PR #190 (merged as `ac6b343`). `ScenarioResults.test.tsx` proves negative profit shows the indicator; positive, `0.00` and `-0.00` do not; and otherwise identical scenarios differing only by profit sign render opposite indicator states. Mutation `SC-7-05-M1` forced the predicate to false and was killed by the negative-profit visibility and opposite-sign contrast tests (20 passed, 2 failed); after restoration, the focused suite passed 22/22. Full frontend suite: 419/419; `pnpm lint` and `pnpm build` passed. See [PR #190](https://github.com/TanerCRB/StafffingCalculator/pull/190).

- [x] **SC-7-06** — Export whole-scenario results to PDF and spreadsheet (F-11, AC-06).
  *Done when:* backend artifact tests prove: (1) the spreadsheet preserves every field and nested
  value from the `ScenarioResults` response contract, including component states, currencies, and
  each `assumptions_used` object, preserving distinctions between numeric strings, `null`, `n/a`,
  and named states; (2) the PDF identifies `scenario_id`, `scenario_status`, and a UTC generation
  timestamp, and contains revenue, personnel-cost, and additional-cost values/states/currencies,
  `included_cost`, `profit`, `margin`, `markup`, `profitability_state`, and the same assumption
  objects; (3) an approved scenario exported again after defaults change retains the original
  calculated values and assumptions (AC-10), excluding the intentionally different generation
  timestamp. For both formats, mutation-checked artifact tests prove that when either
  `PERSONNEL_COSTS_READ` or project `can_view_personnel_costs` is absent, the gated scenario-cost
  fields (`amount`, `assumptions_used`, `paid_absence_amount`, `paid_absence_budget_amount`,
  `paid_absence_assumptions_used`, `fully_loaded_amount`, `surcharge_amount`,
  `paid_absence_fully_loaded_amount`, `paid_absence_surcharge_amount`, `fixed_amount_amount`,
  `fixed_amount_assumptions_used`, `assigned_fte_amount`, `assigned_fte_assumptions_used`) and
  profitability fields (`included_cost`, `profit`, `margin`, `markup`) are omitted, never zeroed;
  ungated states/currencies, revenue, and additional-cost fields remain present. The two permission
  factors are independently contrasted. Version identity is `scenario_id` plus `scenario_status`
  under ADR-0004; draft exports describe the current draft at generation time, and reproducibility
  is proven only for approved scenarios. Spreadsheet detail is limited to the existing whole-scenario
  response contract; per-position, per-period, and multi-scenario comparison exports remain out of
  scope.
  **Out of scope (explicit):** BI/API and other export formats; per-position or per-period detail;
  multi-scenario comparison export (separate task after SC-7-04).
  **Basis:** `Wymagania/Requirements_EN.md` §4 F-11, §7 AC-06/AC-10; ADR-0002, ADR-0003, ADR-0004,
  ADR-0005; `docs/PLAN.md` SC-7-01/02/04.
  **Done 2026-09-30:** PR #196 (merged as `c6b786b`). Artifact evidence: `backend/tests/test_scenario_results_export.py::test_xlsx_preserves_scalar_kinds_null_n_a_and_unicode`, `::test_exports_include_scenario_identity_status_timestamp_and_result_fields`, `::test_exports_omit_only_gated_personnel_cost_values_when_either_gate_is_closed`, `::test_approved_exports_keep_snapshot_values_after_catalog_changes`, `::test_xlsx_rolls_detail_rows_to_additional_sheets`, and `::test_pdf_paginates_large_nested_result_without_repeating_container_json`. XLSX walks the complete `ScenarioResults` tree, keeps scalar types and genuine nulls distinct, and rolls detail rows across worksheets. PDF records scenario identity, status, UTC generation timestamp, component values/states/currencies, and assumptions on paginated pages. Approved exports retain frozen values after catalogue defaults change. Each personnel-cost gate condition independently omits gated amounts/assumptions and profitability fields while retaining allowed values. Backend: 1,503 passed, 6 xfailed; frontend: 424 passed; Ruff and `uv lock --check` passed; both GitHub Actions test checks passed. Mutations SC-7-06-M1-M3 are recorded in the capability registry and [Issue #110](https://github.com/TanerCRB/StafffingCalculator/issues/110#issuecomment-5915979083).

- [x] **SC-7-07** — Show the scenario cost breakdown (F-11), Issue #107.
  *Done when:* Frontend tests for K-01..K-05 prove base personnel, paid absence, and additional cost render independently from SC-7-01 results; named uncalculable components differ from zero and do not suppress other lines; the personnel-cost gate withholds personnel amounts while additional cost follows its independent visibility rule. Record the five named mutations at verification.
  **Out of scope (explicit):** Reporting-period breakdown until source values are available by period; PDF and spreadsheet exports (SC-7-06); aggregation across scenarios (SC-6-02).
  **Done 2026-09-30:** PR #192 (merged as `dcaa776`). `frontend/src/features/projects/ScenarioResults.test.tsx` proves K-01–K-05: independent base and paid-absence amounts; additional costs independent from personnel components; named unavailable states distinct from zero while other lines remain; and personnel-cost gating independent from additional-cost visibility. Mutations SC-7-07-M1–M5 were all killed (5, 4, 6, 2, and 3 failing tests respectively); baseline and restored focused suite passed 22/22. Frontend suite: 424/424; lint and build passed; GitHub Actions backend and frontend checks passed. See [PR #192](https://github.com/TanerCRB/StafffingCalculator/pull/192) and [Issue #107](https://github.com/TanerCRB/StafffingCalculator/issues/107).

- [x] **SC-7-08** — Update the scenario race test docstrings after the three-read status guard change (Issue #122, PR #197).
  *Done when:* Docstrings in `backend/tests/test_scenario_results_race.py` describe the guard as comparing scenario status frozen separately by the revenue, personnel-cost, and additional-cost reads; only docstrings change.
  **Out of scope (explicit):** Assertions, fixtures, test logic, and production behavior; the follow-up is limited to correcting test documentation.
  **Done 2026-09-30:** PR #197 (merged as `787a211`). Artifact: the docstring-only diff in `backend/tests/test_scenario_results_race.py`. QA mutation `SC-7-08-M1` restored the obsolete two-read/rate-source wording; manual K-01 review rejected it ([Issue #122 QA record](https://github.com/TanerCRB/StafffingCalculator/issues/122#issuecomment-5916719973)). Backend: 1,490 passed, 6 xfailed; frontend: 424 passed; Ruff passed; both CI checks passed. This records documentation accuracy only and does not re-prove runtime race behavior, already evidenced by SC-7-03.

- [x] **SC-4-07** — Pokaż przychód Outcome-based i Story Points na karcie scenariusza (F-06.3,
  F-06.4, frontend): konsument istniejącego API, zamyka ograniczenie D-9 SC-4-03 (ADR-0003 aneks
  SC-4-03 pkt 8) i nienazwaną dotąd degradację po SC-4-04 — scenariusz Outcome-based i Story Points
  był błędem odczytu obu sekcji karty, bo kontrakt frontendu znał tylko wartości T&M (Issue #125).
  *Done when:* `frontend/src` (vitest) dowodzi kryteriów K-01..K-07, każde z zarejestrowanym
  przebiegiem mutacyjnym: (1) Story Points (AC-09) i Outcome-based czytelne w sekcji reguły i w
  sekcji wyników; walidator kształtu pozostaje zbiorem zamkniętym, wartości założeń sparowane z
  `model_type` — wartownik spoza kontraktu i T&M z `not_applicable` nadal dają nazwany błąd odczytu;
  blok założeń bez linii T&M dla modeli niegodzinowych; (2) przychód gwarantowany i oczekiwany jako
  dwie osobno podpisane kwoty (AC-08: 20000 / 23000 PLN); `no_probabilities` → nazwany stan, nigdy 0
  ani kwota gwarantowana; (3) reguły parowania przed renderem (`expected_state`/`expected_amount`;
  `profitability_state` ⇄ pola zbiorcze jednokierunkowo — AC-05 `margin "n/a"` przy `calculated`
  czytelne); (4) cztery kategorie podpisane po `category`, `null` = brak, jawne `0` = 0; (5)
  parametry `outcome_terms` jak zapisane (4 miejsca przez `lib/money.ts`), `null` = nieobecny; (6)
  `profitability_state = currency_mismatch` jako osobna linia stanu sekcji wyników, niezależna od
  bramki kosztu osobowego, przy polach nadal "niedostępne"; tekst odróżnialny od `not_applicable` i
  od bramki; komunikat `revenue.state = currency_mismatch` dobrany do `model_type`; (7) sekcja wyników
  dla Outcome pokazuje tylko przychód gwarantowany z etykietą, wybór po `model_type`; T&M bez zmian.
  Istniejące asercje frontendu bez zmian (nowe pola dopisane wyłącznie do danych fixture'ów — decyzja
  Q4); `git diff main -- backend/` pusty.
  **Decyzje bramki 1 (2026-09-25, analyst + architect, zaakceptowane przez człowieka):** Q1=A bez
  formularzy tworzenia reguł Outcome/Story Points; Q2=A sekcja wyników tylko z przychodem
  gwarantowanym i przyczyną `profitability_state` (wybór prezentacji, nie kontrola dostępu —
  ekspozycja pod `RESULTS_READ` z ADR-0005 aneks SC-4-03 bez zmian); Q3=A zbiór zamknięty, każde
  zadanie dopisuje tylko wartości swojego modelu (wiąże Fixed Price #66/#113); Q-A=B przyczyna
  `currency_mismatch` jako linia sekcji niezależna od bramki; Q-B=B parowanie z `model_type`; Q-C
  aneks ADR-0003 (render i etykiety po `model_type`, nigdy po `rate_source`); Q4=A dane fixture'ów
  istniejących testów uzupełnione, asercje bez zmian; Q5=A parametry reguły z 4 miejscami przez
  `roundDecimalString`; Q6=A komunikat `currency_mismatch` przychodu dobrany do modelu.
  **Nowa/zmieniona decyzja architektoniczna:** aneksy 2026-09-25 SC-4-07 do **ADR-0003** (zamknięcie
  D-9, luka rejestru SC-4-04, kontrakt frontendu dla kolejnych modeli) i **ADR-0005** (linia stanu
  sekcji a bramka pól, Q-A=B); `architecture-sensitive-paths.md` — ADR-0003 w wierszach frontendu.
  **Out of scope (explicit):** formularze tworzenia/edycji/usunięcia reguł (po #126); parametry
  Story Points (`GET` ich nie zwraca, #126 Q2); zysk/marża oczekiwana (#127); Fixed Price (#113);
  ekrany what-if i porównania; mobile/responsive (NF-09); backend (zero zmian).
  **Fundament:** API dowiedzione (SC-4-03, SC-4-04, `capabilities.md`); ADR-0009/ADR-0010 w statusie
  Draft; parowanie R-01 SC-7-02, które to zadanie rozszerza, ma w rejestrze status "test, no
  mutation". Podstawa: Issue #125, `Wymagania/Requirements_EN.md` §4 F-06.3, F-06.4, F-10, §7
  AC-05, AC-08, AC-09; `ADR-0003-model-modeli-komercyjnych.md` (aneksy SC-4-03, SC-4-04, SC-4-07);
  `ADR-0005-model-dostepu.md`; `ADR-0002-obsluga-pieniedzy.md`; `ADR-0010-awaria-renderu-frontendu.md`;
  `docs/PLAN.md` SC-4-03, SC-4-04, SC-4-06, SC-7-02.
  **Done 2026-09-25:** PR #131 (scalone `1bffd21`, zintegrowany z SC-5-02 i SC-7-03). Dowód:
  `frontend/src/features/projects/ScenarioCommercialModels.test.tsx` (K-01..K-07, R-01, R-03, R-04),
  `ScenarioCommercialModels.qa.test.tsx` (QA, dwie rundy),
  `backend/tests/test_story_points_revenue.py::test_r_01_sc_4_07_*` — 327 testów frontendu i 953
  backendu zielono, CI zielone. QA: dwie rundy, dowód trzyma (mutacje, które przeżyły testy
  dewelopera, zabite testami kontrastowymi QA; mutacje równoważne nazwane). Invariant Guardian: PASS.
  Security-auditor: PASS (Q-A=B potwierdzone). Reviewer: PASS WITH RESERVATIONS ×2 — R-01..R-06 i
  F06 naprawione decyzjami człowieka, w tym jednoliniowa poprawka backendu (R-01, odejście od
  "backend: zero zmian") i zmiana wyłącznie danych fixture'ów istniejących testów (Q4, R-03).
  Zaakceptowane, nienaprawione: ekspozycja przychodu oczekiwanego i kategorii w `/results` pod
  `RESULTS_READ` (B-01, ADR-0005 aneks SC-4-03 — ukrycie w UI to wybór prezentacji); ścieżka otwartej
  bramki kosztu osobowego dowiedziona tylko na fixture (`PLACEHOLDER_PERMISSIONS`). Zob.
  `docs/architecture/capabilities.md`.

- [x] **SC-8-01** — Zapisz historię zmian przy zatwierdzeniu scenariusza (audit log zatwierdzenia,
  F-12 punkt pierwszy).
  *Done when:* wywołanie `POST /projects/{project_id}/scenarios/{scenario_id}/approve` zapisuje
  dokładnie jeden wiersz w `audit_log` (autor w granicach dzisiejszej tożsamości placeholder, czas,
  dotknięty scenariusz/projekt); żadna inna akcja zapisu istniejąca dziś w repozytorium nie tworzy
  wiersza w tej tabeli.
  **Out of scope (explicit):** przeglądanie/ekran historii (osobne zadanie, np. `SC-8-02`); zapis
  zdarzeń dla duplikacji/archiwizacji/edycji/kopiowania (osobne zadania bloku 8); realna atrybucja
  autorstwa — czeka na ADR uwierzytelniania; kontrola dostępu do zasobu historii (F-13).
  **Decyzje bramki 1 (2026-09-27, analyst + architect, zaakceptowane przez człowieka):** tabela
  `audit_log` dedykowana zdarzeniom scenariusza (`action_type` dziś jednoelementowe,
  `scenario_approved`), nie generyczna tabela systemowa; `affected_data` jako prawdziwe klucze obce
  (`scenario_id`, `project_id`), nigdy kopia opisowa; zapis w tej samej transakcji co migawka i
  przestawienie statusu, po potwierdzonym przestawieniu statusu, przed `session.commit()`; brak
  wpisu w `SNAPSHOT_TABLES`/`SCENARIO_CHILD_COPIERS`.
  **Nowa/zmieniona decyzja architektoniczna:** aneks 2026-09-27 do **ADR-0004** (kształt tabeli,
  `affected_data`, miejsce w kolejności transakcji zatwierdzenia).
  **Podstawa:** Issue #14, `Wymagania/Requirements_EN.md` §4 F-12 (punkt pierwszy), §7 AC-10;
  `ADR-0004-wersjonowanie-kalkulacji.md` (sekcja "Konsekwencje", aneksy 2026-09-18, SC-3-02 pkt 9,
  2026-09-27); `ADR-0005-model-dostepu.md` (warunek "ADR uwierzytelniania").
  **Done 2026-09-28:** PR #151 (scalone `091d5cd`). Dowód: `backend/tests/test_scenario_approval_audit_log.py`
  (`test_k_01_approving_a_scenario_leaves_exactly_one_audit_log_row`,
  `test_k_02_each_approvals_row_names_its_own_scenario_not_the_other`,
  `test_k_03_performed_by_carries_the_identity_the_request_context_carried`,
  `test_a_duplicated_scenario_carries_zero_audit_log_rows_of_its_own`); migracja
  `backend/migrations/versions/a1b2c3d4e5f6_create_audit_log.py` — 1036 testów backendu zielono, CI
  zielone. QA: PASS, cztery mutacje uruchomione i potwierdzone niezależnie (usunięcie zapisu → 4/4
  czerwone; podmiana `scenario_id` → tylko K-02 czerwony; podmiana `performed_by` → tylko K-03
  czerwony; fałszywy wpis w `SCENARIO_CHILD_COPIERS` → tylko kanarek czerwony). Invariant Guardian:
  PASS (pełna zgodność z aneksem ADR-0004 2026-09-27, niezależnie zweryfikowana kod-po-kodzie).
  Security-auditor: PASS WITH RESERVATIONS — zaakceptowane, nienaprawione: `performed_by` niesie
  tożsamość z nagłówka żądania bez uwierzytelniania (ryzyko generalne już przyjęte w ADR-0005);
  błędna atrybucja zapisana dziś pozostanie trwała nawet po przyszłym ADR uwierzytelniania, bo
  tabela jest append-only bez ścieżki korekty — do uwzględnienia przy projektowaniu SC-8-02; brak w
  repo rejestru danych osobowych/polityki retencji (luka preegzystująca, nie wprowadzona tym
  zadaniem). Reviewer: PASS WITH RESERVATIONS — zaakceptowane, nienaprawione: R-01 (low, dwa pliki
  testowe spoza `committing_client` czyszczą `scenarios`/`projects` własnym wzorcem — dziś
  nieszkodliwe, mogłyby zawalić teardown w przyszłości przez `ON DELETE RESTRICT`, jeśli któryś
  zacznie wywoływać `approve`); R-02 (low, pierwsze w repo rozszerzenie zamkniętego enuma migracją
  czeka SC-8-02+ — `ALTER TYPE ... ADD VALUE` wymaga osobnej migracji przed pierwszym writerem
  nowej wartości). Zob. `docs/architecture/capabilities.md`.

- [x] **SC-4-02** — Calculate the Fixed Price revenue of a scenario (F-06.2, AC-07, revenue
  part). Written as the second model in the revenue dispatcher after T&M (SC-4-01, PR #64); after
  the sync with `main` of 2026-09-28 it joins T&M, Story Points (SC-4-04) and Outcome-based
  (SC-4-03).
  *Done when:* `backend/tests` prove criteria K-01, K-02, K-05..K-07 (analyst round 2, gate 1
  accepted 2026-09-25; K-03/K-04 dropped with D-3 = C):
  1. (K-01) AC-07, revenue part: a price of 150000 PLN → a revenue of 150000 PLN before and after a
     staffing change that raises the personnel cost from 100000 to 120000 PLN (the test measures
     the cost change as a precondition); the same change on a twin T&M scenario changes the
     revenue; a catalogue gap does not block the FP revenue; the FP formula module imports neither
     the T&M formula, nor `app.data.rate_windows`, nor the cost path, at any depth.
  2. (K-02) The price belongs to exactly one scenario: two FP scenarios of one project read their
     own prices; a project copy and a scenario duplicate (SC-6-01) carry the rule and the details
     with new ids, with an equal price and currency; editing the copy does not change the source.
  3. (K-05) Creation with a price and the price edit are refused under `approved` in the write
     statement (a two-connection race test per path), `404` out of scope before `409`, FP without a
     price → `422`, a stale `updated_at` → `409` distinguishable from the `409 approved`;
     `test_commercial_terms_access.py::test_the_request_carries_nothing_but_the_model`
     re-armed (not weakened): the placeholder body `{"model_type": "fixed_price"}` replaced with
     `{"model_type": "not_a_model"}` with an assertion of the discriminator refusal
     (`union_tag_invalid`) — with a real FP the old body got a `422` for the missing
     `agreed_price`, silently losing the proof "unknown model → `422`".
  4. (K-06) The shape is enforced by the database: the composite rule/details type FK, the price
     `CHECK` `>= 0`, the names of the existing constraints survive the migration; missing details →
     `incomplete_commercial_terms` (`"n/a"`, never `0`); price currency ≠ `scenarios.currency` →
     `currency_mismatch`.
  5. (K-07) `GET …/results` and `…/scenarios/compare` for an FP scenario (`draft` and `approved`)
     and what-if for an FP scenario in `draft` → `200`, revenue equal to the `commercial-terms`
     answer (what-if for `approved` stays `404` per SC-6-04, ADR-0015 point 5 — wording corrected
     2026-09-25); no cost field (field-set equality); the T&M response byte for byte unchanged.
     The FP revenue's `assumptions_used` reports `model_type = fixed_price`,
     `rate_source = fixed_price_terms`, `hours_source = not_applicable`,
     `vendor_axis = not_applicable` and `price_adjustments = not_included`, identically for `draft`
     and `approved` (control FP-6 of ADR-0003; `test_fixed_price_revenue.py`:
     `_assert_fixed_price_revenue_fields` and the K-07 tests calling it, and
     `test_k_07_an_approved_fixed_price_scenario_answers_results_and_compare_with_the_same_price`) —
     this test kills an FP `rate_source` derived from the scenario status
     (`live_catalog`/`approved_snapshot`). **Race (decision of 2026-09-28):** an approval landing
     between the FP revenue read and the cost read → `409` on `…/results` and on what-if (never
     `200` "Approved" with the draft's price, never the what-if `404`); without the interleaving an
     FP draft answers `200` on both — `test_fixed_price_race.py`:
     `test_k_07_an_approval_raced_between_the_fixed_price_revenue_and_cost_reads_is_a_409`
     (`results`/`what_if` × both personnel-cost permission sets),
     `test_k_07_contrast_no_approval_in_flight_a_fixed_price_draft_answers_200` and
     `test_k_07_fixed_price_terms_is_classified_as_status_compared_and_nothing_else_moves`.

  **Gate 1 decisions (2026-09-25, analyst + architect, accepted by the human — all as
  recommended):** D-1 = A (only the price of the whole project, milestones deferred); D-3 = C
  (price adjustments outside the task, Issue #112; the answer says explicitly that adjustments are
  not included; D-2 moot); D-4 = C at gate 1, revised below; D-5: price `CHECK` `>= 0` (AC-05
  reachable for FP); D-6 = A (price edit in a draft with the ADR-0007 marker and the `approved`
  guard). **D-4 revision (2026-09-25, human decision after the merge of SC-4-04, Issue #66;
  supersedes the earlier revision from the block-4 coordination, Issue #67):** FP reports
  `rate_source = fixed_price_terms`, `hours_source = not_applicable`,
  `vendor_axis = not_applicable` — following `story_points_terms` from SC-4-04; the value
  `not_used` is not introduced. An FP response class of its own, chosen by the `model_type`
  discriminator (`fixed_price_terms` does not enter the shared T&M/SP `Literal`). SC-4-02 no longer
  waits for SC-4-03 (#67): FP migration `b8f2d6a41c93` after `d2f6a91c4b58`, `CHECK
  model_type_known` = (`time_and_material`, `story_points`, `fixed_price`); SC-4-03 rebases later.
  **Dependency:** the `/results`/what-if race guard today compares `rate_source` and gives `409`
  for every model with a `rate_source` outside the catalogue (a defect already on `main` for SP); the
  fix in a separate Issue #118 (comparing the scenario status, ADR-0015 addendum) — SC-4-02 depends
  on the merge of #118. K-07 unchanged. Re-arming (not weakening) three existing tests — the
  placeholder model replaced with a value that is never a model (`not_a_model`); the exact set of
  models widened with `fixed_price` — accepted:
  `test_commercial_terms_schema.py::test_k_04_the_details_row_cannot_claim_another_model_and_a_rule_cannot_name_an_unknown_one`,
  `test_story_points_revenue.py::test_k_03_the_registries_are_keyed_by_exactly_the_two_real_models`,
  `test_story_points_revenue.py::test_k_03_unsupported_model_type_and_copy_refusal_still_correct_with_two_real_models`;
  the fourth — `test_the_request_carries_nothing_but_the_model` — in K-05.
  The drift guard
  `test_commercial_terms_schema.py::test_the_model_and_the_migration_agree_on_every_sql_expression`
  re-armed (not weakened): the SC-4-01 migration is compared with what it itself created, the
  agreement of the model with the database is checked by the test of the newest migration — one
  rule for block 4, re-armed by the task that lands first. Re-arming (not weakening) the field sets
  in `test_commercial_terms_access.py` with the FP fields — accepted (precedent SC-5-06).
  Architect: ADR-0003 and ADR-0004 addenda (2026-09-25, SC-4-02) accepted; no `adr-deviation`.

  **Sync with `main` (2026-09-28, human decision on Issue #66, option A):** #118 (SC-7-03) and
  SC-4-03 are merged. Migration `b8f2d6a41c93` re-parented onto the single head `c4d7e2a9b1f6`;
  `CHECK model_type_known` = (`time_and_material`, `story_points`, `outcome_based`,
  `fixed_price`), `downgrade` restores exactly the list `b9e3c7a1f264` (SC-4-03) created. Under the
  block-4 drift-guard rule, `LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH` in
  `test_commercial_terms_schema.py` now points at `b8f2d6a41c93`; `b9e3c7a1f264` stays compared with
  its own list. The registry drift guard keeps its `main` name
  `test_k_03_the_registries_are_keyed_by_exactly_the_two_real_models` (SC-4-04's mutation-log row
  in `capabilities.md` refers to it; the earlier rename to `..._three_real_models` was dropped), its
  exact set widened to the four models.

  **Race-guard classification (2026-09-28, human decision on Issue #66, option A, after the STOP
  on ADR-0015's R-06 validity condition):** the FP rule rows have a draft edit path (D-6 = A), so
  the exemption of a status-independent revenue from component (a) of the race guard does not
  apply to FP. `fixed_price_terms` is classified in a separate set,
  `app.domain.revenue.DRAFT_EDITABLE_RULE_SOURCES` (not a widening of `STATUS_DEPENDENT_SOURCES`),
  and `app.data.scenario_results.refuse_a_status_race` — the one guard of `/results`, `/compare`
  and what-if — compares the status for a revenue in either set. The FP answer keeps
  `rate_source = fixed_price_terms`; T&M, Story Points and Outcome-based behaviour is unchanged.
  ADR-0015, addendum 2026-09-28 (SC-4-02) — drafted by the Architect.

  **Post-review decisions (2026-09-28, human, Issue #66):**
  - R-01 — `_view_of` reads the FP price once and prices the revenue from the same value it states
    as `commercial_terms.agreed_price`; a price edit committed during the read can no longer split
    the two. Test: `test_fixed_price_review_fixes.py::test_r_01_a_price_edit_committed_during_the_read_cannot_split_revenue_and_agreed_price`.
  - R-02 — the downgrade of `b8f2d6a41c93` no longer deletes FP rules: with an FP rule present it
    fails loudly on the narrowed CHECK and loses nothing (the SC-4-03 pattern). Test:
    `test_fixed_price_schema.py::test_the_fixed_price_migration_downgrade_refuses_while_a_fixed_price_rule_exists`;
    the no-rule case stays `test_the_fixed_price_migration_downgrades_and_upgrades_again`.
  - R-03 — the price edit writes only when the whole-scenario FP rule is the scenario's only rule:
    a whole-scenario rule next to a segment-scoped one → `409` (`CommercialTermsEditAmbiguous`,
    predicate inside the guarded `UPDATE`), nothing written; an FP rule that is only segment-scoped
    → `404`, unchanged. Tests:
    `test_fixed_price_review_fixes.py::test_r_03_the_price_edit_of_a_scenario_with_a_second_segment_rule_is_refused_unwritten`
    and QA's `test_fixed_price_edit_boundaries.py` (3 tests).
  - R-05 — the FP rule read shape carries `outcome_terms: null`, like every other model's rule
    (SC-4-07 point 11); the FP field-set equality in `test_fixed_price_revenue.py`
    (`FP_TERMS_FIELDS`) is re-armed with the key (still an equality); the T&M response is byte for
    byte unchanged.
  - R-06 — the module docstring of `test_fixed_price_guards.py` describes the re-armed
    `test_the_request_carries_nothing_but_the_model` (`not_a_model` body, `union_tag_invalid`).

  **Out of scope (explicit):** price adjustments (bonuses, penalties, scope changes); milestones;
  allocation of the revenue to periods (SC-4-05); profit/margin/profit decline of AC-07 (block 7);
  the FP rule screen and widening the `isRevenueAssumptionsShape` validation — until then FP
  scenario cards are the named `unreadable` state (ADR-0010 point 2), Issue #113; mixed models per
  phase/workstream (Issue #65); editing a segment-scoped FP rule (the price edit reaches only the
  whole-scenario rule, R-03); the Compare screen is `unreadable` for a project with an FP scenario
  until #113 (R-04); a price edit leaves no author trace — accepted exception (Security 2; the FP
  write events stay on the deferred `audit_log` list, ADR-0004 FP point 5); R-07 (recorded, not
  fixed — human decision 2026-09-28): a segment rule committed by another transaction while a price
  edit waits on the scenario lock is not seen by the edit's `NOT EXISTS` predicate (it keeps the
  statement snapshot; EvalPlanQual re-checks only the locked rows), so the edit lands and the
  answer's read then raises `MultipleCommercialRulesNotSupported` (a `500`) — reachable today only
  through data-layer writes; the task that exposes `scope_ref` in the API fixes it by re-checking
  "exactly one rule" in the same transaction before `commit()`, or by building the post-commit view
  without `_rule_of`'s single-row assumption.

  Basis: `Wymagania/Requirements_EN.md` §4 F-06.2, §7 AC-05/AC-07; Issue #66;
  `ADR-0003-model-modeli-komercyjnych.md` (addendum 2026-09-25 SC-4-02);
  `ADR-0004-wersjonowanie-kalkulacji.md` (addendum 2026-09-25 SC-4-02); `ADR-0007` (the marker on
  `commercial_terms`); `docs/PLAN.md` SC-4-01, SC-6-01, SC-7-01.
  **Done 2026-09-28:** PR #159 (merged `181504e`, commit `dc633ec`). Evidence:
  `backend/tests/test_fixed_price_revenue.py` (K-01 `test_k_01_*` incl. AC-07 with the T&M twin and
  the import-graph test, K-02 `test_k_02_*`, K-07 `test_k_07_*`), `test_fixed_price_copy.py` (K-02),
  `test_fixed_price_guards.py` (K-05), `test_fixed_price_schema.py` (K-06, downgrade refuses while an
  FP rule exists), `test_fixed_price_race.py` (K-07 race, ADR-0015 addendum 2026-09-28),
  `test_fixed_price_review_fixes.py` (R-01, R-03), `test_fixed_price_edit_boundaries.py` (QA: marker
  interleaving, segment-rule boundaries, dispatcher entry); migration
  `backend/migrations/versions/b8f2d6a41c93_create_fixed_price_terms.py`; decisions: gate 1 and later
  decisions on Issue #66, ADR-0003/ADR-0004/ADR-0015 addenda 2026-09-25/2026-09-28. 1143 backend tests
  green, CI green. QA: two rounds, 40 mutations (`SC-4-02-M1..M40`) — survivors M14, M15 (round 1) and
  M7, M8, M33 (round 2) closed with QA tests; open survivors M23 (defence in depth) and M25
  (equivalent under the request lifecycle). Invariant Guardian: PASS (both rounds). Security auditor:
  PASS. Reviewer: round 1 STOP (R-01..R-06) fixed or recorded per human decisions; round 2 PASS WITH
  RESERVATIONS — **accepted, not fixed:** R-07 (Low, recorded in the ADR-0003 post-review addendum
  point 5 and "Out of scope" above). **Does not prove:** the FP race on `/compare` specifically (same
  guard as `/results`); the price-edit step of the R-06 interleaving end to end; R-03 under a
  concurrent segment-rule write (R-07); the frontend (#113); a multi-step downgrade across
  `b9e3c7a1f264`. See `docs/architecture/capabilities.md`.

- [x] **SC-1-12** — Decide caller authentication with Keycloak (OIDC) and the application user record (F-13, Issue #150).
  *Done when:* The human-accepted ADR-0018 resolves Z-1..Z-7, assigns every applicable authentication-related closure condition in ADR-0004, ADR-0005, and ADR-0013 to ADR-0018 or SC-1-14, defines the blocked role/permission and authentication-implementation successor Stories, and keeps caller authentication at `no evidence`. This task changes no application code.
  **Out of scope (explicit):** Keycloak deployment, realm export/runtime configuration, application wiring, role and permission design, named-person register changes, and application database encryption; each has its own documented closing condition.
  **Basis:** Issue #150; `Wymagania/Requirements_EN.md` §4 F-13, §5 NF-04/NF-11, §7 AC-06; ADR-0004, ADR-0005, ADR-0013, ADR-0019; `docs/architecture/capabilities.md`.
  **Done 2026-10-01:** PR #211 (merged as `3f896671`). Evidence: accepted `docs/architecture/decisions/ADR-0018-uwierzytelnianie.md` resolves Z-1..Z-7; the dated ADR-0005 routing table assigns the authentication/role closure conditions, including all three B-01 rows; ADR-0018 identifies the blocked successor Stories; caller authentication remains `no evidence` in `docs/architecture/capabilities.md`. The security audit and human decisions are recorded in Issue #150. This decision does not prove runtime authentication.
- [x] **SC-1-13** — Persist and apply manually entered exchange rates to scenario results (F-02; prerequisite for SC-6-07, Issue #102).
  *Done when:* backend tests prove the accepted K-01..K-07 criteria on Issue #189: effective-dated rates resolve for ordered currency pairs; all supported result components convert into the scenario currency using Decimal and explicit rounding; organization, project, and scenario values follow the approved source hierarchy and saved rates remain reproducible; missing rates yield an explicit unavailable result without partial totals; PostgreSQL rejects overlapping windows per pair; access, personnel-cost visibility, and approved-version boundaries remain enforced. QA records the named mutations.
  **Gate 1 decisions (2026-09-30, Issue #189):** all supported revenue and cost components are in scope; pairs are directed with no implicit reciprocal lookup; rate source hierarchy is organization default → project override → scenario override, with the selected rate copied into the saved scenario as required for reproducibility; each component uses the rate effective in its own period.
  **Foundation verified:** directed, effective-dated exchange-rate storage, organization → project → scenario resolution, period-by-period conversion, missing-rate handling, and approval snapshots are implemented and covered by the tests below. The SC-1-13 project-override addendum to ADR-0006 records the Gate 1 decisions and remains subject to the ADR's human approval process.
  **Out of scope (explicit):** automated rate feeds; UI; hypothetical rate-change analysis (SC-6-07); non-ISO currency identifiers.
  **Basis:** `Wymagania/Requirements_EN.md` §4 F-02 and §6; ADR-0002, ADR-0004, ADR-0005, ADR-0006, ADR-0008; Issue #189.
  **Done 2026-09-30:** PR #200 (merged as `39a15f2`). Evidence: `backend/tests/test_exchange_rates.py` (K-01 directed pair and hierarchy, K-02 per-period Decimal conversion and one final rounding, K-04 all-or-unavailable); `backend/tests/test_exchange_rates_api.py` (K-05 PostgreSQL overlap exclusion, K-06 permission and project scope, K-07 approved-scenario write refusal); `backend/tests/test_scenario_results.py::test_exchange_rate_composes_revenue_personnel_and_additional_cost_in_scenario_currency` (revenue, personnel components, paid absence, fixed amount, assigned FTE, additional costs, profitability, and unchanged result after approval and source-rate edit); `::test_no_scenario_currency_does_not_sum_mixed_currency_additional_costs` (contrast). QA mutation M1 and its outcome are recorded in `docs/architecture/capabilities.md` and [Issue #189](https://github.com/TanerCRB/StafffingCalculator/issues/189#issuecomment-5917397362). Full backend: 1514 passed, 6 xfailed; frontend: 424 passed; Ruff and migration SQL generation passed; both required CI jobs passed. Invariant Guardian, Reviewer, and Security Auditor reviews were in-session, not independent. Does not prove automated feeds, UI, or SC-6-07 what-if analysis.
- [x] **SC-4-08** — Edit and delete scenario commercial rules (backend): let project managers correct draft Fixed Price, Outcome-based, and Story Points terms, and remove a rule when needed, without changing the commercial model of an existing rule. Covers Time & Material, Fixed Price, Outcome-based, and Story Points (Issue #126; gate 1 approved 2026-09-30; Fixed Price was already merged via SC-4-02 before gate 1).
  *Done when:* backend tests prove the approved criteria K-01..K-05 with recorded SC-4-08 mutations: (1) a valid full replacement on a draft updates the readable rule and calculated revenue for Fixed Price, Outcome-based, and Story Points; T&M edit is refused; (2) invalid/incomplete model fields, probability totals/precision/ranges, negative amounts, or a changed `model_type` are rejected without changing aggregate or detail rows; (3) stale markers and approved-scenario writes are distinct named conflicts, with exactly one winner for concurrent edits and no write during an approval race; (4) missing, foreign-project, and out-of-scope rules return indistinguishable 404s, and callers without `COMMERCIAL_WRITE` cannot edit/delete; (5) a current-marker delete atomically removes aggregate and details for all four models, GET reports `no_commercial_terms`, POST can recreate a rule, and copies preserve the edited values independently of later source edits. Existing regressions named in Issue #126 remain green. Evidence and mutation outcomes are recorded in `docs/architecture/capabilities.md` below.
  **Gate-1 decisions (2026-09-30, approved by human):** Q1=A — include edits for Fixed Price, Outcome-based, and Story Points and deletion for all four models, reversing the D-5 premise by ADR-0003 addendum; Q2=A — expose Story Points price/accepted points from GET under `COMMERCIAL_READ`; Q3=A — allow delete/recreate using a new rule id, keeping `model_type` immutable per row; Q4=A — full replacement with the create shape plus concurrency marker; Q5=A — use `COMMERCIAL_WRITE` for edit/delete. The architecture impact map is approved. The human set `state:implementation` before code work.
  **Out of scope (explicit):** UI; editing `scope_ref`-scoped rules; changing `model_type` in place; change history; autosave; new commercial rule components; edits to approved scenarios.
  **Foundation verified:** `commercial_terms.updated_at` now guards replacement and deletion, and the aggregate/detail delete is atomic; SC-4-08 real concurrency and deletion tests prove both foundations. Basis: Issue #126; `Wymagania/Requirements_EN.md` NF-05, F-06.5, AC-04; ADR-0003, ADR-0004, ADR-0005, ADR-0007; `docs/PLAN.md` SC-4-01..SC-4-07.
  **Done 2026-09-30:** PR #194 (merged as `6c66f6c`). Evidence: `backend/tests/test_scenario_commercial_terms_edit_delete.py` (K-01..K-05, including real two-connection writer/approval and creator/delete interleavings), plus Fixed Price and Story Points regressions; both required CI test jobs passed. Local full backend run: 1489 passed, 6 xfailed, and one environment-specific failure in `test_application_import_fails_when_nothing_is_configured_at_all` (the child process from an empty cwd cannot import `app`). Ruff passed; frontend 424 tests, lint, and production build passed. QA M1-M5 and contrast evidence are recorded below; Invariant Guardian, Reviewer, and Security Auditor: PASS.
- [x] **SC-4-09** — Include approved price adjustments in Fixed Price revenue (F-06.2, Issue #112).
  *Done when:* mutation-checked backend tests prove that only approved adjustments affect Fixed Price revenue by their kind-defined signed amount; pending writes and approval decisions enforce their separate permissions; terminal states and corrections follow the approved lifecycle; adjustment currency matches the Fixed Price rule currency; scenario copies create new pending adjustments; and revenue remains independent of staffing, effort, and cost.
  **Out of scope (explicit):** Milestones and revenue-period allocation (SC-4-05); profit and margin (block 7); adjustments for other commercial models; currency conversion; real-user role assignment.
  **Done 2026-10-01:** PR #203 (merge commit `1602d37`). Evidence: `backend/tests/test_fixed_price_adjustments.py` (`test_adjustment_amount_is_nonnegative_and_kind_defines_delta_sign`, `test_fixed_price_revenue_includes_approved_adjustments_only`, `test_adjustment_write_permission_currency_decision_and_terminal_lifecycle`, `test_scenario_duplicate_copies_adjustments_as_pending_and_no_snapshot_rows`, and `test_adjustment_create_retry_after_scenario_approval_returns_existing_row`); full backend suite (1,527 passed, 6 xfailed), frontend suite (424 passed), and both required CI jobs passed. QA mutations SC-4-09-M1/M2 and their limits are recorded in `docs/architecture/capabilities.md`. The evidence covers the approved lifecycle and revenue behavior; it does not prove simultaneous duplicate-request races.
- [x] **SC-4-10** — Show Fixed Price rules and scenario revenue (F-06.2, frontend, Issue #113; gate 1 approved 2026-10-01).
  *Done when:* frontend tests prove K-01..K-06: Fixed Price rule and revenue on the scenario card and Compare screen; whole-scenario price creation and editing; distinct success, refusal, and unresolved write outcomes; and correct money formatting. Each named SC-4-10 mutation is killed.
  **Out of scope (explicit):** Backend (SC-4-02/#66); price adjustments (SC-4-09/#112); milestones and revenue-period allocation; profit, margin, and cost presentation (block 7).
  **Basis:** Issue #113; `Wymagania/Requirements_EN.md` §4 F-06.2 and §7 AC-07; ADR-0002, ADR-0003, ADR-0009, ADR-0010; `docs/architecture/capabilities.md`.
  **Done 2026-10-01:** PR #206 (merge commit `a6c106a`). Evidence: `ScenarioCommercialModels.test.tsx` covers Fixed Price card display and the accepted/rejected revenue-source pairing; `CompareScenariosScreen.test.tsx` covers the server-calculated amount/currency and four-decimal agreed-price formatting; `ScenarioCommercialTerms.test.tsx` covers draft whole-scenario create/edit and success, refusal, and unresolved outcomes. Frontend suite (433 passed), lint, production build, both required CI jobs, and Invariant Guardian passed. The Fixed Price pairing mutation was killed by the Compare screen test (the mutated response was rejected as unreadable and no comparison table rendered); money-formatting mutation evidence is documented below.

- [ ] **SC-4-11** — Complete commercial-term editing in the UI (F-06, frontend, Issue #237; user-approved scope A, 2026-10-05).
  *Done when:* frontend tests prove: (1) draft Story Points and Outcome-based rules can be created and the server's saved parameters and revenue are rendered; (2) draft rules of those two models can be fully replaced using the current `updated_at`, without changing `model_type`; (3) draft rules for all four models can be deleted using the current marker and the empty-rule state is shown after re-read; (4) approved scenarios offer no write controls; (5) refusals and unresolved outcomes are distinct from successful writes. QA kills the missing DELETE-marker mutation; frontend tests, lint and production build pass.
  **Out of scope (explicit):** Backend API and revenue formulas (SC-4-08); changing a rule's `model_type`; writes to approved scenarios; change history and autosave.
  **Gate-1 scope decision (2026-10-05, user-approved A):** include full same-model edits for draft Outcome-based and Story Points in addition to the originally described create/delete UI; retain `model_type` immutability.
  **Basis:** Issue #237; SC-4-08 backend contract; `Wymagania/Requirements_EN.md` F-06; ADR-0002, ADR-0003, ADR-0004, ADR-0005, ADR-0007, ADR-0009 and ADR-0010; `docs/architecture/capabilities.md`.
  **Done 2026-10-05:** [PR #262](https://github.com/TanerCRB/StafffingCalculator/pull/262), merged as `3754cc5`. Evidence: `frontend/src/features/projects/ScenarioCommercialTerms.test.tsx` (create, same-model replacement, refusal and unresolved outcomes, delete for all four models, empty state after re-read, Approved write controls); `ScenarioCommercialModels.test.tsx` (saved model details and revenue rendering). Frontend suite 484/484, ESLint, `tsc -b`, production build, `git diff --check`, and required GitHub CI 2/2 passed. QA mutation removing `updated_at` from DELETE was killed by all four model deletion tests; source was restored byte-identically. Mutation details are recorded below.

- [x] **SC-7-09** — Calculate expected profit and margin for an Outcome-based scenario (Issue #127).
  **Done 2026-10-01:** Outcome-based results expose expected profit and margin from rounded expected revenue and included cost; non-applicable states, currency and personnel-cost gates, and per-scenario comparison are covered by `backend/tests/test_outcome_expected_profitability.py::test_k_01_*` through `::test_k_07_*`.
- [x] **SC-7-10** — Include fixed-amount and assigned-FTE costs in scenario profitability (F-10), Issue #181.
  *Done when:* `backend/tests` prove that each resolved personnel-cost component contributes exactly once to `included_cost`, `profit`, `margin`, and `markup`; a required unresolved component retains its named state and makes all four aggregates non-numeric; approved scenarios use frozen inputs; component states identify exclusions; `assigned_fte` above `headcount` remains accepted, carries a plausibility marker, and is priced unchanged. Every criterion has a recorded mutation result.
  **Out of scope (explicit):** FTE overheads — separate F-07 follow-up, to close when that dedicated task reaches gate 1; FTE as an F-08 charge base — separate allocation requirement, to close when its dedicated task reaches gate 1; frontend presentation — this task defines backend result semantics only and uses existing named states as exclusion signals.
  **Basis:** Issue #181; `Wymagania/Requirements_EN.md` F-07 and F-10; ADR-0013 addendum SC-5-04, points 10–11; `docs/architecture/capabilities.md` SC-5-04 known gaps; `backend/app/domain/scenario_results.py`.
  **Done 2026-10-03:** K-01–K-05 are proved by result aggregation, unresolved-state, approved-input, and above-headcount response tests in `backend/tests/test_scenario_results.py`, `test_profitability_currency.py`, `test_assigned_fte_cost_scenario.py`, and `test_assigned_fte_api.py`; mutation outcomes are recorded in the [Gate 2 report](https://github.com/TanerCRB/StafffingCalculator/issues/181#issuecomment-5960167139) and [Gate 3 QA continuation](https://github.com/TanerCRB/StafffingCalculator/issues/181#issuecomment-5962248721). The final focused suite passed 101 tests. K-03 evidence mutates approved exchange-rate and personnel-cost reads; no separate live-calendar mutation was run.
- [x] **SC-7-11** — Show period results and staffing timeline (F-10, F-11; Issue #240).
  *Done when:* a selected-scenario Overview shows revenue, costs, profit, margin, and aggregate planned FTE by reporting period; tests prove scenario isolation, Gate 1 boundary attribution, partial-period handling, zero-denominator and below-target margin behavior, negative-profit signaling, approved-input and resolved-target reproducibility, and the personnel-cost visibility gate, with named mutations recorded for each criterion.
  **Out of scope (explicit):** scenario comparison (SC-7-04); the existing whole-scenario cost breakdown; PDF/XLSX export (Issue #241); named-person staffing pending a separate personal-data assessment.
  **Basis:** Issue #240; `Wymagania/Requirements_EN.md` F-10/F-11; ADR-0002, ADR-0003 SC-7-11 addendum, ADR-0004, ADR-0005, ADR-0008, and ADR-0012.
  **Implementation evidence (2026-10-06):** [PR #279](https://github.com/TanerCRB/StafffingCalculator/pull/279), merged as `4b65628`. Tests cover K-01–K-08 in `backend/tests/test_scenario_results.py`; project scope, `RESULTS_READ`, and personnel-cost shaping in `backend/tests/test_scenario_results_access.py`; selected-scenario isolation, margin cues, unallocated totals, gated fields, and read refusals in `frontend/src/features/overview/ProjectOverviewScreen.test.tsx`; and Overview navigation in `frontend/src/shell/AppShell.test.tsx`. Full local gates passed: backend 1,636 tests and Ruff; frontend 540 tests, ESLint, TypeScript, and Vite build. Both GitHub CI workflows passed. QA recorded four killed mutations for K-07 and K-08; Guardian, Reviewer, and Security Audit passed. Named-person staffing and partial-month calendar proration remain out of scope.
  **Done 2026-10-07:** Follow-up [PR #286](https://github.com/TanerCRB/StafffingCalculator/pull/286), merged as `e6fe645`, adds `backend/tests/test_scenario_results.py::test_k_01_approved_period_results_keep_frozen_calendar_and_resolved_target` and `frontend/src/features/overview/ProjectOverviewScreen.test.tsx` (“shows anonymous planned FTE per period and changes only the period whose staffing changed”). K-01–K-06 mutations were detected and restored; the approved-calendar mutation was rerun after tightening the draft FTE expectation to `100 / (22 × 6.50)`. The final focused runs passed backend 19/19 and UI 8/8; pre-push full suites passed backend 1,643 and frontend 550. Mutation outcomes are recorded in `docs/architecture/capabilities.md` below.
- [x] **SC-5-10** — Correct the ADR citation in the personnel-surcharge test (Issue #161).
  *Done when:* `test_qa_finding_approving_a_scenario_with_a_configured_surcharge_changes_its_own_cost` cites ADR-0005, addendum 2026-09-25, point 3 for the sentence; and review confirms no same mix-up in sibling `test_personnel_cost*.py` files.
  **Done 2026-10-01:** The corrected docstring and scoped sibling review are recorded in [PR #210](https://github.com/TanerCRB/StafffingCalculator/pull/210); the sentence is in [ADR-0005, 2026-09-25 SC-5-02 addendum, point 3](https://github.com/TanerCRB/StafffingCalculator/blob/a711665567bf58dba7f9bf7f69798aacf53d2aa5/docs/architecture/decisions/ADR-0005-model-dostepu.md#L895), and the corrected [test docstring](https://github.com/TanerCRB/StafffingCalculator/blob/a711665567bf58dba7f9bf7f69798aacf53d2aa5/backend/tests/test_personnel_cost_surcharge.py#L471) is the artifact for the criterion.
  **Out of scope (explicit):** Other ADR citation accuracy issues — each has an independent source and proof requirement; file separately.
  **Basis:** Issue #161; reviewer verification of #157; ADR-0005, addendum 2026-09-25, SC-5-02, point 3.
*(further rows are added by the Product Owner role, one per task, following gate 1)*
- [x] **SC-5-12** — Return a safe 422 for bare non-finite JSON numbers at the API boundary (Issue #178).
  *Done when:* `backend/tests` prove that raw POST and PATCH bodies containing bare `NaN`, `Infinity` or `-Infinity` for SC-5-04's `assigned_fte` return 422 without echoing the rejected value and without writing data; raw POST bodies with those tokens in additional-cost `amount` (a separate request schema) do the same; finite-value contrasts succeed and persist; six existing strict xfail marks for the assigned-FTE bare-token cases are removed; ordinary 422 body shape is pinned and unchanged.
  **Out of scope (explicit):** Changing numeric validation policy; frontend error rendering while the 422 response shape remains unchanged; broad JSON parser/configuration changes beyond this response contract.
  **Basis:** Issue #178; PR #176 QA/reviewer findings; `docs/architecture/capabilities.md` SC-5-04 known gap; ADR-0005 and ADR-0009.
  **Done 2026-10-02:** [PR #217](https://github.com/TanerCRB/StafffingCalculator/pull/217), merged as `a0fc84f`. Evidence: `backend/tests/test_assigned_fte_api.py` covers the bare-token POST/PATCH cases, finite create contrast, and unchanged ordinary 422 shape; `backend/tests/test_additional_cost.py::test_k_03_bare_non_finite_amount_is_rejected_without_echo_or_write` covers all three amount tokens, no echo/no write, and a persisted finite amount. Full backend suite: 1560 passed; Ruff passed; frontend suite: 433 passed; required CI passed 2/2. QA mutation killed 9 cases and was restored; see the mutation log and PR #217.

- [x] **SC-5-13** — Manage fixed-amount costs for a scenario (Issue #236).
  *Done when:* Analyst criteria K-01–K-07 prove fixed-amount cost listing and management, correct refusal behavior, result visibility, and keyed write replay/concurrency against real PostgreSQL; frontend retries reuse the same key.
  **Out of scope (explicit):** New formulas, categories and charge bases — revisit under SC-5-07+ when specified; charts/PDF export — separate Block 7 work; general idempotency for other write endpoints — each endpoint establishes its own contract.
  **Basis:** Issue #236; F-08; SC-5-05; ADR-0014 and ADR-0009; `docs/architecture/capabilities.md`.
  **Done 2026-10-05:** [PR #264](https://github.com/TanerCRB/StafffingCalculator/pull/264), merged as `91466fd`. Evidence: `backend/tests/test_additional_cost_idempotency.py` proves same-key replay, concurrent deduplication, changed-payload conflict, retention, legacy no-key behavior, and permission re-check before replay; `backend/tests/test_additional_cost.py`, `test_additional_cost_access.py`, and `test_additional_cost_guards.py` cover saved costs, scope and approved-scenario guards; `frontend/src/features/projects/ScenarioAdditionalCostsSection.test.tsx` covers selected-scenario listing, saved writes, refusals, approved-scenario behavior, retry key reuse and denied replay; `frontend/src/features/projects/ScenarioResults.test.tsx` covers result visibility. Backend suite 1608 passed with Ruff clean; frontend suite 497 passed with lint and build clean; GitHub CI passed 2/2. QA mutations removing idempotency-key handling failed 4/6 targeted backend tests; a false UI-success mutation failed 7/13 component tests; both were restored ([QA report](https://github.com/TanerCRB/StafffingCalculator/issues/236#issuecomment-5998122368)). These mutations do not individually prove every database atomicity constraint or every refusal path.

- [x] **SC-1-14** — Define application roles and permission mapping (Issue #253).
  *Done when:* A human-approved role/permission ADR records role dimensions, permission-to-role mapping, grant authority, and scope rules for project access, organization defaults, scenario reads and draft writes, and scenario approval; it resolves the authentication/role conditions routed to SC-1-14 by ADR-0005, including the dormant B-01 combinations, without claiming runtime authentication or API enforcement is implemented.
  **Out of scope (explicit):** Keycloak runtime configuration, application authorization enforcement, assumption write APIs, and UI; these remain separate successor work under ADR-0018, SC-1-22, SC-1-23, and SC-1-21.
  **Basis:** ADR-0005, including the 2026-10-01 routing addendum; ADR-0018, Section 6 and Required successor work; F-13; Issues #150, #235, and #253.
  **Done 2026-10-05:** [PR #258](https://github.com/TanerCRB/StafffingCalculator/pull/258), merged as `0f5b62c`. Evidence: accepted [ADR-0022](architecture/decisions/ADR-0022-role-and-permission-assignment.md) records permission dimensions, grant authority, project and organization scope, separate scenario and approval permissions, the routed B-01 combinations, and the required decisions for history-read, `PEOPLE_READ`, `SCENARIO_COPY`, and `COMMERCIAL_ADJUSTMENT_APPROVE`; its acceptance controls A22-1–A22-6 are explicit. No capability or mutation row is added: this task records authorization policy only; ADR-0022 explicitly leaves runtime authentication and API enforcement unproven and out of scope.
- [x] **SC-1-15** — Return a named refusal when a project copy cannot be written (Issue #179), a follow-up to SC-1-03 exposed by SC-5-04.
  *Done when:* Backend tests prove that a classified database refusal during POST /projects/{id}/copy returns a named 409 containing the reported SQLSTATE and actual constraint identifier without row values or a partial copied aggregate; an unclassified failure remains a 500; an accepted copy retains its existing response and complete aggregate. Mutations removing classification, diagnostic identity, or rollback fail the tests.
  **Out of scope (explicit):** Changing copy permissions (ADR-0005); changing the accepted SC-5-04 mixed-version rollout (Issue #79 H-7).
  **Basis:** Issue #179; docs/PLAN.md SC-1-03 and SC-5-04; ADR-0001, ADR-0004, ADR-0005, ADR-0008; PR #176's separate project-copy follow-up.
  **Done 2026-10-02:** [PR #220](https://github.com/TanerCRB/StafffingCalculator/pull/220) (merged as `3d5b333`). Evidence: `backend/tests/test_project_copy_write_refusal.py::test_k_01_classified_check_refusal_is_409_and_unclassified_failure_is_500`, `::test_k_02_refusal_names_the_actual_database_constraint`, `::test_k_03_refusal_excludes_failing_row_values_and_driver_detail`, `::test_k_04_refusal_rolls_back_project_grant_and_scenario_for_a_separate_connection`, and `::test_k_05_accepted_copy_keeps_response_and_complete_aggregate`; full backend suite 1568 passed, Ruff and both required CI jobs passed. Mutations M1–M3 and their results are recorded in `docs/architecture/capabilities.md` and [Issue #179](https://github.com/TanerCRB/StafffingCalculator/issues/179#issuecomment-5947340755). This does not prove every SQLSTATE or every child copier in the cascade.

- [x] **SC-1-18** — Create projects from the Projects screen (F-01; Issue #228).
  *Done when:* Tests prove a caller with `PROJECT_CREATE` can create a project with all six F-01 fields and read it in the list; after a lost response, retrying with the same caller, `Idempotency-Key`, and payload returns the original result without a second project; invalid input and definitive refusals remain visible; reusing a key with a different payload under the same caller is rejected; a different caller cannot replay or learn the first caller's result; current authorization is checked before replay; and concurrent identical retries create one project.
  **Out of scope (explicit):** Implementing project search/filter/pagination (Issue #229, SC-1-17; SC-1-18 reconciles creates with that existing server-paginated list); editing (Issue #230, SC-1-16), copying (Issue #231), and archiving (Issue #232) — separate project actions with distinct write invariants; scenario creation (Issue #233) — separate scenario workflow.
  **Basis:** Issue #228 and [approved Gate 1 expansion](https://github.com/TanerCRB/StafffingCalculator/issues/228#issuecomment-5983292247); F-01; ADR-0005, ADR-0009 addendum 2026-10-04, ADR-0010, and ADR-0019 owner-field assessment.
  **Done 2026-10-04:** [PR #248](https://github.com/TanerCRB/StafffingCalculator/pull/248), merged as `87eff8585e9631896614c706f1aa2c32c8785540`. Evidence: `frontend/src/features/projects/ProjectCreateForm.test.tsx` (K-01–K-05, R-01–R-04, page-two reconciliation and replay); `backend/tests/test_project_create_idempotency.py` (K-06/K-07 replay, changed payload, caller scope, authorization re-check and concurrency); `backend/tests/test_project_create_required_fields.py`; `backend/tests/test_access_control.py`; backend suite 1,583 passed and Ruff/Alembic checks passed; frontend suite 464 passed, lint and build passed; required GitHub CI passed 2/2. QA mutations and results are recorded in `docs/architecture/capabilities.md`.
- [x] **SC-1-16** — Edit project details from the Projects screen (F-01, frontend).
  *Done when:* Frontend tests prove a successful edit displays the PATCH response, the original `updated_at` token is sent byte-for-byte, a stale-token `409` preserves input and explains the conflict, and edits to delivery period/reporting currency are explained and refused when an approved scenario exists. A refused write is never presented as saved.
  **Out of scope (explicit):** Project creation, copying, archiving, and scenario editing — these are separate actions or data invariants outside metadata editing.
  **Basis:** Issue #230; `Wymagania/Requirements_EN.md` F-01; SC-1-02; ADR-0005 and ADR-0007.
  **Done 2026-10-04:** [PR #244](https://github.com/TanerCRB/StafffingCalculator/pull/244), merged as `39e7e8e`. Evidence: `ProjectListScreen.test.tsx` tests “sends the unmodified detail token with a project edit,” “renders the accepted PATCH values in the project row,” “keeps entered values and explains a stale edit refusal,” “explains approved-scenario field freezes and contrasts an editable draft project,” and “re-reads the active search after editing a field that removes the project from its results”; backend tests `backend/tests/test_project_edit.py::test_sc_1_02_02_frozen_fields_are_refused_by_the_data_layer_once_a_scenario_is_approved` and `::test_sc_1_02_02_the_api_reports_the_frozen_field_refusal_as_409_and_writes_nothing`; frontend suite 446 passed, backend suite 1,577 passed, frontend lint/build and backend Ruff passed, required CI 2/2 passed. The approved-field UI mutation and result are recorded below and in PR #244.
- [ ] **SC-1-17** — Search, filter and paginate the Projects list (F-01).
  *Done when:* backend and frontend tests prove case-insensitive substring search over project name, client and owner; active/archived filtering and combined criteria; 20-item server-side pages with stable ordering and no duplicates or omissions over a stable dataset; inaccessible projects never appear in rows or totals; criterion changes reset to page 1; reset clears criteria; and the UI distinguishes no matches from an empty accessible project list.
  **Out of scope (explicit):** Project writes; client-side filtering of truncated pages; fuzzy/ranked search, filters beyond status, and user-configurable page size.
  **Basis:** Issue #229; docs/PLAN.md SC-1-05, SC-1-07 and SC-1-09; ADR-0005 and ADR-0017; docs/architecture/capabilities.md.
  **Done 2026-10-04:** [PR #242](https://github.com/TanerCRB/StafffingCalculator/pull/242), merged as `97f104f`. Evidence: `backend/tests/test_sc_1_17_projects.py` (K-01, K-02, K-04, K-05); `frontend/src/features/projects/ProjectListSearchPagination.test.tsx` (search, combined filters, pagination resets, reset, empty states and retry); full backend suite 1,577 passed, Ruff passed; frontend suite 440 passed, lint and build passed; required CI passed 2/2. QA mutations for caller scope and stable ordering are recorded in the capability registry.

- [ ] **SC-1-19** — Copy a project from the Projects screen (F-01).
  *Done when:* Frontend tests prove that a successful copy appears separately with draft scenarios and is selected through the existing Projects screen interaction (selected/pressed project row name control and visible scenario panel); a `403` and the named `409` for terms using a model absent from this version's supported detail registry are shown without a phantom copy; after either refusal, one user invocation makes exactly one POST attempt and is not automatically retried. The `409` is a rare mixed-version refusal and is not currently capability-tested; Gate 1 accepted this evidence gap.
  **Out of scope (explicit):** changing backend copy semantics or authorization (SC-1-03 and existing decisions); copying a scenario within its project (SC-6-01); editing (SC-1-16) or archiving (SC-1-04) projects; adding, editing, or deleting scenarios.
  **Basis:** Issue #231; `Wymagania/Requirements_EN.md` F-01; docs/PLAN.md SC-1-03 and SC-1-06; `docs/architecture/capabilities.md`; `backend/app/api/projects.py::copy_project_endpoint`.
  **Done 2026-10-04:** [PR #246](https://github.com/TanerCRB/StafffingCalculator/pull/246), merged as `9771a46`. Evidence: `frontend/src/features/projects/ProjectListScreen.test.tsx` tests `shows the validated copy and selected scenarios after reconciling the server list`, `copies the project whose row action was selected`, `clears an Archived filter and refreshes from the server when copying an archived project`, `keeps copied details selected and lets the user page to a copy with a repeated name`, `does not add a phantom row when copy permission is denied`, and `explains the named unsupported-model refusal without adding a phantom row`; frontend suite 453/453, lint and build passed; push hook passed backend 1577/1577 and frontend 453/453; GitHub checks 2/2 passed. QA mutation restoring automatic page walking failed at the off-page message assertion and passed after restoration (capability registry mutation log). This does not prove backend copy semantics beyond the existing SC-1-03 tests, nor that every mixed-version deployment reaches the named 409.
- [x] **SC-1-20** — Archive a project from the Projects screen.
  *Done when:* Frontend tests prove that a PM can archive an active project and still see it listed as Archived; a refused archive leaves its prior status visible; repeated activation during a pending request sends no duplicate submission; and the confirmation clearly states that archiving cannot be undone before submission.
  **Out of scope (explicit):** Unarchiving — one-way archive is defined by SC-1-04; revisit separately if restoration is needed. Deleting — separate retention decision. Editing and copying — separate F-01 actions.
  **Basis:** Issue #232; `Wymagania/Requirements_EN.md` F-01; SC-1-04 and SC-1-05; ADR-0004; `docs/architecture/capabilities.md`.
  **Done 2026-10-05:** [PR #250](https://github.com/TanerCRB/StafffingCalculator/pull/250), merged as `38ad464`. Evidence: `frontend/src/features/projects/ProjectListScreen.test.tsx` (`archives an active project and keeps it listed as Archived`, `retains Active status when archive request is refused`, `does not send duplicate archive requests while one is pending`, `explains archive cannot be undone before submission`, and `checks the refreshed list when an archive request returns 5xx`); frontend suite 469/469, lint and build passed; push hook backend suite 1583/1583 and frontend suite 469/469; GitHub CI 2/2 passed. QA killed all five recorded mutations in the capability registry. This UI evidence does not re-prove backend authorization/persistence or individually exercise timeout, malformed-success, and network-failure reconciliation; see the implementation report on Issue #232.

- [x] **SC-1-21** — Edit target margin and overload assumptions (Issue #235).
  **Reserved 2026-10-05 after Gate 1 approval and merge of prerequisites SC-1-14, SC-1-22, and SC-1-23.**
  *Done when:* An authorized user can view `target_margin_percent` and `overload_threshold_percent` for the selected scenario with the resolved source (`scenario`, `project`, or `organization`); save or reset either draft scenario override after previewing the inherited value and source; and, with organization-level authorization, save either organization default. Project access alone cannot authorize organization-default writes. Changed organization defaults immediately affect existing drafts without a more-specific override; approved scenarios retain their frozen snapshot. Success, validation failure, authorization denial, and conflict remain distinguishable, and failed saves preserve unsaved input. The UI uses the merged SC-1-22 and SC-1-23 APIs and SC-1-14 permission mapping.
  **Out of scope (explicit):** Other assumptions and their APIs; writes to approved scenarios; project-level assumption writes; other organization settings or template management; authentication-provider/runtime changes.
  **Basis:** Issue #235; F-02; SC-1-10, SC-1-14, SC-1-22, and SC-1-23; ADR-0004, ADR-0005, ADR-0007, ADR-0009, ADR-0012, and ADR-0018; `Wymagania/prototyp/spec/UI_SPEC.md` UI-13, UI-24 and C-07.
  **Done 2026-10-06:** [PR #277](https://github.com/TanerCRB/StafffingCalculator/pull/277), merged as `31a469f`. Evidence: `frontend/src/features/projects/ScenarioAssumptionsSection.test.tsx` (K-01–K-06 and Draft→Approved snapshot refresh); `backend/tests/test_scenario_assumption_overrides.py::test_k_01_resolved_assumptions_require_project_scenario_and_conditional_organization_reads`; `backend/tests/test_scenario_assumption_reset_preview.py`; backend suite 1,631 passed with Ruff clean; frontend suite 532 passed across 32 files, ESLint, TypeScript, and Vite build passed. QA killed all three recorded mutations; results are in [Issue #235](https://github.com/TanerCRB/StafffingCalculator/issues/235#issuecomment-6017408113) and `docs/architecture/capabilities.md`. Guardian and Security Auditor passed with the accepted ADR-0018 runtime-auth/grant-provisioning reservation; Reviewer passed. This does not prove runtime OIDC authentication or production grant provisioning.

- [x] **SC-1-22** — Save organization defaults for margin and overload threshold (Issue #254).
  *Done when:* An authorized organization administrator can update either supported default through the documented API and read the persisted value after reload. Project access alone cannot authorize the write. Under ADR-0012, existing drafts without a more-specific override resolve the live organization default; explicit project and scenario overrides remain effective. Approved scenario snapshots remain unchanged. Concurrent stale writes follow ADR-0007 conflict behavior and return a distinguishable conflict response. Validation and authorization failures are explicit and leave persisted values unchanged.
  **Out of scope (explicit):** Scenario-level writes, project-level override writes, UI, additional assumptions, and authentication-provider/runtime changes.
  **Basis:** SC-1-10; ADR-0005; ADR-0007; ADR-0009 browser-write contract accepted for this task in Issue #235 Gate 1; ADR-0012; UI_SPEC UI-24; Issue #235.
  **Done 2026-10-05:** [PR #258](https://github.com/TanerCRB/StafffingCalculator/pull/258), merged as `0f5b62c`. Evidence: `backend/tests/test_organization_defaults_api.py` (K-01–K-06); full backend suite 1,595 passed and Ruff clean; pre-push frontend suite 473 passed. QA mutations removing either required PATCH permission failed K-01, and removing the `updated_at` predicate failed K-06; results are recorded in [Issue #254](https://github.com/TanerCRB/StafffingCalculator/issues/254#issuecomment-5997046781) and the capability registry below. Guardian and Reviewer passed. Security Auditor's runtime OIDC/grant-assignment reservation was accepted with owner TanerCRB and expiry 2026-12-31 ([Issue #254](https://github.com/TanerCRB/StafffingCalculator/issues/254#issuecomment-5997191823)). This evidence does not prove runtime OIDC/grant assignment or a genuine overlapping first-insert race.

- [x] **SC-1-23** — Save scenario draft assumption overrides (Issue #255).
  **Reserved 2026-10-05 after Gate 1 approval.** Implement field-level PATCH semantics: omitted fields remain unchanged; explicit `null` clears only that override and resumes project-then-organization inheritance. Preserve the approved-scenario data-layer guard, scoped scenario read/write permissions, validation, and stale-marker conflict behavior.
  **Done 2026-10-05:** [PR #261](https://github.com/TanerCRB/StafffingCalculator/pull/261), merged as `f73f1e6`. Evidence: `backend/tests/test_scenario_assumption_overrides.py` (K1-K6, including project-scope revocation); backend suite 1,602 passed, Ruff clean, push-hook frontend tests passed, and GitHub CI passed 2/2. QA mutation results: [Issue #255 QA report](https://github.com/TanerCRB/StafffingCalculator/issues/255#issuecomment-5999475878) and the capability mutation log below.

- [x] **SC-8-02** — Browse scenario versions and approval history (F-12, Issue #239).
  **Reserved 2026-10-05 after Gate 1 approval.**
  **Done 2026-10-06:** [PR #271](https://github.com/TanerCRB/StafffingCalculator/pull/271), merged as `4aa0cc6`. Evidence: `backend/tests/test_scenario_history.py` (K-01-K-05 and pagination), `backend/tests/test_scenario_approval_audit_log.py::test_approval_refuses_non_synthetic_actor_before_writing_audit_or_snapshot`, and `frontend/src/features/projects/ScenarioHistorySection.test.tsx`; backend suite 1,621 passed, Ruff clean, frontend suite 504 passed, lint/build clean, CI 2/2. Mutation results are recorded in the PR #271 description and the capability mutation log below.
  *Done when:* Mutation-checked synthetic-data tests prove that reading history requires `PROJECT_READ`, `SCENARIO_HISTORY_READ`, `CATALOG_READ`, and assigned-project scope with out-of-scope concealment; personnel-cost fields require both cost gates; approval metadata identifies its synthetic UUIDv4 actor as an unverified placeholder; and each scenario has its own status and inputs, with drafts distinguished from approved scenarios and no inferred lineage between them.
  **Out of scope (explicit):** Creating history events, linking copies into version families, changing immutable snapshots, and comparing scenarios — copies remain independent scenario records under the current model.
  **Basis:** Issue #239; `Wymagania/Requirements_EN.md` F-12 and F-13; SC-8-01; ADR-0004, ADR-0005, ADR-0017, ADR-0019; `docs/architecture/capabilities.md`.
- [x] **SC-8-03** — Approve ready scenarios from the UI (F-01, F-12, Issue #238).
  **Reserved 2026-10-06 after Gate 1 approval.**
  *Done when:* Frontend tests prove that a ready draft can be approved and remains Approved after reload; an incomplete draft has no actionable approval; refusal and concurrent-change responses are visible without displaying a false Approved state; and approved values appear frozen while further changes are made to a copy.
  **Out of scope (explicit):** Historical version/audit-log browsing (SC-8-02); new snapshot rules or changes to backend approval semantics; PM-role authorization, which remains deferred until the approval-role mapping is decided and implemented.
  **Basis:** Issue #238; `Wymagania/Requirements_EN.md` F-01 and F-12; SC-8-01/SC-8-02; ADR-0004, ADR-0005, ADR-0007, ADR-0009, ADR-0022; frontend approval endpoint and UI.
  **Done 2026-10-06:** [PR #275](https://github.com/TanerCRB/StafffingCalculator/pull/275), merged as `bf2c6ba`. Evidence: `ProjectListScreen.test.tsx::reloads an approved scenario from the server after a successful approval`; `ScenarioApprovalSection.test.tsx` covers readiness, refusal, concurrent change, unresolved connection outcome, and frozen approved values; `ScenarioAdditionalCostsSection.test.tsx::hides additional-cost writes for approved scenarios while retaining their values` contrasts the editable draft test. Frontend suite 513/513 across 31 files, lint/build clean, CI 2/2. QA: PROOF HOLDS; SC-8-03-M1..M5 all killed and restored. Invariant Guardian PASS; Reviewer PASS. The Security Auditor's reservation was accepted under the bounded development/test exception recorded [on Issue #238](https://github.com/TanerCRB/StafffingCalculator/issues/238#issuecomment-6016887027); production use remains outside that exception.
- [x] **SC-1-24** — Expose the scenario concurrency marker with resolved assumptions.
  **Identifier reserved 2026-10-05; scope approved at Gate 1 on 2026-10-05.**
  *Done when:* The authorized scenario assumptions read returns its current `updated_at` marker with both resolved values and sources; a client can use it for the first draft save/reset, and a stale marker still receives the existing conflict response without writing. Current authorization, project scope, and ADR-0012 resolution behavior remain unchanged.
  **Out of scope (explicit):** Frontend (Issue #235); changes to PATCH semantics or permissions; runtime authentication/provider changes; other assumptions; scenario-list token; migrations.
  **Basis:** F-02; SC-1-10 and SC-1-23; ADR-0005, ADR-0007, ADR-0009, ADR-0012, ADR-0022; Issues #235 and #255.
  **Done 2026-10-05:** [PR #268](https://github.com/TanerCRB/StafffingCalculator/pull/268), merged as `1a71883`. Evidence: `backend/tests/test_scenario_assumption_overrides.py::test_k_01_resolved_assumptions_marker_supports_first_override_and_rejects_stale_token` and `::test_k_01_marker_tracks_persisted_timestamp_without_changing_resolution`; backend suite 1,610 passed, Ruff passed, frontend pre-push suite 497 passed, and GitHub CI passed 2/2. The QA mutation removing response shaping failed both focused marker tests and was restored; see the capability mutation log. QA did not separately exercise reset using the marker returned by GET.


- [x] **SC-1-25** — Define and expose a draft-only reset preview for target margin and overload threshold, with an explicit authorization boundary for inherited values (Issue #270).
  **Reserved 2026-10-06 after Gate 1 approval.** Require `ORGANIZATION_DEFAULTS_READ` when either post-reset value resolves to an organization default; deny reset unless the full preview is authorized; conceal out-of-scope and nonexistent scenarios with the same refusal.
  *Done when:* An in-scope draft preview returns the post-reset target margin and overload threshold with their sources after skipping scenario overrides; approved and out-of-scope scenarios are refused; organization-default values remain permission-gated; preview does not write; reset requires the complete authorized preview and serializes source checks through its guarded write. Proof: `backend/tests/test_scenario_assumption_reset_preview.py` K-01 through K-09.
  **Out of scope (explicit):** Frontend integration (Issue #235), other assumptions, approved-scenario preview/reset, and binding the preview GET to a later reset PATCH with a freshness token. The separate-request freshness limitation is covered by the accepted exception recorded in [Issue #270](https://github.com/TanerCRB/StafffingCalculator/issues/270#issuecomment-6013362243).
  **Basis:** Issue #270; F-02; SC-1-23 and SC-1-24; ADR-0004, ADR-0005, ADR-0007, ADR-0012, ADR-0018.
  **Done 2026-10-06:** [PR #272](https://github.com/TanerCRB/StafffingCalculator/pull/272), merged as `0988bb9`. Evidence: `backend/tests/test_scenario_assumption_reset_preview.py` K-01 through K-09, including the K-08 positive permission contrast and PostgreSQL two-session K-09; backend suite 1,630 passed and Ruff clean; frontend suite 504 passed, lint and production build clean; [backend CI](https://github.com/TanerCRB/StafffingCalculator/actions/runs/37439848594) and [frontend CI](https://github.com/TanerCRB/StafffingCalculator/actions/runs/37439848587) passed. QA killed the organization-read and full-preview PATCH authorization mutations and the missing organization-default lock mutation; see the capability mutation log. Invariant Guardian and Reviewer passed. Security Auditor returned PASS WITH RESERVATIONS; the human accepted the freshness exception, recorded with owner, reason, and expiry in [Issue #270](https://github.com/TanerCRB/StafffingCalculator/issues/270#issuecomment-6013362243).

- [x] **SC-1-26** — Create a new scenario in an existing project (F-01, Issue #233).
  **Reserved 2026-10-06 after Gate 1 approval.**
    **Done 2026-10-07:** [PR #283](https://github.com/TanerCRB/StafffingCalculator/pull/283), merged as `1894a78`. Backend evidence: `backend/tests/test_scenario_creation.py` covers both required permissions and project scope with no-write refusals, persisted project ownership, per-scenario readiness, exact-name uniqueness and concurrent conflict, and archived-project refusal. Frontend evidence: `frontend/src/features/projects/ProjectListScreen.test.tsx` covers server-returned scenario data, named refusals with no phantom card, and unresolved timeout without automatic retry. QA mutation checks killed removal of either permission, project scope, readiness shaping, uniqueness mapping, archived guard, frontend optimistic insertion, and timeout retry. Full backend (1,642 passed), Ruff, frontend (549 passed), lint/build, and [CI](https://github.com/TanerCRB/StafffingCalculator/pull/283) passed. Mutation evidence is recorded below and in the gate reports for Issue #233.
  *Done when:* Backend and frontend tests prove that a caller with `SCENARIO_CREATE`, `PROJECT_READ`, and project access creates a named draft in an active project; the scenario is persisted under that project and reports its own missing-input state. Tests also prove that removing either permission separately or removing project access refuses creation without a write, that out-of-scope projects are indistinguishable from nonexistent ones, exact per-project name uniqueness, archived-project refusal, and no phantom UI card after refused or unresolved writes.
  **Out of scope (explicit):** Scenario duplication, approval, and editing individual inputs; each is a separate action or lifecycle behavior.
  **Basis:** Issue #233; `Wymagania/Requirements_EN.md` F-01/F-02; SC-1-05; ADR-0001, ADR-0003, ADR-0005, ADR-0009, and ADR-0010.

- [x] **SC-7-12** — Download scenario PDF and spreadsheet from the UI (F-11, Issue #241).
  **Done 2026-10-06:** [PR #280](https://github.com/TanerCRB/StafffingCalculator/pull/280), merged as `b571a95`. Evidence: `frontend/src/features/projects/ScenarioResults.test.tsx` covers both formats, scenario-specific filenames, refusal handling, empty responses, and format isolation (34 tests in the file passed; full suite 545 passed after syncing SC-7-11); frontend lint/build and [frontend CI](https://github.com/TanerCRB/StafffingCalculator/actions/runs/37520071815) and [backend CI](https://github.com/TanerCRB/StafffingCalculator/actions/runs/37520071705) passed. QA killed the format, scenario-ID, and empty-Blob mutations; see the SC-7-12 mutation rows in `docs/architecture/capabilities.md`. Existing server-side scope and personnel-cost gates remain evidenced by `backend/tests/test_scenario_results_access.py::test_k_03_a_scenario_outside_the_callers_scope_is_the_same_404_as_no_scenario` and `backend/tests/test_scenario_results_export.py::test_exports_omit_only_gated_personnel_cost_values_when_either_gate_is_closed`.
  *Done when:* Frontend tests prove that a PM with project access can download PDF and XLSX reports for the selected scenario with filenames identifying the report and scenario; a caller without project access cannot obtain either format, and a caller without personnel-cost permission receives neither personnel-cost fields in either report. Tests also prove that generation or authorization failures are communicated and do not produce a blank or corrupt successful download.
  **Out of scope (explicit):** Changing report contents or formats (separate product decision); other backends or delivery channels (separate user need); cross-scenario exports (separate comparison need).
  **Basis:** Issue #241; `Wymagania/Requirements_EN.md` F-11 and F-13; backend export in Issue #110 / SC-7-06; ADR-0005.
