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

### 2026-09-25 — SC-5-02 (Issue #77, narzuty): podstawienie what-if obejmuje narzut bez nowego kodu — konsekwencja rozstrzygnięta na bramce 1 (ADR-0013, Q3)

Pkt 3 "Decyzji" wyżej już stwierdza: "Podwyżka dotyka WSZYSTKICH konsumentów wspólnego słownika
stawek, nie tylko kosztu bazowego (…) Podstawienie musi nastąpić raz, na współdzielonej strukturze
stawek, przed wywołaniem obu konsumentów — nigdy osobno na wejściu do jednego z nich." SC-5-02 (F-07,
narzuty i koszt w pełni obciążony) zadał ten mechanizm wprost na bramce 1, zamiast zostawić go do
odkrycia: czy koszt w pełni obciążony, gdy stanie się trzecim konsumentem słownika stawek (obok
`base_personnel_cost` i `paid_absence_cost`), dziedziczy podstawienie automatycznie, czy wymaga
własnej, osobnej ścieżki substytucji narzutu (ADR-0013, wpis z tej daty, Q3).

**Rozstrzygnięcie (bramka 1, 2026-09-25 — zapadło jako konsekwencja Q4 ADR-0013/ADR-0005 tej daty,
nie jako osobna decyzja tego dokumentu):** narzut jest modelowany jako PROCENT od stawki bazowej
(`default_cost_rate`), nie jako kwota o własnym rozstrzygnięciu. Ponieważ narzut jest wartością
wyprowadzoną z dokładnie tej samej stawki godzinowej, którą pkt 1–3 tego dokumentu podstawiają raz na
słowniku `WorkedMonth`/`MonthCostRate` per-(pozycja, miesiąc) przed wywołaniem konsumentów, koszt w
pełni obciążony **nie potrzebuje własnej ścieżki podstawienia w what-if**: podniesienie stawki
bazowej podnosi z automatu bazę, do której narzut jest procentem, bez dodatkowego kodu w
`app.data.scenario_what_if`.

1. **Warunek, na którym ta konsekwencja stoi — SC-5-02 musi go dowieść, nie założyć.** Formuła
   narzutu musi czytać stawkę bazową z TEGO SAMEGO słownika per-(pozycja, miesiąc), który
   `_worked_months` buduje i który what-if podstawia (`rates = {(month.position_id,
   month.period_month): month.rate ...}`, pkt 3 "Decyzji") — nie z osobnego zapytania do katalogu
   wewnątrz formuły narzutu. Naruszenie tego warunku (np. formuła narzutu doczytująca
   `default_cost_rate` samodzielnie z bazy zamiast z podstawionego słownika) odtwarza dokładnie lukę,
   przed którą ostrzega pkt 3 "Decyzji" ("nigdy osobno na wejściu do jednego z nich") — koszt w pełni
   obciążony podniósłby się na żywej ścieżce, a milcząco nie w hipotezie what-if.
2. **Warunek ponownego otwarcia.** Gdyby narzut kiedykolwiek przestał być czystym procentem (wariant
   kwotowy absolutny obok procentowego — ryzyko nazwane już jako warunek ponownego rozpatrzenia w
   ADR-0013/ADR-0005, aneks tej daty, pkt 1/1 odpowiednio), ta konsekwencja przestaje obowiązywać z
   automatu: kwota absolutna narzutu nie jest wartością wyprowadzoną ze stawki podstawionej przez
   what-if i wymagałaby własnej decyzji o podstawieniu — rozszerzenia pkt 3 "Decyzji", analogicznego
   do tego, jak `paid_absence_cost` dostał tam własne miejsce jako drugi konsument. Nazwane teraz, żeby
   przyszłe zadanie wprowadzające wariant kwotowy nie odkryło tego jako nowej, nienazwanej luki.
3. **Ten aneks nie otwiera pkt 4 "Decyzji" (`rate_source`) ponownie.** Koszt w pełni obciążony, tak
   jak `base_personnel_cost` i `paid_absence_cost` dziś, dziedziczy istniejący, zamknięty trzyelementowy
   zbiór `LIVE_CATALOG`/`APPROVED_SNAPSHOT`/`WHAT_IF_HYPOTHETICAL` bez nowej wartości — SC-5-02 nie
   dodaje czwartego stanu `rate_source` tym zadaniem, i strażnik wyścigu `ScenarioResultsRaceDetected`
   nie wymaga ponownego audytu z tego powodu.
### 2026-09-25 — strażnik wyścigu porównuje statusy zamrożone przez odczyty; przychód wchodzi do porównania tylko, gdy zależy od statusu (SC-7-03, Issue #118, bramki 1 i 2)

> Rozstrzygnięcia człowieka: bramka 1 (2026-09-25) — **Q1 (A), Q2 (A), Q3 (B), Q5 (A)**; bramka 2
> (2026-09-25, reviewer R-01) — opcja A (pkt 8). **Q4 zmienione z A na B (decyzja człowieka
> 2026-09-25, po otwarciu PR #123).** Powód: kolizja z SC-4-03 (Issue #67, PR #120), scalonym do
> `main` przed SC-7-03, który naprawił ten sam objaw inaczej i ustalił na `main` semantykę "wyścig
> liczy się tylko, gdy przychód zależy od statusu" (ADR-0003, aneks SC-4-03, pkt 8, 10a, 12b;
> dowody `backend/tests/test_story_points_scenario_results.py`,
> `backend/tests/test_outcome_scenario_results.py`). Przyjęta semantyka `main`; zachowany mechanizm
> SC-7-03 (status zamrożony przez każdy z trzech odczytów odświeżających scenariusz, strażnik po
> trzecim odczycie, w what-if koszt dodatkowy przed strażnikiem i przed sprawdzeniem `draft`). Q4/A
> ("jednolicie dla każdego modelu, bez gałęzi zależnej od modelu") przestaje obowiązywać.
> Składnik (b) reguły z pkt 2 (koszt ↔ koszt dodatkowy dla każdego modelu) **potwierdzony przez
> człowieka 2026-09-25 (pkt 2b, opcja A)**; odrzucona alternatywa i jej skutek w pkt 2.
> Aneks uchyla mechanizm z pkt 4 i 5 w opisanym niżej zakresie; tekst tych punktów zostaje bez zmian
> jako zapis decyzji z 2026-09-24.

**Uzasadnienie.** Pkt 5 wiąże strażnika z porównaniem "PRAWDZIWYCH `rate_source` obu stron". Ukryte
założenie — `rate_source` przychodu i kosztu to funkcja tego samego faktu (status w chwili odczytu)
w jednym słowniku — było prawdziwe tylko dla T&M. SC-4-04 (PR #115) dodał do słownika przychodu
`story_points_terms`; audyt z pkt 4 obejmował tylko słownik kosztu. Skutek: `…/results`,
`…/compare` i what-if dla Story Points odpowiadały `409` bez wyścigu. SC-4-03 zamknął ten objaw na
`main`, zawężając porównanie `rate_source` do źródeł zależnych od statusu (`STATUS_DEPENDENT_SOURCES`)
i dodając `not_applicable` (Outcome-based). Zostały dwie wady świadka `rate_source`, których zawężenie
nie usuwa: (1) nie widzi trzeciego odświeżenia scenariusza (koszt dodatkowy, pkt 8 — R-01); (2)
porównuje wartości dwóch odrębnych słowników (ADR-0003, aneks SC-7-03, pkt 2).

**Decyzja.**

1. **Świadek: status zamrożony przez każde z trzech wywołań odświeżających scenariusz** (przychód —
   `commercial_terms_for_caller`, koszt osobowy — `scenario_cost_for_caller`, koszt dodatkowy —
   `additional_costs_for_caller`; `status_at_read`), skopiowany zaraz po własnym `session.refresh`
   tego wywołania. `rate_source` kosztu nie wchodzi do strażnika w żadnej roli. `rate_source`
   przychodu wyłącznie **klasyfikuje** przychód jako zależny albo niezależny od statusu (pkt 2) —
   nigdy nie jest porównywany z `rate_source` kosztu. Poza tym `rate_source` pozostaje deskryptorem
   F-06.5.
2. **Reguła (Q4/B) — jedna dla `…/results`, `…/compare` i what-if.** Niech `s_P`, `s_K`, `s_D` to
   statusy zamrożone odpowiednio przez odczyt przychodu, kosztu osobowego i kosztu dodatkowego.
   Strażnik odmawia `409` wtedy i tylko wtedy, gdy:
   - **(a)** przychód zależy od statusu **i** `s_P ≠ s_K`; albo
   - **(b)** `s_K ≠ s_D`.

   *Zależność przychodu od statusu:* `revenue.assumptions_used.rate_source ∈
   STATUS_DEPENDENT_SOURCES` (`live_catalog`, `approved_snapshot`; `app.domain.revenue`). Test
   przynależności do zbioru jednego słownika, nie porównanie dwóch słowników. Klasyfikacja przez
   `rate_source`, nie przez `model_type`, bo: (i) to konwencja ADR-0003 (aneks SC-4-03, pkt 10a,
   12a) — model sam deklaruje wartość spoza zbioru, jeśli nie czyta katalogu; przyszły model czytający
   katalog (Fixed Price, #66, jeśli tak zostanie zaprojektowany) trafia do zbioru bez drugiej listy;
   (ii) odpowiedzi bez modelu (`no_commercial_terms`, `model_type = None`), `unsupported_model_type`
   i niekompletna reguła T&M niosą `rate_source` wybrane ze statusu — zostają w porównaniu tak jak na
   `main` przed tym aneksem; klasyfikacja po `model_type` wymagałaby osobnej listy modeli i osobnego
   rozstrzygnięcia dla `None`/nieznanego modelu. **Kierunek awarii nazwany:** nowa wartość
   `rate_source` przychodu spoza zbioru jest traktowana jako "niezależna od statusu" — model, który
   czyta status, a zgłosi własną wartość, po cichu osłabi składnik (a). Zabezpieczenie: obowiązek
   klasyfikacji z pkt 7 i test wyścigu `/results` dla każdego nowego modelu (ADR-0003, pkt 10a).

   *Uzasadnienie (a):* ADR-0003, aneks SC-4-03, pkt 8 i 12b — przychód modelu bez katalogu czyta
   wyłącznie własne, strzeżone przed zapisem wiersze scenariusza, więc jest ten sam przed i po
   zatwierdzeniu; jego `s_P` nie utrwala momentu, od którego zależy liczba. Dla przychodu zależnego
   od statusu (T&M) `rate_source = f(status)`, `f` różnowartościowa na `{draft, approved}` (ADR-0004:
   dwa statusy, przejście jednokierunkowe) — zbiór wykrywanych przeplotów przychód↔koszt identyczny
   jak przy porównaniu `rate_source` (SC-7-01, SC-4-03).

   **Warunek ważności zwolnienia z (a) (bramka 2, reviewer R-06, decyzja człowieka 2026-09-25,
   opcja A).** "Ten sam przed i po zatwierdzeniu" nie wynika ze strażnika zapisu — ten chroni wiersz
   reguły dopiero po zatwierdzeniu, a odczyt nie trzyma blokady. Wynika z tego, że wiersze
   `story_points_terms` i `outcome_terms` nie mają dziś żadnej ścieżki edycji ani usunięcia w wersji
   roboczej (ADR-0003: aneks SC-4-04 D-5/A, aneks SC-4-03 pkt 9 — tylko tworzenie). Zwolnienie z (a)
   obowiązuje, **dopóki** tak jest. Zadanie, które doda edycję wiersza reguły modelu niezależnego od
   statusu, musi przywrócić ten przychód do składnika (a) albo dostarczyć inny dowód spójności —
   inaczej przeplot "edycja wersji roboczej → zatwierdzenie → odczyt kosztu" daje `200` z
   `scenario_status: "Approved"` i przychodem, którego zatwierdzony scenariusz nigdy nie miał.

   *Uzasadnienie (b):* `s_D` to status, który współdzielony obiekt `Scenario` niesie po ostatnim
   odświeżeniu na tej ścieżce — serializowany jako `scenario_status` w `…/results` i rozgałęziający
   what-if (sprawdzenie `draft`, `_worked_months`). Koszt osobowy zależy od statusu dla każdego
   modelu przychodu. `s_K ≠ s_D` oznacza odpowiedź `scenario_status: "Approved"` obok kosztu
   policzonego z żywego katalogu — połowa `/results` wady R-01 (pkt 8), niezależna od modelu
   przychodu. Koszt dodatkowy sam od statusu nie zależy (ADR-0004, aneks SC-5-05, pkt 1); wchodzi do
   porównania jako nośnik raportowanego statusu, nie jako wielkość zależna.

   *Dla T&M* (i każdej odpowiedzi z `rate_source` ze zbioru) reguła sprowadza się do "wszystkie trzy
   statusy równe" — zachowanie identyczne z SC-7-03 przed zmianą Q4 (A15-4).

   *Alternatywa odrzucona w rekomendacji (literalne B: odmowa tylko przy przychodzie zależnym od
   statusu, przy dowolnej niezgodności trzech statusów):* dla Story Points / Outcome-based
   zatwierdzenie między odczytem kosztu a odczytem kosztu dodatkowego daje na `…/results` `200` z
   `scenario_status: "Approved"` obok `rate_source: live_catalog` kosztu (połowa `/results` R-01
   zostaje otwarta dla modeli niezależnych od statusu; liczby równe migawce z wyjątkiem nazwanej luki
   "edycja okna katalogu"), a na what-if `404` (kolejność z pkt 4). Kontrola A15-7 dla tych modeli
   zmieniłaby oczekiwanie na `200`/`404`.
3. **Warunek wiążący: status to wartość zamrożona w chwili każdego odczytu.** `commercial.scenario`
   i `cost_view.scenario` bywają jednym obiektem identity mapy, odświeżanym (`session.refresh`) przez
   późniejsze wywołanie; porównanie `.status` tego obiektu po obu wywołaniach porównuje wartość samą
   ze sobą i wyłącza strażnika bez sygnału.
4. **Kolejność, miejsce i kształt odmowy.** Strażnik działa po trzecim odczycie (koszt dodatkowy),
   po zasięgu (`404` przed `409`), przed kształtowaniem i bramkami kosztu; w what-if przed
   sprawdzeniem `draft` i przed podstawieniem stawek. Reguła z pkt 2 żyje w **jednym** miejscu
   (`app.data.scenario_results.refuse_a_status_race`, nazwa z `main` po SC-4-03), wołanym przez
   `scenario_results_for_caller` i przez what-if — dwie kopie porównania rozjechały się już raz
   (Issue #118). Treść `409` generyczna (SC-7-01, R-02) — bez `rate_source` i bez statusu. Atrybuty
   wyjątku `ScenarioResultsRaceDetected` są wyłącznie wewnątrzprocesowe (nie trafiają do odpowiedzi
   ani logu); Q2/A — statusy z trzech odczytów zamiast par `rate_source`, bez zmiany API.
5. **Q5/A — uzgodnienie pkt 5 z kodem, zawężone przez Q4/B.** Scenariusz zatwierdzony *w trakcie*
   żądania what-if dostaje `409` wtedy, gdy zatwierdzenie wpadło między dwa odczyty, których statusy
   reguła z pkt 2 porównuje. W przeciwnym razie żądanie jest równoważne odczytowi w całości przed
   zatwierdzeniem (`200` na szkicu) albo w całości po nim (`404`, jak scenariusz `approved` przez
   wszystkie odczyty). Konkretnie:
   - przychód zależny od statusu (T&M): zatwierdzenie między dowolnymi dwoma odczytami → `409`;
   - przychód niezależny od statusu (Story Points, Outcome-based): zatwierdzenie między odczytem
     przychodu a kosztu → `404` (koszt i koszt dodatkowy widziały `approved`; przychód jest ten sam
     przed i po zatwierdzeniu); między odczytem kosztu a kosztu dodatkowego → `409` (pkt 2b).

   Na `…/results` odpowiednio: przychód niezależny od statusu + zatwierdzenie między odczytem
   przychodu a kosztu → `200` z kosztem z migawki i `scenario_status: "Approved"` — spójny wynik
   stanu zatwierdzonego (ADR-0003, aneks SC-4-03, pkt 8 i 12b; kontrola O-5, O-11).
6. Bez zmian: dwa odrębne słowniki `rate_source` w kontrakcie (przychód `live_catalog |
   approved_snapshot | story_points_terms | not_applicable`; koszt `live_catalog | approved_snapshot
   | what_if_hypothetical` — ADR-0003, aneks SC-7-03), nazwana luka "dwa żywe odczyty przy edycji
   okna katalogu", zakres `draft` z pkt 5.
7. Pkt 4: wymóg audytu porównań przez równość obowiązuje dla każdego porównania `rate_source` poza
   strażnikiem — odtąd dla OBU słowników, nie tylko kosztu. Dla strażnika obowiązek zmienia postać:
   **każda nowa wartość `rate_source` przychodu jest w tym samym zadaniu jawnie sklasyfikowana**
   jako należąca do `STATUS_DEPENDENT_SOURCES` (wybierana ze statusu scenariusza) albo nie, z testem
   wyścigu `/results` dla nowego modelu (ADR-0003, aneks SC-4-03, pkt 10a).
8. **Strażnik obejmuje wszystkie odczyty odświeżające scenariusz, nie tylko dwa pierwsze (bramka 2,
   reviewer R-01, decyzja człowieka 2026-09-25, opcja A).** Złożony odczyt ma trzy wywołania
   odświeżające ten sam obiekt `Scenario` (przychód, koszt, koszt dodatkowy —
   `additional_costs_for_caller`). Zatwierdzenie między drugim a trzecim nie zmieniało żadnego z dwóch
   porównywanych wcześniej statusów, a trzeci `session.refresh` przestawiał współdzielony obiekt na
   `approved`: what-if liczył wtedy hipotezę na migawce zatwierdzonego scenariusza i serwował `200`,
   a `/results` zwracał `scenario_status: "Approved"` obok `rate_source: live_catalog`. Wada istniała
   przed SC-7-03 (SC-6-04, SC-7-01) i na `main` po SC-4-03 (strażnik przed trzecim odczytem). Każde
   wywołanie odświeżające scenariusz zamraża własny status; strażnik stosuje regułę z pkt 2 po
   ostatnim z nich. Po ostatnim zamrożonym odczycie żadna instrukcja nie odświeża już scenariusza,
   więc każde dalsze rozgałęzienie na `scenario.status` (w tym sprawdzenie `draft` i
   `_worked_months` w what-if) i pole `scenario_status` odpowiedzi widzą `s_D`, który przeszedł przez
   strażnika. Każde przyszłe wywołanie dokładające `session.refresh(scenario)` do złożonego odczytu
   musi wejść do reguły z pkt 2: jeśli staje się ostatnim odświeżeniem, przejmuje rolę `s_D`; jeśli
   jego wynik zależy od statusu, jego status musi być zgodny z `s_D`.

**Kontrole.**

| Kontrola | Kryterium akceptacji |
|---|---|
| A15-1 | `…/results`, `…/compare` i what-if dla scenariusza `draft` z regułą Story Points albo Outcome-based bez współbieżnego zatwierdzenia odpowiadają `200`. |
| A15-2 | Scenariusz T&M: zatwierdzenie wprowadzone realną współbieżnością dwóch połączeń między odczytem przychodu a kosztu daje `409` z niezmienioną generyczną treścią na `…/results` i what-if (na what-if `409`, nie `404`). |
| A15-3 | Mutacja porównująca status ze współdzielonego obiektu ORM zamiast statusów zamrożonych jest zabijana przez co najmniej jeden test (wyścig T&M z A15-2 albo wyścig z A15-7). |
| A15-4 | Odpowiedzi T&M `…/results`, `…/compare` i what-if bajt w bajt identyczne przed i po zmianie. |
| A15-5 | Treść `409` na trzech ścieżkach nie zawiera wartości `rate_source` ani statusu, niezależnie od bramki kosztu wołającego. |
| A15-6 | Rozbieżność samych `rate_source` przy zgodnym statusie nie daje `409`. |
| A15-7 | Zatwierdzenie wprowadzone realną współbieżnością między odczytem kosztu a odczytem kosztu dodatkowego daje `409` na `…/results` i what-if dla scenariusza T&M i dla scenariusza z przychodem niezależnym od statusu — nigdy `200` z hipotezą na migawce ani z `scenario_status` niezgodnym ze statusem, przy którym policzono koszt. |
| A15-8 | Przychód niezależny od statusu (Story Points, Outcome-based): zatwierdzenie wprowadzone realną współbieżnością między odczytem przychodu a kosztu daje na `…/results` `200` z kosztem z migawki, `scenario_status: "Approved"` i spójnym zyskiem, a na what-if `404` z treścią "poza zasięgiem". |
| A15-9 | Scenariusz bez reguły komercyjnej (`no_commercial_terms`, `rate_source` przychodu wybrane ze statusu): zatwierdzenie wprowadzone realną współbieżnością między odczytem przychodu a kosztu daje `409` — mutacja klasyfikująca odpowiedź bez modelu jako niezależną od statusu (np. klasyfikacja po `model_type` zamiast `rate_source`) jest zabijana. |

### 2026-09-28 — Fixed Price revenue returns to component (a): status-independent source, but a draft edit path (SC-4-02, Issue #66)

> Human decision 2026-09-28 on Issue #66, option A. This entry applies the validity condition of
> the exemption from (a) (SC-7-03 addendum, point 2, R-06) and the classification duty of point 7 of
> that addendum. The text of earlier entries stays unchanged.

**Rationale.** SC-4-02 adds the revenue `rate_source = fixed_price_terms`
(`app.domain.revenue.RATE_SOURCE_FIXED_PRICE_TERMS`). The price is the scenario's own data and is
not chosen from the status (ADR-0003, SC-4-02 addendum, points 5 and 6). SC-4-02 also adds a draft
price edit (`PATCH`, D-6 = A, ADR-0007 marker, `approved` refused in the same statement). That
edit breaks the premise of the exemption: "the same before and after approval" held for
`story_points_terms`/`outcome_terms` only because their rows cannot be edited in a draft. Without
(a), the sequence "revenue read (draft) → price edit → approval → cost read" answers `200` with
`scenario_status: "Approved"` and a price the approved scenario never had.

**Decision.**

1. **Component (a) now covers two classes of revenue:**
   - **T&M, and every answer whose `rate_source` is chosen from the status** (`live_catalog`,
     `approved_snapshot`; `no_commercial_terms`, `unsupported_model_type`, incomplete T&M): the
     source depends on the status, so (a) applies unchanged.
   - **Fixed Price** (`fixed_price_terms`): the source does not depend on the status, but the
     rule rows have a draft edit path. The validity condition of the exemption is not met, so (a)
     applies.

   Story Points and Outcome-based stay exempt from (a). Their validity condition still holds.
2. **Classification of `fixed_price_terms` (point 7):** it is **outside**
   `STATUS_DEPENDENT_SOURCES` (the value is not chosen from the status) and it is **inside
   component (a)** (the value can change in a draft).
3. **Mechanism: a separate, named set of revenue `rate_source` values, not a wider
   `STATUS_DEPENDENT_SOURCES`.** Component (a) applies when `rate_source` ∈
   `STATUS_DEPENDENT_SOURCES` ∪ *(separate set)* **and** `s_P ≠ s_K`. The developer names the set.
   Why it is separate:
   - `STATUS_DEPENDENT_SOURCES` keeps one meaning: "values chosen from the status".
   - The two reasons for being in (a) can be revisited independently.
   - The classification stays by `rate_source`, not by `model_type` (point 2 (i)/(ii) of the
     SC-7-03 addendum).
   - The Fixed Price revenue does not read the status (rule 10 of the Guardian; ADR-0003, SC-7-03
     addendum, point 3). Control FP-6 still holds: the `rate_source` of the answer is
     `fixed_price_terms` in both states.

   The rule still lives in one place (`refuse_a_status_race`, point 4). The named failure direction
   of point 2 still applies: a new value outside both sets is treated as exempt.
4. **Effect on the table of point 5:** Fixed Price behaves like T&M. An approval between any two
   reads gives `409` on what-if. On `…/results`, an approval between the revenue read and the cost
   read gives `409`, not `200`. A15-8 does not apply to Fixed Price.
5. **Evidence:** the Fixed Price race tests for `…/results` and for what-if in `backend/tests/…`
   (written in SC-4-02). Each uses real concurrency on two connections, between the revenue read
   and the cost read. They must kill the mutation "Fixed Price outside (a)". The exact test ids go
   into the capabilities registry at gate 3.
6. **Unchanged:**
   - The behaviour of T&M, Story Points and Outcome-based (A15-1…A15-9).
   - Component (b).
   - The meaning and contents of `STATUS_DEPENDENT_SOURCES`.
   - The steady state of K-07/FP-5: a Fixed Price draft or approved scenario gets `200` on
     `…/results` and `…/compare`. What-if on an `approved` scenario gets `404` (point 5).
7. **When this must be revisited:**
   - **Story Points or Outcome-based gains a draft edit or delete path.** That task adds its value
     to the separate set, or it provides another consistency proof.
   - **Fixed Price loses its edit path.** Removing it from the set then needs a dated addendum.
   - **Fixed Price gains another draft-editable input to its revenue** (for example the price
     adjustments of D-3 = C). It stays covered only while `rate_source` is still
     `fixed_price_terms`.

| Control | Acceptance criterion |
|---|---|
| A15-10 | Fixed Price scenario: an approval committed by real concurrency on two connections between the revenue read and the cost read gives `409` with the unchanged generic body on `…/results` and on what-if (on what-if `409`, not `404`). A mutation that exempts `fixed_price_terms` from (a) is killed. |
| A15-11 | `STATUS_DEPENDENT_SOURCES` equals `{live_catalog, approved_snapshot}`. Fixed Price `assumptions_used.rate_source` is `fixed_price_terms` for a draft and for an approved scenario. `…/results` and `…/compare` of a Fixed Price scenario (draft and approved) with no concurrent approval answer `200`. |

### 2026-09-29 — SC-5-08 (Issue #80): the "hourly rate" wording of point 6 and of the SC-5-02 addendum is stale

**Status:** Accepted (human decision 2026-09-29, by merging the ADR acceptance PR for SC-5-08; code merged in #167)

> A note, not a change of decision. The text of point 6 of the Decyzja and of the addendum
> 2026-09-25 (SC-5-02) stays unchanged; this entry says how to read two phrases of it after
> SC-5-08 (ADR-0013, addendum 2026-09-29 SC-5-08).

1. **The phrases.** Point 6 says "`default_cost_rate` to stawka godzinowa" (the reason the raise is
   a percentage and not an amount). The SC-5-02 addendum says the surcharge is derived from "the
   same hourly rate" that points 1–3 substitute. Both were true while the catalogue refused any unit
   but `hour`. Since SC-5-08 the rate is expressed in `cost_rate_unit` (`hour`, `day` or `month`,
   `hour` by default), so "hourly rate" is to be read as "the cost rate in its own unit".
2. **The conclusions stand.** A percentage raise scales a rate of any unit by the same factor, and
   the amount priced from it scales by that factor too, so the percentage shape of point 6 needs no
   change; the surcharge remains a percentage of the base amount, which is derived from the same
   substituted structure (SC-5-02 addendum, point 1 — the condition holds unchanged).
3. **What the substitution must not do.** The resolved unit rides in the shared per-(position,
   month) structure that point 3 substitutes once, before both consumers. The substitution changes
   the amount only and passes the unit through unchanged; it never substitutes, defaults or drops the
   unit, and neither consumer assumes `hour` (ADR-0013, addendum 2026-09-29 SC-5-08, point 6,
   control U-6). A what-if run over a scenario with a monthly-rate position is the case that shows
   whether a hard-coded `hour` survived in one consumer only.
