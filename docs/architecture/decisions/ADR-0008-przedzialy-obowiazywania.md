# ADR-0008 — Przedziały obowiązywania i ich egzekwowanie w bazie

**Status:** Accepted

## Kontekst

F-03: "Rates shall support effective date ranges." Invariant-guardian, reguła 13: "A rate/cost
lookup resolves by effective-date range; two overlapping active rows for the same subject are
rejected at write time, never silently resolved by »latest row wins« at read time." Wzorzec jest
już *zadecydowany* dwukrotnie w tym repo, ani razu *zbudowany*: `ADR-0006-waluty-i-kursy.md`
(Accepted, dla `exchange_rates`, kolumny `valid_from`/`valid_to`) i `ADR-0003-model-modeli-komercyjnych.md`
(**Draft — pending approval**, dla `commercial_terms`, kolumny `effective_from`/`effective_to`,
jedyne miejsce, gdzie pada nazwa `btree_gist`). Decyzja w statusie Draft nie jest podstawą dla
implementacji. SC-2-01 (katalog wymiarów roli i stawek domyślnych, F-03) jest pierwszym zadaniem,
które faktycznie tworzy taką tabelę — a więc pierwszym użyciem wzorca, i tym, które go ustanawia
dla trzech tabel naraz (ten katalog, `exchange_rates`, `commercial_terms`), nie tylko dla siebie.

Zero wystąpień `EXCLUDE`/`btree_gist`/`daterange`/`CREATE EXTENSION` w kodzie, migracjach, testach
i CI (sprawdzone repo-wide). Ten ADR domyka fundament, zanim SC-2-01 na nim stanie.

## Decyzja

1. **Nazwa kolumn: `effective_from`/`effective_to`**, nie `valid_from`/`valid_to`. F-03 mówi
   dosłownie "effective date ranges" — para jest zakotwiczona w wymaganiach. `ADR-0006` (Accepted)
   dostaje jednozdaniowy aneks ujednolicający nazwę; jest to poprawka nazewnicza, nie zmiana
   mechanizmu — istniejący kod `exchange_rates` (jeśli powstał) migruje przy pierwszej okazji, nie
   wstecznie w tym zadaniu.
2. **Reprezentacja: kolumna generowana `valid_period daterange GENERATED ALWAYS AS
   (daterange(effective_from, effective_to + 1, '[)')) STORED`**, obok kolumn `effective_from DATE
   NOT NULL` i `effective_to DATE NULL` (`NULL` = bezterminowa, brak wartownika typu
   `9999-12-31`). `+ 1` jest tu konieczne — `effective_to` jest włączające (punkt 3), a `daterange`
   przyjmuje granicę górną wyłączającą; bez tego przesunięcia ostatni dzień okna nie byłby przez
   nie objęty. Kolumna generowana, nie samo wyrażenie powtórzone w każdym zapytaniu: największym
   ryzykiem tego wzorca nie jest ograniczenie samo, a wyszukiwanie stawki używające innych granic
   niż ograniczenie — błąd niewidoczny w teście na dacie w środku okresu. Jedna kolumna, czytana i
   przez `EXCLUDE`, i przez wyszukiwanie (`valid_period @> :date`, z indeksem gist), likwiduje tę
   klasę błędu przez konstrukcję.
3. **Granica: półotwarta `[)`**, kanoniczna forma PostgreSQL dla typu dyskretnego (`DATE`) —
   `daterange` i tak kanonizuje każdy zapis do tej postaci. **`effective_to` jest włączające w
   API i w danych wejściowych** ("obowiązuje do 31.12" znaczy dokładnie to), z konwersją
   `effective_to + 1 dzień` w **dokładnie jednym miejscu**: konstrukcji `valid_period`. Nie wolno
   duplikować tej konwersji w kodzie wyszukującym — dlatego kolumna generowana, nie wyrażenie.
4. **`EXCLUDE USING gist`, kluczowany na pełnej krotce wymiarów biznesowych + `valid_period WITH
   &&`.** Waluta **nie** wchodzi do klucza (Q7) — krotka wymiarów ma w danym momencie co najwyżej
   jedną stawkę, w jednej walucie; przeliczenie idzie przez `exchange_rates` (ADR-0006), nie przez
   równoległe wiersze w kilku walutach. `btree_gist` jest wymagane nie dla samego zakresu dat (gist
   obsługuje `&&` na `daterange` natywnie), a dla operatorów `=` na kolumnach identyfikatorów
   wewnątrz tego samego indeksu gist.
5. **`CREATE EXTENSION IF NOT EXISTS btree_gist`** w migracji Alembica (faza expand), **plus**
   jawny wpis w `backend/README.md` nazywający uprawnienie roli bazy wymagane do tej operacji
   (docelowe środowisko nie jest jeszcze wybrane — open decision #5 w wymaganiach; rola z prawem
   `CREATE` na bazie jest zwykle wystarczająca, bo `btree_gist` jest `trusted` od PostgreSQL 13,
   ale to nie jest potwierdzone na środowisku, którego nie ma). Downgrade migracji **nie** usuwa
   rozszerzenia — jest obiektem bazy, nie jednej tabeli, i inne tabele (`exchange_rates`,
   `commercial_terms`) go dziedziczą.
6. **Precyzja stawki: `NUMERIC` ze skalą większą niż jednostka minor waluty** (np. `NUMERIC(14,4)`
   dla stawek, nie `NUMERIC(12,2)` jak `round_money`). Stawka jest danym wejściowym, nie wynikiem
   zaokrąglenia — zaokrąglenie przy zapisie cicho zmieniłoby wprowadzoną wartość. Zaokrąglanie do
   jednostki waluty zostaje regułą konsumenta (`app.core.money.round_money`), egzekwowaną w
   momencie użycia stawki w kalkulacji, nie w momencie jej zapisania do katalogu.
7. **Dowód, którego CI nie daje.** Testcontainers uruchamia obraz z rolą nadrzędną — zielony test
   na `EXCLUDE` w CI dowodzi, że migracja i mechanizm są poprawne, **nie** dowodzi, że rola
   aplikacji na środowisku docelowym może wykonać `CREATE EXTENSION`. To ograniczenie dowodu
   zapisane jest tu wprost, żeby trafiło do rejestru możliwości jako "czego to nie dowodzi", a nie
   zostało odkryte przy pierwszym wdrożeniu.

## Konsekwencje

- Trzy tabele (katalog stawek SC-2-01, `exchange_rates` ADR-0006, `commercial_terms` ADR-0003)
  współdzielą jeden wzorzec kolumn i jedno ograniczenie integralności — zmiana kształtu wzorca po
  fakcie jest migracją fazy contract dotykającą wszystkich trzech, nie jednej.
- Kolumna generowana jest tylko-do-odczytu z perspektywy ORM — SQLAlchemy musi zadeklarować ją
  jako `Computed(...)`, nigdy jako pole przypisywane przy insercie/update.
- Reguła 13 Strażnika Niezmienników dostaje pierwszą implementację; jej druga połowa ("rejected at
  write time") jest dokładnie tym, co robi `EXCLUDE`, i zniknie bez śladu, jeśli rozszerzenie nie
  powstanie na docelowej bazie — stąd punkt 7.
- Naruszenie `EXCLUDE` jest błędem bazy, którego domyślny komunikat naturalnie zawiera wartości
  wiersza (w tym stawkę kosztową, NF-11) — każde zadanie korzystające z tego wzorca musi owinąć
  ten błąd tym samym mechanizmem co `ProjectWriteFailed`/`_describe_without_values`
  (`backend/app/data/project_writes.py`), nie przepuszczać go dalej.

## Rozważane alternatywy

- **Walidacja nakładania wyłącznie w aplikacji (check-then-act)** — odrzucone: to jest dokładnie
  mutacja, która w tym repo już dwukrotnie przeżyła dostarczone testy (SC-1-02, strażnik
  współbieżności przeniesiony do Pythona; SC-1-04, zamrożenie pól) i wymagała dopisania testu
  wyścigu, by ją zabić. ADR-0001 wymaga integralności egzekwowanej w bazie tam, gdzie to możliwe.
- **Dwie kolumny `DATE` + wyrażenie `daterange(...)` powtórzone w zapytaniach, bez kolumny
  generowanej** — odrzucone: jedno wyrażenie w dwóch miejscach (ograniczenie, wyszukiwanie) to
  dwie szanse na rozjazd granic, wykrywalny tylko testem na styku dni, nie testem na dacie w
  środku okresu.
- **Waluta w kluczu `EXCLUDE`** — odrzucone: rozstrzyganie "która waluta" przy odczycie jest nowym,
  nienazwanym mechanizmem wyboru, dokładnie tym, przed czym broni reguła 13 w drugiej połowie.

## Powiązane wymagania

F-03 ("Rates shall support effective date ranges"), invariant-guardian reguła 13, reguła 15
(typ tylko-datowy dla daty kalendarzowej), ADR-0001 (integralność w bazie), ADR-0002 (Decimal,
brak float, jawna precyzja), ADR-0006 (waluty i kursy — źródło wzorca, aneks nazewniczy).

## Aneksy

### 2026-09-19 — zaokrąglenie do wyświetlenia jest rzutem z utratą, nigdy wejściem (SC-2-02)

Punkt 6 nazywa konsumenta zaokrąglenia jednym konkretnym: regułą użytą w momencie użycia stawki w
kalkulacji, nie w momencie zapisu do katalogu. SC-2-02 (ekran katalogu, read-only) wprowadza drugiego
konsumenta, którego punkt 6 nie przewidział wprost: ekran, który zaokrągla przechowywaną stawkę
`NUMERIC(14,4)` do jednostki minor waluty wyłącznie po to, żeby ją pokazać.

1. **Wolno — to rzut do prezentacji, nie zapis i nie kalkulacja.** Uzasadnienie punktu 6 ("stawka
   jest danym wejściowym, nie wynikiem zaokrąglenia") broni zapisu: zaokrąglenie przy zapisie cicho
   zmieniałoby wprowadzoną wartość. Odczyt nie zmienia niczego w bazie.
2. **Konsekwencja przyjęta razem z tym wpisem, nazwana wprost:** dwa różne wiersze katalogu (np.
   100,0049 i 100,0050) mogą renderować się identycznie na tym ekranie; wartość na ekranie nie jest
   wartością w bazie. Ekran nie jest źródłem do przepisania stawki z powrotem — pierwsze zadanie z
   edycją stawki ładuje pełną precyzję z API, nigdy wartość widzianą na ekranie, i dowodzi tego
   własnym kryterium.
3. **Granica z punktu 3 bez zmian.** `effective_to` dociera do klienta jako włączające i jest
   wyświetlane dosłownie; `null` to okno bezterminowe (punkt 2), nie brak danych. Ani klient, ani
   ekran nie odtwarza konwersji `+ 1 dzień` — jedynym miejscem tej konwersji zostaje kolumna
   generowana `valid_period`.
4. **Ekran nie rozstrzyga, która stawka obowiązuje.** Lista okien bez filtra daty (SC-2-02, decyzja
   bramki 1 pkt 3) pokazuje kilka okien jednej krotki naraz; scalanie ich albo wybieranie
   "najnowszego" po stronie klienta byłoby drugim mechanizmem rozstrzygania, dokładnie tym, przed
   którym broni reguła 13. Rozstrzyganie należy do `GET /catalog/rates/effective`.

### 2026-09-21 — poddostawca jako piąta kolumna klucza `EXCLUDE` (SC-2-03)

Punkt 4 keyuje ograniczenie na "pełnej krotce wymiarów biznesowych + `valid_period WITH &&`", a
"Konsekwencje" nazywają zmianę kształtu tego wzorca migracją dotykającą trzech tabel, nie jednej.
SC-2-03 (Issue #46) jest pierwszą taką zmianą: poddostawca wchodzi do klucza jako piąta kolumna.

Podstawą nie jest F-03 w jego pierwotnym brzmieniu. F-03 wymieniał rolę, senioritet, lokalizację,
typ zaangażowania i stawki domyślne z przedziałami dat — poddostawcy nie znał; w wymaganiach padał
on wyłącznie jako kategoria kosztu w F-08. Podstawą jest decyzja biznesowa z Issue #46;
`Wymagania/Requirements_EN.md` dostał własny, datowany aneks przy F-03 tym samym zadaniem, żeby
przyszły czytelnik nie odczytał tej kolumny jako realizacji pierwotnej litery F-03.

1. **Poddostawca jest w kluczu, waluta nadal nie — i to nie jest niespójność.** Kryterium punktu 4
   brzmi: krotka wymiarów ma w danym momencie co najwyżej jedną stawkę. Dla waluty to prawda
   (przeliczenie idzie przez `exchange_rates`, nie przez równoległe wiersze). Dla poddostawcy jest
   fałszem z definicji: ta sama rola, ten sam senioritet, ta sama lokalizacja i ten sam typ
   zaangażowania mają jednocześnie stawkę wewnętrzną i stawkę poddostawcy A, i stawkę poddostawcy B.
   Bez tej kolumny w kluczu ograniczenie odrzucałoby wiersze legalne.
2. **Poddostawca jest osią własności stawki, nie wariantem lokalizacji.** Nie zastępuje
   `location_id`: ten sam poddostawca ma różne stawki dla różnych lokalizacji, dokładnie jak stawka
   wewnętrzna (rozstrzygnięcie człowieka, Issue #46).
3. **Reprezentacja "stawki wewnętrznej":** `vendor_id UUID NULL` + klucz `EXCLUDE` po
   `COALESCE(vendor_id, '00000000-0000-0000-0000-000000000000'::uuid)`. PostgreSQL zgłasza
   naruszenie `EXCLUDE` tylko gdy każdy operator klucza zwróci `TRUE`, a `NULL = NULL` zwraca
   `NULL` — naiwne dopisanie nullowalnej kolumny do klucza wyłączyłoby ochronę przed nakładaniem
   dokładnie dla stawek wewnętrznych, tych, które dziś są chronione. Sentinel: nil UUID PostgreSQL
   (`00000000-0000-0000-0000-000000000000`), zapisany jako **literał**, nie jako wywołanie
   `uuid_nil()` — `uuid_nil()` zwróciłoby tę samą wartość, ale wymaga rozszerzenia `uuid-ossp`,
   którego ta baza nie ma (jest tylko `btree_gist`); kod i migracja piszą literał. Z `CHECK` na
   `catalog_vendors` zabraniającym wiersza o `id` równym temu literałowi — kolizja z realnym
   poddostawcą wykluczona konstrukcyjnie, nie tylko nieprawdopodobieństwem `uuid4()`. Obowiązkowy
   dowód: test wstawiający dwa nakładające się okna stawki wewnętrznej dla jednej krotki,
   oczekujący odmowy (K-02, SC-2-03).
4. **Przebudowa ograniczenia jest jedną migracją, nie parą expand/contract.** Współistnienie
   starego, czterokolumnowego ograniczenia z nowym jest sprzeczne z celem zadania: stare odrzucałoby
   stawkę poddostawcy nakładającą się w czasie ze stawką wewnętrzną na tej samej krotce — wiersz, o
   który całe zadanie chodzi. Szczegóły i warunek wygaśnięcia tego odstępstwa — w ADR-0001, aneks z
   tą samą datą.
5. **Punkt 7 rośnie, nie zmienia się.** Klucz `EXCLUDE` zyskuje piątą kolumnę `uuid`, czyli kolejny
   operator `=` wewnątrz indeksu gist — zależność od `btree_gist` jest po tym zadaniu większa, nadal
   nieudowodniona dla roli aplikacyjnej na środowisku docelowym (open decision #5).
6. **Czego ten aneks nie rozstrzyga.** Pominięcie poddostawcy przy odczycie (`GET
   /catalog/rates/effective` bez `vendor_id`) **nie wolno** interpretować jako "dowolny" — musi
   znaczyć "wewnętrzna" (K-03/K-04, SC-2-03). To byłby drugi mechanizm rozstrzygania obok okna dat,
   dokładnie to, przed czym broni reguła 13 w swojej drugiej połowie.

**Status decyzji podniesiony tym zadaniem z Draft do Accepted** (rozstrzygnięcie bramki 1, Issue
#46) — mechanizm okien obowiązywania jest mutation-checked od SC-2-01, to drugie zadanie, które na
nim stoi.

### 2026-09-21 — okno obowiązywania wpisywane i edytowane przez człowieka (SC-2-04)

Aneks z 2026-09-19 przewidział drugiego konsumenta zaokrąglenia (ekran pokazujący stawkę) i zamknął
się zdaniem: „pierwsze zadanie z edycją stawki ładuje pełną precyzję z API, nigdy wartość widzianą na
ekranie, i dowodzi tego własnym kryterium".

1. **Uruchomiło się — SC-2-04 jest tym zadaniem.** Rozstrzygnięcie bramki 1 (2026-09-21, Issue #49)
   przyjęło zakres szerszy niż rekomendowany: formularz obejmuje dodawanie **i** edycję istniejącego
   wiersza. Warunek z pkt 2 aneksu z 2026-09-19 („pierwsze zadanie z edycją stawki ładuje pełną
   precyzję z API, nigdy wartość widzianą na ekranie, i dowodzi tego własnym kryterium") przestaje
   być odłożony i staje się obowiązkiem tego zadania. Źródłem wypełnienia formularza edycji jest
   wyłącznie odpowiedź API (`NUMERIC(14,4)` jako string dziesiętny), nigdy tekst z komórki tabeli,
   który `formatMoneyString` zaokrąglił do dwóch miejsc — inaczej edycja samej daty okna przepisałaby
   stawkę 100,0049 na 100,00, czyli cicha zmiana danej wejściowej, ta sama, której zakazuje pkt 6
   decyzji. Kryterium musi być obalalne: mutacja „prefill z wartości renderowanej zamiast z
   odpowiedzi API" ma wywracać test; samo przejście ścieżki edycji niczego tu nie dowodzi, bo
   wartość zaokrąglona i pełna są równe dla każdej stawki o dwóch miejscach. Granica tego kryterium,
   nazwana od razu: dla wołającego bez `PERSONNEL_COSTS_READ` pole `default_cost_rate` nie wraca z
   API wcale (ADR-0005, bramka kosztowa), więc dla tego jednego pola kryterium jest dowodliwe
   wyłącznie w teście z uprawnieniem — dla pozostałych pól na obu gałęziach bramki. Dowiedzione
   po stronie backendu (pełna precyzja przekracza granicę API niezmieniona):
   `backend/tests/test_catalog_edit.py::test_q_2_a_cost_rate_that_is_sent_is_written_at_full_precision`;
   dowód po stronie ekranu (formularz ładuje z API, nie z komórki) — zadanie frontendowe.
2. **Każda afordancja prefillu uruchamia tamten warunek natychmiast.** „Duplikuj wiersz", „nowe okno
   dla tej krotki", jakikolwiek przycisk kopiujący wartość z tabeli do formularza — przepisuje
   wartość zaokrągloną do dwóch miejsc do pola, z którego powstanie nowy wiersz `NUMERIC(14,4)`.
   Albo jest to jawnie poza zakresem zadania, albo dostaje kryterium dowodzące, że źródłem jest pełna
   precyzja z API. Milczenie nie jest trzecią możliwością. SC-2-04 nie wprowadza żadnej afordancji
   duplikowania — formularz dodawania startuje pusty, formularz edycji ładuje się z API (pkt 1).
3. **Granica z pkt 3 decyzji bez zmian, teraz także na wejściu.** `effective_to` jest włączające w
   formularzu, dokładnie tak jak w API; klient nie odtwarza konwersji `+ 1 dzień` ani przy zapisie,
   ani przy wyświetleniu. Jedynym miejscem tej konwersji zostaje kolumna generowana `valid_period`.
   Puste `effective_to` znaczy okno bezterminowe (pkt 2 decyzji), nie „brak danych". Dowiedzione:
   `test_an_explicit_null_effective_to_opens_the_window_and_an_omitted_one_changes_nothing`.
4. **Nakładanie okien rozstrzyga `EXCLUDE`, nie ekran.** Klient nie sprawdza kolizji przed wysłaniem —
   byłoby to check-then-act (reguła 13, druga połowa) i dodatkowo sprawdzenie fałszywe: lista stawek
   jest stronicowana, więc klient nie ma zbioru, na którym mógłby je wykonać. Odmowa `409` z bazy jest
   jedynym orzeczeniem o nakładaniu; ekran ją pokazuje, nie uprzedza. Na ścieżce edycji ten `409`
   musi być rozróżnialny od `409` nieaktualnego znacznika współbieżności — patrz ADR-0007, aneks
   2026-09-21, pkt 4.
5. **Ekran nadal nie rozstrzyga, która stawka obowiązuje** (pkt 4 decyzji) — także po dodaniu lub
   edycji wiersza: żadnego scalania okien ani wybierania „najnowszego" po stronie klienta.
6. **Konsekwencja stronicowania, przyjęta razem z tym aneksem:** nowo dodane okno o starej dacie
   `effective_from` nie musi trafić na pierwszą stronę listy (porządek `effective_from DESC, id DESC`,
   domyślny `limit`). „Dodano, a wiersza nie widać" jest stanem prawdziwym, nie awarią — to samo
   dotyczy wiersza **edytowanego**: zmiana `effective_from` może przenieść go na inną stronę listy,
   więc „zapisano, a wiersza nie widać" jest stanem prawdziwym także po edycji. Sukces zapisu musi
   być zakomunikowany zdaniem, a nie pojawieniem się wiersza (ADR-0009, decyzja pkt 3, G-6).
