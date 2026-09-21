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

*(further rows are added by the Product Owner role, one per task, following gate 1)*
