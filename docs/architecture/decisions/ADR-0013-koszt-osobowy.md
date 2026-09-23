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
   miesiąca niezgodne walutą między sobą). Zakaz sumy częściowej: jeden miesiąc bez stawki
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
`unit = 'hour'` w bazie); kosztu nieobecności płatnych (`absence_type.generates_cost`, budżet
urlopowy — inna podstawa godzin, własna reguła bramki ADR-0005 aneks SC-3-03 pkt 4); kosztów
poddostawców na pozycji (pozycja nie ma dziś `vendor_id`); przeliczenia walut (niezgodność jest
stanem nazwanym, nie kursem — ADR-0006 bez zmian).

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
