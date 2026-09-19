# ADR-0008 — Przedziały obowiązywania i ich egzekwowanie w bazie

**Status:** Draft — pending approval

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
   (daterange(effective_from, effective_to, '[)')) STORED`**, obok kolumn `effective_from DATE
   NOT NULL` i `effective_to DATE NULL` (`NULL` = bezterminowa, brak wartownika typu
   `9999-12-31`). Kolumna generowana, nie samo wyrażenie powtórzone w każdym zapytaniu: największym
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
