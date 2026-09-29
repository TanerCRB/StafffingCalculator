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

### 2026-09-22 — podstawa godzinowa kalendarza bez okna obowiązywania (SC-3-02)

„Konsekwencje" nazywają trzy tabele dzielące jeden wzorzec przedziału obowiązywania i czynią zmianę
jego kształtu migracją dotykającą wszystkich naraz. SC-3-02 wprowadza
`working_calendar.standard_hours_per_day` — wartość organizacyjną, która w realnej organizacji
zmienia się w czasie, a mimo to wzorca tego **nie** dostaje. Zapisane tutaj z tego samego powodu, z
którego ADR-0004 zapisał brak ograniczenia `EXCLUDE` na okresie pozycji obsady: brak mechanizmu
musi być rozstrzygnięciem, nie przeoczeniem odkrytym przez następne zadanie.

1. **Jednostką wersjonowania jest kalendarz, nie kolumna.** Zmiana standardowej długości dnia pracy
   to nowy kalendarz, do którego scenariusze są przepinane świadomie.
2. **Podstawa odmowy: okno na tej kolumnie byłoby drugim mechanizmem rozstrzygania w czasie** obok
   samego zbioru dni kalendarza. Dla daty D trzeba by odpowiedzieć, czy podstawa godzinowa pochodzi
   z wiersza, którego `valid_period` obejmuje D, czy z kalendarza, który scenariusz wskazał. Reguła
   13 Strażnika broni dokładnie przed istnieniem dwóch takich mechanizmów naraz.
3. **Odtwarzalność zatwierdzonej kalkulacji nie opiera się tu na oknie, tylko na migawce**
   (ADR-0004, aneks z tą samą datą). Jedyny wymóg, któremu okno by tu służyło — AC-04/AC-10 — jest
   już spełniony mechanizmem strukturalnym, i to mocniejszym: brakujący wiersz migawki jest widoczny.
4. **Koszt przyjęty świadomie.** Organizacja zmieniająca standardowy dzień pracy w połowie roku
   zakłada nowy kalendarz, a każdy scenariusz `draft` wskazujący stary zachowuje starą podstawę,
   dopóki ktoś go nie przepnie — nic o tym nie przypomina. Wymóg wynikający z tego wprost:
   rozwiązana pojemność musi nazwać, z którego kalendarza i z jakiej podstawy powstała (F-02,
   "identify the source of each inherited or overridden value").
5. **Warunek wygaśnięcia, datowany.** Pierwsze żądanie dwóch różnych długości dnia pracy pod JEDNĄ
   nazwą kalendarza wygasza ten aneks i wymaga tabeli-dziecka na wzorcu z punktu 2 decyzji
   (`effective_from`/`effective_to` + kolumna generowana `valid_period` + `EXCLUDE` kluczowane na
   `calendar_id`) oraz funkcji rozwiązującej po dacie. Nie wolno wprowadzić tego jako skutku
   ubocznego zadania o czymś innym.
6. **Czego ten aneks nie obejmuje: dzień częściowo roboczy** (np. 24 grudnia do 13:00). Kolumna
   godzin na wierszu dnia byłaby nadpisaniem podstawy kalendarzowej, czyli trzecim mechanizmem —
   jawnie poza zakresem, wymaga własnego, datowanego wpisu. Milczenie nie jest trzecią możliwością.
7. **Rozstrzygnięcie bramki 1 (2026-09-22, G-2): `catalog_locations.calendar_id` jest nullowalne.**
   Pominięcie nie znaczy "domyślne 8 godzin" ani żaden inny cichy fallback — pozycja w lokalizacji
   bez kalendarza dostaje nazwany stan "brak kalendarza" w wyliczonej pojemności, nigdy `0` i nigdy
   wyjątek. Ta sama zasada co dla `vendor_id`/`vendor` w SC-2-03: pominięcie jest stanem, nie luką
   do wypełnienia domysłem.

### 2026-09-22 — budżet urlopowy jako czwarty konsument wzorca przedziału obowiązywania (SC-3-03)

Drugi aneks tej daty w tym pliku i osobny wpis, nie dopisek do poprzedniego — precedens dosłowny:
2026-09-21, dwa aneksy (SC-2-03 i SC-2-04). **Konsekwencja nazewnicza przyjęta razem z tym
wpisem:** odwołanie brzmiące „ADR-0008, aneks 2026-09-22" bez nazwy zadania — takie jak te w
`backend/app/models/catalog.py` i `backend/app/models/staffing.py` — znaczy aneks **SC-3-02**.
Każde nowe odwołanie do któregokolwiek z tych dwóch musi nazwać zadanie.

„Konsekwencje" wyliczają trzy tabele dzielące jeden wzorzec kolumn i jedno ograniczenie
integralności. Budżet urlopowy (SC-3-03, F-05) jest **czwartą** tabelą, której ten wzorzec zostaje
przypisany, i **drugą, która go faktycznie buduje**: z trzech pierwotnych powstał jeden katalog
stawek (SC-2-01), a `exchange_rates` (ADR-0006) i `commercial_terms` (ADR-0003, wciąż Draft)
pozostają zadecydowane i nieistniejące — dokładnie stan, który sekcja „Kontekst" nazywa pułapką
(„zadecydowany dwukrotnie, ani razu zbudowany").

1. **Budżet dostaje wzorzec w całości, z jednym nazwanym zawężeniem.** `effective_from DATE NOT
   NULL`, `effective_to DATE` **włączające** na wejściu i w API, kolumna generowana `valid_period`
   jako jedyne miejsce konwersji `+ 1 dzień`, `EXCLUDE USING gist` na kluczu × `valid_period WITH
   &&`, `btree_gist`, `CHECK` porządkujący parę dat (bez niego `daterange` daje zakres pusty, a `&&`
   przestaje cokolwiek chronić — pkt 2, 3 i 4 decyzji plus komentarz przy
   `effective_period_ordered`). Jedyne odstępstwo — zakaz okna bezterminowego na **tej** tabeli —
   jest nazwane w pkt 10b i nigdzie indziej; poza nim żadnej własnej odmiany, bo drugi kształt tego
   samego wzorca jest tym, co „Konsekwencje" nazywają migracją fazy contract dotykającą wszystkich
   tabel naraz.
2. **Dlaczego budżet dostaje to, czego odmówiono `standard_hours_per_day`** (aneks z tą samą datą,
   SC-3-02). Tamta odmowa nie była niechęcią do wzorca — jej podstawą był pkt 2 tamtego aneksu:
   okno na tej kolumnie byłoby **drugim** mechanizmem rozstrzygania w czasie obok samego zbioru dni
   kalendarza. Budżet nie ma konkurenta: żadna inna tabela nie odpowiada na pytanie „ile dni
   przysługuje na dzień D", a jednostką wersjonowania nie może tu być kalendarz, bo budżet zmienia
   się niezależnie od niego (nowy regulamin, nie nowy zbiór świąt). Reguła 13 Strażnika zostaje
   spełniona w ten sam sposób co dla stawek: **jeden** mechanizm rozstrzygania, egzekwowany przez
   `EXCLUDE` przy zapisie, nigdy przez „najnowszy wiersz wygrywa" przy odczycie.
3. **Klucz: `(calendar_id, engagement_type_id)`, oba `NOT NULL`, i tylko te dwa** (rozstrzygnięcie
   bramki 1, 2026-09-22, Q-2, potwierdzone po rozstrzygnięciu pkt 8 — klucz **nie** jest
   reotwierany). Nullowalna kolumna w kluczu znaczyłaby „dowolny", czyli drugi, nienazwany
   mechanizm rozstrzygania nałożony na okno dat — ten sam argument, którym `RATE_DIMENSION_COLUMNS`
   trzyma wszystkie cztery wymiary stawki jako `NOT NULL`. Mutacja do zabicia testem: poluzowanie
   którejkolwiek z dwóch kolumn do `NULL` z odczytem „pasuje do wszystkiego".
   a. **Budżet wisi na kalendarzu, nie na lokalizacji**, bo to kalendarz jest jednostką reżimu czasu
      pracy — pkt 1 aneksu SC-3-02 („jednostką wersjonowania jest kalendarz, nie kolumna") — a
      lokalizacja wskazuje kalendarz, nie odwrotnie. Konsekwencje przyjęte razem z tym wyborem:
      lokalizacje wskazujące jeden kalendarz **dzielą budżet**, a lokalizacja bez kalendarza
      (`catalog_locations.calendar_id IS NULL`, aneks SC-3-02 pkt 7) nie ma też budżetu i daje
      **jeden** nazwany stan do pokazania, nie dwa niezależne.
   b. **Odrzucone: `location_id` w kluczu.** Pozwoliłoby dwóm lokalizacjom na jednym kalendarzu
      mieć różne budżety, ale rozszczepiałoby „brak kalendarza" i „brak budżetu" na dwa niezależne
      nazwane stany, które każda odpowiedź musiałaby rozróżniać, i wymagałoby kompletu wierszy
      budżetu przy każdej nowej lokalizacji. **Warunek ponownego rozpatrzenia:** pierwsze żądanie
      dwóch różnych budżetów pod jednym kalendarzem — jest to przebudowa ograniczenia `EXCLUDE`,
      czyli jedna migracja bez pary expand/contract (aneks 2026-09-21, SC-2-03, pkt 4), i wymaga
      własnego, datowanego wpisu tutaj.
4. **Żadnego aparatu sentinela z SC-2-03 — i to nie jest niespójność.** `VENDOR_KEY_SENTINEL`,
   `COALESCE(vendor_id, …)`, `CHECK` na `catalog_vendors` i literał nil UUID istnieją wyłącznie
   dlatego, że `vendor_id` jest nullowalne i `NULL` znaczy tam jedną konkretną rzecz („stawka
   wewnętrzna"), a `NULL = NULL` nie jest `TRUE` (aneks 2026-09-21, pkt 3). Tutaj żadna kolumna
   klucza nie ma znaczenia „brak wartości", więc kopiowanie tamtego aparatu dodałoby wyrażenie,
   wartownik i `CHECK`, które niczego nie chronią, a sugerują istnienie stanu „budżet niczyj".
   Klucz jest z dwóch zwykłych kolumn i z `valid_period`.
5. **Budżet jest liczbą dni albo FTE, nie kwotą — i jest o jedno mnożenie od pieniędzy.**
   `NUMERIC` z jawną, zadeklarowaną skalą, `Decimal` nigdy `float` (ADR-0002, NF-01) — ta sama
   podstawa, którą `standard_hours_per_day` dostaje `NUMERIC(4,2)`: ta liczba mnoży się przez
   podstawę godzinową i przez stawkę, więc błąd binarny tutaj dociera do każdej liczby kosztowej i
   przychodowej. Pkt 6 decyzji obowiązuje bez zmian, mimo że nie chodzi o pieniądze: wartość
   wprowadzona nie jest zaokrąglana przy zapisie, zaokrąglenie jest regułą konsumenta w momencie
   użycia. **Jednostka jest jedna, nazwana i egzekwowana w bazie** (wzorem `CHECK unit = 'hour'` na
   stawce), nie domyślana z wielkości liczby: „20" jako dni i „20" jako FTE-dni to dwa różne wyniki
   z tego samego wiersza, a takiej pomyłki nie wykryje żaden test na wartości ze środka zakresu.
6. **Pole źródła: obowiązkowe, niepuste, i jest granicą danych osobowych.** Wiersz budżetu niesie
   tekstowe pole źródła (regulamin, punkt umowy zbiorowej, decyzja organizacyjna) o jedynej regule
   treści „niepusty ciąg" — i **nigdy nie jest to autor wpisu**. Dwa powody, oba już rozstrzygnięte
   gdzie indziej: (a) kolumna „kto wpisał" byłaby pierwszą kolumną wiążącą wiersz katalogu z
   użytkownikiem i wygaszałaby zarówno wyjątek „dane organizacyjne bez zasięgu", jak i zwolnienie z
   funkcji-strażnika (ADR-0005, aneks 2026-09-21 SC-2-04 pkt 6; ADR-0001) — wprowadzenie jej
   wymagałoby własnego, datowanego wpisu tam, nie tutaj; (b) wolny tekst jest wobec danych osobowych
   **nieklasyfikowany**, dokładnie jak `catalog_vendors.name` (audyt 2026-09-21: „traktować jako
   nieklasyfikowane") i `absence_type.name` (ADR-0005, aneks 2026-09-22 SC-3-02 pkt 10) — a ponieważ
   budżet wchodzi do migawki (ADR-0004, aneks z tą samą datą, SC-3-03), treść tego pola staje się
   trwała i nieusuwalna. **Warunek, który uruchamia się z chwilą, gdy SC-3-03 wystawia jakąkolwiek
   ścieżkę zapisu budżetu:** ten sam, który ADR-0005 aneks SC-3-02 pkt 10 nałożył na pierwsze
   zadanie wystawiające zapis `absence_type` — rozstrzygnięcie wprost ograniczenia treści albo
   zasady erasure/rectification dla wierszy `approved_snapshot_*`, które ten tekst już skopiowały,
   jako warunek wstępny, nie do odkrycia po fakcie.
7. **Brak wiersza budżetu jest nazwanym stanem, nigdy cichym zerem** (rozstrzygnięcie bramki 1,
   Q-3). Ta sama zasada, którą `calendar_id IS NULL` dostało w aneksie SC-3-02 pkt 7 i `vendor_id`
   w aneksie 2026-09-21 pkt 6: pominięcie jest stanem, nie luką do wypełnienia domysłem. Dwie
   odpowiedzi są zakazane — `0` dni (pozycja z pełną zdolnością rozliczalną, czyli cicho lepsza
   marża) i wyjątek. Wymóg, który z tego wynika i który trzeba dowieść: **„brak wiersza" musi być
   w odpowiedzi odróżnialne od „wiersz o wartości 0"**, bo zerowy budżet jest wartością legalną i
   znaczącą (typ zaangażowania bez prawa do urlopu), a zlanie obu w jedną odpowiedź kasuje różnicę
   bez ostrzeżenia. Ten sam stan obejmuje budżet **wygasły** — patrz pkt 10b.
8. **Typ nieobecności, przeciw któremu budżet się rozlicza: flaga w słowniku** (rozstrzygnięcie
   człowieka, 2026-09-22). Budżet nie niesie `absence_type_id` i klucz z pkt 3 zostaje
   dwuelementowy; typ rozliczany budżetem wskazuje **nowa kolumna logiczna na `absence_type`**
   (np. `is_statutory_leave`). Odrzucone razem z tym rozstrzygnięciem: nazwa typu zaszyta w kodzie —
   NF-10 i precedens `WEEK_PATTERN_EXPRESSION` („wzorzec jest daną, nie kodem") wykluczają ją z
   góry, nie jest trzecią możliwością.
   a. **Baza egzekwuje „co najwyżej jeden", i tyle da się egzekwować.** Częściowy indeks unikalny
      na stałym wyrażeniu z predykatem na fladze (`… ON absence_type ((true)) WHERE
      is_statutory_leave`) odrzuca drugi wiersz z flagą **w tej samej instrukcji, która go
      wstawia**. Sprawdzenie po stronie aplikacji jest tu zakazane po imieniu: check-then-act
      przeżył w tym repozytorium dostarczone testy trzykrotnie (SC-1-02 ×2, SC-2-01), a ADR-0001
      wymaga integralności w bazie tam, gdzie to możliwe.
   b. **Druga połowa („co najmniej jeden") nie jest egzekwowalna indeksem i jest nazwanym stanem,
      nie wyjątkiem.** Słownik bez żadnego wiersza z flagą to ten sam przypadek co brak wiersza
      budżetu (pkt 7): odpowiedź mówi, że typu ustawowego nie wskazano, i **nigdy** nie zgaduje —
      ani „pierwszy typ alfabetycznie", ani typ o nazwie zawierającej „urlop", ani cichy `0`.
      Zgadywanie byłoby drugim mechanizmem rozstrzygania obok flagi, dokładnie tym, przed czym
      broni reguła 13 w swojej drugiej połowie.
   c. **Konsekwencja przyjęta świadomie: flaga nie ma okna obowiązywania, więc jej przeniesienie
      zmienia znaczenie wszystkich wierszy budżetu naraz, bez śladu.** Scenariusze `draft`
      podchwytują zmianę po cichu — tak jak podchwytują każdą zmianę katalogu. Zatwierdzone są
      chronione tylko dlatego, że flaga wchodzi do migawki (ADR-0004, aneks z tą samą datą,
      SC-3-03, pkt 8); bez tamtego punktu to rozstrzygnięcie byłoby cichą regresją AC-10, nie
      uproszczeniem.
9. **Zbieg budżetu z ręczną nieobecnością rozstrzyga `max`, na okresie okna obowiązywania budżetu**
   (rozstrzygnięcie bramki 1 P-4 i decyzja człowieka z 2026-09-22). Budżet i lista ręcznych
   nieobecności są dwoma źródłami tej samej wielkości, a reguła 13 w drugiej połowie broni przed
   pozostawieniem wyboru odczytowi. Reguła: `max(budżet okna, suma ręcznych nieobecności typu z
   pkt 8 w tym oknie)`. **Okresem jest okno obowiązywania, nie rok i nie miesiąc** — żadne nowe
   pojęcie kalendarzowe nie wchodzi do systemu, a jedyny przedział, którego budżet używa, to ten,
   który już ma.
   a. **Kolejność jest częścią reguły, nie szczegółem: `max` rozstrzyga się na oknie, a dopiero
      jego wynik jest proratowany na miesiące** (pkt 10). Odwrotna kolejność — `max` per miesiąc —
      daje wynik drastycznie inny: budżet 24 dni na oknie rocznym i 20 dni urlopu zaplanowanych w
      lipcu dają przy kolejności nakazanej 24 dni, a przy kolejności odwróconej 42. To jest mutacja
      do zabicia testem, nie preferencja stylistyczna.
   b. **`max` nie da się wyrazić ograniczeniem bazy** — ani `EXCLUDE`, ani `CHECK` nie sięgają
      dwóch tabel z dwóch różnych agregatów — więc jest to reguła wyliczenia i wymaga testów po
      **obu** stronach zbiegu (budżet większy, ręczne większe, równe). Sama ścieżka „przeszło"
      niczego tu nie dowodzi: dla wartości równych obie implementacje dają to samo.
   c. **Cena przyjęta razem z tą regułą:** ręczna nieobecność mieszcząca się w budżecie jest w
      wyniku **niewidoczna** — zaplanowanie 5 dni urlopu przy budżecie 26 dni nie zmienia ani jednej
      liczby. To jest poprawne i będzie odebrane jako awaria, więc musi zostać powiedziane na
      ekranie, nie odkryte przez użytkownika.
10. **Proracja miesięczna: budżet okna dzielony równomiernie przez miesiące okna** (rozstrzygnięcie
    człowieka, 2026-09-22, G-2). Pojemność liczona jest per miesiąc (SC-3-02), a budżet w oknie
    obowiązywania sam z siebie nie mówi, ile dni przypada na jeden miesiąc — bez nazwanej reguły
    każdy konsument wymyśliłby własną, co jest dokładnie tą klasą rozjazdu, przed którą broni pkt 3
    decyzji (jedna konwersja, jedno miejsce). Reguła: **dni budżetu ÷ liczba miesięcy okna**,
    w `Decimal`, **bez zaokrąglenia pośredniego**; jedyny punkt zaokrąglenia zostaje tam, gdzie był
    (`app.core.money.round_money` na wartości końcowej, ADR-0002, pkt 6 decyzji). Zaokrąglanie
    proracji do pełnych dni w każdym miesiącu jest mutacją, która przy dwunastu miesiącach gubi albo
    dokłada kilka dni, a widać to dopiero w sumie rocznej.
    a. **Niezmiennik, który kryterium ma dowieść, i który jest mocniejszy niż sama formuła: suma
       proracji po wszystkich miesiącach okna równa się budżetowi okna.** Zabija naraz dwie rzeczy —
       zaokrąglenie pośrednie oraz dwuznaczność miesiąca niepełnego (okno zaczynające się 15
       stycznia): jakkolwiek policzony zostanie miesiąc brzegowy, wynik musi się sumować do
       budżetu, więc żadna interpretacja nie może po drodze wyprodukować ani stracić dnia.
    b. **Okno budżetu jest zawsze domknięte: `CHECK effective_to IS NOT NULL`** (rozstrzygnięcie
       człowieka, 2026-09-22). Proracja z pkt 10 dzieli przez liczbę miesięcy okna, a okno
       bezterminowe nie ma mianownika — więc zamiast dawać tej jednej tabeli wyjątek w regule
       wyliczenia, odbiera się jej możliwość, która ten wyjątek by wymuszała. **Jest to jawne, wąskie
       odstępstwo od pkt 2 decyzji** („`NULL` = bezterminowa"), obowiązujące **wyłącznie** tabelę
       budżetu: pozostałe trzy tabele wzorca zachowują okno bezterminowe, a wspólne wyrażenie
       `valid_period` nie zmienia się wcale (`effective_to + 1` na `NULL` nadal daje górną granicę
       nieograniczoną — po prostu żaden wiersz tej tabeli takiej wartości nie osiągnie). `CHECK`
       porządkujący parę dat zostaje w kształcie wspólnym, z gałęzią `effective_to IS NULL`, która
       na tej tabeli jest martwa — usunięcie jej byłoby drugim rozjazdem ze wzorcem tam, gdzie
       jeden wystarczy.
       **Cena przyjęta świadomie:** data końca jest obowiązkowa przy każdym budżecie, więc budżet
       *wygasa* — a scenariusz planowany poza ostatnie okno wpada w nazwany stan z pkt 7 („brak
       wiersza"), nigdy w ciche `0`. To jest zamierzone: regulamin urlopowy bez daty końca jest
       założeniem, nie danymi, i lepiej, żeby system o niego upomniał się widocznie.
       **Warunek ponownego rozpatrzenia:** pierwsze zadanie, które nada proracji inny mianownik niż
       długość okna albo usunie prorację, wygasza to odstępstwo — `CHECK` wraca wtedy do wspólnego
       kształtu tym samym zadaniem, nie później.
11. **Pkt 7 decyzji rośnie po raz drugi.** Druga tabela z `EXCLUDE USING gist` znaczy drugą tabelę
    zależną od `btree_gist` — a dowód z CI nadal dowodzi migracji i mechanizmu, nie tego, że rola
    aplikacyjna na nieistniejącym jeszcze środowisku docelowym wykona `CREATE EXTENSION` (open
    decision #5). Naruszenie tego `EXCLUDE` owija się tym samym mechanizmem co każde inne
    (`_describe_without_values`): komunikat bazy niesie wartości wiersza, a te — choć nie są kwotą —
    są warunkami zatrudnienia i nie wracają do wołającego.

### 2026-09-23 — `commercial_terms` wychodzi z listy tabel wzorca (SC-4-01)

"Konsekwencje" wyliczają trzy tabele dzielące wzorzec: katalog stawek, `exchange_rates`,
`commercial_terms`; aneks SC-3-03 nazywa dwie ostatnie "zadecydowanymi i nieistniejącymi".
Rozstrzygnięcie bramki 1 SC-4-01 (P-1, ADR-0003 w wersji z 2026-09-23) zmienia to dla
`commercial_terms`.

1. **`commercial_terms` nie ma przedziału obowiązywania ani `EXCLUDE`.** Jedna reguła na scenariusz
   (`UNIQUE scenario_id`); wersjonowanie reguły (F-06.5 "Commercial rates and terms shall be
   versioned") realizuje mechanizm ADR-0004 — kopia scenariusza i strażnik zapisu pod `approved` —
   nie okno dat. Dwa mechanizmy wersjonowania jednej reguły byłyby drugim mechanizmem rozstrzygania
   (reguła 13, druga połowa).
2. **Wzorzec obowiązuje dalej wszystkie pozostałe tabele** (katalog stawek, budżet urlopowy,
   `exchange_rates`); stawka, którą reguła T&M czyta, jest rozstrzygana tym wzorcem (predykat
   obejmowania całego miesiąca, ADR-0003 pkt 5 — ta sama kolumna `valid_period`, żadnej drugiej
   konwersji granic).
3. **Warunek ponownego rozpatrzenia:** reguły na poziomie fazy/workstreamu albo reguła
   obowiązująca tylko w części okresu scenariusza — wtedy przedział obowiązywania na
   `commercial_terms` wraca jako własny, datowany wpis tutaj.

### 2026-09-23 — koszt dodatkowy nie jest konsumentem tego wzorca (SC-5-05, ADR-0014)

1. **Brak `EXCLUDE`.** Wzorzec rozstrzyga *który jeden wiersz spośród wielu* obowiązuje daną krotkę
   w danym dniu (reguła Strażnika 13, pierwsza połowa — wyszukiwanie po dacie). Koszty dodatkowe nie
   mają tej semantyki: dwa koszty tej samej kategorii w tym samym miesiącu (np. dwie licencje) są
   oba prawdziwe naraz i oba wchodzą do sumy — nie ma "który wygrywa".
2. **Jeden element wzorca zostaje przyjęty przez analogię: zakres zawsze domknięty.** Jak budżet
   urlopowy (pkt 10b wyżej), koszt cykliczny ma obowiązkowy koniec (`CHECK effective_to IS NOT
   NULL` na wierszu kosztu cyklicznego) — koszt bez końca nie ma skończonej sumy do policzenia.
   Powód jest inny niż proracji budżetu (tu nie ma mianownika do podzielenia), ale konsekwencja ta
   sama: koszt bezterminowy to założenie, nie dane, i ma zostać nazwany wprost przy zapisie, nie
   przyjęty cicho.
3. **Warunek ponownego rozpatrzenia:** organizacyjne domyślne ceny per kategoria (poza zakresem
   SC-5-05, ADR-0014 "Czego ten dokument nie rozstrzyga") byłyby pełnym konsumentem tego wzorca —
   rozstrzyganie "która domyślna cena obowiązuje dany dzień" wraca wtedy jako własny, datowany wpis
   tutaj.

### 2026-09-29 — The calendar hour basis as the FTE basis (SC-3-07, Issue #163, gate 1)

**Status:** Draft — pending approval

> Prepared by the Architect for gate 1 of Issue #163 (FTE ↔ hours from the working calendar). Human
> answers Q1 = A, Q2 = B, Q3 = A, Q4 = A, Q5 = A, Q6 = A are recorded on the Issue. Written in
> English (`TEAM-CONTRACT.md` §7); the earlier text of this file stays unchanged. This entry is a
> **consumer** of the calendar rows the SC-3-02 addendum (2026-09-22) created, not a new
> effective-date mechanism: no window, no `EXCLUDE`, no new table.

1. **Source (Q1 = A): the calendar of the position's location.** The basis of one month is the
   calendar's working days of that month times its `standard_hours_per_day`, read through the same
   value object the capacity uses (`app.domain.capacity.CalendarBasis`, `working_days_in_month`) —
   one spelling of "which days are working days". No `weekday() < 5`, no constant `8`, no second
   pattern/holiday logic (criteria K-01 and K-02 of SC-3-02 apply unchanged). A location with
   `calendar_id IS NULL` is the named state `no_calendar` (addendum 2026-09-22, point 7), never a
   default number of hours.
2. **Rejected: a scenario-level source.** A calendar, or a length of the working day, chosen on the
   scenario would be a second mechanism resolving the same question next to the location's calendar
   (Invariant Guardian, rule 13; addendum 2026-09-22, points 1–2: the unit of versioning is the
   calendar). It would also need a place in the ADR-0012 chain and a group assignment under ADR-0004.
   Reopening condition: a scenario that must convert FTE with a basis other than its positions'
   location calendars — then a dated entry here, not a side effect of a task.
3. **FTE is derived from hours only (Q2 = B).** No FTE column, request field or stored figure is
   created. The domain function converts in both directions with one basis: FTE from hours, and hours
   from an FTE value **passed by the caller**. This supplies the conversion the `staffing_position`
   docstring deferred ("FTE as a unit is out of scope until a working calendar exists to convert
   it"); it does not store the unit. Consequence: ADR-0013's `assigned_fte` cost basis (SC-5-04) may
   use this conversion, but a cost basis that takes FTE as a stored *input* is a different decision
   from Q2 = B and needs its own entry.
4. **Gross of absences and of the leave budget (Q3 = A).** The basis is `working_days ×
   standard_hours_per_day` of one person — the gross term of the capacity formula, before the
   absence and budget subtractions. Consequences named: 1.00 FTE is a full calendar month whether
   or not the person is absent; a plan in FTE can exceed `derived_capacity_hours` (net), and that is a
   visible over-allocation, never a refusal (the same rule `planned_allocation_hours` already has).
   Mutation: subtracting absences or the budget share inside the conversion.
5. **Unit of calculation: the calendar month (Q5 = A).** The month is `period_month` (first of the
   month, `FIRST_DAY_OF_MONTH_EXPRESSION`), with the whole month's working days. There is no
   proration to a position's `start_date`/`end_date` within a month and no finer grain (delivery
   segments, ADR-0016). Consequence named: a position that starts on the 20th has, for that month,
   the basis of the whole month; a planner converting FTE to hours for it gets whole-month hours.
6. **Approved scenarios read the snapshot.** For an approved scenario the basis is built from
   `approved_snapshot_working_calendar(_day)` (`app.data.working_calendar.frozen_basis_by_location`,
   ADR-0004 addendum 2026-09-23 SC-5-06, point 1), never from the live tables; a location with no
   frozen key is `no_calendar`, never a live lookup to fill it in. A draft reads the live calendar
   (`basis_by_location`) and its figure moves when the calendar is edited — including by the holiday
   import of ADR-0020. **Named, not repaired:** the capacity grid of an approved scenario still reads
   the live calendar (SC-5-06, point 4), so for one approved scenario the grid and this conversion
   can disagree after a catalogue edit. The conversion follows the invariant (AC-04, AC-10), not the
   grid.
7. **Boundary with ADR-0003, points 6–7 (revenue).** T&M revenue is `billable_hours` from the
   allocation "literally" and is not derived from plan, availability or `derived_capacity_hours`
   (point 6); `standard_hours_per_day` is the calendar's capacity basis and is not "hours in a
   billable day" — "merging them would make a calendar change move the price of a contract"
   (point 7). The conversion output is therefore **not an input of any revenue calculation and is
   not written into any hours column** (`availability_hours`, `planned_allocation_hours`,
   `billable_hours` stay three independent inputs — K-05 of SC-3-01). A consumer that wants
   FTE-derived hours in revenue or cost needs its own dated entry (ADR-0003 point 6, last sentence,
   says the same for the absence flags).
8. **The result names its source.** As `MonthCapacity` does (addendum 2026-09-22, point 4), a
   resolved conversion carries the calendar identifier and name, the standard hours per day and the
   working-day count it was computed from (F-02: identify the source of each derived value).
9. **Surfacing: none in this Story.** See proposal (d) below.

#### Gaps closed by proposal (Analyst gaps of #163), pending human confirmation

**(a) What headcount means.** Decided from `app.models.staffing`: the hours on a position are its
total, "not per head" (`HOURS_PRECISION` note, gate-1 decision 1), and ADR-0003 point 6 says
headcount "is already in it, so it is not multiplied a second time". So the FTE derived from a
position's hours is the FTE of the **position** — the sum over its people — and exceeds 1.00 when
`headcount > 1` (three people full-time: 3.00). It is **not** a per-person figure. Conversely,
`hours = FTE × basis` has **no headcount factor**: the FTE handed in is already a position total.
This differs deliberately from the capacity formula, which multiplies a per-person basis by
`headcount` (K-04): there the input is a person-count, here the input is already a total.
Per-person FTE is `position FTE ÷ headcount`, a second, separately named figure this Story does not
produce. Options: per-position (proposed — consistent with the stored hours, no division by
`headcount`, no double multiplication) or per-person (needs a division and a rule for a person
assigned at `headcount = 1` vs anonymous positions; and it re-touches the pseudonymisation question
of ADR-0005 addendum SC-3-02, point 11, as soon as it is shown). Mutation: multiplying by `headcount`
in the FTE → hours direction.

**(b) A month with zero working days.** A named state `no_working_days`, in **both** directions,
with the value `"n/a"` (`app.core.money.NOT_APPLICABLE`, one meaning across the application —
ADR-0002 addendum 2026-09-26, point 3) — decided before any division, so it is neither a
`ZeroDivisionError` nor a `0`. The closed set of states is `resolved`, `no_calendar` (the constant
already in `app.domain.capacity`, not a second spelling) and `no_working_days`. `standard_hours_per_day`
is `> 0` by `CHECK`, so a zero basis arises only from a calendar with no working day that month.
Options for the FTE → hours direction: the named state (proposed, the reasoning of K-23: a `0.00`
is a number every later sum adds up, and the reverse conversion could not round-trip it) or a
mathematically true `0.00` hours. Needs confirming.

**(c) Precision and where rounding lives.** Proposed: the derived ratio is expressed as **a share of
a full-time month in percent** through the existing `app.core.money.ratio_percent(hours,
basis_hours)` — two places, half-up, `100.00` = 1.00 FTE, so FTE resolution is 0.0001. No new
rounding function and no new rounding point: `round()`/`quantize` appear nowhere outside
`app/core/money.py`. Hours from FTE is one exact `Decimal` multiplication and a **single**
`round_money` at the end (two places, the scale of `NUMERIC(10,2)`), as the capacity does. The
rounded percent is a lossy projection and never an input (addendum 2026-09-19, SC-2-02): the
FTE → hours direction takes the exact `Decimal` FTE, never a percent that was rounded. Consequence
named: hours → FTE → hours is not the identity — 100.00 h over a 168 h basis is 59.52 %, and 59.52 %
converted back would be 99.99 h; a caller must not chain the rounded figure. Options: (A) this
(proposed; needs only the short ADR-0002 addendum of the same date recording that a dimensionless
FTE share is not a fourth quantity class); (B) a new ratio helper with four places in `money.py`
(adds a rounding point to the one allowed module and needs a real ADR-0002 addendum for a new
class); (C) `round_money` on the FTE ratio itself (two places: 0.01 FTE = 1.68 h of 168 — too coarse
for a third of a person).

**(d) Surfacing.** Proposed: **a domain function only in this Story — no new API field, no endpoint,
no frontend change.** Consequences: no public-API row of `architecture-sensitive-paths.md` fires,
and neither do ADR-0005 (permissions), ADR-0009 (write) or ADR-0017 (lists); no contract type and no
shape check changes. The price: the capability can be proven at the domain and data layers only —
nothing reaches a user, which the registry entry must say in its "what this does not prove" — and a
follow-up Story is needed before anyone sees the figure. When that Story surfaces it, four things
attach at once: the fixed-point-string rule for hours-like quantities (ADR-0002 addendum 2026-09-26);
the pseudonymisation question when an FTE or `headcount` per position becomes visible (ADR-0005
addendum SC-3-02, point 11); the personnel-cost gate the moment any monetary figure is derived from it
(ADR-0005 addenda SC-3-03, points 4 and 8 — until then the FTE carries no cost and is not gated);
and the API surface rows above. Option: surface it now as a response field — then all of these
apply in this Story.

| Control | Acceptance criterion |
|---|---|
| FTE-1 | The basis of a position comes from its location's calendar: with a Monday–Saturday pattern and one exceptional day the working-day count follows the data, and no calendar in the tests carries a `standard_hours_per_day` of `8.00` or the code a constant `8` or `weekday() < 5`. |
| FTE-2 | Adding absences or a leave budget to a position does not change its FTE ↔ hours conversion for the same calendar and month. |
| FTE-3 | For a position with `headcount = 3`, 3 × the basis in hours is `3.00` FTE, and `3.00` FTE is 3 × the basis in hours; changing `headcount` with FTE and hours fixed changes neither result. |
| FTE-4 | A month with no working day gives the state `no_working_days` and `"n/a"` in both directions — no exception, no `0`; a location without a calendar gives `no_calendar`; the three states are distinct and a consumer can tell them apart by the state, not by the value. |
| FTE-5 | The FTE share comes from `ratio_percent` and the hours from a single `round_money`; no other rounding call exists in the module; 100.00 h over a 168 h basis is `59.52` and converting the **exact** FTE back gives `100.00` h. |
| FTE-6 | The conversion of an approved scenario uses the snapshot: editing the calendar, its days, the location's calendar or `standard_hours_per_day` after approval changes nothing, and a location with no frozen row gives `no_calendar` rather than a live lookup. |
| FTE-7 | No revenue reader and no hours column reads or receives the conversion's output: changing a calendar's `standard_hours_per_day` changes the conversion but not the T&M revenue of a scenario. |
| FTE-8 | A resolved conversion names its calendar (id and name), `standard_hours_per_day` and working-day count. |
| FTE-9 | No API route, response field or frontend contract is added by this Story. |
