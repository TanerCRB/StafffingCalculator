# ADR-0013 — Koszt osobowy (F-07): predykat rozstrzygania stawki kosztowej, kształt wyniku, podstawa "bazowy"

**Status:** Draft — pending approval

> Dokument powstał w odpowiedzi na pytanie bramki 1 architekta (Q-7) i product-ownera (Q-1, Q-2)
> dla SC-5-01 (Issue #9, F-07 zawężone na bramce 1 do wyłącznie podstawy worked time — reszta jako
> SC-5-02..). Rozstrzygnięcie zapadło na bramce 1 (2026-09-23).

## Kontekst

F-07: "Personnel costs shall support hourly/daily/monthly rates, bonuses/benefits/employer
overheads and other components, with a configurable cost basis per position (worked time /
assigned FTE / fixed amount) and a clear distinction between the base rate and the fully loaded
cost." Issue #9 zawężone na bramce 1 do pierwszego wycinka: koszt bazowy z podstawy worked time,
bez narzutów, bez FTE, bez kwoty stałej.

Reguła kosztu ma dwa naturalne miejsca w istniejących ADR i żadne z nich nie pasuje w całości:
ADR-0003 opisuje wyłącznie przychód (reguła 10 Strażnika niezmienników: koszt liczony niezależnie
od modelu przychodu — wiązanie reguły kosztu z ADR modelu komercyjnego byłoby w napięciu z tą
regułą) i ADR-0008 opisuje mechanizm przedziałów obowiązywania w ogólności, nie stany wyniku ani
formułę wyliczenia. SC-5-02..04 (narzuty, kwota stała, FTE) będą rozszerzać tę samą regułę —
potrzebne jest jedno miejsce, nie rozproszenie po ADR-0003/ADR-0008 zadanie po zadaniu.

## Decyzja

1. **Predykat rozstrzygania stawki kosztowej — lustro ADR-0003 aneks R-01, niezależny od predykatu
   przychodu.** Dla pary (pozycja obsady, miesiąc alokacji): stawka kosztowa jest rozstrzygnięta,
   gdy wewnętrzne okna (`vendor_id IS NULL`, ADR-0003 pkt 4) tej krotki katalogu razem pokrywają
   każdy dzień miesiąca i mają jedną wspólną parę (`default_cost_rate`, `currency`). Ten predykat
   nie czyta `default_selling_rate` i nie jest budowany z modułu ścieżki przychodu
   (`app.data.commercial_terms`) — osobna funkcja, osobny moduł (`app.data.personnel_cost`).
   Symetrycznie: predykat przychodu nie czyta `default_cost_rate` (ADR-0003 pkt 4, bez zmian).
2. **Dwa kształty wyniku, nigdy trzeci.** `calculated` (kwota kosztu bazowego + `assumptions_used`
   ze stawkami/oknami per miesiąc) albo stan nazwany: `no_cost_rate` (co najmniej jeden miesiąc
   alokacji bez rozstrzygniętej stawki kosztowej — wskazuje który) albo `currency_mismatch`
   (waluta rozstrzygniętej stawki różna od `scenarios.currency`, albo okna tej samej krotki i
   miesiąca niezgodne walutą między sobą — doprecyzowane aneksem 2026-09-23, bramka 2). Zakaz sumy częściowej: jeden miesiąc bez stawki
   przełącza CAŁY wynik pozycji w stan nazwany, nigdy nie jest pomijany w sumie. Zakaz `0` jako
   substytutu braku stawki.
3. **Koszt bazowy = Σ (`planned_allocation_hours` × stawka kosztowa rozstrzygnięta dla miesiąca
   tej pozycji), jedno zaokrąglenie na końcu** (`app.core.money.round_money`, ADR-0002) — nigdy
   zaokrąglanie per miesiąc ani per pozycja przed sumowaniem. Bez mnożenia przez `headcount`
   (alokacja miesięczna to już suma pozycji — decyzja 1 ADR dla SC-3-01, `docs/PLAN.md`).
4. **"Worked time" = `planned_allocation_hours`, nie `billable_hours` ani dostępność.** Obejmuje
   wysiłek nierozliczalny — domyka pozycję jawnie odłożoną w Out of scope SC-3-01 ("koszt
   nierozliczalnego wysiłku"). `billable_hours` pozostaje wyłącznie podstawą przychodu (ADR-0003).
   Miesiąc z `planned_allocation_hours = 0` liczy się do wyniku tak samo jak każdy inny — lustro
   R-04 ADR-0003 (mutacja "miesiąc zerowy pomijany w sumie" musi zabić).
5. **`default_cost_rate` w katalogu to stawka BAZOWA, przed narzutami — nie w pełni obciążony
   koszt.** Wynik tego zadania jest jawnie oznaczony jako koszt bazowy (dowód: równość zbioru pól
   odpowiedzi, brak jakiegokolwiek pola sugerującego "fully loaded", narzut, zysk, marżę).
   **Konsekwencja nazwana naprzód:** organizacje, które dziś wpisały do katalogu stawki JUŻ w
   pełni obciążone, dostaną w SC-5-02 (narzuty) podwójne naliczenie narzutu, dopóki nie poprawią
   danych katalogu — SC-5-02 musi nazwać to wprost we własnej dokumentacji i rozważyć flagę
   "stawka już zawiera narzuty" jako część swojego zakresu, nie tego zadania.
6. **Rozstrzyganie stawki kosztowej dla zatwierdzonego scenariusza** — zakres i mechanizm migawki
   opisane w ADR-0004, aneks 2026-09-23 (SC-5-01): okna miesięcy wycenionych predykatem sprzedażowym
   LUB kosztowym (pkt 1 wyżej) są zamrażane; ten sam predykat kosztowy stosowany identycznie na
   żywej ścieżce, w kopiarce migawki i w czytelniku migawki (trzy miejsca, jeden predykat).
7. **Widoczność: `STAFFING_READ` na endpoincie, koniunkcja `PERSONNEL_COSTS_READ` ∧
   `can_view_personnel_costs` na polu kosztu** — mechanizm w ADR-0005, aneks 2026-09-23 (SC-5-01).
   Ten ADR nie powtarza mechanizmu bramki, tylko go zakłada.
8. **Miejsce dla SC-5-02..04.** Kolejne zadania F-07 rozszerzają ten dokument aneksem, nie tworzą
   równoległej reguły: SC-5-02 (narzuty, rozróżnienie stawka bazowa / w pełni obciążony koszt),
   SC-5-03 (kwota stała jako podstawa, wybór podstawy per pozycja), SC-5-04 (FTE, po zadaniu
   wprowadzającym konwersję FTE→godziny).

## Czego ten dokument nie rozstrzyga

Narzutów/premii/benefitów i kosztu w pełni obciążonego (SC-5-02); kwoty stałej jako podstawy
(SC-5-03); podstawy FTE (SC-5-04); stawek kosztowych dziennych/miesięcznych (katalog dziś wymusza
`unit = 'hour'` w bazie); kosztów poddostawców na pozycji (pozycja nie ma dziś `vendor_id`);
przeliczenia walut (niezgodność jest stanem nazwanym, nie kursem — ADR-0006 bez zmian); kosztu
nieobecności płatnych rozstrzyga aneks 2026-09-23 SC-5-06 niżej (dopłata budżetu, wpływ na
przychód, `generates_revenue` — pozostają poza zakresem, patrz aneks).

## Konsekwencje

- Nowy moduł danych (`app.data.personnel_cost` albo analogiczny) jako jedyne miejsce znające
  predykat kosztowy — symetryczny do `app.data.commercial_terms`, nigdy nie importowany przez niego
  ani odwrotnie (reguła 10 Strażnika).
- `PERSONNEL_COST_FIELDS` (projektu, ADR-0005 aneks 2026-09-19) pozostaje pustym zbiorem po tym
  zadaniu — pole kosztu żyje w nowym `SCENARIO_COST_FIELDS` (ADR-0005, aneks 2026-09-23 SC-5-01
  pkt 3), nie w payloadzie projektu. Test `test_project_personnel_cost_visibility.py::test_k_06_…`
  zostaje zielony bez zmian.
- Gałąź pozytywna bramki kosztowej pozostaje nieosiągalna w produkcji do czasu ADR
  uwierzytelniania — nazwane w rejestrze możliwości, nie naprawiane tym zadaniem.

## Aneksy

### 2026-09-23 — doprecyzowanie pkt 2: rozróżnienie stanów walutowych, stan `no_cost_currency` (SC-5-01, bramka 2)

**Status:** Draft — pending approval

Pkt 2 w brzmieniu "okna tej samej krotki i miesiąca niezgodne walutą między sobą" jako przypadek
`currency_mismatch` jest niespełnialny: taki miesiąc nie spełnia predykatu pkt 1 (jedna para
`default_cost_rate`, `currency`) i nigdy nie dochodzi do porównania walut. Rozstrzygnięcie:

1. **Niejednorodna waluta MIĘDZY oknami jednej (pozycja, miesiąc) → miesiąc nierozstrzygnięty →
   `no_cost_rate`**, tak samo jak zmiana stawki kosztowej w trakcie miesiąca i luka. Lustro ADR-0003
   aneks R-01; spójne z ADR-0004 aneks 2026-09-23 SC-5-01, C-3 (taki miesiąc nie zamraża żadnego
   okna).
2. **`currency_mismatch` wyłącznie po rozstrzygnięciu wszystkich miesięcy**, gdy (b) waluta
   rozstrzygniętych stawek różni się od `scenarios.currency` (jeśli zadeklarowana), albo (c)
   rozstrzygnięte (pozycja, miesiąc) mają różne waluty między sobą. Bez przeliczenia (ADR-0006).
   Kolejność: `no_cost_rate` ma pierwszeństwo przed `currency_mismatch`.
3. **`no_cost_currency` — trzecia wartość stanu nazwanego** (nadal jeden kształt "stan nazwany", nie
   trzeci kształt wyniku): scenariusz bez żadnego wiersza alokacji i bez `scenarios.currency`
   (kolumna nullable). Wynik `0.00` musiałby nie nazywać waluty, co łamie pkt 2. Lustro
   `no_revenue_currency` (ADR-0003 aneks R-01). Wiersze alokacji z `planned_allocation_hours = 0`
   NIE wyzwalają tego stanu — podlegają pkt 1 i 4 jak każdy miesiąc.
4. **Scenariusz bez `scenarios.currency` z rozstrzygniętymi stawkami w jednej walucie →
   `calculated` w tej walucie** (lustro ścieżki przychodu).

| Kontrola | Kryterium akceptacji |
|---|---|
| W-1 | Zmiana samej waluty okna w trakcie miesiąca przy tej samej kwocie stawki kosztowej → `no_cost_rate`, nie `currency_mismatch`. |
| W-2 | Dwie pozycje rozstrzygnięte w różnych walutach → `currency_mismatch`, obie waluty w `assumptions_used.currencies`; stawki w jednej walucie przy innej `scenarios.currency` → `currency_mismatch`; kontrast ze zgodną walutą → `calculated`. |
| W-3 | Pusty plan: `scenarios.currency` zadeklarowana → `calculated`, `0.00` w tej walucie; brak → `no_cost_currency`, bez kwoty. |

### 2026-09-23 — koszt nieobecności płatnych jako osobna składowa (SC-5-06)

**Status:** Draft — pending approval

1. Składowa "koszt nieobecności płatnych" jest osobna od kosztu bazowego z pkt 3. Pkt 4 ("worked
   time = `planned_allocation_hours`") zostaje bez zmian, źródło godzin tej składowej jest inne i
   nazwane osobno.
2. Źródło godzin: (a) ręczne instancje nieobecności typu z `generates_cost = true`, liczone jak w
   pojemności (SC-3-02); (b) dla typu `is_statutory_leave` z `generates_cost = true` dodatkowo
   dopłata budżetu `BudgetShare.hours` (ADR-0008 aneks SC-3-03 pkt 9–10). Nigdy pełny budżet
   dodany do wpisów ręcznych — urlop ustawowy w koszcie = wpisy ręczne + dopłata, nie ich suma z
   pełnym uprawnieniem.
3. Stawka: predykat z pkt 1 (bez zmian), rozstrzygana per (pozycja, miesiąc). Koszt nieobecności
   liczy się wyłącznie w miesiącach z wierszem alokacji (zakres migawki, ADR-0004 aneks
   2026-09-23 SC-5-06, bez zmian zakresu). Dolicza się niezależnie od wielkości
   `planned_allocation_hours` — ryzyko podwójnego liczenia przy nieodjętym urlopie z planu
   świadomie przyjęte, nienaprawiane tym zadaniem.
4. Stany: `no_calendar`, `no_budget`, `no_statutory_leave_type` to stany nazwane **składowej**,
   nigdy `0`. Kwota bazowa (pkt 3) i jej kształt (dwa kształty, zakaz sumy częściowej) pozostają
   nietknięte — brak sumy łącznej kosztu osobowego w tym zadaniu (suma → blok 7). `no_budget` przy
   zatwierdzonym scenariuszu (po aneksie ADR-0004 SC-5-06 pkt 5) oznacza wyłącznie "typ kosztowy
   albo nienazwany i brak okna" — nigdy "typ nie został zamrożony" (typ ustawowy jest zamrażany
   zawsze, gdy jest kalendarz).
5. `cost_basis` składowej = `base` (stawka przed narzutami, pkt 5) — SC-5-02 obejmuje ją tak samo
   jak koszt pracy.
6. Kwota (i część budżetowa) podlega tej samej koniunkcji co pkt 7 (`PERSONNEL_COSTS_READ` ∧
   `can_view_personnel_costs`), nowe pole w `SCENARIO_COST_FIELDS` — trzecia funkcja kształtująca
   (aneks 2026-09-23 SC-5-01 pkt 4), bez nowego aneksu ADR-0005. Budżet jako liczba dni/godzin w
   odpowiedzi obsady/katalogu zostaje poza koniunkcją (ADR-0005 aneks SC-3-03 pkt 4).
7. Poza zakresem: `generates_revenue` (F-05 druga połowa, F-06 rozdziela koszt od przychodu);
   zapis/edycja `absence_type` (brak ścieżki HTTP, ADR-0005 aneks SC-3-02 pkt 10 — gałąź pozytywna
   `generates_cost` przyjęta nieosiągalna w produkcji jak SC-5-01, fixture/seed).

| Kontrola | Kryterium akceptacji |
|---|---|
| N-1 | Typ z `generates_cost=false` nie wnosi nic do składowej; kontrast z typem z `true` przy identycznych datach. |
| N-2 | Urlop ustawowy w koszcie = wpisy ręczne + dopłata budżetu; mutacja "pełny budżet + wpisy ręczne" wywraca test. |
| N-3 | Brak kalendarza, brak budżetu i brak typu ustawowego to stany nazwane składowej, nigdy `0`; kwota bazowa SC-5-01 nietknięta. |
| N-4 | Moduł składowej nie importuje ścieżki przychodu (test strukturalny grafu importów, lustro C-5). |

**Aneks — granica reużycia dla przeliczenia bez zapisu, cztery miejsca nie dwa (2026-09-24, bramka
1, SC-6-04, ADR-0015).**

"Jedna funkcja, trzy miejsca" (pkt 6 wyżej: live/kopier/migawka) staje się **jedna funkcja, cztery
miejsca** — mechanizm what-if (ADR-0015) jako czwarty wywołujący `_worked_months` i
`base_personnel_cost`, na podstawionym, nigdy nie zapisanym zestawie stawek. `paid_absence_cost`
liczy z DOSŁOWNIE tego samego słownika stawek per-(pozycja, miesiąc) co `base_personnel_cost` —
podstawienie musi nastąpić raz, na współdzielonej strukturze, przed wywołaniem obu konsumentów,
nigdy osobno. `rate_source` (pkt niżej w tym dokumencie, zamknięty dwuelementowy zbiór
`LIVE_CATALOG`/`APPROVED_SNAPSHOT`) przestaje być zamknięty — ADR-0015 dodaje trzecią wartość,
`WHAT_IF_HYPOTHETICAL`, z wymogiem audytu każdego porównania przez równość (w szczególności
strażnika wyścigu `ScenarioResultsRaceDetected`), żeby trzecia wartość nigdy nie wyciekła do
kompozycji zaprojektowanej dla dwóch stanów. Pełne uzasadnienie: ADR-0015.

### 2026-09-25 — SC-5-02 (Issue #77, narzuty i koszt w pełni obciążony): rozstrzygnięcia bramki 1

Pkt 8 wyżej wyznacza to miejsce dla SC-5-02 wprost: "SC-5-02 (narzuty, rozróżnienie stawka bazowa /
w pełni obciążony koszt)". Poprzednia wersja tego wpisu (2026-09-25, mapa wpływu Architekta, "Draft —
pending approval") nazwała pięć pytań bramki 1 i nie rozstrzygnęła żadnego. Człowiek rozstrzygnął
wszystkie pięć na bramce 1 (2026-09-25) — ten wpis zastępuje treść pytań treścią decyzji; nic z sekcji
"Decyzja" ani z jej dotychczasowych aneksów nie zostaje przez to zmienione, poza tym, co punkty 1–5
niżej nazywają wprost jako rozszerzenie zapowiedziane w pkt 8.

1. **Q1 — `profit`/`margin`/`markup`/`included_cost` (F-10) NIE przechodzą w tym zadaniu na koszt w
   pełni obciążony; zostają na koszcie bazowym.** [Opcja B]. `included_cost` nadal sumuje wyłącznie
   `base_personnel_cost.cost` (SC-7-01, `backend/tests/test_scenario_results.py`, zielony bez zmian).
   Rozbieżność nazwana wprost, nie odkrywana przy reklamacji: organizacje korzystające z narzutów będą
   miały `profit`/`margin`/`markup` systematycznie zawyżone od chwili, gdy SC-5-02 wprowadzi koszt w
   pełni obciążony, bo F-10 nadal czyta wyłącznie koszt bazowy. To jest nazwane ograniczenie F-10, nie
   defekt SC-5-02, i **musi wejść do `docs/architecture/capabilities.md`** w chwili, gdy SC-5-02
   zamyka bramkę 3 — jako świadomie przyjęte ograniczenie, obok stanu SC-7-01 jako dowiedzionej,
   zamkniętej capability na koszcie bazowym. Przełączenie F-10 na koszt w pełni obciążony (Opcja A,
   odrzucona tu) zostaje osobnym, przyszłym zadaniem, które otwiera mutation-checked testy SC-7-01 —
   nie częścią SC-5-02.
2. **Q2 — narzut WCHODZI do migawki zatwierdzonego scenariusza W TYM SAMYM zadaniu (SC-5-02), mimo że
   SC-5-02 nie buduje własnego czytelnika migawki.** [Opcja A, "zamrozić teraz"]. Precedens wiążący
   wprost, nie tylko analogiczny: ADR-0004, aneks 2026-09-23 SC-4-01, pkt 2b — `default_cost_rate`
   został zamrożony całe zadanie wcześniej, niż cokolwiek go czytało, dokładnie dlatego, że "migawka
   nie ma ścieżki UPDATE: scenariusz zatwierdzony przed blokiem 5 bez zamrożonego kosztu nie
   odzyskałby go nigdy". Ten sam argument, słowo w słowo, stosuje się do narzutu. Mechanizm migawki:
   ADR-0004, aneks tej daty (SC-5-02, drugi aneks tej daty w tamtym pliku). Bramka dostępu do wiersza
   migawkowego: ADR-0005, aneks tej daty (rozstrzygnięcie Q4 niżej, przeniesione).
3. **Q3 — podstawienie what-if (ADR-0015) obejmuje narzut AUTOMATYCZNIE, bez własnej ścieżki kodu —
   konsekwencja wprost kształtu rozstrzygniętego w Q4.** Ponieważ narzut jest PROCENTEM od stawki
   bazowej (`default_cost_rate`), nie kwotą o własnym rozstrzygnięciu, jest on wartością wyprowadzoną
   z dokładnie tej samej stawki godzinowej, którą ADR-0015 pkt 3 już podstawia raz, na współdzielonym
   słowniku stawek per-(pozycja, miesiąc), przed wywołaniem wszystkich konsumentów ("Podstawienie musi
   nastąpić raz, na współdzielonej strukturze stawek, przed wywołaniem obu konsumentów — nigdy osobno
   na wejściu do jednego z nich"). Koszt w pełni obciążony, gdy stanie się trzecim konsumentem tego
   słownika obok `base_personnel_cost` i `paid_absence_cost`, dziedziczy podstawioną stawkę bazową z
   automatu — **pod warunkiem, że formuła narzutu SC-5-02 czyta stawkę z tego samego słownika, a nie z
   osobnego zapytania do katalogu** wewnątrz swojej własnej funkcji. SC-5-02 NIE projektuje osobnej
   ścieżki podstawienia narzutu w what-if — warunek i pełne rozwinięcie: ADR-0015, aneks tej daty.
4. **Q4 — klasyfikacja surowej stawki/procentu narzutu: PARAMETR ORGANIZACYJNY, w kształcie PROCENTU
   od stawki bazowej (nie kwoty absolutnej), bramkowany samym `CATALOG_READ` poza kontekstem
   projektu.** [Opcja B]. Precedens wiążący: budżet urlopowy (ADR-0005, aneks 2026-09-22 SC-3-03, pkt
   3) — "mnożnik nie ujawnia kwoty bez stawki bazowej, która jest już bramkowana osobno". Kształt
   (procent, nie kwota) jest tym, co decyduje o klasyfikacji, nie odwrotnie — gdyby narzut był kwotą
   absolutną, byłby bliżej `default_cost_rate` (mirror SC-2-01 pkt 3) niż liczby dni budżetu, i ta
   klasyfikacja wymagałaby ponownego rozpatrzenia (ADR-0005, aneks tej daty, pkt 1). Pełne
   rozstrzygnięcie i uzasadnienie: ADR-0005, aneks tej daty.
5. **Q5 — flaga "stawka już zawiera narzuty" żyje W TYM SAMYM wierszu i tej samej tabeli co
   `default_cost_rate`.** Zweryfikowane w kodzie: `backend/app/models/catalog.py`, klasa
   `CatalogDefaultRate`, `__tablename__ = "catalog_default_rates"` — nie osobna tabela ustawień
   organizacji, nie kolumna per reguła komercyjna. Konsekwencja: flaga (i procent narzutu, Q4)
   dziedziczą za darmo mechanizm przedziału obowiązywania tej samej krotki (rola × senioritet ×
   lokalizacja × typ zaangażowania × poddostawca) — `effective_from`/`effective_to`, kolumna
   generowana `valid_period`, `EXCLUDE USING gist` (ADR-0008) — zamiast wymagać nowego, równoległego
   mechanizmu czasu: nowa wartość flagi dla tej samej krotki otwiera nowe okno `EXCLUDE` dokładnie
   tak, jak zmiana samej stawki. SC-5-02 nie potrzebuje własnej decyzji ADR-0008, tylko musi dowieść,
   że flaga i procent wchodzą do tego samego okna co `default_cost_rate` i `default_selling_rate` —
   żadna kolumna `catalog_default_rates` nie ma dziś własnego, niezależnego okna obowiązywania.
   Konsekwencja dla migawki (Q2): flaga i procent są kolejnymi kolumnami
   `ApprovedSnapshotCatalogDefaultRate`/`approved_snapshot_catalog_default_rate`, zamrażanymi razem ze
   stawką bazową w tej samej transakcji zatwierdzenia — nie osobnym wierszem ani osobną tabelą
   migawkową (pełne rozwinięcie mechanizmu migawki: ADR-0004, aneks tej daty).

**Pytanie, które SC-5-02 musi jeszcze rozstrzygnąć we własnym zakresie, nazwane tu, nie
zablokowane przez bramkę 1.** Wiersze `catalog_default_rates` z `vendor_id NOT NULL` (stawka
poddostawcy, ADR-0005 aneks 2026-09-21 SC-2-03) niosą tę samą kolumnę procentu/flagi co wiersze
wewnętrzne, bo obie żyją w jednej tabeli (Q5). Ceny kontrahenta bywają z natury już w pełni
obciążone (umowa z poddostawcą rzadko rozbija stawkę na bazę i narzut) — czy SC-5-02 dopuszcza
niezerowy procent na takim wierszu, wymusza go na `0`, czy zostawia pole bez znaczenia biznesowego
dla `vendor_id NOT NULL`, nie jest rozstrzygnięte żadnym z Q1–Q5 i nie było zadane na bramce 1. Nie
blokuje wejścia w fazę `code` — formuła kosztu bazowego (pkt 1 "Decyzja") czyta wyłącznie
`vendor_id IS NULL` i ten predykat nie zmienia się tu — ale SC-5-02 musi nazwać wybór wprost we
własnym "Done when", nie zostawić pole poddostawcy milcząco niezdefiniowane.

### 2026-09-25 — SC-5-03 (Issue #78, kwota stała jako podstawa kosztu): rozstrzygnięcia bramki 1

Pkt 8 wyżej wyznacza to miejsce dla SC-5-03 wprost: "SC-5-03 (kwota stała jako podstawa, wybór
podstawy per pozycja)". Bramka 1 (2026-09-25) rozstrzygnęła cztery pytania (Q1–Q4) bez zastrzeżeń —
ten wpis zapisuje treść decyzji, nie zmienia niczego z sekcji "Decyzja" ani z dotychczasowych
aneksów, poza tym, co punkty 1–5 niżej nazywają wprost jako rozszerzenie zapowiedziane w pkt 8.

1. **Q1 — kwota stała ma WŁASNĄ walutę, wiązaną do `scenarios.currency`; dwa stany nazwane,
   wzorem ADR-0014 pkt 7.** Pozycja z `cost_basis = 'fixed_amount'` nie czyta żadnej stawki
   katalogowej — jej "koszt" jest liczbą wpisaną wprost, z własną walutą. Kształt wyniku dla takiej
   pozycji: `calculated` (kwota w tej walucie) albo stan nazwany — `currency_mismatch` (waluty
   różnych pozycji `fixed_amount` różnią się między sobą albo różnią się od zadeklarowanej
   `scenarios.currency`, bez przeliczenia, ADR-0006) albo `no_cost_currency` (scenariusz bez
   `scenarios.currency` i bez żadnej rozstrzygniętej kwoty/stawki — ani `fixed_amount`, ani
   worked time). Cytat wiążący: ADR-0014 pkt 7 — "`calculated` (kwota, waluta, `assumptions_used`…)
   albo stan nazwany: `currency_mismatch` (koszty w więcej niż jednej walucie albo w walucie innej
   niż `scenarios.currency`…), `no_cost_currency` (brak kosztów i brak `scenarios.currency`)… Zakaz
   sumy częściowej" — ten sam kształt, nie nowy wynaleziony dla trzeciej podstawy kosztu. Dwie
   podstawy (worked time, pkt 2 wyżej; fixed amount, ten punkt) mają OSOBNE zestawy stanów
   nazwanych rozstrzygane przez OSOBNE predykaty, dispatchowane przez `cost_basis` pozycji — nigdy
   jeden wspólny predykat czytający oba źródła na raz (patrz pkt 4 niżej, izolacja modułu).
2. **Q2 — CHECK w migracji (`cost_basis = 'fixed_amount' → fixed_amount IS NOT NULL`), nie walidacja
   aplikacyjna.** Wzorem ADR-0001 i precedensu G-1 ADR-0014 ("`CHECK amount > 0` w bazie… wiersz
   kosztu reprezentuje realną pozycję kosztową") — integralność tej krotki jest egzekwowana w bazie,
   nie w warstwie żądania, żeby żaden zapis z pominięciem API (seed, migracja danych, przyszła druga
   ścieżka zapisu) nie mógł wytworzyć wiersza `fixed_amount` bez kwoty. **Pytanie nierozstrzygnięte
   przez bramkę 1, nazwane tu dla SC-5-03, nie blokujące:** czy ten sam CHECK obejmuje też kolumnę
   waluty towarzyszącej `fixed_amount` (`cost_basis = 'fixed_amount' → fixed_amount IS NOT NULL AND
   <waluta> IS NOT NULL`), czy waluta zostaje nullable z jakimś fallbackiem. SC-5-03 musi nazwać ten
   wybór wprost we własnym "Done when" — nie zostawić kolumny waluty milcząco niezdefiniowaną (ten
   sam wzorzec co pytanie o `vendor_id NOT NULL` zostawione SC-5-02 w poprzednim aneksie tego pliku).
3. **Q3 — kolumny bezpośrednio na `staffing_position`, nie tabela-wnuczka; kopiowanie przez istniejący
   `copy_staffing_positions`, bez nowego rejestru.** `cost_basis` i `fixed_amount` (+ waluta) są
   kolumnami tego samego wiersza, który agregat pozycji już kopiuje jako całość (ADR-0004, aneks tej
   daty, niżej) — żadnego nowego mapowania starych-na-nowe identyfikatorów, żadnej nowej krotki w
   `SCENARIO_CHILD_COPIERS`, bo nie powstaje żadna nowa tabela. To odwraca wcześniejsze przewidywanie
   z Out of scope SC-5-01 ("SC-5-03 — nowe wejście, ścieżka zapisu, strażnik `approved`, wpis w
   `SCENARIO_CHILD_COPIERS`") w części dotyczącej rejestru — konsekwencja nazwana wprost w ADR-0004,
   aneks tej daty, nie milcząca korekta.
4. **Q4 — `cost_basis`/`fixed_amount` NIGDY w schemacie `GET .../staffing-positions`; widoczne
   wyłącznie przez bramkowany endpoint kosztu, wzorem `default_cost_rate`.** Rozwinięcie w
   ADR-0005, aneks tej daty (niżej) — ten dokument nie powtarza mechanizmu bramki, tylko go zakłada
   (jak pkt 7 wyżej dla koniunkcji `PERSONNEL_COSTS_READ`).
5. **K-01 (dwie formuły, osobne ścieżki z tej samej dyspozycji) — izolacja modułu, reguła 10
   Strażnika.** Formuła `fixed_amount` NIE czyta `catalog_default_rates` i nie woła predykatu z pkt 1
   "Decyzja" (rozstrzyganie stawki kosztowej) — czyta wyłącznie własne kolumny wiersza pozycji.
   Dispatch po `cost_basis` żyje w jednej, wspólnej funkcji wywołującej (nowej albo istniejącej w
   `app.data.personnel_cost`/`app.domain.personnel_cost`), ale same dwie formuły są dwiema
   niezależnymi funkcjami — formuła `fixed_amount` nie importuje modułu rozstrzygania stawki
   katalogowej ani `app.data.commercial_terms` (ścieżka przychodu), tak samo jak formuła worked time
   (pkt 1) nie importuje żadnej z nich. Test strukturalny grafu importów (wzorem C-5, aneks
   2026-09-23 SC-5-01) musi objąć obie formuły osobno, nie tylko parę już istniejącą.
6. **K-06 (brak kwoty przy `fixed_amount` — stan nazwany/nieosiągalny, nigdy `0`) jest zastosowaniem
   zasady "dwa kształty, nigdy trzeci" (pkt 2 wyżej), wzmocnionym o gwarancję bazy z Q2.** Dzięki
   CHECK z pkt 2 stan "`cost_basis = 'fixed_amount'` i brak kwoty" jest **nieosiągalny w aplikacji**
   z konstrukcji — formuła kosztu nigdy nie musi dla niego wynajdywać nazwanego stanu domenowego,
   bo baza nie dopuszcza takiego wiersza do istnienia. To silniejsza i trwała gwarancja niż "gałąź
   pozytywna nieosiągalna w produkcji" (która jest tymczasowa, do ADR uwierzytelniania) — tu
   nieosiągalność jest strukturalna i nie wygasa z żadnym przyszłym ADR. Jedyne nazwane stany, jakie
   `fixed_amount` faktycznie potrzebuje, to stany walutowe z pkt 1 — i zakaz `0` obowiązuje tam
   identycznie jak w pkt 2 dla worked time: `currency_mismatch`/`no_cost_currency` nigdy nie zwraca
   kwoty zastępczej.
7. **Migawka: brak trzech miejsc, bo brak czego zamrażać.** Pkt 6 wyżej ("rozstrzyganie stawki
   kosztowej dla zatwierdzonego scenariusza… ten sam predykat kosztowy stosowany identycznie na
   żywej ścieżce, w kopiarce migawki i w czytelniku migawki — trzy miejsca, jeden predykat") dotyczy
   wyłącznie worked time, gdzie wartość jest dziedziczona spoza scenariusza (katalog). `fixed_amount`
   jest daną własną scenariusza (ADR-0004, aneks tej daty, grupa 2) — nie ma trzeciego miejsca do
   zbudowania: jedynym miejscem liczącym ten koszt, przed i po zatwierdzeniu, jest formuła czytająca
   żywy wiersz `staffing_position`, chroniony strażnikiem zapisu (`approved`), nie migawką. To nie
   jest luka wobec pkt 6 — to inna klasa danych, dla której "trzy miejsca" nie ma zastosowania.

| Kontrola | Kryterium akceptacji |
|---|---|
| F-1 | Pozycja `cost_basis='fixed_amount'` z walutą inną niż zadeklarowana `scenarios.currency` → `currency_mismatch`, nigdy `0`. |
| F-2 | Dwie pozycje `fixed_amount` w różnych walutach → `currency_mismatch`; kontrast ze zgodną walutą → `calculated`. |
| F-3 | Scenariusz bez `scenarios.currency` i bez żadnej rozstrzygniętej kwoty/stawki (ani worked time, ani fixed amount) → `no_cost_currency`, bez kwoty. |
| F-4 | `cost_basis='fixed_amount' AND fixed_amount IS NULL` odrzucone przez bazę (CHECK), nieosiągalne w aplikacji — asercja na `pg_constraint`, wzór ADR-0014 D-10. |
| F-5 | Formuła `fixed_amount` nie importuje `app.data.commercial_terms` ani modułu rozstrzygania stawki katalogowej (test strukturalny grafu importów, mirror C-5). |

### 2026-09-29 — SC-5-08 (Issue #80, daily and monthly cost rates): the cost-rate unit, the conversion rules, the `no_calendar` state

**Status:** Accepted (human decision 2026-09-29, by merging the ADR acceptance PR for SC-5-08; code merged in #167)

> Gate 1 of SC-5-08 (2026-09-29) resolved Q-1..Q-6 (Q-1 = B, Q-2 = A, Q-3 = A, Q-4 = named state,
> Q-5 backend only, Q-6 downgrade refuses). This entry records the decisions that touch this
> document. It fulfils the placeholder of "Czego ten dokument nie rozstrzyga" ("stawek kosztowych
> dziennych/miesięcznych (katalog dziś wymusza `unit = 'hour'` w bazie)") and the F-07 sentence of
> the Kontekst ("hourly/daily/monthly rates"). The text of the earlier sections and entries stays
> unchanged; where it says "hourly rate" or multiplies hours by the rate, this entry narrows the
> reading as stated below.

1. **The unit of the cost rate is its own column: `cost_rate_unit` (`hour` / `day` / `month`),
   separate from `unit`.** `catalog_default_rates.unit` stays the unit of the *selling* rate and
   stays pinned to `hour` (ADR-0002, addendum 2026-09-21 point 4, unchanged for the selling rate).
   `cost_rate_unit` is `NOT NULL DEFAULT 'hour'`. The cost predicate never reads `unit` and the
   revenue predicate (ADR-0003, `app.data.commercial_terms`) never reads `cost_rate_unit` (point 1
   of the Decyzja and ADR-0003 point 4, unchanged; a structural test forbids the reference).
2. **The unit joins the cost predicate (point 1 of the Decyzja is widened, not replaced).** A
   (position, month) pair has a resolved cost rate when the internal windows cover every day of
   the month **and** share one (`default_cost_rate`, `currency`, `cost_rate_unit`) — the same "one
   value or no answer" rule as for the surcharge percentage and its flag (SC-5-02). A unit change
   inside a month is `no_cost_rate`, exactly like a change of the amount or the currency: the month
   has two candidate answers, and no first-window pick is allowed. `month_has_cost_rate` carries
   the unit in **all three places** of point 6 (the live read, the snapshot copier's per-window
   check, the snapshot reader), and the fourth caller of ADR-0015 inherits it through the shared
   rate structure. The unit is read from the frozen row for an approved scenario, never from the
   live catalogue.
3. **The conversion rules (point 3 of the Decyzja, generalised).** For one (position, month), with
   `H = planned_allocation_hours`, `S = standard_hours_per_day` of the position's location
   calendar and `D = working_days_in_month` of the same calendar for that month:
   - `hour`: `H x rate` (unchanged);
   - `day`: `H / S x rate`;
   - `month`: `rate x H / (D x S)`.

   The month amounts are summed **unrounded** and `app.core.money.round_money` is applied **once,
   at the end** — no rounding of a per-day figure, of a per-month figure or of a per-position
   figure before summing (point 3 of the Decyzja, unchanged in this respect). Point 3's "no
   multiplication by `headcount`" and point 4's "worked time = `planned_allocation_hours`" are
   unchanged.
   Named consequences, accepted and not repaired by this task: (a) a full-capacity month costs
   exactly the monthly rate; **hours above capacity cost more** (the month rate is a price of
   capacity hours, and the amount is not capped at the rate); (b) a **zero-hour month with a calendar is a legal
   `0.00`**, not `no_cost_rate` (without a calendar, point 7, Q-E) (point 4's mirror of ADR-0003 R-04 holds: the zero month is part of
   the result, never skipped); (c) a mid-month working-day count is the calendar's, so two months
   with equal hours cost differently when their `D` differs.
4. **A new named state: `no_calendar`.** A position whose resolved cost-rate unit is `day` or
   `month` in some month, and whose location has no working calendar, cannot be priced: the state
   withholds the **whole scenario base cost** (point 2, "zakaz sumy częściowej", unchanged) —
   positions priced per hour included, no partial sum, never `0` as a substitute, never a skipped
   position. A scenario whose positions are all hourly never needs a calendar: the calendar is not
   read for `hour` rows. This is a third *value* of the named state alongside `no_cost_rate`,
   `currency_mismatch` and `no_cost_currency`, not a third *shape* of result ("dwa kształty,
   nigdy trzeci" holds). The paid-absence component's own `no_calendar` (addendum 2026-09-23
   SC-5-06, point 4) is a separate, component-level state and is unchanged.
5. **The calendar basis is read from the same source as the rate.** A draft reads the live
   calendar (`basis_by_location`); an approved scenario reads only its approval snapshot
   (`frozen_basis_by_location`, ADR-0004, addendum 2026-09-29 SC-5-08) — never a live lookup to
   fill a missing key. Absence of a location's key is the `no_calendar` state.
6. **The unit reaches every consumer of the cost-rate structure.** The base cost, the fully loaded
   cost (the surcharge percentage applies to the base amount **after** unit conversion, still one
   final rounding), the paid-absence component and the what-if raise (ADR-0015) all price hours
   through the resolved unit; none hard-codes `hour`. A what-if raise is a percentage (ADR-0015
   point 6) and scales the amount irrespective of the unit; the unit travels unchanged in the
   shared per-(position, month) structure and is never itself substituted.
7. **Three edge cases, decided at gate 1 of SC-5-08 (2026-09-29, Q-C, Q-D, Q-E; the entry as a
   whole stays a draft pending approval).** The criteria K-01..K-07 fixed the outcomes above but
   not these:
   - **Q-C — precedence: `currency_mismatch` takes precedence over `no_calendar`.** When both
     hold, the result is `currency_mismatch`. The order of named states is therefore `no_cost_rate`
     (which also precedes `no_calendar`, since `no_calendar` needs the resolved unit and cannot
     arise in a month that is already unresolved), then `currency_mismatch`, then `no_calendar`.
   - **Q-D — new named state `no_working_days`.** It applies only to a **month-unit** position in
     a month whose calendar has **zero working days** (`D = 0`, where the `month` formula would
     divide by zero). A **day-unit** position needs only `standard_hours_per_day` and never
     `D`, so it is never `no_working_days`. Like every named state it withholds the whole scenario
     base cost, is never `0`, and is never an unnamed exception; it is a fourth value of the
     named state, not a third shape of result.
     When positions of one scenario fail with both calendar states, `no_calendar` is reported before
     `no_working_days` (approved by the human, 2026-09-29): the missing calendar is the one to fix first.
   - **Q-E — a day/month position requires a calendar regardless of hours.** A **zero-hour**
     day/month position in a location without a calendar is `no_calendar`, not `0.00`. The legal
     `0.00` of point 3(b) is a zero-hour month **with** a calendar. Only a scenario whose
     positions are all hourly is calendar-free (point 4).

| Control | Acceptance criterion |
|---|---|
| U-1 | The same hours priced in the three units give three results by the rules of point 3; a full-capacity month at a monthly rate costs exactly the monthly rate; hours above capacity cost more; a zero-hour month is `0.00`, not `no_cost_rate`. |
| U-2 | Rounding happens once, at the end of the sum: for a value where rounding a per-day or per-month figure before summing would change the result, the result carries a single rounding. |
| U-3 | A day/month position whose location has no calendar gives `no_calendar` for the whole scenario cost, also when another position is hourly, with no amount; a scenario of hourly positions only and no calendar gives `calculated`. |
| U-4 | A change of `cost_rate_unit` inside a month with the same amount and currency gives `no_cost_rate`. |
| U-5 | The unit is part of `month_has_cost_rate` in the live query, in the snapshot copier and in the snapshot reader; an approved scenario is priced from the frozen unit, not the live catalogue. |
| U-6 | The fully loaded cost, the paid-absence component and the what-if raise apply the unit; no path assumes `hour`. |
| U-8 | When `currency_mismatch` and `no_calendar` both hold, the result is `currency_mismatch`; `no_cost_rate` precedes both. |
| U-9 | A month-unit position in a month whose calendar has zero working days gives `no_working_days` for the whole scenario cost, with no amount and no division error; a day-unit position in the same month is priced from `standard_hours_per_day`. |
| U-11 | When one scenario has both a position with no calendar and a month-unit position in a month with zero working days, the result is `no_calendar`. |
| U-10 | A zero-hour day/month position in a location without a calendar gives `no_calendar`, not `0.00`; the same position with a calendar gives `0.00`. |
| U-7 | Revenue modules do not reference `cost_rate_unit` and cost modules do not import revenue modules (structural import-graph test, mirror of C-5). A position with a monthly cost rate and an hourly selling rate yields the same revenue as with an hourly cost rate. |

### 2026-09-29 — SC-5-04 (Issue #79, `assigned_fte` cost basis): stored FTE input, third component, exact basis

**Status:** Draft — pending approval

> Gate 1 of SC-5-04 (2026-09-29). Human decisions on Issue #79: FTE is a **stored per-position input**;
> it is **gross of absences**; **overheads are excluded**; FTE-6 is carried by this task; H-1..H-8 of
> the impact map are answered "continue with the recommendation". This entry fulfils point 8 of the
> Decision section, ADR-0008 addendum 2026-09-29 (SC-3-07) points 3 and 7, and the last sentence of
> ADR-0003 point 6. Earlier text stays unchanged.

1. **A third cost basis, `assigned_fte`, priced as a separate third component**, beside the worked-time
   base cost, the paid-absence component and the fixed-amount component. Never added to any of them; no
   total (that belongs to the F-10 block). Point 4 of the Decision is unchanged: an FTE position's cost
   does not read `planned_allocation_hours`. Rejected: folding it into base cost (would change point 4
   and let the surcharge apply).
2. **The stored FTE is a fraction and a position total.** `Decimal`, scale 4, `1` = one FTE of the
   **position** (no `headcount` factor, no comparison with it). Stored exactly, never rounded on write
   (ADR-0008 point 6). Database `CHECK`s: `assigned_fte > 0` when set; `NOT NULL` when
   `cost_basis = 'assigned_fte'`; `NULL` for every other basis; not together with `fixed_amount`. There is
   **no cap and no bound tied to `headcount`** (reviewer R-01, decided by the human 2026-09-29,
   accepted): a value above `headcount` is accepted without bound and is **not surfaced anywhere** -
   no state, no marker, no warning, in no response. The input is a fraction, never a percent; the API
   field description says "fraction, 1 = one FTE, not a percent". **Named limitation:** a
   percent-vs-fraction typo (`50` for `0.5`) is stored and priced 100 times too high with state
   `calculated`, and nothing detects it.
3. **Formula: the exact product, one rounding.** `H = fte × D × S` (working days of the month and the
   calendar's `standard_hours_per_day` from the position's location calendar). `H` is not rounded; the
   rounded output of `fte_to_hours` is not an input (point 3 of the Decision; ADR-0002 rule 2). `H` is
   priced through `priced_amount`; months are summed unrounded; `round_money` is applied once at the
   end of the component. `hour` = `fte × D × S × rate`; `day` = `fte × D × rate`; `month` = `fte × rate`.
4. **Months.** An FTE position is priced in the months in which it has an allocation row, only those.
   Hours in those rows are not an input of this component. A position with `assigned_fte` and no
   allocation rows is the named state `no_planned_months`, never `0.00`. Grid hours and stored FTE can
   disagree; nothing reconciles them.
5. **States and precedence.** `no_cost_rate`, `currency_mismatch`, `no_calendar`, `no_working_days`,
   `no_planned_months`; `no_cost_currency` under the same condition as the other components. An FTE
   position needs a resolved calendar basis whatever the rate unit. This deliberately differs from
   SC-5-08 points 4 and 7. A state withholds only this component. No partial sum, no `0`.
6. **Dispatch is exclusive.** A position is priced by exactly one formula, chosen by `cost_basis`. A
   `fixed_amount` or `assigned_fte` position with allocation rows is never priced by the worked-time
   formula. `month_has_cost_rate` and the approval's rate freeze keep covering every position whose rate
   is read (worked time and FTE); the dispatch is applied at the formula input, not inside the
   predicate's statement. This corrects the reading in code; the SC-5-03 addendum text is unchanged.
7. **Overheads excluded.** No surcharge, no fully loaded figure. The surcharge pair still belongs to the
   predicate that resolves the month.
8. **Sources, live or frozen.** Draft reads live rate and calendar (`basis_by_location`); approved reads
   frozen rate/unit and frozen calendar (`frozen_basis_by_location`), never a live lookup to fill a
   missing key (`no_calendar`). Carries FTE-6. `assigned_fte` is own data of the scenario (ADR-0004
   SC-5-03 points 1-4): no snapshot, no new `SCENARIO_CHILD_COPIERS` entry.
9. **Visibility.** `assigned_fte` never appears in `GET .../staffing-positions` or any `STAFFING_READ`-only
   response. Position `POST` with `cost_basis = 'assigned_fte'` and every cost-basis `PATCH` require
   `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs`. Amount and `assumptions_used` join
   `SCENARIO_COST_FIELDS`; `state` stays visible and carries no amount.
10. **What-if and profitability.** The ADR-0015 what-if reaches the component through the shared
    per-(position, month) rate structure. `included_cost` does not include this component (as for
    fixed amount): profit/margin/markup overstated for scenarios with FTE positions until the block that
    sums components. Named, not repaired. **Named consequences (reviewer R-02, accepted by the human
    2026-09-29):** (a) after H-1 a `fixed_amount` position with allocation rows leaves `included_cost`
    (the worked-time formula no longer prices it), so the profit, margin and markup of such scenarios
    **rise**, including `approved` ones, because results are computed live, not frozen; (b) `assigned_fte`
    positions never enter profit; (c) no marker of either exists on any response; (d) the F-10 block
    must sum the components and is the task that removes both effects.
11. **Paid absence.** Unchanged for FTE positions; the overcount on a gross FTE is named, not repaired.
12. **Independence.** The FTE formula imports neither the revenue path nor the other two formulas; only
    the dispatcher in `app.data.personnel_cost` may import several. T&M revenue is unchanged by
    `assigned_fte`.

| Control | Acceptance criterion |
|---|---|
| FA-1 | DB refuses non-positive, missing-on-FTE, present-on-other-basis, or with-`fixed_amount` `assigned_fte`; above `headcount` accepted. |
| FA-2 | Hour rate, non-8h day, non-default working days: `fte × D × S × rate`, one rounding; day/month formulas as point 3; wrong `D`/`S`, constant 8, `weekday() < 5` mutations killed. |
| FA-3 | Unrounded basis hours used; a value where 2-place rounding changes the cent carries one rounding; `fte_to_hours` unchanged. |
| FA-4 | `fixed_amount`/`assigned_fte` positions with allocation rows are not priced by the worked-time formula (both directions); approved FTE rates stay frozen. |
| FA-5 | `assigned_fte` position without allocation rows gives `no_planned_months`, never `0.00`. |
| FA-6 | `no_calendar`/`no_working_days` apply to an hourly FTE position; precedence of point 5; only this component withheld. |
| FA-7 | After approval, editing catalogue rate/unit, calendar, its days or a location's `calendar_id` changes no FTE figure; missing frozen key gives `no_calendar`. |
| FA-8 | What-if raise changes the FTE component once through the shared structure; persisted scenario unchanged. |
| FA-9 | `GET .../staffing-positions` field set identical; FTE in no `STAFFING_READ`-only response; amount and `assumptions_used` withheld without the conjunction; creating/switching writes refused without it. |
| FA-10 | No surcharge on the FTE component; fully loaded figures unchanged by an FTE position. |
| FA-11 | Structural import test (mirror of C-5/U-7); T&M revenue unchanged by `assigned_fte`. |
| FA-12 | Copy keeps `cost_basis` and `assigned_fte` on an independent row; copier is not a closed column list. |
| FA-13 | One migration widens `cost_basis_known`, adds the column and CHECKs; downgrade refuses when `assigned_fte` rows exist; schema-drift test compares model and migration text. |
13. **Switching the basis clears the figure the new basis must not carry** (reviewer R-04). Leaving
    `assigned_fte` clears `assigned_fte` (a switch to any other basis); entering `assigned_fte` clears
    `fixed_amount` and `fixed_amount_currency`. Intended: the CHECKs of point 2 forbid the stray value.
    **Asymmetry, named:** the SC-5-03 switch `fixed_amount` -> `worked_time` leaves the stray amount on
    the row, where nothing reads it.
14. **Positions read and grid read are two statements** under `READ COMMITTED` (reviewer R-03): a
    concurrent edit between them can give a positions list and a grid of two moments. Draft-only and
    transient; accepted, not repaired.
15. **Security-auditor points, accepted.** (a) `assigned_fte_state` and its currency are visible
    without the conjunction, as `fixed_amount_state` is: they reveal that FTE-basis positions exist and
    the defect class (`no_cost_rate`, `no_calendar`, ...), never the value. (b) There is no upper
    bound against `headcount`; only callers holding `PERSONNEL_COSTS_READ` and
    `can_view_personnel_costs` can write the value. (c) **Verified in code:** `POST /projects/{id}/copy`
    requires `PROJECT_COPY` and `POST .../scenarios/{id}/duplicate` requires `SCENARIO_COPY`; neither
    requires the conjunction. The reflective copier carries `cost_basis`, `assigned_fte` and
    `fixed_amount` to the copy, so a caller without the conjunction can create a copy holding figures
    they cannot read; both responses (`ProjectDetail`, `ScenarioListItem`) contain no position and no
    cost figure. Named, accepted consequence, the same one `fixed_amount` has carried since SC-5-03;
    nothing worse.
