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
