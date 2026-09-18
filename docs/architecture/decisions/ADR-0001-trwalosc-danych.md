# ADR-0001 — Trwałość danych backendu

**Status:** Draft — pending approval

## Kontekst

Backend (`backend/`, FastAPI) na razie nie ma warstwy trwałości — patrz `backend/README.md` i
zadanie `SC-1-01` w `docs/PLAN.md`, pierwsze zadanie wymagające zapisu Projektu. Wymagania
wymuszające konkretny wybór:

- NF-01: obliczenia pieniężne muszą używać arytmetyki dziesiętnej (`Decimal`) i jawnych reguł
  zaokrąglania — silnik bazy musi to wspierać natywnie, bez utraty precyzji przy zapisie/odczycie.
- NF-04: szyfrowanie danych w spoczynku i w tranzycie, autoryzacja egzekwowana po stronie serwera.
- NF-03: scenariusz z 200 pozycjami staffingowymi i 36 miesiącami, 95% przeliczeń w 2 sekundy.
- F-12: wersje kalkulacji i historia zmian muszą być trwałe i odtwarzalne.
- F-13: dostęp ograniczany per projekt, oddzielne uprawnienie do kosztów osobowych — wymaga
  egzekwowania na poziomie zapytań, nie tylko UI.

## Decyzja

PostgreSQL jako jedyny silnik bazy danych, SQLAlchemy 2.x jako ORM, Alembic do migracji.
Migracje zawsze wstecznie kompatybilne: expand → deploy kodu → contract (patrz
`agents/invariant-guardian.md`, reguła 13). Kolumny pieniężne jako `NUMERIC` z jawną precyzją
(nie `FLOAT`/`DOUBLE`) — mapowane na `Decimal` w Pythonie, nigdy `float`.

## Konsekwencje

- Testy integracyjne mechanizmów izolacji/unikalności/ograniczeń działają na prawdziwym
  PostgreSQL w kontenerze — atrapa (SQLite, mock) nie dowodzi niczego o zachowaniu tych
  mechanizmów (patrz `agents/developer-backend.md`).
- Każda zmiana schematu żyje wyłącznie w pliku migracji (reguła Strażnika Niezmienników nr 14).
- Szyfrowanie w spoczynku (NF-04) to ustawienie środowiska wdrożeniowego (np. szyfrowanie
  dysku/instancji bazy), nie mechanizm aplikacji — poza zakresem tego ADR, wymaga osobnej decyzji
  gdy dojdzie zadanie dotyczące wdrożenia.

## Rozważane alternatywy

- **SQLite** — odrzucone: brak realnego wsparcia dla współbieżnych zapisów i mechanizmów
  izolacji wymaganych przez F-13 na docelową skalę.
- **MongoDB / dokumentowa** — odrzucone: model danych (F-06) jest silnie relacyjny (projekt →
  scenariusz → pozycje staffingowe → reguły komercyjne → koszty), a NF-01 wymaga dokładnej
  arytmetyki dziesiętnej, którą relacyjne `NUMERIC` daje bez dodatkowej pracy.

## Powiązane wymagania

NF-01, NF-03, NF-04, F-12, F-13
