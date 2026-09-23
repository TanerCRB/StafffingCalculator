# ADR-0012 — Założenia konfigurowalne: łańcuch organizacja → projekt → scenariusz i źródło wartości

**Status:** Accepted

> Dokument powstał w odpowiedzi na pytania bramki 1 architekta (P-A..P-D) i produkt ownera
> (Q-1..Q-5, G-1, G-2) dla SC-1-10 (Issue #4). Rozstrzygnięcie zapadło na bramce 1 (2026-09-23) —
> wszystkie punkty "DO DECYZJI" domknięte w treści poniżej.

## Kontekst

F-02: "The system shall provide organization-level defaults that users can override for a project
or scenario", "shall identify the source of each inherited or overridden value", "Changes to
default settings shall not automatically modify saved calculations." AC-04. Jedyny dotychczasowy
opis źródła wartości to `rate_source` w ADR-0006 — dwa poziomy (organizacja, scenariusz), tylko dla
kursów walut, tabela nieistniejąca. SC-1-10 (Issue #4) jest pierwszym zadaniem budującym łańcuch, na
dwóch polach: marża docelowa (`scenarios.target_margin_percent`, kolumna istniała już wcześniej) i
próg przeciążenia alokacji (nowe pole, konsumenta dziś nie ma — odłożone z SC-3-01). Wzorzec
ustanowiony tu obowiązuje każde kolejne założenie F-02, nie tylko te dwa.

Żaden przyjęty ADR nie opisuje wprost, jak ma wyglądać taki trzypoziomowy łańcuch. ADR-0004 ma dwie
osobne taksonomie (grupy pól Projektu; grupy tabel-dzieci scenariusza), a SC-1-10 dotyka obu naraz.
Istniejące migawki przechowują źródło jako identyfikator wiersza źródłowego, nigdy jako wynik
rozstrzygnięcia poziomu (org/projekt/scenariusz) — ten ADR ustanawia, że tak zostaje.

## Decyzja

1. **Trzy poziomy, jeden kierunek.** Wartość rozstrzygana jest: nadpisanie scenariusza, jeśli
   istnieje; w przeciwnym razie nadpisanie projektu; w przeciwnym razie wartość domyślna
   organizacji; w przeciwnym razie brak wartości. `NULL` na poziomie scenariusza i projektu znaczy
   "dziedzicz", nie "zero" — `0` jest wartością rozstrzygniętą, nie stanem pustym (mutacja
   "sklejanie po prawdziwości", `scenario or project or org`, musi zabić test — gubi `0`). Brak na
   wszystkich trzech poziomach jest nazwanym stanem "brak danej" w rozumieniu F-01
   (`scenario_readiness`), nigdy `0` ani wyjątkiem.

2. **Źródło nie jest przechowywane jako osobna kolumna — wynika z tego, który poziom rozstrzygnął.**
   Słownik: `scenario | project | organization`. Ten słownik jest **drugim, niezależnym** od
   `rate_source` (ADR-0006, `organization | scenario | manual`, bez poziomu projektu) — rozjazd
   nazwany świadomie, nie scalony. Ujednolicenie, jeśli w ogóle potrzebne, jest osobną decyzją przy
   pierwszym zadaniu budującym `exchange_rates`.

3. **Wartości domyślne organizacji: osobna tabela `organization_defaults`, bez kolumny zasięgu**
   (ADR-0001, expand). Dokładnie jeden wiersz egzekwowany przez bazę (`CHECK id = 1`), nie
   konwencją aplikacyjną. Migracja **nie zasiewa** wiersza (P-D) — brak wiersza organizacji jest
   tym samym nazwanym stanem "brak danej", co brak na pozostałych dwóch poziomach; istniejące testy
   gotowości scenariusza (`test_project_list.py:142,207,239`) zostają prawdziwe bez zmiany asercji.

4. **Bez przedziału obowiązywania** dla marży i progu przeciążenia — nie są stawką ani kosztem
   (ADR-0008 nie ma zastosowania, F-02 traktuje je odrębnie od "changes in rates and costs over
   time"). Założenie z przedziałem obowiązywania, gdy powstanie, wymaga własnego wpisu.

5. **Przynależność do grup ADR-0004:** wartość domyślna organizacji — dziedziczona, wchodzi do
   migawki jako surowa wartość (pkt 6); nadpisanie projektu — pole Projektu grupy 2
   (`FROZEN_BY_APPROVED_SCENARIO`), dołączone do istniejącego `PATCH /projects`; nadpisanie
   scenariusza — dana własna scenariusza (dziś fixture-only, brak ścieżki zapisu, jak SC-3-01/
   SC-3-02 — poza zakresem SC-1-10).

6. **Migawka zamraża surową wartość domyślną organizacji, nie wynik łańcucha.** Piąte CTE w
   `_snapshot_statement` (wzorzec S-01, SC-3-03) kopiuje wiersz `organization_defaults` obowiązujący
   w chwili zatwierdzenia do `approved_snapshot_organization_defaults` — bez pola źródła, bez FK do
   źródła. Zgodne z precedensem SC-3-03 pkt 7c/ADR-0004 SC-3-03 pkt 7b: obecność albo brak wiersza w
   migawce **jest** faktem (analogia do stanu D w `_copy_absence_types` — brak wiersza organizacji
   przy zatwierdzeniu zostaje brakiem na zawsze, nawet gdy wartość domyślna pojawi się później).
   Odrzucone: zamrażanie wyniku łańcucha (wartość+źródło) jako nowej klasy danych migawki — byłaby
   to druga kopia dla nadpisania scenariusza, czego SC-3-01 pkt 2 odrzuca wprost jako "pierwsze
   miejsce, w którym dwie kopie mogłyby się rozjechać".

7. **SC-1-10 jest pierwszym czytelnikiem migawki w repozytorium.** Dla zatwierdzonego scenariusza
   wartość i źródło rozstrzygane są z zamrożonego wejścia organizacji (migawka) i z **żywego**
   poziomu projektu (poziom projektu nie jest zamrażany — chroniony wyłącznie przez strażnik zapisu
   grupy 2, pkt 9, nie przez migawkę; bezpośredni zapis SQL z pominięciem `update_project` może więc
   przesunąć wynik — ryzyko nazwane, przyjęte razem z tym punktem). Dla scenariusza w stanie draft
   (`status != approved`) rozstrzyganie czyta żywe wartości na wszystkich trzech poziomach.

8. **"Zapisana kalkulacja" (F-02) znaczy "zatwierdzona" (AC-04), nie "jakikolwiek draft".** Draft
   podąża za żywą wartością domyślną organizacji — świadomie zaakceptowane, nazwane w regule 9
   `agents/invariant-guardian.md` (aneks do tej reguły, patrz Konsekwencje). Wartość dziedziczona z
   organizacji liczy się jako "dostarczona" dla gotowości scenariusza do zatwierdzenia (F-01) —
   również dla scenariusza już zatwierdzonego, gdzie gotowość liczona jest z łańcucha zamrożonego,
   nie z żywych kolumn.

9. **Zapis dowolnego pola grupy 2 Projektu (istniejące `reporting_currency`/`delivery_period_*` ORAZ
   nowe nadpisania) jest serializowany z zatwierdzeniem scenariusza tego projektu, jednym
   mechanizmem dla wszystkich pól naraz** (wzorem `scenario_guard.py`). Naprawia lukę istniejącą od
   wcześniejszych zadań: `approve_scenario` blokował dotąd wyłącznie wiersz `scenarios`, strażnik
   pól grupy 2 w `project_writes.py` był gołym `EXISTS(...)` bez blokady — pod `READ COMMITTED`
   zapis mógł zacommitować się między odczytem statusu a zatwierdzeniem równoległej transakcji.
   Mechanizm musi być dowiedziony testem wyścigu dwóch połączeń, w obu kolejnościach startu — dowód
   i stan wdrożenia żyją wyłącznie w `docs/architecture/capabilities.md`, nie w tym dokumencie.

## Konsekwencje

- Draft może zmienić wynik bez akcji autora, gdy zmieni się wartość domyślna organizacji (pkt 8) —
  ta sama klasa skutku, którą ADR-0004 już akceptuje dla innych pól dziedziczonych w draftach.
- Nowe założenie F-02 dochodzące do łańcucha wymaga przypisania trzech poziomów do grup ADR-0004 w
  chwili dodania — pole bez przypisania wpada domyślnie do grupy 1 pól Projektu (aneks 2026-09-18),
  czyli cicha regresja AC-10. Ten ADR nie zamyka tej klasy błędu, tylko ją nazywa dla każdego
  kolejnego pola.
- Poziom projektu jest jedynym poziomem łańcucha nie chronionym migawką (pkt 7) — spójność
  zatwierdzonej kalkulacji wobec niego opiera się wyłącznie na strażniku zapisu (pkt 9), nie na
  zamrożeniu. Nazwane ryzyko, nie zamknięte tym ADR: bezpośredni SQL z pominięciem `update_project`
  je omija.
- Dwa niezależne słowniki źródeł (`rate_source` ADR-0006, ten ADR) współistnieją w repozytorium do
  czasu, aż któreś zadanie zdecyduje inaczej — nazwane wprost, żeby nie było odkryte po fakcie.
- Reguła 9 `agents/invariant-guardian.md` wymaga aneksu doprecyzowującego "already-saved" do
  "already-approved", z odesłaniem do pkt 8 tej decyzji i do zdania o drafcie podążającym za żywą
  wartością. Stan synchronizacji z `.claude/agents/` (`node tools/sync-agents.mjs`) nie jest
  śledzony w tym dokumencie — patrz `docs/architecture/capabilities.md`.

## Rozważane alternatywy

- **Materializacja wartości domyślnej w scenariuszu przy utworzeniu** — źródło stałoby się
  nieodróżnialne od nadpisania bez dodatkowej kolumny; nowy mechanizm, którego ADR-0004 nie opisuje.
  Odrzucone.
- **Dwa poziomy, bez poziomu projektu** — F-02 nazywa poziom projektu wprost; późniejsze dodanie
  zmieniłoby naraz schemat, słownik źródeł i kształt migawki. Odrzucone.
- **Jedna generyczna tabela klucz–wartość (`jsonb`) dla wszystkich założeń F-02** — odrzucone na tej
  samej podstawie co generyczna migawka (ADR-0004 aneks SC-3-02 pkt 3a; ADR-0002/NF-01, brak
  jednolitego typu i precyzji dla różnych założeń).
- **Zamrażanie wyniku łańcucha (wartość+źródło) zamiast surowej wartości organizacji** — odrzucone,
  patrz pkt 6 (nowa klasa danych migawki, druga kopia dla nadpisania scenariusza).
- **Nazwać wyścig projekt↔zatwierdzenie (P-C) jako ryzyko zamiast go naprawiać** — rozważone i
  odrzucone na bramce 1: dotyczy już pól istniejących w produkcji (`reporting_currency`,
  `delivery_period_*`), nie tylko nowych pól tego zadania.

## Powiązane wymagania

F-01, F-02, F-04 (próg przeciążenia — konsument odłożony), F-12, AC-04, AC-10; ADR-0001 (trwałość
danych, tabela singletonowa bez zasięgu), ADR-0004 (wersjonowanie kalkulacji, obie taksonomie grup),
ADR-0005 (model dostępu — tabela organizacyjna bez zasięgu, aneks 2026-09-19 pkt 1), ADR-0006
(waluty i kursy — rozjazd słownika źródeł nazwany, nie scalony), ADR-0007 (współbieżna edycja —
mechanizm P-C to nowa para blokad w `scenario_guard.py`), ADR-0008 (przedziały obowiązywania — nie
dotyczy marży/progu).
