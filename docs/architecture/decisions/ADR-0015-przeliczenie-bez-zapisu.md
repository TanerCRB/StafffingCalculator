# ADR-0015 — Przeliczenie bez zapisu ("what-if"): wzorzec, słownictwo, granica reużycia

**Status:** Draft — pending approval (2026-09-24, bramka 1 SC-6-04)

> Projekt przygotowany przez rolę Architekta na bramkę 1 SC-6-04 (Issue #88). Rekomendacje
> Architekta poniżej — Q-A (słownictwo `rate_source`) zaakceptowana przez człowieka 2026-09-24
> (opcja A, bez zastrzeżeń); treść poniżej już to odzwierciedla.

## Kontekst

F-09 pkt 3 (analiza wrażliwości): PM chce zobaczyć wpływ hipotetycznej zmiany jednej zmiennej
wejściowej na wynik scenariusza, bez ręcznego przeliczania i bez trwałej zmiany danych scenariusza.
SC-6-04 (pierwsza z czterech Story tego bloku — podwyżka wynagrodzeń) jest pierwszym zadaniem w tym
repozytorium budującym mechanizm "przelicz bez zapisu, na podstawionym wejściu". Żaden istniejący
ADR nie adresuje tej kategorii operacji: ADR-0004 opisuje wersjonowanie i niemutowalność
ZAPISANYCH kalkulacji (zatwierdzenie, kopiowanie) — what-if nigdy nie zapisuje niczego, więc strona
zapisu ADR-0004 (`scenario_guard`, `SCENARIO_CHILD_COPIERS`) jest strukturalnie nieadresowana.
ADR-0013 definiuje formułę kosztu osobowego i zamknięty słownik `rate_source` (`LIVE_CATALOG` /
`APPROVED_SNAPSHOT`) — wynik hipotetyczny nie jest żadnym z nich.

## Decyzja

1. **Mechanizm: podstawienie w istniejące funkcje domenowe, nigdy nowa, niezależna ścieżka
   licząca.** What-if wywołuje `app.domain.personnel_cost.base_personnel_cost`,
   `app.domain.paid_absence_cost.paid_absence_cost`, `app.data.personnel_cost._worked_months` i
   `app.data.paid_absence_cost.paid_absence_months` (nie tylko pierwsze dwie — patrz pkt 3; ostatnie
   dwie mieszkają w osobnym module `paid_absence_cost`, nie w `personnel_cost` — poprawka
   2026-09-25, deweloper SC-6-04 zaimportował z właściwych modułów mimo tej nieścisłości tekstu)
   oraz `app.domain.scenario_results.scenario_profitability`, na PODSTAWIONYM zestawie stawek, zamiast
   pisać własną arytmetykę. "Jedna funkcja, trzy miejsca" (ADR-0013 pkt 6: live/kopier/migawka)
   staje się "jedna funkcja, cztery miejsca" — what-if jako czwarty wywołujący, nie równoległy
   mechanizm.
2. **Bezpieczeństwo "bez zapisu" wynika ze struktury danych, nie z dyscypliny wołającego.**
   `WorkedMonth`, `MonthCostRate`, `CostRateWindow` to zwykłe `@dataclass(frozen=True)`, budowane
   świeżo z `sa.Row` wewnątrz `_worked_months` — nigdy nie były mapowane przez SQLAlchemy ani
   śledzone przez sesję. `dataclasses.replace` na takim obiekcie nie może przyczepić mutacji do
   identity map sesji. Ani `app.domain.personnel_cost`, ani `app.domain.scenario_results` nie
   przyjmują `Session` jako argumentu. Jedyny obiekt mapowany ORM w tej ścieżce (`Scenario`, przez
   `session.refresh`) jest wyłącznie czytany (`.status`, `.currency`), nigdy przypisywany.
   Dowód (nowy wzorzec testowy, zbudowany w SC-6-04, poprawiony 2026-09-25 po znalezisku QA): (a)
   **surowy odczyt `SELECT` wiersza scenariusza, POZA identity mapą sesji, z kanarkiem
   `updated_at`, przed i po wywołaniu what-if** — `session.new`/`session.dirty`/`session.deleted`
   puste NIE wystarcza jako dowód: SQLAlchemy `autoflush` czyści atrybut z `.dirty` w chwili, gdy
   sesja wykona kolejną instrukcję, więc przypisanie do jedynego obiektu ORM-mapowanego w tej
   ścieżce (`scenario.name = ...`) przeżywało kontrolę session-bookkeeping mimo realnego zapisu do
   bazy (zademonstrowane mutacją, QA, 2026-09-24) — tylko raw-row re-read to wykrywa; (b) test
   grafu importów potwierdzający, że moduł what-if importuje wyłącznie wymienione funkcje domenowe
   i ich typy wejściowe — żaden model ORM, żadne wywołanie `Session.add`/`flush` w jego własnym
   źródle.
3. **Podwyżka dotyka WSZYSTKICH konsumentów wspólnego słownika stawek, nie tylko kosztu
   bazowego.** `paid_absence_cost` liczy z DOSŁOWNIE tego samego słownika stawek per-(pozycja,
   miesiąc) co `base_personnel_cost` (`rates = {(month.position_id, month.period_month):
   month.rate ...}`, ten sam obiekt karmi oba). Podstawienie musi nastąpić raz, na współdzielonej
   strukturze stawek, przed wywołaniem obu konsumentów — nigdy osobno na wejściu do jednego z nich.
4. **Q-A (nowe słownictwo, zaakceptowane 2026-09-24, opcja A): `CostAssumptionsUsed.rate_source`
   rozszerzony o trzecią wartość, `WHAT_IF_HYPOTHETICAL`.** Tańsze niż równoległa struktura danych
   (opcja B), zachowuje kształt odpowiedzi zbliżony do `GET .../results`. Warunek wiążący: każde
   miejsce w kodzie porównujące ten enum przez równość (w szczególności strażnik wyścigu
   `ScenarioResultsRaceDetected` w `scenario_results_for_caller`) musi zostać jawnie zaudytowane,
   żeby trzecia wartość nigdy nie wyciekła do porównania zaprojektowanego dla dokładnie dwóch
   stanów. What-if NIGDY nie wywołuje `scenario_results_for_caller` z podstawionym widokiem —
   buduje własną kompozycję, czytając prawdziwe (niepodstawione) źródła osobno.
5. **Status scenariusza: tylko `draft` (bramka 1, decyzja człowieka, 2026-09-24).** Nie omija to
   całej złożoności ADR-0004 — tylko stronę zapisu (brak `scenario_guard`, brak wpisu w
   `SCENARIO_CHILD_COPIERS`, bo nic się nie zapisuje). Strona ODCZYTU pozostaje dwufazowa
   (przychód czytany osobno od kosztu, jak w SC-7-01) i what-if musi odziedziczyć identyczny
   strażnik wyścigu `ScenarioResultsRaceDetected`, porównując PRAWDZIWE `rate_source` obu stron
   PRZED nałożeniem podwyżki — nigdy źródło wyprowadzone z podstawionej odpowiedzi. Scenariusz,
   który zmienia status na `approved` w trakcie żądania what-if, dostaje ten sam kształt odmowy
   404-never-403 co scenariusz spoza zasięgu (ADR-0001/ADR-0005) — nie cichy przelicz na migawce,
   nie osobny typ błędu.
6. **Kształt podwyżki: procentowa, nie kwotowa.** `default_cost_rate` to stawka godzinowa;
   ADR-0013 nie ma słownictwa dla kwoty bezwzględnej (brak odpowiedzi na pytanie "w jakiej walucie
   jest podwyżka", gdy koszt scenariusza jest już w stanie `currency_mismatch`). Procent jest
   bezwymiarowy i spójny z każdym stanem nazwanym formuły kosztu. **Dolna granica: `-100%`**
   (poprawka 2026-09-25, bramka 2, reviewer R-01) — poniżej tej wartości stawka staje się ujemna,
   co `base_personnel_cost`/`paid_absence_cost` sumowałyby bez odrzucenia, dając fizycznie
   niemożliwy, ale pewny `200` (koszt ujemny, zysk większy niż przychód, marża >100%). Dokładnie
   `-100%` zeruje stawkę (legalna wartość rzeczywista — "darmowa godzina"), nigdy jej nie neguje.
   Granica egzekwowana na warstwie API/schematu (`ge=SALARY_RAISE_PERCENT_FLOOR`), nie wewnątrz
   funkcji domenowej — przyszły wariant (SC-6-05/06/07), jeśli reużyje mnożnika przez inny punkt
   wejścia, musi ją zastosować ponownie, nie dziedziczy jej za darmo (reviewer, nienazwane ryzyko na
   przyszłość, niebllokujące).
7. **Kształt żądania: `GET`, nie bezciałowy `POST`.** Wzorzec siostrzanych akcji (`copy_project`,
   `archive`, `approve`, `duplicate` — ADR-0004 aneks SC-6-01 pkt 2) jest celowo bezciałowy, bo są
   to akcje ZAPISUJĄCE. What-if nic nie zapisuje — to odczyt z parametrem, więc `GET
   /projects/{project_id}/scenarios/{scenario_id}/what-if?salary_raise_percent=<decimal>`, wzorem
   `GET .../results` i `GET .../compare`, nie POST.
8. **Uprawnienie: `RESULTS_READ` bez zmian, żadnego nowego.** Koniunkcja kosztowa
   (`PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs`) aplikowana na polach hipotetycznego
   wyniku identycznie jak na `GET .../results` (ADR-0005 aneks 2026-09-24 SC-7-01 pkt 1) — `state`
   bez koniunkcji (aneks SC-5-01 pkt 7). Mechanizm rozszerza już nazwany, uśpiony gap B-01 (ADR-0005
   aneks SC-5-01 pkt 5, rozszerzony SC-7-01/SC-6-02): wołający z `STAFFING_READ` + `CATALOG_READ`
   może teraz przeliczać magnitudy podwyżki po stronie serwera zamiast ręcznie — to samo ryzyko,
   szerszy promień, nie nowa kategoria wycieku. Nie wprowadza żadnego pola widocznego pod słabszą
   bramką niż istniejący endpoint wyniku.

## Konsekwencje

- Pierwszy w tym repozytorium mechanizm operujący na danych, które nigdy nie dotykają sesji
  SQLAlchemy jako obiekty mapowane — precedens dla SC-6-05/06/07 (pozostałe warianty analizy
  wrażliwości), które odziedziczą ten sam wzorzec zamiast wynajdywać własny.
- `CostAssumptionsUsed.rate_source` przestaje być zamkniętym dwuelementowym zbiorem — każde
  przyszłe zadanie dodające czwarty, piąty stan tego pola musi powtórzyć audyt z pkt 4, nie
  zakładać, że lista jest nadal wyczerpująca przez sam typ.
- Fundament formuł (`base_personnel_cost`, `paid_absence_cost`, `scenario_profitability`) jest
  dowiedziony i mutation-checked (SC-5-01, SC-5-06, SC-7-01) — ryzyko tego zadania leży wyłącznie
  w nowym mechanizmie podstawienia i w nowym wzorcu dowodu "zero zapisu", nie w samej arytmetyce.

## Rozważane alternatywy

- **Osobna, węższa struktura wyniku bez `rate_source`** (opcja B dla Q-A) — odrzucona: unika
  dotykania zamkniętego słownictwa ADR-0013, ale wymusza równoległy typ danych, który rozjedzie się
  z `PersonnelCostResult`/`PersonnelCostUnavailable` przy każdym kolejnym stanie formuły kosztu
  (np. SC-5-02 narzuty).
- **Podwyżka kwotowa zamiast procentowej** — odrzucona: brak słownictwa na walutę podwyżki przy
  `currency_mismatch`, procent jest jedynym kształtem spójnym z każdym stanem formuły.
- **What-if jako bezciałowy `POST`** (wzorem `copy`/`archive`/`approve`) — odrzucona: te akcje
  zapisują, what-if nie zapisuje nigdy; `GET` z parametrem zapytania jest uczciwszym kształtem dla
  czystego odczytu.
- **Rozszerzenie zakresu what-if na scenariusze `approved`** — odłożone (nie odrzucone na zawsze):
  wymagałoby powielenia strażnika wyścigu dla DRUGIEJ, podstawionej kompozycji, dla hipotezy, której
  wartość biznesowa (testowanie podwyżki względem liczb już zamrożonych jako dostarczona prawda,
  AC-04) jest marginalna wobec tego kosztu. Osobna decyzja, gdyby zgłosiła się potrzeba.

## Powiązane wymagania

`Wymagania/Requirements_EN.md` §4 F-09 pkt 3; `docs/PLAN.md` SC-5-01, SC-5-06, SC-7-01;
`ADR-0004-wersjonowanie-kalkulacji.md` (niemutowalność, strażnik wyścigu); `ADR-0005-model-dostepu.md`
(koniunkcja kosztu osobowego, B-01); `ADR-0013-koszt-osobowy.md` (formuła, słownictwo
`rate_source`); Issue #88 (SC-6-04).

## Aneksy

### 2026-09-25 — strażnik wyścigu porównuje status scenariusza, nie `rate_source` (SC-7-03, Issue #118, bramka 1)

> Rozstrzygnięcia człowieka (bramka 1, 2026-09-25): **Q1 (A), Q2 (A), Q3 (B), Q4 (A), Q5 (A)** —
> zgodnie z rekomendacją analityka i architekta. Aneks uchyla mechanizm z pkt 4 i 5 w opisanym niżej
> zakresie; tekst tych punktów zostaje bez zmian jako zapis decyzji z 2026-09-24.

**Uzasadnienie.** Pkt 5 wiąże strażnika z porównaniem "PRAWDZIWYCH `rate_source` obu stron". Ukryte
założenie — `rate_source` przychodu i kosztu to funkcja tego samego faktu (status w chwili odczytu)
w jednym słowniku — było prawdziwe tylko dla T&M. SC-4-04 (PR #115) dodał do słownika przychodu
`story_points_terms`; audyt z pkt 4 obejmował tylko słownik kosztu. Skutek: `…/results`,
`…/compare` i what-if dla Story Points odpowiadają `409` bez wyścigu; to samo czeka Fixed Price (#66)
i Outcome-based (#67).

**Decyzja.**

1. Wykrywany fakt bez zmian: status scenariusza zmienił się między odczytem przychodu a odczytem
   kosztu. Zmienia się świadek: status odczytany przez każde z dwóch wywołań (ten, który wybrał
   źródło stawek), nie `assumptions_used.rate_source`. `rate_source` przestaje sterować jakąkolwiek
   logiką; pozostaje deskryptorem F-06.5.
2. Równoważność dla T&M: tam `rate_source = f(status)` i `f` jest różnowartościowa na
   `{draft, approved}` (ADR-0004: dwa statusy, przejście jednokierunkowe) — zbiór wykrywanych
   przeplotów identyczny. Dla modeli, których przychód nie zależy od statusu (SP; przyszłe FP/OB),
   stary strażnik był zawsze fałszywie dodatni; nowy odmawia dokładnie przy zmianie statusu —
   **jednolicie dla każdego modelu** (Q4/A: bez gałęzi zależnej od modelu; koszt i tak zależy od
   statusu, więc wynik z dwóch momentów pozostaje wynikiem niespójnym).
3. **Warunek wiążący: status to wartość zamrożona w chwili każdego odczytu.** `commercial.scenario`
   i `cost_view.scenario` bywają jednym obiektem identity mapy, odświeżanym (`session.refresh`) przez
   późniejsze wywołanie; porównanie `.status` tego obiektu po obu wywołaniach porównuje wartość samą
   ze sobą i wyłącza strażnika bez sygnału.
4. Kolejność i kształt odmowy bez zmian: po zasięgu (`404` przed `409`), przed kształtowaniem i
   bramkami kosztu; w what-if przed sprawdzeniem `draft` i przed podstawieniem stawek. Treść `409`
   generyczna (SC-7-01, R-02) — bez `rate_source` i bez statusu. Atrybuty wyjątku
   `ScenarioResultsRaceDetected` są wyłącznie wewnątrzprocesowe (nie trafiają do odpowiedzi ani
   logu); Q2/A — zastąpione statusami z obu odczytów, bez zmiany API.
5. **Q5/A — uzgodnienie pkt 5 z kodem.** Scenariusz zatwierdzony *w trakcie* żądania what-if (między
   odczytem przychodu a kosztu) dostaje `409` (wyścig), nie `404`; `404` dostaje scenariusz, który
   był `approved` przez oba odczyty. Tak zachowywał się kod od SC-6-04; ten punkt poprawia tekst
   pkt 5, nie kod.
6. Bez zmian: słowniki `rate_source` w kontrakcie (przychód `live_catalog | approved_snapshot |
   story_points_terms`; koszt `live_catalog | approved_snapshot | what_if_hypothetical` — dwa
   odrębne słowniki, ADR-0003 aneks SC-7-03), nazwana luka "dwa żywe odczyty przy edycji okna
   katalogu", zakres `draft` z pkt 5.
7. Pkt 4: wymóg audytu porównań przez równość przestaje dotyczyć strażnika wyścigu, ale obowiązuje
   dla każdego innego porównania — odtąd dla OBU słowników `rate_source`, nie tylko kosztu.
8. **Strażnik obejmuje wszystkie odczyty odświeżające scenariusz, nie tylko dwa pierwsze (bramka 2,
   reviewer R-01, decyzja człowieka 2026-09-25, opcja A).** Złożony odczyt ma trzy wywołania
   odświeżające ten sam obiekt `Scenario` (przychód, koszt, koszt dodatkowy —
   `additional_costs_for_caller`). Zatwierdzenie między drugim a trzecim nie zmieniało żadnego z dwóch
   porównywanych statusów, a trzeci `session.refresh` przestawiał współdzielony obiekt na `approved`:
   what-if liczył wtedy hipotezę na migawce zatwierdzonego scenariusza i serwował `200`, a `/results`
   zwracał `scenario_status: "Approved"` obok `rate_source: live_catalog`. Wada istniała przed SC-7-03
   (SC-6-04, SC-7-01). Każde wywołanie odświeżające scenariusz zamraża własny status, a strażnik
   odmawia `409`, jeśli którekolwiek dwa się różnią. Po ostatnim zamrożonym odczycie żadna instrukcja
   nie odświeża już scenariusza, więc każde dalsze rozgałęzienie na `scenario.status` (w tym
   `_worked_months` w what-if) i pole `scenario_status` odpowiedzi widzą status, który przeszedł przez
   strażnika. Każde przyszłe wywołanie dokładające `session.refresh(scenario)` do złożonego odczytu
   musi wejść do tego porównania.

**Kontrole.**

| Kontrola | Kryterium akceptacji |
|---|---|
| A15-1 | `…/results`, `…/compare` i what-if dla scenariusza `draft` z regułą Story Points bez współbieżnego zatwierdzenia odpowiadają `200`. |
| A15-2 | Zatwierdzenie wprowadzone realną współbieżnością dwóch połączeń między odczytem przychodu a kosztu nadal daje `409` z niezmienioną generyczną treścią. |
| A15-3 | Mutacja porównująca status ze współdzielonego obiektu ORM po obu wywołaniach jest zabijana przez co najmniej jeden test. |
| A15-4 | Odpowiedzi T&M `…/results`, `…/compare` i what-if bajt w bajt identyczne przed i po zmianie. |
| A15-5 | Treść `409` na trzech ścieżkach nie zawiera wartości `rate_source` ani statusu, niezależnie od bramki kosztu wołającego. |
| A15-6 | Rozbieżność samych `rate_source` przy zgodnym statusie nie daje `409`. |
| A15-7 | Zatwierdzenie wprowadzone realną współbieżnością między odczytem kosztu a odczytem kosztu dodatkowego daje `409` na `…/results` i what-if — nigdy `200` z hipotezą na migawce ani z `scenario_status` niezgodnym z odczytami. |
