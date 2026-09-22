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
