# ADR-0004 — Wersjonowanie i niemutowalność zatwierdzonych kalkulacji

**Status:** Accepted

## Kontekst

F-12: "Approved versions shall be immutable; further changes shall require a new version or
copy." "Reports shall be reproducible using saved inputs, exchange rates, calendars, and
calculation rule versions." AC-04: zmiana domyślnej stawki organizacji nie może zmienić wyniku
już zatwierdzonej kalkulacji. AC-10: regeneracja raportu dla zatwierdzonej wersji po zmianie
domyślnych ustawień musi dać ten sam wynik co oryginał. F-02: "Changes to default settings shall
not automatically modify saved calculations."

## Decyzja

Kalkulacja (scenariusz w konkretnym stanie) ma status `draft` lub `approved`. Zatwierdzenie
(`approved`) jest operacją jednokierunkową wykonywaną przez człowieka (rola: calculation author
lub administrator, F-13) i tworzy **migawkę** (snapshot) — kopiuje w momencie zatwierdzenia
wszystkie wartości, które AC-04/AC-10 wymagają jako odtwarzalne: rozwiązane stawki, kursy walut,
kalendarze robocze, wersję reguł komercyjnych. Migawka jest osobnym zestawem wierszy (nie
referencją do "aktualnych" wartości organizacji) — zapis do tabel `approved_snapshot_*`,
odrzucany na poziomie warstwy dostępu do danych przy próbie modyfikacji (reguła Strażnika
Niezmienników: zapis do zatwierdzonej kalkulacji odrzucany w warstwie dostępu do danych, nie
tylko w UI). Dalsze zmiany wymagają nowej wersji (kopii) scenariusza ze statusem `draft`.

## Konsekwencje

- Raport wygenerowany dla wersji `approved` czyta wyłącznie z migawki, nigdy z żywych tabel
  stawek/kursów/kalendarzy — spełnia AC-10 z definicji, nie przez dodatkową logikę porównawczą.
- Historia zmian (F-12, "change history identifying the author, time, and affected data")
  wymaga osobnej tabeli `audit_log` niezależnej od migawek — migawka mówi *co* było zatwierdzone,
  audit log mówi *kto i kiedy* to zmienił.
- Duplikowanie scenariusza (F-09, AC-02) i tworzenie nowej wersji po zatwierdzeniu (F-12) to ten
  sam mechanizm kopiowania na poziomie danych — jedna funkcja, dwa punkty wejścia.

## Rozważane alternatywy

- **Event sourcing (log zdarzeń, stan odtwarzany przez replay)** — odrzucone na start: NF-03
  (95% przeliczeń < 2s dla 200 pozycji × 36 miesięcy) byłoby trudniejsze do spełnienia z pełnym
  replay przy każdym odczycie zatwierdzonej wersji; migawka daje odczyt O(1) bez replay.
- **Wersjonowanie przez "soft delete" + kolumnę `version` w tej samej tabeli** — odrzucone:
  łatwo o przypadkowy `UPDATE` trafiający też w zatwierdzony wiersz; osobny zestaw tabel
  migawkowych daje twardą granicę egzekwowaną przez uprawnienia bazy, nie tylko konwencję kodu.

## Powiązane wymagania

F-12, F-02 (brak retroaktywnej zmiany), F-09 (duplikacja scenariusza), AC-02, AC-04, AC-10

## Aneksy

### 2026-09-18 — zakres migawki wobec pól Projektu (SC-1-02, edycja)

Sekcja "Decyzja" wylicza, co obejmuje migawka: rozwiązane stawki, kursy walut, kalendarze
robocze, wersję reguł komercyjnych. Ta lista jest wyliczeniem scenariusza i nie obejmowała pól
Projektu, mimo że część z nich jest dziedziczona przez kalkulację i widoczna w raporcie —
`reporting_currency`, `delivery_period_start`/`delivery_period_end` — a inne są nagłówkiem
raportu bez wpływu na wyliczenie: `name`, `client`, `owner`, `description`.

**Rozstrzygnięcie — pola Projektu dzielą się na dwie grupy:**

1. **Pola opisowe** (`name`, `client`, `owner`, `description`) — edytowalne niezależnie od
   statusu scenariuszy. Zatwierdzona wersja pokazuje ich wartość **bieżącą**, nie historyczną —
   nie wchodzą do migawki, bo nie wchodzą do żadnego wyliczenia. To jawne, świadome ograniczenie
   odtwarzalności nagłówka raportu, nie przeoczenie.
2. **Pola wchodzące do wyliczenia lub jego granic** (`reporting_currency`,
   `delivery_period_start`, `delivery_period_end`) — gdy projekt ma choć jeden scenariusz
   `approved`, edycja tych pól jest odrzucana w warstwie dostępu do danych, tą samą regułą i w
   tym samym miejscu co zapis do zatwierdzonej kalkulacji. Analogia do ADR-0006: wartość
   dziedziczona przez zatwierdzoną kalkulację przestaje być polem projektu, a staje się częścią
   tej kalkulacji.

**Konsekwencja przyjęta razem z tym aneksem:** nowe pole Projektu wymaga przypisania do jednej z
dwóch grup w chwili dodania. Pole dodane bez przypisania wpada domyślnie do grupy 1 — jeśli
kiedyś wejdzie do wyliczenia, będzie to cicha regresja AC-10, nie błąd widoczny w testach.

**Rozważane i odrzucone:** zamrożenie wszystkich pól Projektu po zatwierdzeniu (za drogie dla
literówek w nazwie klienta) i rozszerzenie migawki o pola prezentacyjne (rozdyma migawkę i każe
zatwierdzonemu raportowi pokazywać nazwę, której nikt już nie rozpozna).

### 2026-09-18 — kopiowanie Projektu jako trzeci punkt wejścia mechanizmu kopiowania (SC-1-03)

Sekcja "Konsekwencje" nazywa dwa punkty wejścia jednej funkcji kopiującej: duplikowanie
scenariusza (F-09, AC-02) i tworzenie nowej wersji po zatwierdzeniu (F-12). Kopiowanie Projektu
(F-01, SC-1-03) jest trzecim.

**Rozstrzygnięcie:**

1. To ten sam mechanizm, trzeci punkt wejścia — nie druga funkcja kopiująca. Kopiowanie Projektu
   kopiuje wiersz projektu i zleca kopiowanie scenariuszy tej samej funkcji, której używa
   duplikowanie pojedynczego scenariusza.
2. **Zakres:** kopiowane są wszystkie scenariusze projektu, każdy ze statusem `draft` na kopii —
   zgodnie z regułą "dalsze zmiany wymagają nowej wersji (kopii) scenariusza ze statusem draft".
3. **Migawka nie jest kopiowana.** Wiersze `approved_snapshot_*` należą do zatwierdzenia, a
   zatwierdzenie jest operacją jednokierunkową wykonywaną przez człowieka — kopia nie przeszła
   tej operacji. **Konsekwencja wprost wobec F-12: kopia zatwierdzonej kalkulacji nie jest
   odtwarzalna tak jak oryginał** — odtwarzalność zostaje przy oryginale, kopia startuje jako
   draft i odzyskuje odtwarzalność dopiero przy własnym zatwierdzeniu.
4. **Kopiowanie jest kaskadą definiowaną w warstwie danych, nie listą pól przepisywaną w
   kolejnych zadaniach.** Każda nowa tabela-dziecko scenariusza (staffing, koszty, stawki, reguły
   komercyjne — ADR-0003) musi wejść do kaskady w tym samym zadaniu, w którym powstaje. Tabela
   pominięta w kopiowaniu nie wywołuje błędu — daje kopię ze współdzieloną referencją do danych
   źródła, czyli dokładnie to, czego zabrania AC-02.

**Rozważane i odrzucone:** kopiowanie wyłącznie powłoki Projektu (nie oszczędza pracy tam, gdzie
boli); odmowa kopiowania projektu z `approved` scenariuszem (uniemożliwia najczęstszy przypadek
użycia — wariant zatwierdzonej oferty).

### 2026-09-18 — archiwizacja Projektu a niezmienność i odtwarzalność (SC-1-04)

Archiwizacja Projektu (F-01, SC-1-04) bywa mylona z dwiema rzeczami, którymi nie jest.

1. **Nie jest odrzuconym wariantem "soft delete".** Sekcja "Rozważane alternatywy" odrzuca soft
   delete z kolumną `version` jako mechanizm wersjonowania kalkulacji. `status` Projektu
   (`active`/`archived`) nie jest mechanizmem wersjonowania i nie wchodzi z tym w sprzeczność.
2. **Nie narusza odtwarzalności (F-12, AC-10)** i nie wymaga do tego nowej logiki: raport wersji
   `approved` czyta wyłącznie z migawki. Jedyny warunek: **archiwizacja nie usuwa i nie ukrywa
   żadnego wiersza** — ani projektu, ani scenariusza, ani migawki. Zarchiwizowany projekt zostaje
   widoczny, oznaczony (już dowiedzione dla odczytu, SC-1-05).
3. **Zakres zamrożenia: archiwizacja jest stanem widoczności, nie granicą niezmienności.** Nie
   zmienia tego, co wolno zapisać w scenariuszach projektu. Jedyną granicą niezmienności w tym
   ADR pozostaje status `approved` kalkulacji — dwa niezależne mechanizmy niezmienności w jednym
   miejscu kosztowałyby więcej niż dają.
4. **Odwracalność:** F-01 wymienia "archive", nie wymienia odarchiwizowania. Odarchiwizowanie
   jest poza zakresem SC-1-04 i wymaga własnego zadania — do tego czasu stan jest jednokierunkowy,
   i musi być tak przedstawiony użytkownikowi, bo stan jednokierunkowy nazwany "archiwizacją" jest
   odczytywany jako usunięcie.

### 2026-09-18 — historia zmian (F-12) odłożona dla SC-1-02..04

`audit_log` (patrz sekcja "Konsekwencje") nie istnieje jeszcze w schemacie. SC-1-02, SC-1-03 i
SC-1-04 są pierwszymi trzema akcjami generującymi zdarzenia, których F-12 wymaga w historii.
**Jawne, datowane odstępstwo:** tabela `audit_log` i zapis do niej odłożone do bloku 8 planu
(historia i odtwarzalność). Do tego czasu edycje, kopie i archiwizacje wykonane w blokach 1–7 nie
będą miały odtwarzalnego autora ani czasu w rejestrze historii — `updated_at` na wierszu nie jest
substytutem historii zmian (brak autora, brak "affected data"). **Warunek zamknięcia:** blok 8
planu, przed jakimkolwiek zadaniem opierającym się na F-12 jako spełnionym w całości.
