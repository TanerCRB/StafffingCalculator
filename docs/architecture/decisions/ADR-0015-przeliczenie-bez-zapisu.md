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
   `app.domain.personnel_cost.paid_absence_cost`, `app.data.personnel_cost._worked_months` i
   `app.data.personnel_cost.paid_absence_months` (nie tylko pierwsze dwie — patrz pkt 3) oraz
   `app.domain.scenario_results.scenario_profitability`, na PODSTAWIONYM zestawie stawek, zamiast
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
   Dowód (nowy wzorzec testowy, do zbudowania w SC-6-04, brak precedensu w repo): (a)
   `session.new`/`session.dirty`/`session.deleted` puste przed i po wywołaniu what-if; (b) test
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
   bezwymiarowy i spójny z każdym stanem nazwanym formuły kosztu.
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
