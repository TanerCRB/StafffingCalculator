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
