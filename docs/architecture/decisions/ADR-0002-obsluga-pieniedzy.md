# ADR-0002 — Obsługa pieniędzy: Decimal i jawne zaokrąglenia

**Status:** Draft — pending approval

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
