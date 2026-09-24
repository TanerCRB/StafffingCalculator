# ADR-0001 — Trwałość danych backendu

**Status:** Accepted

## Kontekst

Backend (`backend/`, FastAPI) na razie nie ma warstwy trwałości — patrz `backend/README.md` i
zadanie `SC-1-01` w `docs/PLAN.md`, pierwsze zadanie wymagające zapisu Projektu. Wymagania
wymuszające konkretny wybór:

- NF-01: obliczenia pieniężne muszą używać arytmetyki dziesiętnej (`Decimal`) i jawnych reguł
  zaokrąglania — silnik bazy musi to wspierać natywnie, bez utraty precyzji przy zapisie/odczycie.
- NF-04: szyfrowanie danych w spoczynku i w tranzycie, autoryzacja egzekwowana po stronie serwera.
- NF-03: scenariusz z 200 pozycjami staffingowymi i 36 miesiącami, 95% przeliczeń w 2 sekundy.
- F-12: wersje kalkulacji i historia zmian muszą być trwałe i odtwarzalne.
- F-13: dostęp ograniczany per projekt, oddzielne uprawnienie do kosztów osobowych — wymaga
  egzekwowania na poziomie zapytań, nie tylko UI.

## Decyzja

PostgreSQL jako jedyny silnik bazy danych, SQLAlchemy 2.x jako ORM, Alembic do migracji.
Migracje zawsze wstecznie kompatybilne: expand → deploy kodu → contract (patrz
`agents/invariant-guardian.md`, reguła 13). Kolumny pieniężne jako `NUMERIC` z jawną precyzją
(nie `FLOAT`/`DOUBLE`) — mapowane na `Decimal` w Pythonie, nigdy `float`.

## Konsekwencje

- Testy integracyjne mechanizmów izolacji/unikalności/ograniczeń działają na prawdziwym
  PostgreSQL w kontenerze — atrapa (SQLite, mock) nie dowodzi niczego o zachowaniu tych
  mechanizmów (patrz `agents/developer-backend.md`).
- Każda zmiana schematu żyje wyłącznie w pliku migracji (reguła Strażnika Niezmienników nr 14).
- Szyfrowanie w spoczynku (NF-04) to ustawienie środowiska wdrożeniowego (np. szyfrowanie
  dysku/instancji bazy), nie mechanizm aplikacji — poza zakresem tego ADR, wymaga osobnej decyzji
  gdy dojdzie zadanie dotyczące wdrożenia.

## Rozważane alternatywy

- **SQLite** — odrzucone: brak realnego wsparcia dla współbieżnych zapisów i mechanizmów
  izolacji wymaganych przez F-13 na docelową skalę.
- **MongoDB / dokumentowa** — odrzucone: model danych (F-06) jest silnie relacyjny (projekt →
  scenariusz → pozycje staffingowe → reguły komercyjne → koszty), a NF-01 wymaga dokładnej
  arytmetyki dziesiętnej, którą relacyjne `NUMERIC` daje bez dodatkowej pracy.

## Powiązane wymagania

NF-01, NF-03, NF-04, F-12, F-13

## Aneksy

### 2026-09-18 — mechanizm izolacji per projekt (F-13)

Impact map dla Issue #3 (SC-1-05/06) zidentyfikował lukę: ten ADR wymaga egzekwowania granicy
projektu "na poziomie zapytań, nie tylko UI", ale nie rozstrzygał **jak**. Decyzja: **wariant B —
jedna współdzielona funkcja warstwy dostępu do danych** stosująca filtr `project_access`
(ADR-0005), bez PostgreSQL Row Level Security. Uzasadnienie: to narzędzie wewnętrzne z kontrolą
dostępu per-projekt w obrębie jednej organizacji (F-13), nie wielodzierżawowy SaaS z nieufnymi
najemcami — koszt dyscypliny RLS/`SET LOCAL` na pulę połączeń nie jest tu proporcjonalny do
ryzyka. Warunek: funkcja musi być **jedyną** ścieżką odczytu projektów — używana identycznie przez
odczyt interaktywny, eksport (F-11) i każdy przyszły interfejs serwer-serwer (reguła 6 w
`agents/invariant-guardian.md`). Zapytanie z pominięciem tej funkcji jest naruszeniem tego ADR.

### 2026-09-19 — tabela bez predykatu dostępu nie dostaje funkcji-strażnika (SC-2-01)

Aneks wyżej stawia warunek: jedna funkcja musi być jedyną ścieżką odczytu *projektów*. Katalog
wymiarów roli i stawek domyślnych (SC-2-01, F-03) jest pierwszą tabelą, do której ten warunek się
nie stosuje, bo nie ma czego filtrować — wiersz katalogu nie należy do projektu (ADR-0005, aneks
2026-09-19 "pierwszy zbiór danych bez zasięgu projektu").

**Rozstrzygnięcie:** katalog **nie** dostaje modułu `catalog_reads.py` analogicznego do
`project_reads.py`. Odczyt to zwykły `select()` w warstwie danych. Funkcja-strażnik istnieje po to,
by predykatu zasięgu nie dało się pominąć przez zapomnienie; tam, gdzie predykatu nie ma, opakowanie
nie dodaje gwarancji, a nazwa symetryczna do `project_reads` sugerowałaby czytelnikowi filtr, którego
nie ma — mylenie w kierunku fałszywego poczucia bezpieczeństwa.

Granice tego wyjątku, żeby nie stał się regułą:

1. **Kryterium jest strukturalne, nie ocenne:** tabela nie ma żadnej kolumny wiążącej wiersz z
   projektem, użytkownikiem, jednostką biznesową ani najemcą, i żaden endpoint nie zawęża jej po
   tożsamości wołającego. "Uznaliśmy te dane za publiczne wewnątrz organizacji" nie jest kryterium.
2. **Moment wygaśnięcia wyjątku:** pierwszy predykat per wołający na tej tabeli (katalog per
   jednostka biznesowa, wersje widoczne tylko dla administratorów, wielodzierżawowość) czyni
   rozsypane po endpointach `select()` tym samym ryzykiem pominięcia, przed którym broni
   `project_reads` — i wymaga wtedy własnej funkcji-strażnika oraz własnego, datowanego wpisu tutaj.
3. **Co NIE przenosi się do warstwy odczytu:** bramka stawki kosztowej. Zostaje w warstwie
   kształtowania odpowiedzi (ADR-0005) — drugie miejsce decydujące o widoczności kosztów jest
   dokładnie tym, co konsolidowało SC-1-08.

### 2026-09-19 — pierwsza tabela z zasięgiem dziedziczonym przez rodzica (SC-3-01)

Aneks wyżej zwalnia z funkcji-strażnika tabelę bez predykatu zasięgu, a jego warunek wygaśnięcia
jest strukturalny: "tabela nie ma żadnej kolumny wiążącej wiersz z projektem". Pozycja obsady
(F-04, SC-3-01) ma taką kolumnę pośrednio — `scenario_id → scenarios.project_id` — więc wyjątek jej
nie obejmuje i obowiązuje warunek z aneksu 2026-09-18: predykat zasięgu musi być w tej samej
ścieżce, która pobiera wiersze, a nie w kodzie wołającego zbudowanym od nowa.

**Rozstrzygnięcie:** odczyt i zapis pozycji obsady **nie** dostają własnej, niezależnej funkcji
zasięgu. Adres niesie oba identyfikatory (`/projects/{project_id}/scenarios/{scenario_id}/...`);
zasięg pochodzi wyłącznie z `project_for_caller(session, caller, project_id)` — ten sam punkt
wejścia co dla projektu — a przynależność scenariusza do projektu sprawdzana jest względem już
wczytanej kolekcji `Project.scenarios` (eager load w `project_for_caller`), nie osobnym
zapytaniem. Scenariusz spoza tej kolekcji (inny projekt, albo nieistniejący) daje ten sam `404` co
projekt spoza zasięgu — jedna funkcja, nie dwie komponujące się na jednym zapytaniu.

**Czego to nie zmienia:** bramka kosztów osobowych zostaje w warstwie kształtowania odpowiedzi, nie
przenosi się do warstwy odczytu (ADR-0005, aneks 2026-09-19). Nieodróżnialność odmowy od
nieistnienia zostaje własnością `project_for_caller`: scenariusz z innego projektu i scenariusz
nieistniejący wracają jako ten sam brak wyniku.

### 2026-09-21 — kolumna kontrahenta nie jest kolumną zasięgu; przebudowa ograniczenia `EXCLUDE` (SC-2-03)

Aneks z 2026-09-19 zwalnia katalog z funkcji-strażnika kryterium strukturalnym: "tabela nie ma
żadnej kolumny wiążącej wiersz z projektem, użytkownikiem, jednostką biznesową ani najemcą, i żaden
endpoint nie zawęża jej po tożsamości wołającego". SC-2-03 dodaje do wiersza stawki kolumnę
wskazującą podmiot gospodarczy (`vendor_id`). Czy to już jest "kolumna wiążąca" w rozumieniu tamtego
zdania — tekst tego nie rozstrzygał, a milczące odczytanie go w którąkolwiek stronę byłoby
dokładnie tym dryfem, przed którym broni zasada aneksów. Stąd ten wpis.

1. **Rozstrzygnięcie: `vendor_id` jest atrybutem *kontrahenta* stawki, nie nosicielem zasięgu.**
   Wymienione w tamtym zdaniu podmioty — projekt, użytkownik, jednostka biznesowa, najemca — to
   podmioty, w imieniu których działa wołający. Poddostawca jest drugą stroną umowy, nie stroną
   wołającą. Wyjątek z 2026-09-19 obowiązuje więc dalej: katalog nadal nie dostaje modułu
   `catalog_reads.py`, odczyt zostaje zwykłym `select()`.
2. **Warunek wygaśnięcia bez zmian i wprost przypomniany.** Drugi punkt tamtego aneksu mówi o
   pierwszym predykacie per wołający na tej tabeli. Gdyby kiedykolwiek powstało zawężanie
   widoczności stawek po poddostawcy (np. "cennik poddostawcy X widzą tylko osoby pracujące z X"),
   wyjątek wygasa w tym momencie — wymaga własnej funkcji-strażnika oraz własnego, datowanego wpisu
   tutaj. Ten aneks takiego zawężania **nie** wprowadza; zakres widoczności rozstrzyga ADR-0005,
   aneks z tą samą datą (zero nowego uprawnienia, decyzja biznesowa Issue #46).
3. **Przebudowa ograniczenia `EXCLUDE` w jednej migracji — odstępstwo od expand → deploy →
   contract, nazwane i ograniczone.** Zdjęcie czterokolumnowego ograniczenia i założenie
   pięciokolumnowego w jednym kroku tej reguły nie spełnia. Podstawa odstępstwa jest faktograficzna,
   nie wygodnościowa: nie istnieje żadne wdrożone środowisko — środowisko docelowe nie zostało
   wybrane (open decision #5, `backend/README.md`, ADR-0008 pkt 5 i 7), a aplikacja odmawia startu
   poza `development`/`test`. Jedyne bazy, których ta migracja dotyka, to efemeryczne kontenery
   testowe i lokalne bazy deweloperskie. Wariant literalny (oba ograniczenia obok siebie, stare
   zdejmowane później) sprawdzony i **niewykonalny, nie tylko droższy**: stare ograniczenie
   odrzucałoby wiersze, o które chodzi w zadaniu.
   **Warunek zamknięcia:** odstępstwo wygasa z chwilą wyboru i uruchomienia pierwszego środowiska
   trwałego. Pierwsza migracja po tym momencie nie może się na ten punkt powołać.
4. **Kształt kolumny zostawia poprawnym `INSERT` wykonywany przez kod sprzed tej migracji** —
   `vendor_id` nullable, bez wymogu `server_default`.
5. **Czego ten aneks nie zmienia.** Bramka stawki kosztowej zostaje w warstwie kształtowania
   odpowiedzi i nie przenosi się do warstwy odczytu (punkt 3 aneksu z 2026-09-19) — także dla
   wiersza z poddostawcą.

### 2026-09-22 — kalendarz bez funkcji-strażnika, nieobecność z zasięgiem dziedziczonym (SC-3-02)

1. **Kalendarz roboczy, jego dni i słownik typów nieobecności nie dostają modułu-strażnika.**
   Kryterium strukturalne z aneksu 2026-09-19 spełnione (ADR-0005, aneks z tą samą datą pkt 1) —
   odczyt i zapis to zwykły `select()`/`insert()` w warstwie danych.
2. **`catalog_locations.calendar_id` nie wygasza wyjątku.** Wskazuje inny wiersz organizacyjny, nie
   podmiot wołający — ten sam wzorzec co `vendor_id` (aneks 2026-09-21 pkt 1). Warunek wygaśnięcia
   (pierwszy predykat per wołający) pozostaje niespełniony.
3. **Instancja nieobecności dostaje zasięg wyłącznie przez ponowne użycie `project_for_caller`,
   bez własnej funkcji.** Ten sam wzorzec co pozycja obsady (aneks 2026-09-19 "pierwsza tabela z
   zasięgiem dziedziczonym przez rodzica"): adres niesie `project_id`/`scenario_id`, przynależność
   `position_id → staffing_position.scenario_id` sprawdzana względem już wczytanej kolekcji, `404`
   dla pozycji spoza zasięgu tożsame ze `404` dla pozycji nieistniejącej.
4. **Tabele migawkowe (`approved_snapshot_*`) dziedziczą zasięg scenariusza** i nie dostają własnej
   funkcji odczytu — czytane wyłącznie przez tę samą ścieżkę co reszta danych scenariusza, mimo że
   treść pochodzi z tabeli bez zasięgu (kalendarz, słownik typów).

### 2026-09-24 — porównanie scenariuszy jako zbiór, nie pojedynczy zasób (SC-6-02, bramka 1)

1. **Żądanie nazywa zbiór `scenario_id` jako powtórzony query param** (`GET
   /projects/{project_id}/scenarios/compare?scenario_id=...&scenario_id=...`), nie ciało żądania —
   ten sam wzorzec co `GET /catalog/rates` (odczyt, bez ciała). Wołający wybiera podzbiór, endpoint
   nie zwraca automatycznie wszystkich scenariuszy projektu.
2. **Sprawdzenie zasięgu N-krotne, ten sam mechanizm co dla jednego zasobu, nie nowy.**
   Przynależność każdego `scenario_id` sprawdzana względem już wczytanej kolekcji
   `Project.scenarios` (aneks 2026-09-19, SC-3-01) — `scenario_id` z INNEGO projektu niż ten w
   ścieżce nie jest nawet nazywalny w zakresie tego żądania (ścieżka ustala `project_id` z góry),
   więc "po cichu zestawione międzyprojektowo" jest strukturalnie niemożliwe, nie tylko sprawdzane.
3. **Semantyka częściowego niepowodzenia: all-or-nothing, nie partial-success.** Jeden `scenario_id`
   spoza zasięgu/nieistniejący wśród kilku → CAŁA odpowiedź `404`, nieodróżnialna od żądania z tym
   samym id jako jedynym argumentem. Bez nowego kształtu odpowiedzi (marker per pozycja) — decyzja
   bramki 1, nie tylko brak czasu na zaprojektowanie alternatywy.
4. **Wyścig przy N złożonych odczytach: całościowy `409`, nie per-scenariusz marker.** Jeśli
   KTÓRYKOLWIEK z N wywołań `scenario_results_for_caller` rzuci `ScenarioResultsRaceDetected`, cała
   odpowiedź porównania odmawia `409` — spójne z pkt 1 aneksu SC-7-01 (ADR-0005/ADR-0004: "nigdy nie
   miesza dwóch momentów tego samego scenariusza"), rozszerzone na "nigdy nie miesza wyniku jednego
   rasującego scenariusza z resztą w tej samej odpowiedzi 200". Nazwany kompromis: jeden zajęty
   scenariusz blokuje porównanie pozostałych N-1, które nie rasowały — zaakceptowane, nie naprawiane
   w tym zadaniu.
5. **"Obsada" (F-09 pkt 2) POZA zakresem metryk liczbowych tego zadania.** Żadna wartość skalarna
   (peak headcount / suma osobo-miesięcy / FTE) nie istnieje dziś w kodzie ani nie ma jednoznacznej
   definicji w `Requirements_EN.md` — F-10 ("Planned hours and FTE") to osobne, niezbudowane
   zadanie, F-11 sugeruje, że obsada w naturze jest serią (staffing timeline), nie pojedynczą
   liczbą jak pozostałe cztery metryki. SC-6-02 porównuje wyłącznie
   `revenue`/`personnel_cost`/`additional_cost`/`included_cost`/`profit`/`margin`/`markup` (kształt
   `GET .../results`, SC-7-01) — obsada jako metryka porównania odłożona do zadania po zbudowaniu
   F-10.
