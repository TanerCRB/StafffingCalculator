# ADR-0002 — Obsługa pieniędzy: Decimal i jawne zaokrąglenia

**Status:** Accepted

## Kontekst

NF-01: "Monetary calculations shall use decimal arithmetic and explicit rounding rules." AC-01,
AC-05, AC-07, AC-08, AC-09 podają konkretne, weryfikowalne wyniki liczbowe (np. przychód
20 000 PLN, marża 30%) — muszą być odtwarzalne co do grosza, niezależnie od kolejności operacji.
F-10 wymaga, żeby metryka z zerowym mianownikiem (np. marża przy przychodzie 0) zwracała
`"n/a"`, nigdy `NaN`/`Infinity`/błędną liczbę (AC-05).

## Decyzja

- Backend: `decimal.Decimal` wszędzie, gdzie wartość jest pieniądzem lub udziałem procentowym
  liczonym z pieniędzy. Zaokrąglanie wyłącznie przez jedną funkcję — `app.core.money.round_money`
  (`ROUND_HALF_UP`, precyzja zależna od waluty, domyślnie 2 miejsca). Zakaz `float` na ścieżce
  pieniądza — sprawdzane przez Strażnika Niezmienników.
- Frontend: kwoty z API jako string (nigdy `number` z JS `float`), formatowane wyłącznie przez
  `frontend/src/lib/money.ts` — nigdy `toFixed()` ad hoc w komponencie.
- Waluta jako para (kwota, kod ISO 4217) w każdym polu pieniężnym — nigdy sama liczba bez
  jednostki (F-02: reporting currency per projekt, F-08: currency per koszt).
- Dzielenie przez zero (Margin = Profit/Revenue, Markup = Profit/Cost) zwraca `"n/a"`, nigdy
  wyjątek ani `0`/`null` — `0` sugerowałby fałszywie zerową marżę.

## Konsekwencje

- Każda funkcja licząca metrykę finansową ma sygnaturę zwracającą `Decimal | Literal["n/a"]`
  (backend) lub odpowiadający typ unii (frontend), wymuszane przez sprawdzanie typów, nie tylko
  konwencję.
- Testy AC-01/AC-05/AC-07/AC-08/AC-09 z Requirements_EN.md §7 stają się bezpośrednimi testami
  jednostkowymi `app.core.money` i reguł przychodu (patrz Story F-06, F-10).

## Rozważane alternatywy

- **`float` + zaokrąglanie na wyjściu** — odrzucone: błędy reprezentacji binarnej kumulują się
  przy wielu operacjach (setki pozycji staffingowych × 36 miesięcy), a NF-01 wprost wymaga
  arytmetyki dziesiętnej.
- **Liczby całkowite w groszach (integer cents)** — rozważone, odrzucone: część reguł (F-06.3
  outcome-based, share of benefit; F-06.4 story points pricing tiers) operuje na ułamkowych
  stawkach i procentach, gdzie `Decimal` z jawną precyzją jest czytelniejszy niż ręczne skalowanie
  groszowe w każdej regule.

## Powiązane wymagania

NF-01, F-02, F-06 (base formulas), F-08, F-10, AC-01, AC-05, AC-07, AC-08, AC-09

## Aneksy

### 2026-09-19 — źródło jednostki minor waluty przy wyświetlaniu kwoty (SC-2-02)

Decyzja mówi dwie rzeczy, które SC-2-02 (ekran katalogu ról i stawek) zderza ze sobą: zaokrąglanie
wyłącznie przez `app.core.money.round_money` z „precyzją zależną od waluty, domyślnie 2 miejsca",
oraz że frontend formatuje kwoty wyłącznie przez `frontend/src/lib/money.ts`. Stan faktyczny na
2026-09-19: `round_money` kwantyzuje do modułowej stałej `TWO_PLACES` i **nie przyjmuje waluty** —
klauzula „precyzja zależna od waluty" nie jest zaimplementowana w żadnej warstwie. Katalog przechowuje
stawki jako `NUMERIC(14,4)`; SC-2-02 jest pierwszym konsumentem, który musi je zaokrąglić do
wyświetlenia.

1. **Rozstrzygnięcie:** 2 miejsca po przecinku dla każdej waluty, bez wyjątku — lustro dzisiejszego
   `round_money`. Zero zmian backendu wymaganych teraz.
2. **Reguła kierunkowa, niezależna od wariantu:** liczba miejsc po przecinku dla danej waluty ma
   jedno źródło dla obu warstw. Front nie wprowadza reguły waluty, której backend nie zna — byłby to
   drugi punkt zaokrąglania dla tej samej kwoty (reguła 2 Strażnika Niezmienników), a rozjazd
   ujawniłby się jako różnica między ekranem a przyszłym eksportem, nie jako błąd.
3. **Punkt wejścia bez zmian:** formatowanie kwoty z API idzie przez `frontend/src/lib/money.ts` i
   operuje na stringu dziesiętnym; funkcja przyjmująca `number` (`formatMoney`) nie jest dopuszczalna
   dla kwoty pochodzącej z API.
4. **Warunek zamknięcia:** pierwsza waluta w katalogu, której jednostka minor nie ma 2 miejsc (np.
   JPY = 0, BHD = 3) — wtedy „2 miejsca dla każdej waluty" przestaje być dopuszczalne i wraca tu jako
   nowy, datowany wpis wymagający zmiany `round_money` (parametr waluty) i frontendu razem.

### 2026-09-21 — kwota wpisana przez człowieka: kierunek wejścia (SC-2-04)

Aneks z 2026-09-19 rozstrzygnął wyłącznie kierunek wyjścia. SC-2-04 jest pierwszym zadaniem, w
którym kwota idzie w drugą stronę: z formularza do API, na dodawaniu i na edycji.

1. **Kwota opuszcza przeglądarkę jako string dziesiętny, nigdy jako `number`.** Ta sama zasada co
   dla odczytu i z tego samego powodu: `number` w JS jest binarnym floatem, a NF-01 wymaga arytmetyki
   dziesiętnej. Żadnego `parseFloat`/`Number()` na ścieżce wejścia.
2. **Front nie zaokrągla na ścieżce wejścia.** Katalog przechowuje stawkę jako `NUMERIC(14,4)`, a
   `round_money`/`TWO_PLACES` jest regułą prezentacji (aneks 2026-09-19 pkt 1). Zaokrąglenie
   wartości wpisanej cicho zmieniłoby daną wejściową — dokładnie to, czego zakazuje ADR-0008 pkt 6.
   Precyzja powyżej czterech miejsc jest odmawiana przez serwer jako `422` (dowiedzione, R-05
   SC-2-01, potwierdzone na ścieżce edycji:
   `backend/tests/test_catalog_edit.py::test_q_2_an_amount_more_precise_than_the_column_is_refused_not_rounded`);
   klient może tę granicę zapowiedzieć w walidacji kształtu (NF-07), ale nie wolno mu wartości uciąć.
   Ten sam punkt pokrywa ścieżkę powrotną przy edycji — patrz ADR-0008, aneks 2026-09-21 pkt 1:
   formularz edycji ładuje pełną precyzję z odpowiedzi API, nigdy z zaokrąglonej komórki tabeli.
3. **Waluta jest podawana jawnie, jako kod ISO-4217, i nie pochodzi z reguły znanej tylko frontowi.**
   Punkt 2 aneksu z 2026-09-19 obowiązuje bez zmian: „Front nie wprowadza reguły waluty, której
   backend nie zna". **Rozstrzygnięcie (bramka 1, P-5: wariant A):** pole tekstowe na kod ISO-4217;
   jedynym autorytetem pozostaje `CHECK` w bazie (`currency = upper(currency)`, ISO-4217) —
   odrzucony wariant stałej listy walut we froncie, bo byłby to dokładnie zakazany kierunek: reguła
   waluty znana tylko klientowi. Jeśli lista walut ma kiedyś powstać, jej miejscem jest backend
   (ADR-0006), osobnym zadaniem.
4. **Jednostka nie jest wyborem.** Baza wymusza `unit = 'hour'`; kontrolka oferująca wybór obiecywałaby
   możliwość, której nie ma. NF-07 („forms shall explain input units") jest spełnione przez nazwanie
   jednostki, nie przez udawany wybór.
5. **Warunek zamknięcia z aneksu 2026-09-19 pkt 4 bez zmian:** pierwsza waluta, której jednostka minor
   nie ma dwóch miejsc, wymaga zmiany `round_money` i frontu razem i wraca tu nowym wpisem.

### 2026-09-24 — stan złożony przy kompozycji kilku źródeł (SC-7-01)

Reguła n/a przy zerowym mianowniku (wyżej) dotyczy dokładnie jednego dzielenia. SC-7-01 (zysk/
marża/markup scenariusza) jest pierwszym zadaniem składającym więcej niż jedno niezależnie
nazwane źródło stanu — przychód (`RevenueState`), koszt osobowy (`PersonnelCostState`/
`PaidAbsenceCostState`), koszt dodatkowy (`AdditionalCostState`) — w jedną odpowiedź; żaden z
tych trzech enumów nie jest tym samym typem, choć część nazw pokrywa się przypadkowo (np.
`currency_mismatch` znaczy co innego w każdym).

1. **Rozstrzygnięcie (bramka 1, 2026-09-24, decyzja człowieka na rekomendację Analityka):** gdy
   więcej niż jedno źródło jest jednocześnie w stanie nazwanym niekalkulowalnym (nie rozstrzygniętym
   zerem), odpowiedź nazywa stan KAŻDEGO źródła osobno — nie zwija się do jednego wspólnego
   sentinela (np. uniwersalne `"n/a"`) i nie wybiera jednego reprezentanta wg priorytetu. `profit`/
   `margin`/`markup` nie udają liczby w żadnym z tych przypadków.
2. **Kształt pola pozostaje decyzją Developera, nie tej decyzji.** Kryterium akceptacji (K-05,
   Analyst SC-7-01) jest neutralne wobec tego, czy odpowiedź niesie jedno pole zbiorcze zdolne
   nazwać źródło+przyczynę, czy trzy pola per źródło — wiążąca jest wyłącznie zasada "nazwij
   źródło, nie zwijaj".
3. **Warunek ponownego otwarcia:** pierwsze zadanie, które musi zwrócić jedną nazwę stanu dla
   całej odpowiedzi (np. UI potrzebujący jednego komunikatu zamiast trzech) — wymaga wtedy osobnej
   decyzji o priorytetyzacji źródeł, nie rozszerzenia tego punktu przez milczenie.

### 2026-09-25 — równość walut przychodu i kosztów w wynikach złożonych (SC-4-03, runda weryfikacji 1)

Decyzja człowieka 2026-09-25 (ustalenie R-01 weryfikacji SC-4-03). Wyniki złożone (`/results`
SC-7-01, what-if SC-6-04, porównanie SC-6-02) wymagają równości waluty przychodu i waluty każdego
wyliczonego źródła kosztu; w przeciwnym razie `profit`/`margin`/`markup` = nazwany stan
`currency_mismatch`, nigdy suma kwot w różnych walutach (brak przeliczenia — ADR-0006). Reguła
zamyka także przypadek istniejący przed SC-4-03: koszt dodatkowy w walucie innej niż koszt osobowy.
Zasada "nazwij źródło, nie zwijaj" z aneksu 2026-09-24 pozostaje bez zmian. Szczegóły: ADR-0003,
aneks 2026-09-25 SC-4-03 pkt 7.

### 2026-09-26 — godziny jako trzecia, jawnie dopuszczona klasa wielkości dziesiętnej (SC-3-04)

Decyzja człowieka na bramce 1 SC-3-04 (rekomendacja Architekta). Godziny (`availability_hours`/
`planned_allocation_hours`/`billable_hours`/`derived_capacity_hours`/`absence_budget_hours`,
`NUMERIC(10,2)`) przekraczają granicę API jako fixed-point string z tego samego powodu co pieniądz
— backend już to zakładał bez formalnego aneksu (`backend/app/api/schemas/staffing.py`: "hours are
one multiplication away from it", NF-01). Niniejszy aneks czyni to jawnym.

1. **Osobny moduł, nie rozszerzenie `money.ts`.** Formatowanie godzin ląduje w
   `frontend/src/lib/hours.ts`, reużywającym `roundDecimalString`/`isDecimalString` z
   `frontend/src/lib/money.ts` — `money.ts` zostaje dokładnie tym, co obiecuje jego nagłówek
   ("money/percentage only"); godziny nie mają ani waluty, ani procentu, więc dzielą gramatykę
   fixed-point, nie tożsamość modułu.
2. Zaokrąglanie wyłącznie przez `roundDecimalString` — zakaz `Number()`/`toFixed()` ad hoc na
   wartości godzinowej, tak samo jak dla pieniądza.
3. Sentinel `"n/a"` (`derived_capacity_hours`, `absence_budget_hours` przy stanie niekalkulowalnym)
   czytany identycznie jak dla marży/markupu — jedno znaczenie tego stringa w całej aplikacji.
4. `working_days`/`absence_day_equivalents` (liczby całkowite, nie decimal-string) nie przechodzą
   przez ten moduł — inny przypadek, nierozróżniany przypadkowo z godzinami.
5. **Warunek ponownego otwarcia:** pierwsze zadanie potrzebujące trzeciej wielkości bez waluty i
   bez procentu w tym module (nie godzin) rozstrzyga, czy `hours.ts` staje się ogólniejszym
   "unit.ts", czy dostaje rodzeństwo — nie rozszerza tego punktu przez milczenie.

### 2026-09-29 — the unit of the cost rate becomes a choice; the selling-rate unit does not (SC-5-08, Issue #80)

**Status:** Draft — pending approval

> Gate 1 of SC-5-08 (2026-09-29, Q-1 = B, Q-5: backend only, the frontend is Issue #164). This entry
> narrows point 4 of the addendum 2026-09-21 ("Jednostka nie jest wyborem") and leaves its text
> unchanged.

1. **What is superseded, and what is not.** Point 4 of the addendum 2026-09-21 says the rate unit
   is not a choice because the database enforces `unit = 'hour'`. That stays true for `unit`, the
   unit of the **selling** rate: it remains pinned to `hour`, and a control offering a choice for it
   would still promise a capability that does not exist. Point 4 is superseded **only for the cost
   rate**, which since SC-5-08 has its own column `cost_rate_unit` with the values `hour`, `day`,
   `month` (ADR-0013, addendum 2026-09-29 SC-5-08, point 1). The supersession takes effect together
   with the frontend follow-up (Issue #164); until that task, the catalogue form and the cost cell
   are governed by point 4 as written, and the backend's acceptance of `day`/`month` is not a licence
   for a client to offer them.
2. **Source of the three values: the backend, one closed set, never a list known only to the
   client.** The authority is the `CHECK` in the database; the API schema mirrors it; the frontend
   takes the values from the closed value set of the API contract type and its shape check
   (`frontend/src/api/contracts/**`, `frontend/src/api/client.ts`, ADR-0003 addendum SC-4-07,
   points 4–5: the closed sets are checked at the boundary and a new value is added there, not in a
   component). This is the direction of point 3 of the addendum 2026-09-21 (currency): the frontend
   introduces no rule the backend does not know, and a value outside the set is refused by the shape
   check, not rendered. Rejected: a hard-coded list in a component.
3. **The unit is part of the amount's meaning and is displayed with it.** A cost rate is rendered
   with its `cost_rate_unit` (per hour / per day / per month), never with `unit`: the cost cell
   that reads `rate.unit` for the cost rate becomes wrong when the two differ. The NF-07 sentence of
   point 4 ("forms shall explain input units") is satisfied by naming the unit next to the field,
   and now also by the choice itself.
4. **Gated with the rate.** When the cost rate is withheld (ADR-0005, addendum 2026-09-29 SC-5-08),
   its unit is withheld with it; the screen shows the restricted state and never a default `hour`
   for a missing `cost_rate_unit` (the boundary of the addendum ADR-0005 2026-09-21 SC-2-04,
   point 2: the client reconstructs nothing that the server withheld).
5. **The frontend computes no conversion.** The day and month conversions (ADR-0013, addendum
   2026-09-29 SC-5-08, point 3) are backend-only; the frontend shows the rate, its unit and the
   amounts the API returns, and does no arithmetic on them (points 1–3 of this ADR, unchanged).
6. **Unchanged:** the input direction of the addendum 2026-09-21, points 1–3 and 5 (string
   amounts, no client rounding, the currency as an explicit ISO-4217 code) applies to the cost rate
   in every unit; the closing condition of the addendum 2026-09-19 point 4 (a currency whose minor
   unit is not 2 places) is unaffected — a monthly rate is still an amount with two places when
   rounded by `round_money`.

| Control | Acceptance criterion |
|---|---|
| CU-1 | The catalogue form offers a choice of unit for the cost rate only, with exactly the values of the API contract type; no control offers a choice of the selling-rate `unit`. |
| CU-2 | The values offered by the form and accepted by the shape check are one set, defined in the contract type; a value outside it is refused at the boundary and not rendered. |
| CU-3 | A cost rate is displayed with its `cost_rate_unit`, never with `unit`; a withheld rate shows no unit and no default. |
