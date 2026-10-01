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

### 2026-09-19 — trzy konsekwencje SC-1-02..04 zaakceptowane, nie naprawione (weryfikacja SC-1-02..04)

Weryfikacja gate 2 dla SC-1-02..04 (reviewer, security-auditor) wykryła trzy efekty uboczne
mechanizmów już zaakceptowanych w tym ADR. Żaden nie jest błędem implementacji — wszystkie są
konsekwencją decyzji podjętych wyżej, dotąd nienazwaną wprost. Nazwane teraz, żeby nie zostały
odkryte przypadkiem przez kolejne zadanie.

1. **Archiwizacja unieważnia token współbieżności ADR-0007 każdego równoległego edytora.**
   `archive_project` pisze wyłącznie `status`, ale kolumna `updated_at` — czyli token ADR-0007 —
   rusza przez `onupdate` niezależnie od tego, które pole faktycznie się zmieniło. Caller A czyta
   projekt (token T0), caller B go archiwizuje (token → T1), A zapisuje edycję pól opisowych z T0
   i dostaje 409 "projekt się zmienił od odczytu" — komunikat prawdziwy, ale wskazujący złą
   przyczynę: A nie edytował niczego, co B dotknął. **Zaakceptowane:** token jest własnością
   wiersza Projektu, nie pojedynczego pola, i to samo dotyczyłoby każdej przyszłej akcji piszącej
   `status` lub inne pole poza `EDITABLE_FIELDS`. Rozdzielenie tokenów per grupa pól
   kosztowałoby więcej, niż dają rzadkie kolizje archiwizacja-kontra-edycja. Warunek: komunikat
   409 nie może nazywać przyczyny, której nie potwierdził (dziś nie nazywa — mówi tylko "zmienił
   się", nie "ktoś edytował").
2. **Kopia nie jest powiązana z projektem źródłowym i przetrwa odebranie dostępu do źródła.**
   Aneks z 2026-09-18 ("kopiowanie Projektu…") nazywa już, że kopia zespołowego projektu jest
   początkowo niewidoczna dla zespołu — to część tej samej decyzji o nie replikowaniu
   `project_access`. Dwie konsekwencje tego, nienazwane wcześniej: (a) odebranie dostępu do
   źródła nie ma żadnego wpływu na kopię — kopia nie przechowuje odniesienia do źródła, więc nie
   ma nic do unieważnienia; (b) nikt poza autorem kopii nie może się o niej dowiedzieć ani jej
   odnaleźć, włącznie z administratorem źródłowego projektu. **Zaakceptowane** na tych samych
   warunkach co aneks z 2026-09-18: F-12 i F-01 nie wymagają rejestru pochodzenia kopii, a
   `audit_log` jest już odłożone do bloku 8 (aneks powyżej). Kolumna łącząca kopię ze źródłem
   (`copied_from`) jest naturalnym miejscem do tego wrócić, jeśli blok 8 albo przyszłe zadanie
   dotyczące ról/uprawnień (ADR-0005) tego zażąda — nie jest potrzebna wcześniej.
3. **Kopiowanie nie jest idempotentne, a duplikat jest trwały.** `POST /projects/{id}/copy` nie
   przyjmuje ciała żądania, więc dwa wywołania (podwójny klik, retry sieciowy) są nie do
   odróżnienia i tworzą dwa osobne projekty o identycznych polach opisowych. Żadna kolumna
   `projects` nie ma unikalności na `name` (w przeciwieństwie do `scenarios`), a w tym planie nie
   istnieje akcja usuwania — archiwizacja jest stanem widoczności, nie usunięciem (aneks z
   2026-09-18, punkt 2) — więc przypadkowy duplikat zostaje na zawsze. **Zaakceptowane:** koszt
   klucza idempotentności lub odróżniającego sufiksu nazwy przewyższa dziś ryzyko — akcja wymaga
   świadomego kliknięcia, a duplikat jest widoczny i nieszkodliwy (nie wpływa na kalkulacje innych
   projektów). Warunek zamknięcia: jeśli frontend wprowadzi automatyczny retry na tym endpointzie
   (np. w ramach ogólnego mechanizmu ponawiania żądań), warunek znika i idempotency-key przestaje
   być opcjonalny.

### 2026-09-19 — katalog organizacyjny nie jest dzieckiem scenariusza (SC-2-01)

Aneks z 2026-09-18 ("kopiowanie Projektu jako trzeci punkt wejścia") pkt 4 wymienia "stawki" wśród
tabel-dzieci scenariusza, które muszą wejść do kaskady kopiowania w tym samym zadaniu, w którym
powstają. Katalog wymiarów roli i stawek domyślnych (SC-2-01, F-03) **nie** jest taką tabelą:
jego wiersze należą do organizacji, nie do scenariusza ani projektu (ADR-0005, aneks 2026-09-19).
Kopiowanie projektu nie kopiuje katalogu firmy i nie rejestruje go w `SCENARIO_CHILD_COPIERS` —
brak wpisu jest tu poprawnością, nie pominięciem. Zobowiązanie z pkt 4 pozostaje w mocy dla
przyszłych tabel *nadpisań* stawek na poziomie scenariusza, jeśli takie powstaną, oraz dla
migawki: zatwierdzenie kopiuje "rozwiązane stawki" jako wartości, nie jako referencję do
katalogu (Decyzja, AC-04/AC-10 — zmiana stawki domyślnej nigdy nie rusza zatwierdzonej kalkulacji).

### 2026-09-19 — pozycja obsady i alokacja nie wchodzą do migawki (SC-3-01)

Sekcja "Decyzja" wylicza zakres migawki: rozwiązane stawki, kursy walut, kalendarze robocze,
wersję reguł komercyjnych. Wszystkie cztery są wartościami **dziedziczonymi spoza scenariusza** —
mogą zmienić się w świecie zewnętrznym po zatwierdzeniu, i migawka broni odtwarzalności przed tą
zmianą (AC-04, AC-10). Pozycja obsady i jej alokacja miesięczna (F-04, SC-3-01) są pierwszymi
danymi, które do tego wyliczenia wchodzą, a dziedziczone nie są.

**Rozstrzygnięcie — kryterium migawki jest kierunkiem dziedziczenia, nie udziałem w wyliczeniu:**

1. **Wartość dziedziczona spoza scenariusza → migawka.** Bez zmian wobec "Decyzji".
2. **Dane własne scenariusza → strażnik zapisu, nie migawka.** Pozycja obsady i alokacja należą do
   scenariusza; nic spoza scenariusza nie może ich zmienić, więc nie ma czego zamrażać kopią.
   Chroni je odmowa zapisu do scenariusza `approved`, egzekwowana w warstwie dostępu do danych
   ("Decyzja", reguła 7 Strażnika Niezmienników). Migawka pozycji byłaby drugą kopią tych samych
   wierszy, rozstrzygającą to samo pytanie dwa razy — i pierwszym miejscem, w którym dwie kopie
   mogłyby się rozjechać.
3. **Zawężenie zdania z "Konsekwencji".** "Raport wygenerowany dla wersji `approved` czyta wyłącznie
   z migawki" dotyczy **wartości dziedziczonych** (stawki, kursy, kalendarze, wersja reguł) — tam
   wyliczenie tego zdania jest zamknięte. Własne dane wejściowe scenariusza raport czyta z tabel
   scenariusza. To zawężenie zdania, nie zmiana mechanizmu: przed tym aneksem nie istniała żadna
   tabela własna scenariusza, więc zdanie nie miało innego zakresu.
4. **Obowiązek przy każdej nowej tabeli-dziecku scenariusza** (wzorem obowiązku przypisania pola
   Projektu do jednej z dwóch grup, aneks 2026-09-18): tabela powstająca po tym aneksie musi w
   chwili powstania dostać przypisanie "dziedziczona → migawka" albo "własna → strażnik zapisu".
   Tabela dodana bez przypisania wpada domyślnie do grupy 2 i nie dostaje żadnej ochrony poza tą,
   którą strażnik faktycznie egzekwuje — czyli cichą regresję AC-10, jeśli strażnik jej nie obejmie.

**Konsekwencja przyjęta razem z tym aneksem, i warunek, na którym jest przyjęta.** Odtwarzalność
własnych danych wejściowych zatwierdzonego scenariusza opiera się od teraz na mechanizmie
behawioralnym (każda ścieżka zapisu przechodzi przez strażnika), nie strukturalnym (raport czyta
zamrożoną kopię). Sekcja "Rozważane alternatywy" odrzuciła soft delete właśnie argumentem, że
"osobny zestaw tabel migawkowych daje twardą granicę egzekwowaną przez uprawnienia bazy, nie tylko
konwencję kodu" — i ten argument zostaje w mocy jako **warunek**, nie jako sprzeciw: brakujący
wiersz migawki jest widoczny, pominięty strażnik nie jest. Warunek: odmowa liczona przez bazę w tej
samej instrukcji co zapis (nigdy check-then-act — mutacja tej klasy przeżyła dostarczone testy w
tym repo trzy razy: SC-1-02 ×2, SC-2-01), plus obowiązkowy test odmowy i test wyścigu **per
ścieżka zapisu**, nie per zadanie.

**Nie dotyczy tego aneksu:** przedziały obowiązywania. Wzorzec `EXCLUDE USING gist` (ADR-0008) nie
rozciąga się na okres pozycji obsady — nakładające się pozycje tej samej roli w tym samym okresie
są legalne (PM planuje różne osoby na tej samej roli w różnym czasie), a reguła 13 Strażnika
Niezmienników mówi o wyszukiwaniu **stawki**, nie o okresie pozycji. Jedyną unikalnością jest
`UNIQUE (position_id, period_month)` na alokacji. Brak `EXCLUDE` na `staffing_position` jest
poprawnością, nie pominięciem — zapisane tu z tego samego powodu, z którego zapisano brak wpisu
katalogu w `SCENARIO_CHILD_COPIERS` (aneks 2026-09-19).

### 2026-09-19 — kaskada kopiowania dla tabeli-wnuka i strażnik zapisu dla INSERT-a (SC-3-01)

Aneks 2026-09-18 pkt 4 zobowiązuje każdą nową tabelę-dziecko scenariusza do wejścia do
`SCENARIO_CHILD_COPIERS` w tym samym zadaniu, w którym powstaje. Kontrakt rejestru
(`ScenarioChildCopier = Callable[[Session, Scenario, Scenario], None]`) niesie tylko scenariusz
źródłowy i kopię — a alokacja miesięczna (SC-3-01) jest **wnuczką** scenariusza: wskazuje na
`position_id`, którego wartość jest nowa na kopii, i osobno zarejestrowany kopiujący dla samej
alokacji nie miałby skąd wziąć tego mapowania.

**Rozstrzygnięcie (bramka 1, SC-3-01):**

1. **Jeden kopiujący na cały agregat, nie jeden na tabelę.** Wpis w `SCENARIO_CHILD_COPIERS`
   odpowiadający za pozycje obsady kopiuje w tej samej funkcji także ich wiersze miesięczne,
   trzymając mapowanie starych→nowych identyfikatorów pozycji lokalnie, w zamknięciu funkcji.
   Kontrakt `ScenarioChildCopier` zostaje bez zmian — nie jest to zmiana zaakceptowanego,
   mutation-checked szwu, tylko jego naturalne rozszerzenie: `copy_scenario` już robi `flush()`
   między zapisem kopii a wywołaniem kopiujących właśnie po to, żeby kopia miała gotowy wiersz,
   do którego dziecko może wskazać — kopiujący agregatu robi to samo jeszcze raz, jeden poziom
   głębiej, w tej samej funkcji.
   **Konsekwencja nazwana, nie odkryta później:** zdanie "jeden wpis na tabelę" w dotychczasowej
   dokumentacji tego rejestru przestaje być dosłownie prawdziwe — jest "jeden wpis na agregat,
   którego korzeń jest dzieckiem scenariusza". Test kompletności rejestru, jeśli powstanie, musi
   to rozróżnienie znać, inaczej kolejna tabela-wnuczka wygląda na zarejestrowaną, choć nie jest.
2. **Strażnik zapisu do `approved` dla INSERT-a: predykat w tej samej instrukcji, wyścig z
   zatwierdzeniem odłożony jawnie.** Wzorzec `UPDATE ... WHERE NOT _approved_scenario_exists(...)`
   nie przenosi się bez zmian na `INSERT` — instrukcja wstawiająca nie ma `WHERE`, na którym można
   zawiesić predykat o stanie rodzica. Rozstrzygnięcie: `INSERT ... SELECT ... FROM scenarios WHERE
   id = :scenario_id AND status <> 'approved'` (odmowa = zero wierszy afektowanych, diagnoza
   dopiero po odmowie — ten sam podział ról co `_diagnose_refusal`). **Wyścig, w którym
   zatwierdzenie scenariusza commituje się między odczytem statusu a wstawieniem dziecka, nie jest
   tym aneksem domykany** — pod `READ COMMITTED` odczyt rodzica nie blokuje go, więc zatwierdzenie
   i wstawienie mogą obie się powiedzieć. **Warunek zamknięcia, datowany:** nic w działającym
   systemie nie ustawia dziś `ScenarioStatus.APPROVED` — pierwsza prawdziwa ścieżka zatwierdzenia
   scenariusza musi rozstrzygnąć ten wyścig (blokada rodzica w tej samej instrukcji, wyzwalacz w
   bazie, albo inny mechanizm) i domknąć go dla WSZYSTKICH tabel-dzieci scenariusza naraz, nie
   tylko dla tej, która akurat wtedy powstaje. Do tego czasu ryzyko jest przyjęte świadomie i
   niewykonalne do przetestowania (nic nie umie dziś wywołać zatwierdzenia, więc nie ma czym
   wywołać wyścigu) — nazwane, nie zamilczane.

### 2026-09-22 — pierwsza tabela migawkowa; trzecia grupa tabel-dzieci scenariusza (SC-3-02)

Sekcja "Decyzja" wylicza "kalendarze robocze" wśród treści migawki, a aneks z 2026-09-19 (SC-3-01)
ustala kryterium przynależności ("kierunek dziedziczenia, nie udział w wyliczeniu"). SC-3-02 jest
pierwszym zadaniem, które faktycznie buduje tabelę `approved_snapshot_*` — do dziś nazwa ta pada w
całym backendzie dokładnie raz, w docstringu `copy_scenario`. Wzorzec ustanawiany tu obowiązuje
kursy walut (ADR-0006), rozwiązane stawki i wersje reguł komercyjnych (ADR-0003), nie tylko to
zadanie.

1. **Przypisanie grup dla tabel SC-3-02** (obowiązek z aneksu 2026-09-19, pkt 4):
   - `working_calendar`, `working_calendar_day`, `absence_type` — wartości dziedziczone spoza
     scenariusza, **grupa 1: migawka**. Nie są dziećmi scenariusza, więc brak wpisu w
     `SCENARIO_CHILD_COPIERS` jest tu poprawnością, nie pominięciem (precedens: katalog, aneks
     2026-09-19).
   - `staffing_position_absence` — dane własne scenariusza, wnuczka przez `staffing_position`,
     **grupa 2: strażnik zapisu**. Wchodzi do kaskady kopiowania przez rozszerzenie istniejącego
     kopiującego agregatu, nie przez nowy wpis w rejestrze (pkt 1 aneksu 2026-09-19: "jeden wpis na
     agregat, którego korzeń jest dzieckiem scenariusza") — kontrakt rejestru nie niesie mapowania
     starych na nowe identyfikatory pozycji, a wiersz nieobecności go potrzebuje.
   - **Instancje nieobecności nie wchodzą do migawki**, i jest to zastosowanie kryterium z
     2026-09-19, nie wyjątek od niego: nic spoza scenariusza nie może ich zmienić. Do migawki
     wchodzi **słownik typów** nieobecności (nazwa i flagi kosztowe/przychodowe są organizacyjne i
     mogą się zmienić po zatwierdzeniu), nie instancje. *Warunek ponownego rozpatrzenia:* gdyby
     nieobecność przeniosła się kiedyś na poziom osoby albo rejestru organizacyjnego (Issue #31),
     zmienia grupę z 2 na 1 i migawka musi o nią urosnąć.

2. **Trzecia grupa, której taksonomia dwugrupowa nie miała.** Tabela `approved_snapshot_*` sama
   jest dzieckiem scenariusza, a nie należy ani do grupy 1, ani do 2: jest **zapisywalna
   jednokrotnie, przy zatwierdzeniu, i nigdy nie kopiowana** (aneks 2026-09-18, pkt 3). Brak wpisu
   w `SCENARIO_CHILD_COPIERS` jest tu **wymagany**, nie dozwolony — a ponieważ rejestr milczy o
   pominięciach, wymaga testu-kanarka: kopia zatwierdzonego scenariusza ma zero wierszy migawkowych.

3. **Kształt tabeli migawkowej — wzorzec, nie szczegół tego zadania:**
   a. **Jedna tabela migawkowa na jedną tabelę źródłową**, nazwana `approved_snapshot_<tabela>` —
      nie jedna generyczna tabela z kolumną `jsonb`. Podstawa: ADR-0001 (integralność egzekwowana
      w bazie) i ADR-0002/NF-01 — liczba w JSON jest typem zmiennoprzecinkowym, a migawka niesie
      godziny, czyli wartości o jedno mnożenie od pieniędzy. Zapisane wprost, bo generyczny blob
      jest skrótem, po który sięgnie następny implementator.
   b. **Kluczowana przez `scenario_id`; identyfikator wiersza źródłowego przechowywany jako
      wartość** (zwykła kolumna `uuid`), nigdy jako klucz obcy. "Migawka jest osobnym zestawem
      wierszy (nie referencją do »aktualnych« wartości organizacji)" — klucz obcy jest referencją i
      pozwoliłby źródłu zablokować albo kaskadowo ruszyć zamrożoną kopię.
   c. **Kopiowane są wartości, nie nazwy do rozwiązania później:** nazwa kalendarza, podstawa
      godzinowa, wzorzec tygodnia, komplet wierszy dni, nazwa i flagi typu nieobecności.
   d. **Kryterium obowiązkowe, wprost z AC-04/AC-10:** edycja kalendarza źródłowego po zatwierdzeniu
      nie zmienia ani jednej wartości w migawce. Mutacja "zapisz identyfikator jako klucz obcy i
      czytaj przez złączenie" ma wywracać test.

4. **Moment zapisu i kolejność w transakcji zatwierdzenia.** Zatwierdzenie jest jedną transakcją, w
   kolejności: najpierw wiersze migawki, **na końcu** `UPDATE scenarios SET status = 'approved'
   WHERE id = :id AND status = 'draft'` (odmowa = zero wierszy = ktoś zatwierdził wcześniej). Ta
   kolejność jest konieczna: strażniki tabel-dzieci (`status <> 'approved'`) odrzuciłyby zapis
   samej migawki, gdyby status szedł pierwszy. Żaden nowy kształt strażnika nie jest potrzebny.

5. **Rozstrzygnięcie bramki 1 (2026-09-22, P-2): SC-3-02 buduje realny endpoint zatwierdzenia,
   nie tylko funkcję warstwy danych.** Wariant szerszy niż rekomendacja architekta, przyjęty
   świadomie. Wyścig z pkt 269 wyżej ("Warunek zamknięcia, datowany") przestaje być
   niewykonalny do przetestowania od tego zadania — musi zostać rozstrzygnięty i dowiedziony dla
   WSZYSTKICH tabel-dzieci naraz (pozycji, alokacji, nieobecności), nie tylko dla tabel tego
   zadania. **Ryzyko przyjęte świadomie, nie przemilczane:** w środowisku `development`/`test`
   (placeholder identity, brak ADR uwierzytelniania) każdy wołający dotrze do tego endpointu i
   nieodwracalnie zamrozi kalkulację — bez roli, bez audytu (blok 8 odłożony, `audit_log`
   nieistniejący). Zamknięcie: osobny ADR uwierzytelniania (dla roli) + blok 8 planu (dla
   `audit_log`).

### 2026-09-22 — budżet urlopowy wchodzi do migawki RAZEM z kalendarzem (SC-3-03)

Drugi aneks tej daty w tym pliku i osobny wpis, nie dopisek do poprzedniego: punkty aneksu SC-3-02
są cytowane **po numerach** w `backend/app/models/approved_snapshot.py`,
`backend/app/data/scenario_approval.py`, `backend/app/data/scenario_guard.py` i
`backend/app/models/staffing.py`, więc dopisanie punktu w tamtej sekcji jest cichą edycją tekstu, do
którego kod się odwołuje. **Konsekwencja nazewnicza przyjęta razem z tym wpisem:** odwołanie
„ADR-0004, aneks 2026-09-22" bez nazwy zadania znaczy aneks **SC-3-02**; każde nowe odwołanie musi
nazwać zadanie.

Aneks z 2026-09-19 (SC-3-01) pkt 4 zobowiązuje każdą nową tabelę do otrzymania przypisania
„dziedziczona → migawka" albo „własna → strażnik zapisu" **w chwili powstania**, a tabela dodana bez
przypisania wpada domyślnie do grupy 2. To wpis, którego to zobowiązanie wymaga dla budżetu
urlopowego (SC-3-03, F-05).

1. **Przypisanie grupy: budżet urlopowy to wartość dziedziczona spoza scenariusza — grupa 1,
   migawka.** Kryterium z aneksu 2026-09-19 („kierunek dziedziczenia, nie udział w wyliczeniu")
   spełnione wprost: wiersz budżetu należy do organizacji, nie do scenariusza, i może zostać
   zmieniony po zatwierdzeniu przez kogoś, kto nigdy nie słyszał o tej kalkulacji — dokładnie klasa
   wartości, przed którą bronią AC-04 i AC-10. Budżet nie jest dzieckiem scenariusza, więc brak
   wpisu w `SCENARIO_CHILD_COPIERS` jest tu poprawnością, nie pominięciem (precedens: katalog, aneks
   2026-09-19 SC-2-01; kalendarz, aneks 2026-09-22 SC-3-02 pkt 1).
2. **Budżet wchodzi do migawki RAZEM z kalendarzem, nie osobno** (rozstrzygnięcie bramki 1,
   2026-09-22, Q-1). Para „kalendarz + budżet" jest w migawce atomowa: zatwierdzenie, które
   zamroziło kalendarze, a nie zamroziło budżetów, do których te kalendarze prowadzą, jest
   zatwierdzeniem niekompletnym, nie zatwierdzeniem uboższym.
   **Cicha regresja AC-10, której ta decyzja unika, nazwana wprost:** migawka z kalendarzem bez
   budżetu odtworzyłaby **inną zdolność rozliczalną** niż zatwierdzona kalkulacja. Dni robocze
   pochodziłyby z zamrożonego kalendarza, a odjęte od nich dni urlopu — z żywej tabeli budżetu, więc
   zmiana regulaminu urlopowego po zatwierdzeniu przesuwałaby wynik zatwierdzonej oferty bez jednego
   komunikatu i bez jednej zmiany w wierszach, które ktokolwiek ogląda. To jest ten sam kształt
   błędu, który pkt 3d aneksu SC-3-02 każe zabijać kryterium („edycja kalendarza źródłowego po
   zatwierdzeniu nie zmienia ani jednej wartości w migawce") — z tą różnicą, że tutaj obejście nie
   wymaga żadnej mutacji w kodzie, wystarczy **nie dodać** drugiej tabeli.
3. **Zakres kopiowania: to, co kalkulacja tego scenariusza faktycznie czyta.** Te same kalendarze,
   które zamraża `app.data.scenario_approval._copy_calendars`, skrzyżowane z typami zaangażowania
   pozycji obsady: budżety par (kalendarz, typ zaangażowania) wynikających z pozycji scenariusza,
   nie cała tabela budżetów. Migawka wierszy, na które nie wskazuje żadna pozycja, rośnie razem z
   organizacją zamiast razem z kalkulacją i nie zmienia żadnej odpowiedzi. Scenariusz bez pozycji
   zamraża zero budżetów — to jest uczciwy stan draftu, w którym nic nie zaplanowano.
4. **Kształt tabeli migawkowej: wzorzec z pkt 3 aneksu SC-3-02 bez żadnej zmiany.** Jedna tabela
   migawkowa na jedną tabelę źródłową, nazwana `approved_snapshot_<tabela>`, nigdy generyczny blob
   `jsonb` (liczba w JSON jest typem zmiennoprzecinkowym, a budżet mnoży się przez podstawę godzinową
   i przez stawkę — ADR-0002, NF-01); kluczowana przez `scenario_id`; identyfikatory wierszy
   źródłowych przechowywane **jako wartości**, nigdy jako klucze obce; kopiowane wartości, a nie
   nazwy do rozwiązania później. **Trzecia grupa obowiązuje:** nowa tabela dochodzi do
   `SNAPSHOT_TABLES`, **nie** dochodzi do `SCENARIO_CHILD_COPIERS` (brak wpisu jest tu wymagany, nie
   dozwolony), a kanarek „kopia zatwierdzonego scenariusza ma zero wierszy migawkowych" musi objąć
   ją tak samo jak trzy istniejące — rejestr milczy o pominięciach, więc tabela dopisana bez kanarka
   wygląda na objętą, choć nie jest.
5. **Moment zapisu i kolejność w transakcji: bez zmian, żaden nowy strażnik.** Wiersze budżetu
   wchodzą do tej samej transakcji zatwierdzenia, przed `UPDATE scenarios SET status = 'approved'`
   (pkt 4 aneksu SC-3-02), a ich `scenario_id` pochodzi z `unapproved_scenario(...)` wewnątrz
   `INSERT ... SELECT`, nie z parametru — to właśnie czyni nakazaną kolejność własnością instrukcji,
   a nie komentarzem. Strażnik `app.data.scenario_guard` jest jeden dla wszystkich tabel-dzieci i nie
   zyskuje tu drugiego kształtu.
6. **Kanarek odróżniający dwa zera.** Zero wierszy budżetu w migawce znaczy dwie zupełnie różne
   rzeczy: „w źródle nie było wiersza budżetu dla tej pary" — stan nazwany i legalny (ADR-0008, aneks
   z tą samą datą, SC-3-03, pkt 7) — albo „budżet istniał i nie został skopiowany", czyli regresja z
   pkt 2. Same liczniki `ApprovalResult` tych dwóch przypadków nie rozróżniają, więc dowód wymaga
   kontrastu: zatwierdzenie scenariusza, którego pozycje mają budżet, zamraża dokładnie tyle wierszy,
   ile par czyta, a edycja wiersza źródłowego po zatwierdzeniu nie rusza ani jednej liczby w migawce.
7. **Migawka rozstrzyga okno budżetu przez miesiące, które scenariusz faktycznie planuje — nie
   przez dzień zatwierdzenia.** *(Poprawka wprowadzona 2026-09-22 przy weryfikacji tego samego
   zadania, zastępująca pierwotne rozstrzygnięcie tego punktu — patrz „Historia tego punktu" niżej;
   reguła 18 Strażnika nie ma tu zastosowania, bo poprawka zapada przed pierwszym scaleniem tego
   aneksu, nie po nim.)* Budżet jest — inaczej niż nazwa kalendarza i flagi typu nieobecności —
   wartością z **przedziałem obowiązywania** (ADR-0008, aneks SC-3-03), więc „kopiuj wartość, nie
   nazwę do rozwiązania później" (pkt 3c aneksu SC-3-02) miało tu dwa odczyty. Pierwotny wybór
   („jedna liczba na parę, rozstrzygnięta przez `valid_period @> CURRENT_DATE`") okazał się błędny
   w przeglądzie: zatwierdzenie scenariusza planowanego na okres inny niż ten, w którym leży dzień
   kliknięcia „zatwierdź", zamrażało uprawnienie okna **nieużywanego przez ani jeden miesiąc tego
   scenariusza** — regresja tej samej klasy co ta, przed którą broni pkt 2 tego aneksu, tylko że tu
   żadna mutacja kodu nie jest potrzebna, wystarczy zwykłe użycie (scenariusz na przyszły rok, budżet
   przyszłego roku już wprowadzony do katalogu). Nienaprawialne po fakcie: migawka nie ma ścieżki
   UPDATE/DELETE.

   **Rozstrzygnięcie poprawione:** okno(-a) budżetu rozstrzyga się tym samym predykatem co ścieżka
   żywa (`valid_period @> okres_miesiąca`, `app.data.absence_budget.budgets_for_months`), przeciwko
   **zbiorowi miesięcy, które pozycje obsady scenariusza faktycznie planują**
   (`staffing_position_allocation.period_month`) — nie przeciwko jednemu dniu.
   a. **Zamrażane są WSZYSTKIE okna, których dotykają te miesiące, nie jedno.** Scenariusz planujący
      przejście przez zmianę regulaminu urlopowego czyta dwa uprawnienia na żywej ścieżce (jedno na
      miesiące przed zmianą, drugie po) — zamrożenie tylko jednego zafałszowałoby połowę miesięcy
      planu. Klucz odczytu migawki rośnie o okno: `(scenario_id, source_calendar_id,
      source_engagement_type_id, effective_from)`, unikalny dzięki temu, że `EXCLUDE` na tabeli
      źródłowej nie dopuszcza dwóch okien pokrywających jeden miesiąc dla tej samej pary.
   b. **Dzień rozstrzygnięcia (`resolved_on`) znika — atrybucją wiersza jest okno, które niesie.**
      Punkt a) pierwotnej wersji tego rozstrzygnięcia (kolumna z dniem zatwierdzenia) tracił sens
      razem z jednym rozstrzygnięciem na parę: przy wielu zamrożonych oknach per para nie ma już
      jednego „dnia, na który rozstrzygnięto" do zapisania — każdy wiersz migawki sam mówi, do
      którego okna źródłowego należy (`effective_from`/`effective_to`), a to wystarcza.
   c. **Czytelnik migawki rozstrzyga PER MIESIĄC, tym samym predykatem co ścieżka żywa.** To jest
      świadomie węższa wersja wariantu „komplet okien w migawce", odrzuconego w pierwotnej wersji
      tego punktu z tym samym uzasadnieniem (przenosi mechanizm rozstrzygania do czytelnika, musi
      być tam dowiedziony drugi raz — reguła 13 Strażnika, druga połowa) — zaakceptowana teraz, bo
      alternatywa (jedno rozstrzygnięcie na dzień zatwierdzenia) okazała się nie mechanizmem
      uproszczonym, tylko mechanizmem błędnym. Warunek: pierwszy czytelnik migawki (blok 8) musi
      dowieść tego rozstrzygania własnym kryterium, nie założyć go po cichu.
   d. **Scenariusz z pozycjami, ale bez wierszy miesięcy (alokacji), zamraża zero budżetów.** Nic nie
      czyta — ten sam uczciwy stan draftu bez planu co w pkt 3 tego aneksu.
   e. **Konsekwencja przyjęta razem z tą poprawką, nie odkryta później:** scenariusz, którego okres
      dostawy sięga za koniec ostatniego zamrożonego okna (bo w chwili zatwierdzenia katalog nie miał
      jeszcze budżetu na tę część okresu), zostaje bez wiersza budżetu dla tamtych miesięcy —
      nazwany stan „brak budżetu" (ADR-0008 aneks SC-3-03 pkt 7), nie cicha ekstrapolacja ostatniego
      znanego okna. To jest świadome ograniczenie odtwarzalności „w przód", tej samej klasy co pola
      opisowe Projektu pokazywane w wartości bieżącej (aneks 2026-09-18, grupa 1).

   **Historia tego punktu (zapisana, nie usunięta — reguła 18 Strażnika: ADR nie prowadzi własnego
   śledzenia statusu, ale też nie kasuje decyzji bez śladu).** Pierwotna wersja tego punktu (jedna
   liczba na parę, `valid_period @> CURRENT_DATE`, kolumna `resolved_on`) była błędna od chwili
   spisania — nie stała się błędna później przez zmianę okoliczności. Poprawka zapadła w tej samej
   rundzie weryfikacji tego zadania, przed pierwszym scaleniem, więc nie jest to „datowany aneks do
   zaakceptowanej decyzji" w rozumieniu reguły 18 — jest to poprawka błędu w tekście, który jeszcze
   nie stał się faktem historycznym. Gdyby ten punkt trafił już scalony do `main` przed znalezieniem
   błędu, poprawka wymagałaby osobnego, datowanego wpisu zamiast edycji w miejscu.
8. **Flaga typu ustawowego wchodzi do migawki razem z nazwą i dwiema flagami handlowymi.**
   Rozstrzygnięcie z ADR-0008 (aneks SC-3-03, pkt 8) wskazuje typ rozliczany budżetem **flagą na
   `absence_type`**, a nie kolumną na wierszu budżetu — więc od tego zadania odpowiedź na pytanie
   „przeciw czemu liczy się ten budżet" jest wartością organizacyjną, którą ktoś może po
   zatwierdzeniu przenieść na inny typ. Kryterium przynależności z aneksu 2026-09-19 stosuje się
   wprost i bez wyjątku: **dziedziczona, więc grupa 1**. `approved_snapshot_absence_type` rośnie o tę
   kolumnę, a zdanie „nazwa i obie flagi" w pkt 3c aneksu SC-3-02 i w docstringu
   `app.models.approved_snapshot` przestaje być kompletne — nowy wpis je zastępuje, nie kasuje.
   **Bez tego punktu rozstrzygnięcie flagowe byłoby cichą regresją AC-10:** przeniesienie flagi na
   inny typ nieobecności zmieniłoby comparand reguły `max` (ADR-0008, aneks SC-3-03, pkt 9) dla
   zatwierdzonej kalkulacji, której migawka byłaby formalnie kompletna. Mutacja do zabicia testem:
   skopiowanie do migawki nazwy i dwóch flag, a pominięcie trzeciej.

### 2026-09-23 — reguła komercyjna jako dana własna; pierwsza migawka stawek (SC-4-01)

Aneks SC-3-01 pkt 4 zobowiązuje każdą nową tabelę do przypisania grupy w chwili powstania.
"Decyzja" wylicza w migawce "rozwiązane stawki" i "wersję reguł komercyjnych"; SC-4-01 (Issue #8,
T&M) jest pierwszym zadaniem, które buduje którąkolwiek z nich.

1. **`commercial_terms` i `tm_terms` — grupa 2, strażnik zapisu.** Reguła należy do scenariusza
   (ADR-0003 pkt 1); nic spoza scenariusza jej nie zmienia — kryterium "kierunek dziedziczenia, nie
   udział w wyliczeniu" (aneks SC-3-01) spełnione wprost. **Zawężenie zdania z "Decyzji":** "wersja
   reguł komercyjnych" w migawce nie dotyczy reguły scenariusza — dotyczyłaby wyłącznie przyszłych
   wartości domyślnych organizacji dla reguł komercyjnych, jeśli powstaną. Konsekwencje:
   a. `INSERT`/`UPDATE`/`DELETE` obu tabel odrzucane pod `approved` przez `app.data.scenario_guard`
      w tej samej instrukcji co zapis; test odmowy i test wyścigu dwóch połączeń **per ścieżka
      zapisu** (warunek aneksu SC-3-01). Żaden nowy kształt strażnika.
   b. **Jeden wpis agregatu w `SCENARIO_CHILD_COPIERS`**: kopiujący `commercial_terms` kopiuje w tej
      samej funkcji `tm_terms` (pkt 1 aneksu SC-3-01 — "jeden wpis na agregat, którego korzeń jest
      dzieckiem scenariusza"). Kanarek: kopia scenariusza z regułą T&M ma regułę **i** wiersz
      szczegółów o nowych identyfikatorach; kopia bez wiersza szczegółów to reguła niekompletna,
      nie reguła skopiowana.

2. **Nowa tabela migawkowa `approved_snapshot_catalog_default_rate` — grupa 1.** Stawka katalogu
   jest wartością organizacyjną, zmienialną po zatwierdzeniu przez kogoś, kto kalkulacji nie zna —
   dokładnie AC-04. **Jest to pierwsze miejsce w repozytorium, które zamraża stawki, i pierwsze, w
   którym wyliczenie czyta `default_selling_rate`**; do SC-4-01 żaden wynik zatwierdzonej kalkulacji
   od stawki nie zależał, więc brak tej tabeli nie był regresją — od SC-4-01 byłby.
   a. **Kształt: wzorzec pkt 3 aneksu SC-3-02 bez zmian** — jedna tabela na tabelę źródłową,
      kluczowana `scenario_id`, identyfikator wiersza źródłowego jako wartość (nigdy klucz obcy),
      kopiowane wartości: cztery wymiary, `vendor_id`, obie stawki, waluta, jednostka,
      `effective_from`/`effective_to`.
   b. **Zamrażana jest także `default_cost_rate`, choć SC-4-01 jej nie czyta.** Migawka nie ma
      ścieżki UPDATE: scenariusz zatwierdzony przed blokiem 5 bez zamrożonego kosztu nie odzyskałby
      go nigdy (precedens stanu D, aneks SC-3-03). Tabela staje się przez to nośnikiem kosztu
      osobowego: każdy jej przyszły czytelnik podlega bramce kosztowej w kontekście projektu
      (ADR-0005, aneks SC-2-01 pkt 3 — koniunkcja), a SC-4-01 nie wystawia żadnej ścieżki zwracającej
      jej wiersze.
   c. **Zakres: tylko okna, które kalkulacja czyta** — wzorem budżetu (aneks SC-3-03 pkt 3 i 7):
      dla każdej krotki pozycji obsady scenariusza (z `vendor_id IS NULL`, ADR-0003 pkt 4) i każdego
      miesiąca jej alokacji — okno obejmujące cały miesiąc, tym samym predykatem co ścieżka żywa
      (ADR-0003 pkt 5). Nie cały katalog. Dwie pozycje o tej samej krotce zamrażają to okno raz
      (kanarek deduplikacji — ta sama pułapka co `DISTINCT` z `gen_random_uuid()`, SC-3-02 S-01/R-01).
      Miesiąc bez takiego okna nie daje wiersza i zostaje "brakiem stawki" na zawsze (pkt 3d
      obowiązuje w tej samej postaci: edycja stawki po zatwierdzeniu nie zmienia ani jednej wartości
      migawki).
   d. **Zapis: szóste CTE w tej samej instrukcji `_snapshot_statement`** (wzorzec S-01, SC-3-03) —
      zapis katalogu w trakcie zatwierdzenia nie może rozerwać pary "miesiące scenariusza ↔
      zamrożone okna". `scenario_id` z `unapproved_scenario(...)` wewnątrz `INSERT … SELECT`, jak
      pozostałe tabele (pkt 5 aneksu SC-3-03). Tabela dochodzi do `SNAPSHOT_TABLES`, **nie** do
      `SCENARIO_CHILD_COPIERS`; kanarek "kopia zatwierdzonego scenariusza ma zero wierszy
      migawkowych" obejmuje ją.
   e. **Czytelnik migawki rozstrzyga per miesiąc tym samym predykatem co ścieżka żywa** — warunek
      pkt 7c aneksu SC-3-03 obowiązuje: dowodzony własnym kryterium SC-4-01, nie założony.

3. **Czego ten aneks nie obejmuje:** migawki kursów walut (ADR-0006, brak tabeli źródłowej) i
   wartości domyślnych organizacji dla reguł komercyjnych (nie istnieją).

### 2026-09-23 — zakres migawki: okna miesięcy wycenionych stawką sprzedażową LUB kosztową (SC-5-01)

Drugi aneks tej daty w tym pliku, osobny wpis. Odwołanie "ADR-0004, aneks 2026-09-23" musi nazywać
zadanie (SC-4-01 albo SC-5-01) — ta sama konwencja co przy aneksach SC-3-02/SC-3-03.

Pkt 2c aneksu SC-4-01 wiąże zakres zamrożenia z predykatem przychodu (ADR-0003 pkt 5, aneks R-01:
okna miesiąca pokrywają każdy jego dzień i mają jedną parę (`default_selling_rate`, `currency`)).
Pkt 2b zamroził `default_cost_rate` z uzasadnieniem: "koszt niezamrożony teraz jest kosztem, którego
zatwierdzony scenariusz nigdy nie odzyska". SC-5-01 (F-07) jest pierwszym czytelnikiem kosztu i
ujawnia klasę miesięcy, dla której pkt 2c tej obietnicy nie dotrzymuje: okna pokrywają cały miesiąc
i mają jedną parę (`default_cost_rate`, `currency`), ale zmienia się w nim `default_selling_rate`.
Taki miesiąc ma koszt na ścieżce żywej i nie zamraża żadnego okna. Przypadek odwrotny — zmiana samej
stawki kosztowej — jest zamrażany w całości od aneksu R-01 ADR-0003 i luką nie jest.

1. **Zakres: okna, które czyta którakolwiek kalkulacja scenariusza.** Dla każdej pary (pozycja
   obsady, miesiąc alokacji) zamrażane są wszystkie wewnętrzne okna jej krotki nachodzące na
   miesiąc (`vendor_id IS NULL`, ADR-0003 pkt 4), gdy miesiąc jest wyceniony predykatem
   sprzedażowym **lub** predykatem kosztowym. Predykat kosztowy: okna razem pokrywają każdy dzień
   miesiąca i mają jedną parę (`default_cost_rate`, `currency`).
2. **Zasada pkt 2c bez zmian, zmienia się zbiór czytelników.** Nadal nie cały katalog, nie okna
   poddostawców, nie okna, do których nie sięga żaden wyceniony miesiąc. Miesiąc niewyceniony żadnym
   predykatem nie zamraża nic i zostaje "brakiem stawki" w obu wyliczeniach na zawsze. Każdy przyszły
   konsument stawki z tej tabeli rozszerza tę alternatywę własnym aneksem w zadaniu, w którym
   powstaje — nie po pierwszym zatwierdzeniu.
3. **Koszt niezależny od przychodu (F-06, reguła 10 Strażnika).** O zamrożeniu kosztu miesiąca nie
   decyduje predykat przychodu. Predykat kosztowy nie jest budowany z kodu ścieżki przychodu
   (`app.data.commercial_terms`) ani odwrotnie; kopiarka migawki jest jedynym miejscem znającym oba,
   jako alternatywę dwóch niezależnych predykatów.
4. **Jeden predykat na rodzaj stawki, trzy miejsca.** Predykat kosztowy stosowany identycznie na
   żywej ścieżce kosztu, w kopiarce i w czytelniku migawki kosztu (analogicznie do pkt 2e; warunek
   pkt 7c aneksu SC-3-03 — czytelnik dowodzi rozstrzygania per miesiąc własnym kryterium).
5. **Przychód zatwierdzonego scenariusza bez zmian.** Okna miesięcy wycenionych wyłącznie kosztowo
   są w migawce, ale czytelnik przychodu ponownie stawia predykat sprzedażowy (pkt 2e) i odpowiada
   "brak stawki", jak przed zatwierdzeniem.
6. **Zapis bez zmian wobec pkt 2d:** ta sama szósta CTE `_snapshot_statement`, jedna instrukcja, ta
   sama tabela, ten sam licznik `catalog_default_rates` (liczy teraz okna miesięcy wycenionych
   którymkolwiek predykatem), ta sama deduplikacja nad kopiowanymi wartościami.
7. **Bez działania wstecz.** Scenariusze zatwierdzone przed tym aneksem mają migawkę w zakresie
   pierwotnego pkt 2c; migawka nie ma ścieżki UPDATE, a dopisanie okien z żywego katalogu łamałoby
   AC-04. Miesiące opisanej klasy zostają w nich "brakiem stawki kosztowej" — nazwane, nie naprawiane.
8. **Rozważane i odrzucone:** (B) zakres bez zmian — koszt zatwierdzonego scenariusza zależałby od
   predykatu przychodu; (C) koszt zatwierdzonego scenariusza zawsze niedostępny do osobnego zadania
   — to wariant B z opóźnieniem, bo scenariusze zatwierdzone w międzyczasie tracą koszt na zawsze;
   (A′) zamrażanie wszystkich okien nachodzących na planowane miesiące bez predykatu — przeczy
   pkt 2c i istniejącemu kryterium K-08 SC-4-01.

Relacja do ADR-0003, aneks R-01: zdanie "migawka zamraża wszystkie okna wycenionego miesiąca"
pozostaje prawdziwe dla przychodu; ten aneks rozszerza zbiór zamrażanych okien, nie zmienia go.

| Kontrola | Kryterium akceptacji |
|---|---|
| C-1 | Miesiąc z pełnym pokryciem, jedną stawką kosztową i dwiema sprzedażowymi: zatwierdzenie zamraża wszystkie jego okna; koszt zatwierdzonego scenariusza równa się kosztowi szkicu sprzed zatwierdzenia i nie zmienia się po edycji katalogu. |
| C-2 | Ten sam miesiąc po zatwierdzeniu: przychód nadal `no_rate` (kontrast do C-1). |
| C-3 | Miesiąc bez pełnego pokrycia albo ze zmianą waluty nie zamraża żadnego okna (istniejące K-08 SC-4-01 pozostają zielone bez zmian). |
| C-4 | Kopiarka pozostaje jedną CTE `_snapshot_statement`; rozbicie na osobną instrukcję wywraca test S-01. |
| C-5 | `app.data.rate_windows` nie importuje `app.data.commercial_terms`, `app.data.personnel_cost` ani `app.domain.revenue*`/`app.domain.personnel_cost` i nie odwołuje się do `default_selling_rate` ani `default_cost_rate`; `app.data.commercial_terms` i `app.data.personnel_cost` nie importują się wzajemnie (test strukturalny, czerwony na dodaniu któregokolwiek z tych odwołań). |

**Doprecyzowanie pkt 3 (2026-09-23, bramka 2 SC-5-01, Draft — pending approval).** Geometria
miesiąca — miesiąc jako zakres półotwarty, liczba dni, "okna nachodzące na miesiąc pokrywają każdy
jego dzień", złączenia okien wewnętrznych (`vendor_id IS NULL`) katalogu i migawki własnego
scenariusza — żyje w neutralnym module `app.data.rate_windows`, z którego korzystają obie ścieżki.
Nie narusza to reguły 10 Strażnika: moduł nie czyta żadnej kolumny stawki i nie importuje żadnej z
dwóch ścieżek, a połowa predykatu decydująca "co jest wycenione" (jedna para stawka+waluta)
pozostaje osobną funkcją w każdym module (`month_is_priced`, `month_has_cost_rate`). Wspólna
geometria jest świadomie preferowana nad kopią: kopia pozwalałaby obu kalkulacjom rozjechać się o
klauzulę (np. oś poddostawcy). Konsekwencja nazwana: zmiana w `app.data.rate_windows` zmienia
jednocześnie koszt i przychód i wymaga zielonych testów obu ścieżek. Moduł nie może przyjąć żadnego
argumentu ani kolumny stawki — dodanie tam predykatu "wyceniony" wymaga nowego aneksu. Pilnowane
kontrolą C-5.

**Aneks — koszty dodatkowe SC-5-05 (2026-09-23, bramka 1, kierunki zaakceptowane przez człowieka,
ADR-0014).** Nowa tabela kosztu dodatkowego (`scenario_id NOT NULL`, opcjonalny `position_id` tej
samej pozycji/scenariusza) i tabela kategorii kosztu wchodzą w istniejący podział grup:
1. **Koszt dodatkowy — grupa 2** (dana własna scenariusza, strażnik zapisu, nie migawka). Różni się
   od kosztu osobowego SC-5-01 (grupa "dziedziczona" — czyta stawkę katalogu): kwota kosztu
   dodatkowego jest wpisywana wprost do scenariusza, nic spoza scenariusza jej nie zmienia, więc nie
   ma czego zamrażać przy zatwierdzeniu.
2. **Kategoria kosztu — grupa 1** (słownik organizacyjny, bez migawki, etykieta). Zmiana nazwy
   kategorii po zatwierdzeniu scenariusza zmienia nazwę widoczną na zatwierdzonym scenariuszu —
   zaakceptowane ograniczenie, ten sam wzorzec co pola opisowe Projektu (aneks 2026-09-18, grupa 1).
3. **Strażnik zapisu obejmuje INSERT, UPDATE i DELETE** kosztu dodatkowego pod scenariuszem
   `approved`, w tej samej instrukcji co odczyt statusu — ten sam wzorzec co pkt 7 aneksu SC-3-01 i
   reguła Strażnika 7. Wyścig z zatwierdzeniem dowiedziony na dwóch połączeniach jak dla każdej
   pozostałej tabeli-dziecka (aneks SC-3-02 pkt 5).
4. **Kopiowanie (aneks 2026-09-18 pkt 4, kaskada `SCENARIO_CHILD_COPIERS`).** Koszt przypisany do
   pozycji obsady kopiowany wewnątrz istniejącego kopiera agregatu pozycji (ma mapowanie starego-na-
   nowe id pozycji, aneksy SC-3-01/SC-3-02); koszt bez pozycji (poziom scenariusza) dostaje własny
   wpis w `SCENARIO_CHILD_COPIERS`. Kanarek kompletności kaskady musi pokryć obie ścieżki niezależnie.
5. **Brak osobnej tabeli projektowej.** "Koszt projektu" z F-08 = koszt scenariusza bez pozycji;
   nie istnieje mechanizm współdzielenia jednego kosztu między scenariuszami tego samego projektu w
   tym zadaniu (SC-5-05) — świadomie, każdy scenariusz niesie go z osobna, kopiowanie go replikuje.

**Aneks — pierwszy czytelnik migawki kalendarza, budżetu i typu nieobecności (2026-09-23, bramka
1, SC-5-06).**

1. Koszt nieobecności zatwierdzonego scenariusza czyta `approved_snapshot_working_calendar(_day)`,
   `approved_snapshot_absence_budget` i `approved_snapshot_absence_type` (w tym `generates_cost` i
   `is_statutory_leave`), nigdy tabele żywe. Te trzy migawki istnieją i się nie ruszają od SC-3-02/
   SC-3-03 (dowiedzione), ale do tej pory nie miały czytelnika — SC-5-06 jest pierwszym.
2. Okno budżetu dla zatwierdzonego scenariusza jest rozstrzygane per miesiąc tym samym predykatem
   co ścieżka żywa (warunek pkt 7c aneksu SC-3-03 spełniony przez to zadanie).
3. Zakres migawki bez zmian: koszt nieobecności liczy się wyłącznie w miesiącach z wierszem
   alokacji, tak jak dziś pojemność i budżet (bramka 1, Q-3 = A) — bez rozszerzenia okien
   kosztowych/budżetowych migawki, bez działania wstecz.
4. Nazwane, nie naprawiane: siatka pojemności zatwierdzonego scenariusza nadal czyta żywy
   kalendarz (SC-3-02, poza zakresem tej migawki), więc po edycji katalogu możliwy jest rozjazd
   między godzinami pokazanymi w siatce obsady i kosztem nieobecności zatwierdzonego scenariusza
   liczonym z migawki. Poza zakresem SC-5-06.
5. **Zmiana kontraktu S-02 (`ApprovedSnapshotAbsenceType`, SC-3-03).** Typ oznaczony
   `is_statutory_leave` jest zamrażany zawsze, gdy scenariusz ma wiersz alokacji w lokalizacji z
   kalendarzem, niezależnie od zamrożenia budżetu i od rezerwacji jego instancji. Stary kontrakt
   ("obecny ⇔ zamrożone budżety obowiązują", pkt 3 aneksu SC-3-03) powstał, gdy jedynym czytelnikiem
   była pojemność, która używa typu ustawowego wyłącznie przez budżet. Składowa kosztu nieobecności
   (SC-5-06) czyta `generates_cost` tego typu w KAŻDYM miesiącu z kalendarzem, niezależnie od
   budżetu — z zasady pkt 3 aneksu SC-3-03 ("kopiuj to, co kalkulacja scenariusza faktycznie
   czyta") wynika więc, że migawka musi go zamrażać szerzej. To zastosowanie pkt 3, nie wyjątek od
   niego. Nowy kontrakt: "obecny ⇔ typ był nazwany w chwili zatwierdzenia i scenariusz ma alokację
   w lokalizacji z kalendarzem". Para strażnika wyścigu S-01 "typ ustawowy ↔ budżety" przestaje
   istnieć; w jej miejsce wchodzi para "typ ustawowy ↔ kalendarz lokalizacji", chronione tą samą
   jedną instrukcją zapisu migawki. Bez działania wstecz: scenariusze zatwierdzone przed tym
   aneksem, z typem nazwanym-niekosztowym i bez zamrożonego budżetu, zostają z `no_budget` w tej
   składowej na zawsze (migawka nie ma ścieżki UPDATE) — nazwane, nie naprawiane.

| Kontrola | Kryterium akceptacji |
|---|---|
| M-1 | Koszt nieobecności zatwierdzonego scenariusza identyczny tuż przed i po zatwierdzeniu; nie zmienia się po edycji kalendarza, budżetu, `generates_cost` ani przeniesieniu `is_statutory_leave` na inny typ. |
| M-2 | Czytelnik migawki ograniczony do własnego `scenario_id` (kontrast: dwa zatwierdzone scenariusze tej samej krotki katalogu/kalendarza/budżetu, koszty się nie mieszają). |
| M-3 | Typ ustawowy nazwany, niekosztowy, bez zamrożonego budżetu i bez rezerwowanej instancji — składowa kosztu identyczna tuż przed i po zatwierdzeniu (kontrast z brakiem kalendarza w lokalizacji: tam typ NIE jest zamrażany). |

**Aneks — duplikacja scenariusza we własnym projekcie, drugi punkt wejścia (2026-09-24, bramka 1,
SC-6-01, F-09 pkt 1, AC-02).**

1. Duplikowanie scenariusza (F-09) i tworzenie nowej wersji po zatwierdzeniu (F-12) to ten sam
   mechanizm kopiowania na poziomie danych (`copy_scenario`, `SCENARIO_CHILD_COPIERS`) — jedna
   funkcja, dwa punkty wejścia (nazwane już w sekcji Decyzja/Konsekwencje tego dokumentu). SC-6-01
   dodaje pierwszy rzeczywisty wywołujący z `into_project=source.project` — każde wcześniejsze
   wywołanie (SC-1-03) szło do świeżo utworzonego projektu, gdzie kolizja nazwy scenariusza była
   strukturalnie niemożliwa.
2. **Kolizja nazwy jest realna, nie hipotetyczna.** `scenarios` niesie
   `UniqueConstraint(project_id, name)`, a `copy_scenario` kopiuje `name` dosłownie — wywołanie z
   `into_project=source.project` zderza się z samym źródłem (ten sam projekt, ta sama nazwa) za
   każdym razem, gdyby nazwa nie była zmieniana. Rozstrzygnięcie (bramka 1, opcja B): backend
   generuje nazwę duplikatu automatycznie (sufiks `(copy)`/`(copy N)`, retry na kolizję), endpoint
   zostaje bez ciała żądania — zgodnie z konwencją siostrzanych akcji (`copy_project`, `archive`,
   `approve`), które celowo nie przyjmują ciała.
3. Generowana nazwa jest obcinana do budżetu kolumny (`scenarios.name`, `String(200)`, minus
   najdłuższy możliwy sufiks) PRZED wygenerowaniem kandydatów — łańcuchowe duplikowanie duplikatu
   (dozwolony, oczekiwany przypadek użycia F-09: "duplikuj i modyfikuj niezależnie", powtarzalnie)
   zbiega do stałej długości zamiast dryfować do przepełnienia kolumny (poprawka R-01 rundy
   weryfikacji SC-6-01).
4. Status duplikatu nie jest parametrem — zawsze `draft`, niezależnie od statusu źródła (istniejąca
   reguła `copy_scenario`, tu tylko potwierdzona dla nowego wywołującego). Źródło jest wyłącznie
   czytane (`FOR SHARE`), nigdy zapisywane — niezmienność zatwierdzonych kalkulacji (sekcja
   Decyzja) obowiązuje identycznie jak dla SC-1-03.
5. Duplikacja scenariusza generuje kolejne zdarzenie pod odłożonym warunkiem `audit_log` (F-12) —
   dołącza do SC-1-02..04, SC-3-02, SC-4-01, SC-5-05 jako kolejne zadanie z tym samym, powtórzonym
   zamknięciem (aneks 2026-09-18 "historia zmian… odłożona").

### 2026-09-25 — nowa tabela-dziecko scenariusza: segment dostawy/workstreamu, grupa 2, bez migawki (SC-1-11, ADR-0016)

Aneks z 2026-09-19 (SC-3-01) pkt 4 zobowiązuje każdą nową tabelę-dziecko scenariusza do
przypisania "dziedziczona → migawka" albo "własna → strażnik zapisu" **w chwili powstania** —
tabela dodana bez przypisania wpada domyślnie do grupy 2 i nie dostaje żadnej ochrony poza tą,
którą strażnik faktycznie egzekwuje. `scenario_delivery_segment` (ADR-0016, encja fazy/workstreamu,
Issue #65) jest tą tabelą. Ten wpis nazywa przypisanie wprost — nie zostawia je domyślnemu
milczeniu, który tamten aneks nazwał ryzykiem.

1. **Grupa 2 — dana własna scenariusza, strażnik zapisu, nie migawka.** Segment jest strukturalnym
   wejściem planistycznym scenariusza (nazwa i miejsce w hierarchii projekt → scenariusz →
   segment); nic spoza scenariusza go nie zmienia — kryterium "kierunek dziedziczenia, nie udział
   w wyliczeniu" (aneks 2026-09-19 SC-3-01) spełnione wprost, tak samo jak dla `staffing_position`,
   `commercial_terms`/`tm_terms` i `additional_cost`.
2. **Zapis odrzucany pod `approved` w tej samej instrukcji co odczyt statusu.** Ten sam wzorzec
   strażnika co dla pozostałych tabel-dzieci: `INSERT … SELECT … FROM scenarios WHERE id =
   :scenario_id AND status <> 'approved'` (aneks 2026-09-19 SC-3-01, pkt 2) dla `INSERT`; `UPDATE`/
   `DELETE`, jeśli którakolwiek z tych funkcji zapisu powstaje w tym zadaniu, zawieszone na tym
   samym predykacie w klauzuli `WHERE`. Żaden nowy kształt strażnika — `app.data.scenario_guard`
   bez zmian.
3. **Wyścig z zatwierdzeniem: bez nowego mechanizmu, korzysta z już domkniętego.** Warunek
   zamknięcia z aneksu 2026-09-19 SC-3-01 pkt 2 ("pierwsza prawdziwa ścieżka zatwierdzenia
   scenariusza musi rozstrzygnąć ten wyścig … dla WSZYSTKICH tabel-dzieci scenariusza naraz, nie
   tylko dla tej, która akurat wtedy powstaje") został domknięty przez SC-3-02 dla tabel istniejących
   w tamtym czasie. Nowa tabela korzysta z tego samego, już dowiedzionego mechanizmu — nie otwiera
   wyścigu ponownie i nie wymaga własnego testu fundamentu, tylko testu tej konkretnej tabeli
   (kanarek per ścieżka zapisu, warunek aneksu SC-3-01).
4. **Jeden wpis w `SCENARIO_CHILD_COPIERS`, najprostszy kształt rejestru dotąd.** Segment nie ma
   dziś żadnego dziecka ani wnuczka (F-04/alokacja per faza poza zakresem SC-1-11, ADR-0016 pkt 9)
   — kopiujący wstawia, dla każdego wiersza źródłowego, nowy wiersz o nowym `id`, tej samej `name`,
   wskazujący `id` kopii scenariusza. Brak mapowania zagnieżdżonych identyfikatorów (w przeciwieństwie
   do kopiującego agregatu pozycji obsady, aneks 2026-09-19 SC-3-01 pkt 1) — odzwierciedla faktyczny
   kształt agregatu, nie jest osłabieniem gwarancji reguły 17 Strażnika.
5. **Nie wchodzi do `SNAPSHOT_TABLES`.** Konsekwencja wprost z punktu 1: dane własne scenariusza nie
   mają czego zamrażać przy zatwierdzeniu (aneks 2026-09-19 SC-3-01 pkt 2, zdanie ostatnie).
6. **Ten aneks nie wystawia żadnej ścieżki zapisu na zewnątrz warstwy danych.** SC-1-11 buduje
   wyłącznie funkcję(-e) warstwy danych potrzebną do spełnienia punktów 1–5 — żaden endpoint HTTP
   nie wystawia segmentu (Q2 = A, ADR-0016 pkt 8). Uprawnienia (ADR-0005) pozostają nierozstrzygnięte
   do zadania, które doda pierwszy endpoint nad tą tabelą.

### 2026-09-25 — SC-5-02 (Issue #77, narzuty): narzut wchodzi do migawki w tym samym zadaniu (drugi aneks tej daty, osobny wpis, nie dopisek do SC-1-11 wyżej)

Rozstrzygnięcie bramki 1 (2026-09-25, ADR-0013 aneks tej daty, Q2; ADR-0005 aneks tej daty): narzut
WCHODZI do `approved_snapshot_catalog_default_rate` w tym samym zadaniu, w którym powstaje (SC-5-02),
mimo że SC-5-02 nie buduje własnego czytelnika migawki — Opcja A, "zamrozić teraz", nie Opcja B
("odłożyć zamrażanie").

1. **Precedens wiążący wprost, nie tylko analogiczny.** Aneks 2026-09-23 SC-4-01, pkt 2b: "Zamrażana
   jest także `default_cost_rate`, choć SC-4-01 jej nie czyta (…) Migawka nie ma ścieżki UPDATE:
   scenariusz zatwierdzony przed blokiem 5 bez zamrożonego kosztu nie odzyskałby go nigdy." Ten sam
   argument, słowo w słowo, stosuje się do narzutu: scenariusz zatwierdzony w oknie między SC-5-02 a
   przyszłym zadaniem budującym czytelnika kosztu w pełni obciążonego nigdy nie odzyskałby narzutu,
   gdyby SC-5-02 go nie zamroziła — migawka bez ścieżki UPDATE czyni to nieodwracalnym, nie tylko
   niewygodnym.
2. **Kształt: nowa kolumna (kolumny) na ISTNIEJĄCEJ tabeli migawkowej, nie nowa tabela.** Procent
   narzutu i flaga "stawka już zawiera narzuty" (ADR-0013 aneks tej daty, Q4/Q5) rozszerzają
   `ApprovedSnapshotCatalogDefaultRate`/`approved_snapshot_catalog_default_rate` o tyle kolumn, ile
   nowych pól niesie wiersz źródłowy `catalog_default_rates` po SC-5-02 — nie tworzą drugiej tabeli
   migawkowej dla tego samego wiersza. Precedens dosłowny: aneks 2026-09-22 SC-3-03, pkt 8 — flaga
   typu ustawowego weszła do migawki jako nowa kolumna istniejącej tabeli
   `ApprovedSnapshotAbsenceType`, a "zdanie „nazwa i obie flagi" (…) przestaje być kompletne — nowy
   wpis je zastępuje, nie kasuje". Ten sam wzorzec tu: docstring klasy i lista kolumn rosną, tabela
   nie mnoży się.
3. **Zakres okien zamrażanych — bez zmian wobec aneksu 2026-09-23 SC-5-01, pkt 1.** To ten sam
   predykat kosztowy (okna razem pokrywają każdy dzień miesiąca, jedna para `default_cost_rate` +
   `currency`) decyduje, które okna wiersza zamrozić. Narzut podróżuje na TYM SAMYM wierszu co stawka
   bazowa (ADR-0013 aneks tej daty, Q5) — nie ma więc osobnego kryterium zamrożenia do wynalezienia:
   jeśli wiersz stawki bazowej jest zamrażany, jego kolumna(-y) narzutu jest zamrażana razem z nim, w
   tej samej instrukcji `_snapshot_statement` (ta sama szósta CTE, aneks SC-4-01 pkt 2d — kształt
   zapisu bez zmian).
4. **Konsekwencja nazwana wprost, wzorem pkt 2b aneksu SC-4-01.** Tabela poszerza się jako nośnik
   kosztu osobowego — od SC-5-02 niesie też narzut, nie tylko stawkę bazową. SC-5-02 sam nie musi
   wystawiać żadnej ścieżki, która zwraca tę kolumnę: pierwszy czytelnik (przyszłe zadanie budujące
   koszt w pełni obciążony zatwierdzonego scenariusza) podlega tej samej koniunkcji kosztowej co dziś
   `default_cost_rate` migawkowe (ADR-0005, aneks 2026-09-23 SC-5-01 pkt 2), rozszerzonej dla procentu
   surowego poza kontekstem projektu przez ADR-0005 aneks tej daty (`CATALOG_READ` samo, Q4).
5. **Bez działania wstecz.** Scenariusze zatwierdzone przed SC-5-02 nie mają zamrożonej kolumny
   narzutu (kolumna nie istniała w chwili ich zatwierdzenia) — nazwany stan "brak narzutu w migawce",
   nie luka do naprawienia; migawka nie ma ścieżki UPDATE (zasada niezmienna od aneksu SC-3-02, pkt
   3d).
6. **Warunek, który SC-5-02 musi dowieść, nie założyć.** Kanarek analogiczny do M-1 (aneks SC-5-06) i
   do "kopia zatwierdzonego scenariusza ma zero wierszy migawkowych" (aneks SC-3-02, pkt 2): kolumna(-y)
   narzutu zamrożonego wiersza migawki nie zmienia się po edycji katalogu; scenariusz zatwierdzony przed
   i po SC-5-02 rozróżnialny przez samą obecność/brak wartości w nowej kolumnie, nie przez błąd.

### 2026-09-25 — SC-5-03 (Issue #78, kwota stała jako podstawa kosztu): nowe kolumny na `staffing_position`, grupa 2 potwierdzona, kopiowanie i token współbieżności nazwane wprost

Aneks z 2026-09-19 (SC-3-01) pkt 4 zobowiązuje każdą nową tabelę-dziecko scenariusza do przypisania
grupy **w chwili powstania**. `cost_basis`/`fixed_amount` (+ waluta) nie są nową tabelą — są nowymi
kolumnami tabeli już przypisanej do grupy 2 (aneks 2026-09-19 SC-3-01 pkt 2: "Dane własne
scenariusza → strażnik zapisu, nie migawka. Pozycja obsady i alokacja należą do scenariusza"). Ten
wpis nie zmienia tego przypisania — potwierdza je wprost dla nowych kolumn, zamiast zostawić je
domyślnemu milczeniu, które aneks 2026-09-19 nazwał ryzykiem na poziomie tabel; tu stosuje ten sam
rygor na poziomie kolumn.

1. **Kryterium kierunku dziedziczenia (aneks 2026-09-19) spełnione wprost dla nowych kolumn.**
   `fixed_amount` jest liczbą wpisaną wprost przez planistę dla tej pozycji — nic spoza scenariusza
   (katalog, budżet, reguła organizacyjna) jej nie zmienia. Ten sam argument, którym aneks 2026-09-23
   SC-5-05 objął koszt dodatkowy grupą 2: "kwota kosztu dodatkowego jest wpisywana wprost do
   scenariusza, nic spoza scenariusza jej nie zmienia, więc nie ma czego zamrażać przy zatwierdzeniu".
   **Grupa 2 potwierdzona, nie zmieniona.**
2. **Konsekwencja wprost: brak trzeciego miejsca migawkowego, kontrast nazwany z worked time.** Koszt
   bazowy worked time (SC-5-01) zależy od `catalog_default_rates.default_cost_rate` — wartości
   dziedziczonej spoza scenariusza — stąd migawka i "trzy miejsca, jeden predykat" (ADR-0013 pkt 6).
   `fixed_amount` zależy wyłącznie od własnej kolumny wiersza scenariusza — nie wchodzi i nie będzie
   wchodzić do `SNAPSHOT_TABLES`; jedyną ochroną zatwierdzonego scenariusza jest strażnik zapisu
   (`app.data.scenario_guard`, ten sam mechanizm co dla reszty własnych kolumn `staffing_position`),
   nie kopia. To zastosowanie zdania z aneksu 2026-09-19 pkt 2 ("Migawka pozycji byłaby drugą kopią
   tych samych wierszy… i pierwszym miejscem, w którym dwie kopie mogłyby się rozjechać"), nie
   wyjątek od niego.
3. **Kopiowanie: bez nowego wpisu w `SCENARIO_CHILD_COPIERS` — warunek, który SC-5-03 musi dowieść,
   nie założyć.** Ponieważ kolumny żyją na wierszu już kopiowanym przez istniejący kopiujący agregat
   pozycji (aneks 2026-09-19 SC-3-01 pkt 1), podróżują z kopią row automatycznie — **pod warunkiem**,
   że ten kopiujący wstawia kopię przez pełne przepisanie wiersza, a nie przez zamkniętą, wcześniej
   napisaną listę kolumn. Jeśli implementacja koduje listę kolumn wprost, dodanie kolumny do modelu
   bez dodania jej do tej listy jest cichą regresją tej samej klasy, jaką aneks 2026-09-19 pkt 4
   nazwał dla całych tabel ("tabela dodana bez przypisania wpada domyślnie do grupy 2") — tu na
   poziomie kolumny: kopia dostałaby `cost_basis` domyślne/`fixed_amount = NULL` niezależnie od
   źródła, cicho łamiąc K-04 (AC-02: kopia musi mieć wartość niezależną, nie referencję ani wartość
   domyślną). **Kanarek obowiązkowy:** kopia pozycji z `cost_basis='fixed_amount'` ma na kopii tę
   samą kwotę/walutę, na niezależnym wierszu; mutacja do zabicia — kopiujący zwracający `cost_basis`
   domyślne (`worked_time`) niezależnie od źródła.
4. **Token współbieżności ADR-0007 obejmuje nowe kolumny automatycznie — nazwane wprost, nie
   milcząco.** ADR-0007, aneks 2026-09-19 (SC-3-01): "znacznik współbieżności żyje na **pozycji**
   (`staffing_position.updated_at`), nie na każdym wierszu miesiąca i nie na scenariuszu". `cost_basis`
   i `fixed_amount` są kolumnami TEGO SAMEGO wiersza — każda ścieżka zapisu, która je edytuje,
   uczestniczy z automatu w tym samym mechanizmie kontroli optymistycznej co edycja pozostałych pól
   pozycji: ten sam znacznik, ten sam `409` przy niezgodności, żaden nowy token, żaden nowy aneks do
   ADR-0007. To rozróżnienie jest istotne, bo najbliższy precedens tego repozytorium (koszt dodatkowy,
   SC-5-05) poszedł w drugą stronę — dostał **własny, per-wiersz** znacznik (ADR-0007, aneks SC-5-05),
   dokładnie DLATEGO że koszt dodatkowy jest osobną tabelą/wierszem, nie kolumną wiersza już
   objętego tokenem. Nazwane tu wprost, żeby implementacja nie skopiowała precedensu SC-5-05 przez
   analogię tam, gdzie nie pasuje: SC-5-03 nie potrzebuje własnej decyzji ADR-0007 i nie dostaje jej.

**Aneks — reguła Outcome-based jako dana własna scenariusza (2026-09-25, bramka 1, SC-4-03, Issue
#67, zaakceptowany przez człowieka).** Przypisanie grupy dla `outcome_terms` (obowiązek aneksu
SC-3-01 pkt 4); model danych — ADR-0003, aneks 2026-09-25 SC-4-03.

1. **`outcome_terms` — grupa 2, strażnik zapisu.** Wszystkie wartości (opłata, premia, stawka za
   jednostkę, min/max, waluta, jednostki i prawdopodobieństwa kategorii) wpisuje użytkownik do
   scenariusza; nic spoza scenariusza ich nie zmienia — kryterium "kierunek dziedziczenia, nie udział
   w wyliczeniu" (aneks SC-3-01) spełnione wprost.
2. **Strażnik zapisu bez nowego kształtu.** `INSERT` reguły outcome (dziś jedyna ścieżka zapisu,
   ADR-0003 aneks SC-4-03 pkt 9) odrzucany pod `approved` przez `app.data.scenario_guard` w tej
   samej instrukcji co zapis; zero wierszy w `commercial_terms` i `outcome_terms` po odmowie. Test
   odmowy i test wyścigu dwóch połączeń dla tej ścieżki, kontrast na `draft` (warunek aneksu
   SC-3-01: per ścieżka zapisu). Przyszła ścieżka edycji/usunięcia dostaje własne testy w swoim
   zadaniu.
3. **Kopiowanie przez istniejący jeden wpis agregatu** — wzorzec pkt 1b aneksu SC-4-01: kopiujący
   `commercial_terms` kopiuje w tej samej funkcji wiersz `outcome_terms` z kompletem kolumn, z
   nowymi identyfikatorami; bez nowego wpisu w `SCENARIO_CHILD_COPIERS`. Kanarek: kopia scenariusza
   z regułą outcome ma regułę **i** wiersz szczegółów, a jej wynik jest identyczny ze źródłem;
   `model_type` bez gałęzi w kopiującym → `unsupported_model_type`, nigdy reguła bez szczegółów.
4. **Brak migawki.** Wyliczenie outcome nie czyta żadnej wartości dziedziczonej spoza scenariusza
   (brak katalogu stawek, brak kursów — waluta reguły bez przeliczenia), więc zatwierdzenie nie
   zamraża nic nowego: brak tabeli `approved_snapshot_*`, brak zmiany `_snapshot_statement` i
   `SNAPSHOT_TABLES`. Przychód zatwierdzonego scenariusza outcome czyta własne tabele
   (`rate_source = not_applicable`, ADR-0003 aneks SC-4-03 pkt 8). Warunek ponownego rozpatrzenia:
   pierwsza wartość domyślna organizacji dla reguły outcome albo przeliczenie walut — wtedy grupa 1
   dla tej wartości i własny aneks.
5. **Uzgodnienie po merge z `main` (2026-09-25, decyzja człowieka, runda weryfikacji 2).** Obok
   `outcome_terms` na `main` istnieje już `story_points_terms` (SC-4-04) — również grupa 2, bez
   migawki. Pkt 3 (kopiowanie przez jeden wpis agregatu, gałąź per `model_type` w kopiującym)
   obowiązuje obie tabele szczegółów jednocześnie; kopia scenariusza z regułą Story Points albo
   outcome ma regułę **i** wiersz szczegółów właściwego modelu. Pkt 4 bez zmian: żaden z modeli
   bez katalogu stawek nie czyta wartości dziedziczonej spoza scenariusza, więc zatwierdzenie nie
   zamraża nic nowego (`rate_source` = `not_applicable` dla outcome, `story_points_terms` dla Story
   Points — ADR-0003, aneks 2026-09-25 SC-4-03 pkt 12).

### 2026-09-27 — SC-8-01 (Issue #14, F-12): pierwsza tabela `audit_log` — kształt, `affected_data` jako referencja, miejsce w mandatowej kolejności transakcji zatwierdzenia (mapa wpływu Architekta, zaakceptowane na bramce 1, 2026-09-27)

Sekcja "Konsekwencje" nazywa potrzebę tabeli `audit_log` od chwili napisania tego ADR. Aneks
2026-09-18 ("historia zmian… odłożona dla SC-1-02..04") otwiera warunek zamknięcia "blok 8 planu";
aneksy SC-3-02 pkt 9, SC-4-01 (via ADR-0005, aneks tej daty pkt 9 wyżej — "zamknięcie: osobny ADR
uwierzytelniania… + blok 8 planu (dla `audit_log`)") i SC-6-01 pkt 5 powtarzają ten sam, wciąż
niedomknięty warunek dla kolejnych akcji zapisu. SC-8-01 jest pierwszym zadaniem tego bloku i
pierwszym, które faktycznie tworzy tabelę — dla dokładnie jednej akcji: zatwierdzenia scenariusza
(`POST .../approve`, `app.data.scenario_approval.approve_scenario`). Domyka dwa pytania, które
product-owner zostawił architektowi na bramce 1, i jedną lukę, której żaden dotychczasowy aneks nie
nazwał: gdzie w mandatowej kolejności transakcji zatwierdzenia (aneks 2026-09-22, pkt 4) leży zapis
audytu.

1. **Kształt: tabela dedykowana zdarzeniom cyklu życia scenariusza, nie ogólna tabela systemowa z
   polimorficznym odniesieniem do dowolnego zasobu (rozstrzygnięcie bramki 1, opcja B z dwóch
   przedstawionych przez product-ownera).**
   a. Uzasadnienie jest zastosowaniem precedensu, którym ten sam ADR posługuje się już dla tabel
      migawkowych (aneks 2026-09-22 SC-3-02, pkt 3a): "jedna tabela migawkowa na jedną tabelę
      źródłową… nie jedna generyczna tabela z kolumną `jsonb`… generyczny blob jest skrótem, po
      który sięgnie następny implementator." Tu przyczyna jest inna — nie precyzja `Decimal`
      (`audit_log` nie niesie kwot ani godzin) — lecz integralność referencyjna: wiersz z generycznym
      `resource_type` + `resource_id` (bez klucza obcego, bo cel zmienia się per wiersz) traci
      gwarancję "zdarzenie wskazuje istniejący zasób", którą PostgreSQL egzekwowałby za darmo
      (ADR-0001: integralność egzekwowana w bazie, nie tylko w kodzie aplikacji).
   b. **Rozstrzygnięcie:** `audit_log` niesie dziś wyłącznie kolumny, których wymaga zdarzenie
      zatwierdzenia scenariusza (referencje do scenariusza i projektu, znacznik czasu, tożsamość
      wołającego w granicach dzisiejszego placeholdera, `action_type`) i rośnie **przez migrację
      dodającą kolumny lub wartości nowego kształtu w zadaniu, które wprowadza kolejny typ
      zdarzenia**, nie przez współdzielony od początku, generyczny kontener zaprojektowany pod akcje,
      których kształt nie jest dziś znany. To dosłowne zastosowanie wzorca "przypisanie w chwili
      powstania, rozszerzenie własnym datowanym wpisem później", którym ten ADR posługuje się
      konsekwentnie od aneksu 2026-09-19 (SC-3-01, pkt 4) po dziś.
   c. **Kolumna `action_type` jest dopuszczona, o zamkniętym zbiorze wartości dziś jednoelementowym
      (`scenario_approved`)** — pozwala przyszłemu czytelnikowi historii (SC-8-02) sięgać po jedną
      tabelę zamiast po `UNION` wielu, bez zmiany schematu przy każdym kolejnym zadaniu opisującym
      zdarzenie NA TYM SAMYM zasobie (np. przyszła duplikacja scenariusza, jeśli okaże się nieść te
      same kolumny referencyjne). **Warunek, który następne zadanie musi dowieść, nie założyć:**
      pierwszy typ zdarzenia dotyczący INNEGO zasobu niż scenariusz (np. edycja wiersza katalogu)
      wymaga własnego rozstrzygnięcia — nowa tabela, czy nowa nullable kolumna referencyjna tej samej
      tabeli — i własnego, datowanego wpisu tutaj. Ten aneks tego z góry nie rozstrzyga.
   d. **Odrzucone:** ogólna tabela od razu, z zestawem nullable kolumn referencyjnych pod wszystkie
      przewidywane akcje bloku 8 — bo dziś istnieje dokładnie jeden typ zdarzenia, a każda kolumna
      referencyjna poza tą, którą zatwierdzenie faktycznie wypełnia, byłaby polem zgadywanym pod
      zadania, których kształt nie jest znany (ten sam argument, którym ten ADR odrzuca spekulatywne
      pola w tabelach migawkowych).

2. **`affected_data`: referencja (identyfikatory scenariusza i projektu), nigdy kopia opisowa
   (rozstrzygnięcie bramki 1, opcja A z dwóch przedstawionych przez product-ownera).**
   a. `scenario_id` i `project_id` są **prawdziwymi kluczami obcymi** do `scenarios(id)`/
      `projects(id)` — inaczej niż wzorzec tabel migawkowych (aneks 2026-09-22 SC-3-02, pkt 3b:
      "identyfikator wiersza źródłowego przechowywany jako wartość… nigdy jako klucz obcy").
      Rozbieżność nazwana wprost, żeby nie wyglądała na przeoczenie: wzorzec migawkowy chroni przed
      tym, żeby edycja albo skasowanie źródła poruszyło zamrożoną kopię lub zablokowało jej zapis —
      ale nic w tym systemie nie usuwa wiersza `scenario`/`project` (archiwizacja jest stanem
      widoczności, nie usunięciem — aneks 2026-09-18 "archiwizacja Projektu", pkt 2). Klucz obcy tu
      nie grozi ani przemieszczeniem, ani zablokowaniem niczego — daje wyłącznie gwarancję, że
      zdarzenie audytu nigdy nie wskaże nieistniejącego zasobu, więc korzyść integralności jest
      czysta, bez kosztu, przed którym ostrzega wzorzec migawkowy. **Warunek ponownego rozpatrzenia:**
      jeśli kiedykolwiek powstanie twarde usuwanie scenariusza lub projektu, ten punkt wymaga
      ponownego rozstrzygnięcia zachowania klucza obcego (`ON DELETE RESTRICT` jako domyślne
      oczekiwanie — historia nie powinna dać się skasować przez skasowanie tego, czego dotyczy).
   b. **Żadne pole opisowe scenariusza lub projektu (nazwa, właściciel…) nie jest kopiowane do
      wiersza audytu.** Migawka `approved_snapshot_*` jest już źródłem prawdy o TYM, CO zostało
      zatwierdzone; drugi zapis tej samej treści w `audit_log` byłby drugą kopią rozstrzygającą to
      samo pytanie — pierwszym miejscem, w którym dwie kopie mogłyby się rozjechać (ostrzeżenie
      aneksu 2026-09-19 SC-3-01, pkt 2, zastosowane tu przez analogię). `audit_log` mówi wyłącznie
      kto/kiedy/jaka-akcja/na-którym-zasobie (zdanie źródłowe sekcji "Konsekwencje"); "co" zostało
      zatwierdzone jest już w migawce.
   c. **Kierunek na przyszłość, nazwany a nie zbudowany tutaj:** gdyby historia miała kiedyś pokazywać
      nazwę scenariusza/projektu z chwili zdarzenia, rozwiązaniem jest złączenie czytelnika historii
      z bieżącym wierszem projektu/scenariusza (ta sama zasada co pola opisowe Projektu, aneks
      2026-09-18, grupa 1 — pokazują wartość bieżącą, nie historyczną), nie dodanie kolumny opisowej
      do `audit_log`. SC-8-01/SC-8-02 nie budują tego mechanizmu; ten punkt istnieje, żeby SC-8-02 nie
      zaprojektował go w locie.

3. **Miejsce zapisu `audit_log` w mandatowej kolejności transakcji zatwierdzenia — luka w aneksie
   2026-09-22, pkt 4, domykana teraz.** Ten punkt ("najpierw wiersze migawki, na końcu `UPDATE …
   status = 'approved'`") nie wspominał audytu, bo `audit_log` wtedy nie istniał.
   a. **Zapis do `audit_log` należy do TEJ SAMEJ transakcji co migawka i przestawienie statusu —
      nigdy osobny zapis po commicie.** Ryzyko jest symetryczne i obie połówki są równie szkodliwe:
      zapis audytu bez powodzenia zatwierdzenia zostawia rekord zdarzenia, które nigdy się nie
      wydarzyło (zatrucie historii); powodzenie zatwierdzenia bez zapisu audytu odtwarza dokładnie
      problem, który to zadanie miało rozwiązać (nieodwracalna decyzja bez śladu). Tylko jeden commit
      na końcu spełnia oba naraz — to samo "wszystko albo nic", którego reguła K-18 tego ADR już
      wymaga dla migawki.
   b. **Kolejność: po potwierdzonym przestawieniu statusu, przed `session.commit()` — nie jako
      siódma CTE `_snapshot_statement` przed przestawieniem statusu, na wzór sześciu istniejących.**
      Wiersz audytu opisuje zdarzenie "scenariusz X został zatwierdzony", więc jego zapis logicznie
      następuje PO tym, jak baza w tej samej transakcji faktycznie potwierdziła tę zmianę stanu — nie
      przed nią, na spekulację że przestawienie się powiedzie. Umieszczenie go przed przestawieniem
      statusu (jak sześć tabel migawkowych) zostaje odrzucone z innego powodu niż kolejność:
      migawka broni się przed WYŚCIGIEM z innymi zapisami do dzieci scenariusza pod `approved`
      (aneks 2026-09-19 SC-3-01, pkt 2) — `audit_log` nie jest dzieckiem scenariusza w tym sensie i
      nie potrzebuje strażnika `status <> 'approved'`, bo nic poza tą samą transakcją zatwierdzenia
      nigdy do niego nie pisze.
   c. **Nie wchodzi do `SNAPSHOT_TABLES` ani do `SCENARIO_CHILD_COPIERS` — brak wpisu jest tu
      wymagany, nie dozwolony** (wzorem migawki, aneks 2026-09-22 SC-3-02, pkt 2, i wzorem segmentu
      dostawy, aneks 2026-09-25 SC-1-11, pkt 5). Zdarzenie audytu opisuje jednorazowe działanie
      człowieka nad TYM konkretnym scenariuszem — kopia scenariusza (duplikacja, F-09) nie
      "odziedziczyła" tego zatwierdzenia, dokładnie ten sam argument co "Migawka nie jest kopiowana"
      (aneks 2026-09-18, pkt 3: "kopia nie przeszła tej operacji"). **Kanarek obowiązkowy, analogiczny
      do "kopia zatwierdzonego scenariusza ma zero wierszy migawkowych":** duplikat scenariusza
      (zatwierdzonego lub nie) ma zero wierszy `audit_log` wskazujących na siebie jako `scenario_id`
      kopii — kopiowanie nie replikuje historię, tworzy nową, pustą.
   d. **Brak nowego strażnika zapisu.** `audit_log` nie ma dziś żadnej ścieżki zapisu poza tą jedną
      (wewnątrz `approve_scenario`), więc `app.data.scenario_guard` nie zyskuje nowego kształtu.

4. **ADR-0007 (współbieżna edycja): nie dotyczy — nazwane wprost, nie pominięte milcząco.**
   `audit_log` nie ma ścieżki `UPDATE` ani `DELETE` (append-only z definicji zadania: "dokładnie
   jeden nowy wiersz"), więc nie ma zgubionej aktualizacji do ochrony i żaden znacznik współbieżności
   (`updated_at` + `409` na niezgodność) nie jest potrzebny — różni się tym od każdej innej tabeli-
   dziecka scenariusza wprowadzonej dotąd (SC-3-01, SC-3-03, SC-4-01, SC-5-05), które wszystkie
   dostały własny znacznik. **Warunek ponownego rozpatrzenia:** gdyby kiedykolwiek powstała ścieżka
   korygująca błędny wpis audytu — nieprzewidziana i niepożądana przez F-12, bo historia zmian ma
   być trwała — to pytanie wraca i wymaga własnego aneksu, tu i w ADR-0007.

5. **ADR-0005 (model dostępu): bez zmian, bez nowego uprawnienia dla zapisu.** Zapis do `audit_log`
   jest efektem ubocznym akcji już bramkowanej `PROJECT_EDIT` (zatwierdzenie), nie osobną akcją
   użytkownika wymagającą własnej zgody — nikt nie woła zapisu audytu wprost, więc nie ma czynności,
   której nowe uprawnienie miałoby chronić. Odczyt historii (SC-8-02, poza zakresem tego zadania)
   wymaga WŁASNEGO rozstrzygnięcia uprawnień — nazwanego już w Issue #14 jako "kontrola dostępu do
   zasobu historii (F-13) — razem z zadaniem odczytu lub z ADR uwierzytelniania". Ten aneks tego nie
   rozstrzyga i nie powinien: pisanie i czytanie historii mogą mieć uzasadnienie w różnych kręgach
   uprawnionych (ten sam argument ziarnistości akcji, aneks 2026-09-18, pkt 1), a SC-8-01 nie
   wystawia żadnej ścieżki odczytu.

6. **Zasięg (ADR-0001): bez nowej decyzji dla zapisu.** Wiersz `audit_log` opisuje zdarzenie na
   scenariuszu już rozwiązanym przez `scenario_in_scope`/`project_for_caller` PRZED zapisem (patrz
   `approve_scenario`) — dokładnie tak jak migawka dziedziczy zasięg scenariusza (ADR-0005, aneks
   2026-09-22 SC-3-02, pkt 8: "Migawka dziedziczy zasięg scenariusza… mimo że ich treść pochodzi z
   tabeli organizacyjnej bez zasięgu"). Nic nowego do rozstrzygnięcia dla zapisu; filtr zasięgu dla
   ODCZYTU historii jest pytaniem SC-8-02, nie tego zadania.

7. **Czego ten aneks nie obejmuje.** Kształt dokładnych kolumn i typów (poza decyzjami 1–2 powyżej),
   treść pola `action_type` dla akcji innych niż zatwierdzenie, mechanizm odczytu historii i jego
   uprawnienia (SC-8-02), oraz atrybucja autorstwa wykraczająca poza dzisiejszą tożsamość placeholder
   (ADR uwierzytelniania) — wszystkie jawnie poza zakresem SC-8-01 (Issue #14, sekcja "Out of
   scope").

### 2026-09-27 — rejestr osób poza migawką mimo dziedziczenia; przypisanie osoby jako kolumna grupy 2 (SC-2-06)

> Przyjęty przez człowieka na bramce 1 SC-2-06 (Issue #31), 2026-09-27. Ocena wpływu: `ADR-0019-dane-osobowe-rejestr-osob.md` pkt 7.

Aneks 2026-09-19 (SC-3-01) pkt 1 ustala kryterium: "Wartość dziedziczona spoza scenariusza →
migawka", a pkt 4 każe przypisać grupę każdej nowej tabeli w chwili powstania. Rejestr osób
(SC-2-06) jest tabelą **spoza scenariusza**, której wartość (imię) jest widoczna przy pozycji
scenariusza — literalnie kryterium kierowałoby ją do migawki. Ten wpis zapisuje odstępstwo wprost,
zamiast zostawić je milczeniu, które pkt 4 nazywa ryzykiem.

1. **Rejestr osób — dana organizacyjna, bez migawki; odstępstwo od pkt 1 aneksu SC-3-01, nazwane.**
   Imię nie wchodzi do żadnego wyliczenia (koszt, przychód, pojemność, wynik — kryterium K-04
   SC-2-06), a migawka nie ma ścieżki UPDATE (aneks SC-3-02 pkt 3) — imię w migawce byłoby daną
   osobową niemożliwą do sprostowania (RODO art. 16) ani usunięcia (art. 17). Zatwierdzony
   scenariusz pokazuje imię **bieżące** (albo znacznik anonimizacji). Precedensy tej samej klasy:
   pola opisowe Projektu (aneks 2026-09-18, grupa 1 — "jawne, świadome ograniczenie odtwarzalności
   nagłówka raportu") i etykieta kategorii kosztu (aneks SC-5-05 pkt 2). **Zbiór `SNAPSHOT_TABLES`
   i tabel `approved_snapshot_*` bez zmian.** Brak wpisu rejestru osób w `SCENARIO_CHILD_COPIERS`
   jest poprawnością, nie pominięciem (precedens: katalog, aneks 2026-09-19 SC-2-01).
   **Warunek ponownego rozpatrzenia:** pierwsza wartość na wierszu osoby wchodząca do wyliczenia
   (stawka indywidualna, Q-1/Q-2) — ta wartość jest dziedziczona i wchodzi do migawki na zasadach
   pkt 1 aneksu SC-3-01, a jej Story musi rozstrzygnąć razem z Story usuwania, co anonimizacja robi
   z zamrożoną stawką (ADR-0019 pkt 5.3).
2. **Przypisanie (`staffing_position.person_id`, nullable) — kolumna tabeli już w grupie 2,
   grupa potwierdzona na poziomie kolumny** (wzorem aneksu 2026-09-25 SC-5-03 pkt 1): wartość
   ustawia planista w scenariuszu; nic spoza scenariusza jej nie zmienia. Chroni ją strażnik zapisu
   `approved` w tej samej instrukcji co zapis, z testem odmowy i testem wyścigu dwóch połączeń **per
   ścieżka zapisu przypisania** (warunek aneksu SC-3-01). Kolumna, nie osobna tabela — decyzja
   człowieka na bramce 1 SC-2-06 (model A, 2026-09-27).
3. **Sprostowanie osoby nie jest zapisem do zatwierdzonej kalkulacji.** Wiersz osoby nie jest
   dzieckiem scenariusza; strażnik `approved` go nie obejmuje i nie wolno go na niego rozciągać.
4. **Kopiowanie (aneks 2026-09-18 pkt 4; reguła 17 Strażnika).** Kopia pozycji niesie **ten sam**
   `person_id` — odniesienie do danej organizacyjnej, jak `role_id` czy `location_id`, nie
   "współdzielona referencja do danych źródłowego scenariusza" w rozumieniu AC-02: osoba nie jest
   daną scenariusza. Kopia osoby byłaby błędem (dwa rekordy tej samej osoby, sprostowanie trafia w
   jeden). Przez istniejący kopiujący agregatu pozycji (refleksja po mapperze) — bez nowego wpisu w
   `SCENARIO_CHILD_COPIERS`; test dryfu kolumn pozycji przezbrajany o `person_id` po stronie
   "kopiowane" (mapa wpływu SC-2-06, L-1). Kanarek: kopia wskazuje tę samą osobę, liczba osób bez
   zmian, zdjęcie przypisania na kopii nie rusza źródła.
5. **Token współbieżności ADR-0007 — bez nowej decyzji.** Kolumna na wierszu pozycji uczestniczy w
   `staffing_position.updated_at` automatycznie (precedens aneksu SC-5-03 pkt 4). Wiersz osoby ma
   własny `updated_at` na ścieżce sprostowania — zastosowanie "Konsekwencji" ADR-0007 ("ten sam
   wzorzec … reużyć"), nie nowy mechanizm.
6. **Aneks 2026-09-22 SC-3-02 pkt 1** (nieobecność "przeniesiona na poziom osoby … zmienia grupę z 2
   na 1") — **nieuruchomiony**: SC-2-06 nie przenosi nieobecności na osobę.
7. **Historia zmian (aneks 2026-09-18 "historia zmian… odłożona"; SC-8-01).** Przypisanie,
   zdjęcie przypisania, utworzenie i sprostowanie osoby dołączają do listy zdarzeń pod odłożonym
   `audit_log`. Ograniczenie wiążące SC-8-01: historia zapisuje identyfikator osoby, nie imię
   (ADR-0019 pkt 11).

| Kontrola | Kryterium akceptacji |
|---|---|
| A4-31-1 | Sprostowanie imienia osoby przypisanej w scenariuszu `approved` się udaje, a liczba wierszy każdej tabeli `approved_snapshot_*` i zbiór tych tabel są identyczne przed i po. |
| A4-31-2 | Przypisanie i zdjęcie przypisania w scenariuszu `approved` odmówione w tej samej instrukcji co zapis; zatwierdzenie tuż przed instrukcją i wyścig dwóch połączeń dają odmowę bez zapisu; na `draft` — sukces (kontrast). |
| A4-31-3 | Duplikat scenariusza i kopia projektu wskazują tę samą osobę; liczba wierszy rejestru osób bez zmian; liczba pozycji rośnie (kontrast). |

### 2026-09-28 — przypisanie osoby poza znacznikiem `updated_at` pozycji; zmiana pkt 5 aneksu 2026-09-27 (SC-2-06, bramka 2)

**Status:** Accepted (decyzja człowieka 2026-09-28, przed bramką 2 SC-2-06, Issue #31)

> Skutek decyzji człowieka D-4 = B (2026-09-28, Issue #31; security-auditor B-01). Mechanika:
> ADR-0007 aneks 2026-09-28. Aneks 2026-09-27 pozostaje Accepted; ten wpis zmienia jego pkt 5 jawnie.

1. **Pkt 5 aneksu 2026-09-27 („kolumna na wierszu pozycji uczestniczy w `staffing_position.updated_at`
   automatycznie") przestaje obowiązywać dla `person_id`.** `person_id` ma własny znacznik
   (`person_assignment_updated_at`, nazwa robocza), a `updated_at` pozycji nie zmienia się przy
   przypisaniu ani zdjęciu. Precedens aneksu SC-5-03 pkt 4 obowiązuje nadal dla każdej innej kolumny
   pozycji. Uzasadnienie: `updated_at` jest widoczny bez `PEOPLE_READ`, więc jego przesunięcie było
   wyrocznią przypisania (ADR-0019 pkt 4).
2. **Grupa danych bez zmian.** Znacznik przypisania jest kolumną grupy 2 na tabeli już w grupie 2 —
   ustawiany wyłącznie ścieżką przypisania, pod tym samym strażnikiem `approved` w tej samej instrukcji
   (A4-31-2 obowiązuje bez zmian brzmienia).
3. **Kopiowanie (pkt 4 aneksu 2026-09-27):** kopia niesie ten sam `person_id` i **nie** niesie
   znacznika przypisania źródła — dostaje własny (ADR-0007 aneks 2026-09-28 pkt 5). Brak nowego
   wpisu w `SCENARIO_CHILD_COPIERS`.
4. **Migawka:** zbiór tabel `approved_snapshot_*` i ich kolumn bez zmian.

### 2026-09-25 — Fixed Price details as own data; the snapshot unchanged (SC-4-02, gate 1)

The obligation of the SC-3-01 addendum, point 4: a new table gets its group assignment the moment it
is created. SC-4-02 (Issue #66) creates the Fixed Price details table (ADR-0003, addendum 2026-09-25
SC-4-02).

1. **Group 2 — write guard, not snapshot.** The agreed price is entered directly into the scenario;
   nothing outside the scenario changes it (the "direction of inheritance" criterion, SC-3-01
   addendum). Consequences directly from the SC-4-01 addendum, point 1a: every write path of the
   table (the creation and the `UPDATE` edit — D-6 resolved at gate 1: editing in a draft allowed) is
   refused under `approved` in the same statement as the write; a refusal test and a two-connection
   race test **per write path**. No new shape of the guard.
2. **Copying: the same aggregate entry in `SCENARIO_CHILD_COPIERS`** (SC-4-01 addendum, point 1b),
   the details table pointed at by the model registry, never by name. This is the first details
   table carrying domain values — copied by reflection excluding only the key and the copy's own
   timestamps. Canary: a copy (scenario duplication SC-6-01 and project copy SC-1-03) has a rule and
   a details row with new identifiers, with the price and currency equal to the source; a price
   change on the copy does not change the source (AC-02).
3. **The snapshot with no new table and no change of scope.** The price is not frozen (group 2). The
   `_snapshot_statement` copier does not branch on `model_type`: for a Fixed Price scenario with
   allocations it still freezes the windows of months priced by the selling or the cost predicate
   (SC-5-01 addendum, point 1). The cost windows are needed (the cost is independent of the revenue
   model — F-06); the windows priced by the selling predicate alone are frozen for such a scenario
   with no reader. Named and accepted: `model_type` is immutable, so this is a harmless surplus, and
   branching the copier on the commercial rule would tie it to the revenue path (the principle of
   point 3 of the SC-5-01 addendum).
4. **Price adjustments — outside SC-4-02 (D-3 = C at gate 1); direction for their task:** group 2,
   a grandchild in the same aggregate (one copier entry), with no concurrency marker of its own. An
   "approved adjustment" is not an approval of a calculation in the sense of this ADR and does not
   create a snapshot — two different concepts under one word must have different names in the
   schema.
5. The creation and the edit of the price generate events under the deferred `audit_log` condition
   (addendum 2026-09-18 "historia zmian… odłożona" — change history deferred) — this joins the list
   of tasks with the same closure.

**Sync with `main` (2026-09-28, human decision on Issue #66, option A).** On `main`,
`outcome_terms` (SC-4-03) became the first details table carrying domain values before this entry
landed; point 2 is otherwise unchanged — Fixed Price is copied by the same reflection, with its own
set of columns not copied (`app.data.commercial_terms.DETAIL_COLUMNS_NOT_COPIED_BY_MODEL`), and
`scope_ref` (SC-4-05) is remapped above the model dispatch identically for every model. Point 5
still holds after SC-8-01: the 2026-09-27 addendum creates `audit_log` for the approval of a
scenario only, with a closed `action_type` vocabulary, so the Fixed Price write events stay on the
deferred list together with those of SC-6-01 point 5 and SC-2-06 point 7. The approval snapshot
(SC-8-01 did not change its set of tables) still freezes nothing of the price (point 3).

| Control | Acceptance criterion |
|---|---|
| FPS-1 | A price write (creation and edit) to an `approved` scenario is refused, also when the approval commits between the read and the write on a second connection. |
| FPS-2 | A copy of a scenario with a Fixed Price rule has a rule and details with new identifiers, with an equal price and currency; editing the copy does not change the source. |
| FPS-3 | A copy of an approved Fixed Price scenario has zero snapshot rows; the revenue of an approved Fixed Price scenario equals the draft revenue before the approval and does not change after a catalogue edit. |

### 2026-09-28 — Fixed Price price edits leave no author or time trace: accepted exception (SC-4-02, Issue #66, post-review)

> Human decision of 2026-09-28 on Issue #66 ("Human decisions after the Reviewer STOP", Security
> finding 2). It supplements point 5 of the SC-4-02 addendum above. That text stays unchanged.

1. **Accepted exception.** Creating or editing a Fixed Price price on a draft records neither who
   made the change nor when. `audit_log` covers only the approval of a scenario (the SC-8-01 scope,
   addendum 2026-09-27, with a closed `action_type` vocabulary). Every other draft edit is in the
   same position, so this is not a new category of gap.
2. **Known gap, named for a future task.** A task that logs edits in `audit_log` must include the
   Fixed Price price writes (creation and `UPDATE`) together with the other deferred draft edits
   (SC-6-01 point 5, SC-2-06 point 7). Until then, the revenue of a Fixed Price draft can change
   before approval with no trace of who changed it.

### 2026-09-29 — Holiday provenance on the frozen calendar day (SC-3-08, Issue #165, gate 1)

**Status:** Draft — pending approval

> Prepared by the Architect for gate 1 of Issue #165 (human answers Q13, Q17 recorded there). The
> provenance itself, the import and the egress policy are ADR-0020; this entry records only what the
> approval snapshot must do about them. Earlier text of this file stays unchanged.

`working_calendar_day` rows gain provenance (which writer, holiday name, country, year — ADR-0020,
decision 3) once an import can write them. A day is a group-1 (inherited) value: the calendar and its
days are frozen by the approval (addendum 2026-09-22 SC-3-02, points 2–3), so the provenance of a
frozen day must be frozen with it, or an approved scenario cannot say where one of its non-working
days came from after the source row changes.

1. **No new snapshot table; columns on the existing one.** The four provenance facts (`source`,
   name, country code, year) join `approved_snapshot_working_calendar_day`, the way the leave-type
   flag joined `approved_snapshot_absence_type` (addendum 2026-09-22 SC-3-03, point 8) and the
   surcharge joined `approved_snapshot_catalog_default_rate` (addendum 2026-09-25 SC-5-02, point 2).
   `SNAPSHOT_TABLES` and `ApprovalResult` are unchanged, so no counter canary fails for this reason;
   the schema tests that assert the column set of the snapshot day table change deliberately.
   Verified against `app.models.approved_snapshot.ApprovedSnapshotWorkingCalendarDay` (today
   `scenario_id`, `source_calendar_id`, `day`, `kind`) and
   `app.data.scenario_approval._copy_calendar_days` (its `from_select` column list is exactly those
   four plus `id`).
2. **Copied in the same statement, verbatim.** `_copy_calendar_days` stays one CTE of
   `_snapshot_statement` (S-01: one statement, one snapshot of the catalogue) and copies the four
   values from the source row; the deduplicating `SELECT DISTINCT` covers them like every other
   copied value. The names are identical to the live table's, and none begins with `source_` — in
   snapshot tables that prefix is reserved for the identifier of the source row
   (`source_calendar_id`). As on every snapshot table, no CHECK constraint is repeated: the snapshot
   records what was approved and does not re-judge it (`ApprovedSnapshotWorkingCalendar`
   docstring); the live table refuses an unknown origin (ADR-0020, E-04).
3. **No default on the snapshot table — at rest.** `source` is `NOT NULL` on the snapshot with no
   server default (rule of the module docstring: "a snapshot column with a default is a column that
   can be written without a value having been read from the source"); the other three are nullable
   because a hand-entered day has no country or year. Snapshot rows that exist before the migration
   are backfilled `manual` — true by construction, since before SC-3-08 no other writer of a
   calendar day existed (the reasoning `a7c2e5f81b94` used for `is_statutory_leave`).
4. **The mutation this shape exists to kill.** Add the columns to the live table and leave
   `_copy_calendar_days` at its five-column list. With a default on the snapshot column, an
   approved scenario freezes an imported holiday as `manual` — silently, for ever, with no `UPDATE`
   path to repair it. Without a default, the approval fails and writes nothing (one transaction).
   The criterion PROV-1 is worded so that the first mutation is red.
5. **Deployment order (ADR-0001: expand → deploy → contract) — needs a human choice.** Proposed:
   (i) an expand migration adds the columns, backfills `manual` and leaves a temporary default on
   the snapshot columns; (ii) the code that copies the columns and that can import is deployed;
   (iii) a contract migration drops the snapshot default; (iv) only then is the first import run.
   Step (iii) is before step (iv) on purpose: while the default exists, a copier that omits the
   columns mislabels an imported day silently. Alternative: one migration that drops the default
   immediately (the shape SC-5-02 used). During the window between that migration and the code
   deploy, the previous code's five-column copier would meet a `NOT NULL` column with no default and
   every approval would be refused — loudly, with nothing written, but in conflict with ADR-0001's
   "always backward compatible". If chosen, it is a named deviation from ADR-0001, dated here.
6. **External text in an immutable table, bounded.** The holiday `name` is text from an external
   service written into a table with no `UPDATE` or `DELETE` path. It is bounded to the length of
   the other names the snapshot freezes (200), free of control characters and validated before it
   is written (ADR-0020, decision 7), so a bad value that reaches the frozen table is bounded in
   size and form even though its content is not vetted. It is not personal data (public holiday
   names), so the erasure/rectification question ADR-0005 (addendum 2026-09-22 SC-3-03, point 6)
   raised for free text in the snapshot does not apply; if that ever changes for this column it
   returns as a dated entry.
7. **A frozen scenario does not move when an import runs — with one named exception.** An import
   inserts into the live calendar only. An approved scenario reads calendar days from the snapshot
   wherever a snapshot reader exists (`app.data.working_calendar.frozen_basis_by_location`; the
   paid-absence cost since addendum 2026-09-23 SC-5-06, point 1; the FTE conversion, ADR-0008
   addendum 2026-09-29). The exception is the one already named and not repaired in SC-5-06,
   point 4: the staffing grid's capacity of an approved scenario still reads the live calendar, so
   from the first import onwards an import can change working days shown for an approved
   calculation. This entry does not repair it; it records that the import makes an existing accepted
   drift reachable by a new writer (ADR-0020, proposal (i)).
8. **Provenance is a label, never an input.** No calculation reads `source`, name, country or year:
   `CalendarBasis.exceptional_days` stays a mapping from day to kind, and `is_working_day` does not
   branch on where a day came from. Treating an imported day differently from a hand-entered one
   would make provenance a second rule for "is this a working day" (Invariant Guardian, rule 13).

| Control | Acceptance criterion |
|---|---|
| PROV-1 | After approving a scenario whose calendar holds imported and hand-entered days, each frozen day row carries the same source, name, country code and year as its live row; editing the live row, or importing more days, afterwards changes no frozen row. |
| PROV-2 | Once the deployment order of point 5 is complete, none of the provenance columns of `approved_snapshot_working_calendar_day` has a server default (asked of the migrated database), and an `INSERT` omitting `source` is refused. |
| PROV-3 | `SNAPSHOT_TABLES` and `ApprovalResult` are unchanged; the approval is still one statement with six data-modifying CTEs. |
| PROV-4 | Snapshot day rows written before the migration read `source = manual` with no country, year or name. |
| PROV-5 | Two calendars that differ only in provenance labels give the same working-day count, capacity, FTE conversion and paid-absence cost. |
| PROV-6 | The paid-absence cost and the FTE conversion of an approved scenario are identical before and after an import into its source calendar. |

### 2026-09-29 — SC-5-08 (Issue #80, daily and monthly cost rates): the cost-rate unit is frozen with the rate; the calendar basis is already frozen

**Status:** Accepted (human decision 2026-09-29, by merging the ADR acceptance PR for SC-5-08; code merged in #167)

> Gate 1 of SC-5-08 (2026-09-29, Q-1 = B, Q-4, Q-6). A separate entry, not a note under the SC-5-02
> addendum of 2026-09-25: points of that addendum are cited by number in code and tests. Where a
> sentence of an earlier entry says the frozen cost rate is an hourly rate, this entry narrows it as
> stated below; the earlier text stays unchanged.

1. **`cost_rate_unit` is a new column of the existing snapshot table
   `approved_snapshot_catalog_default_rate`, not a new table.** It is frozen on the same row, in
   the same `_snapshot_statement`, and in the same transaction as `default_cost_rate` (the pattern of
   the SC-5-02 addendum, point 2: "a new column on an existing snapshot table"). Group 1 (a value
   inherited from outside the scenario), by the criterion of the SC-3-01 addendum. No change to
   `SNAPSHOT_TABLES`, none to `SCENARIO_CHILD_COPIERS`; the canary "a copy of an approved scenario
   has zero snapshot rows" is unchanged.
2. **The snapshot copier lists its columns explicitly** (`_copy_catalog_default_rates`: one select
   list and one `from_select` column list). A column missing from the list must not be filled
   silently: with a `DEFAULT 'hour'` on the snapshot column it would be, and an approved scenario
   would be priced hourly for ever from a monthly catalogue rate — a silent AC-10 regression with no
   catalogue edit involved (the class of the SC-5-02 and SC-5-03 warnings about closed column
   lists). **Decided at verification (reviewer R-01, human 2026-09-29): the migration adds the
   snapshot column with `DEFAULT 'hour'` only to backfill existing rows and drops the default in the
   same revision** (`ALTER COLUMN … DROP DEFAULT`), like the SC-5-02 pair; an insert that omits the
   column fails on `NOT NULL`. The live catalogue column keeps `NOT NULL DEFAULT 'hour'`. Required
   proof: a
   `month`-rate window frozen by the approval carries `month` on the snapshot row, and a later
   edit of the catalogue unit moves nothing on it. The mutation "unit dropped from the copier" and
   the mutation "unit dropped from the snapshot reader" are killed separately.
3. **Backfill: existing snapshot rows and existing catalogue rows get `hour`; this is a fact, not a
   guess.** Before this task the catalogue refused any unit but `hour` by a `CHECK`
   (`unit_is_hour`), so every cost rate ever frozen was hourly. The backfill therefore reproduces
   what those approvals priced; it does not invent a unit. This differs deliberately from the
   SC-5-02 addendum, point 5 ("no retroactive action": the surcharge did not exist at the time,
   so "absent" was the honest state) — here the value did exist, implicitly, and the column makes
   it explicit.
4. **The snapshot reader reads the unit as part of the cost predicate** (ADR-0013, addendum
   2026-09-29 SC-5-08, point 2): the scenario's own frozen rows are re-asked the predicate per
   month, with the unit among the values that must be single across the month's windows. A unit
   change between two frozen windows of one month is `no_cost_rate` for the approved scenario
   exactly as for a draft.
5. **Finding — the calendar basis is frozen at approval, and its reader already exists.** Verified
   in code (2026-09-29, worktree of SC-5-08):
   - `app.data.scenario_approval._copy_calendars` freezes, per location of the scenario's positions
     that has a `calendar_id`, the calendar's name, `standard_hours_per_day` and `week_pattern` into
     `approved_snapshot_working_calendar`, keyed by `scenario_id` with `source_location_id` and
     `source_calendar_id` as plain values; `_copy_calendar_days` freezes the **complete** set of
     exceptional days of each frozen calendar into `approved_snapshot_working_calendar_day`. Both
     are CTEs of the one `_snapshot_statement` (the SC-3-02 and SC-3-03 addenda, points 3 and 5).
   - `app.data.working_calendar.frozen_basis_by_location(session, scenario_id)` reads only those
     rows, keyed by `source_location_id`; a location with no frozen calendar is absent from the
     mapping. `app.data.paid_absence_cost` already uses it for an approved scenario
     (`scenario.status == APPROVED` → frozen, otherwise `basis_by_location`).
   - `working_days_in_month` (`app.domain.capacity`) is computed from that basis, so both inputs
     of the day and month conversions — `standard_hours_per_day` and the working-day count of a
     month — are reproducible from the snapshot. **No new snapshot table and no new column is
     needed for the calendar basis.** The reproducibility obligation of the base cost is that it
     reads `frozen_basis_by_location` for an approved scenario and never a live calendar; proof
     is the criterion of point 3d of the SC-3-02 addendum applied to the cost: editing the source
     calendar, its days or a location's `calendar_id` after the approval moves no figure of the
     approved cost.
   - **Two consequences, named and not repaired.** (a) A location that had no calendar at approval
     froze none, so an approved scenario with a day/month position there is `no_calendar` for ever,
     whatever calendar the location gets later — the snapshot has no `UPDATE` path, and the state
     is the honest one (it is what the same scenario answered on the day of approval). (b) The
     docstring of `_copy_calendars` says the `no_calendar` state "is derived at read time from the
     live schema"; the reader derives it from the absence of a frozen key
     (`frozen_basis_by_location`, "never a lookup in the live catalogue"). The reader's behaviour
     is the binding one; the docstring is stale wording, left for the developer to correct with the
     code.
6. **Downgrade (Q-6).** The migration's downgrade refuses while any catalogue or snapshot row
   carries a non-`hour` cost-rate unit, because dropping the column would silently reprice those
   rows hourly; the refusal echoes no row values. This is the expand/contract discipline of
   ADR-0001 applied to a snapshot column that cannot be repaired after the fact.

| Control | Acceptance criterion |
|---|---|
| SU-1 | An approval freezes the `cost_rate_unit` of every cost window it reads on the snapshot row; a later edit of the catalogue unit changes no snapshot value; an approved scenario is priced from the frozen unit. |
| SU-2 | Existing catalogue and snapshot rows carry `hour` after the migration; the live column is `NOT NULL DEFAULT 'hour'`, the snapshot column `NOT NULL` with no default; a `CHECK` admits only `hour`, `day`, `month` on the live table. |
| SU-3 | The migration's downgrade refuses, echoing no row values, while a non-`hour` row exists in the catalogue or in the snapshot table, and succeeds otherwise. |
| SU-4 | An approved day/month-rate scenario is priced from `approved_snapshot_working_calendar` and its days: editing the source calendar, its days or the location's `calendar_id` after the approval changes no figure of the cost. |
| SU-5 | A copy of an approved scenario has zero snapshot rows, the new column included in what is not copied. |

### 2026-09-29 - scenario risks and risk reserves (SC-6-08, ADR-0021)

`scenario_risk` and `risk_reserve` are group 2: own data of the scenario, a write refused under `approved` in the same statement as the status read, no snapshot. Setting or clearing `additional_cost.risk_id` is a write under the same guard. Copy: `copy_scenario_risks` is an entry of `SCENARIO_CHILD_COPIERS` that runs **before** `copy_staffing_positions` (position-level costs are copied inside it and need the risk mapping); `copy_scenario_reserves` follows the additional-cost copier. Links are remapped by `UNIQUE(scenario_id, name)` of the risk, never through an id channel (the ADR-0016 / SC-4-05 pattern); `risk_id` is in `ADDITIONAL_COST_COLUMNS_NOT_COPIED`. The registry has six entries. Proof: `backend/tests/test_risk_guards.py`, `backend/tests/test_risk_copy.py`.

### 2026-09-30 — Fixed Price price adjustments stay outside the approval snapshot (SC-4-09, Issue #112)

**Status:** Accepted

> Gate-1 choices approved by the human on 2026-09-30. This resolves the adjustment direction
> recorded in the 2026-09-25 SC-4-02 addendum, point 4; it does not change calculation approval.

1. **Adjustments are group-2 scenario data.** Each adjustment is a child of the Fixed Price rule
   in the scenario aggregate, with no concurrency marker of its own. The adjustment's
   `pending`/`approved`/`rejected` lifecycle is distinct from the scenario's draft/approved
   lifecycle. Approving an adjustment does not create or alter a calculation snapshot.
2. **No snapshot copy or freeze.** Adjustment rows are not members of `approved_snapshot_*` and
   are not copied into a snapshot. An approved calculation's revenue reads only the adjustments
   that were approved before scenario approval; after approval, the aggregate's immutable-write
   guard prevents adjustment creation, editing, or state transition, keeping that revenue fixed.
3. **Scenario copies copy adjustments as pending.** The Fixed Price aggregate copier copies each
   adjustment's kind, amount, and currency into a new row with a new identifier. The copy starts
   each such row as `pending`, regardless of the source decision, so approval is specific to the
   scenario and the copied adjustment must be approved separately before affecting revenue.
4. **A correction is a new row.** Approved and rejected adjustments are terminal; no adjustment
   is revised or reopened. A replacement or correction is a separate pending adjustment.

| Control | Acceptance criterion |
|---|---|
| FPS-AJ-1 | Approval snapshots contain no adjustment rows; copying a scenario copies adjustment kind, amount, and currency into new pending rows with new identifiers, and none affect revenue before approval. |
| FPS-AJ-2 | A scenario approved with a set of approved adjustments returns the same Fixed Price revenue after attempted adjustment writes, which are refused. |
