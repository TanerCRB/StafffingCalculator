# Backend

FastAPI service. Python 3.12+.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate       # Windows; source .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
```

## Run

```bash
cp .env.example .env          # needed: see the note below
uvicorn app.main:app --reload
```

Caller identity is still a request-header placeholder, not authentication (ADR-0005, addendum
2026-09-18). The application **refuses to start** unless the run opts into it explicitly with
`APP_ALLOW_PLACEHOLDER_IDENTITY=true` *and* `APP_ENVIRONMENT` is `development` or `test`. That
refusal is deliberate: a process started with no configuration at all — no `.env`, wrong working
directory, a variable missing from a deployment manifest — must stop rather than fall back to
trusting a header. The test suite opts in from `tests/conftest.py`.

## Database

PostgreSQL only, SQLAlchemy 2.x, Alembic (ADR-0001). The URL comes from `APP_DATABASE_URL`
(see `.env.example`), never from a value committed here.

```bash
alembic upgrade head           # apply migrations
alembic upgrade head --sql     # review the SQL without applying it
alembic revision --autogenerate -m "<what changes>"
```

Migrations are expand → deploy → contract: a destructive step never ships in the same migration
as the code that needs the new shape. Schema changes live only in migration files.

**Jedno nazwane odstępstwo, z datą i warunkiem wygaśnięcia.** Migracja `c1a4f7b92e05` (stawki
poddostawców, SC-2-03) **przebudowuje** ograniczenie `EXCLUDE` na `catalog_default_rates` — `DROP`
i `CREATE` w jednej migracji, nie w parze expand/contract. Powód nie jest kosztowy, tylko
faktograficzny: stare, czterokolumnowe ograniczenie odrzuca dokładnie te wiersze, o które chodzi w
zadaniu, więc współistnienie obu kształtów znaczyłoby "funkcja wyłączona", a nie "wdrożenie
etapowe". Podstawą przyjęcia jest brak jakiegokolwiek wdrożonego środowiska (open decision #5) —
jedyne bazy to efemeryczne kontenery testowe i lokalne bazy deweloperskie. **Odstępstwo wygasa z
chwilą wyboru i uruchomienia pierwszego środowiska trwałego** (ADR-0001, aneks 2026-09-21 pkt 3;
ADR-0008, aneks 2026-09-21 pkt 4). Sama kolumna `vendor_id` jest wstecznie zgodna: nullable, bez
`server_default`, więc `INSERT` z kodu sprzed migracji nadal działa i nadal znaczy "stawka
wewnętrzna".

### Wymagane uprawnienie bazy: `CREATE EXTENSION btree_gist`

Migracja `7b3d5c81e40a` (katalog wymiarów roli i stawek, SC-2-01) wykonuje
`CREATE EXTENSION IF NOT EXISTS btree_gist` **przed** założeniem ograniczenia `EXCLUDE USING gist`
na `catalog_default_rates` (ADR-0008 pkt 5). Rozszerzenie jest potrzebne nie dla samego zakresu dat
(gist obsługuje `&&` na `daterange` natywnie), a dla operatorów `=` na kolumnach `uuid` wewnątrz
tego samego indeksu gist. Bez niego migracja **nie przejdzie** — a wraz z nią nie powstanie jedyny
mechanizm odrzucający nakładające się przedziały obowiązywania stawki.

Rola wykonująca migracje musi mieć prawo zakładania rozszerzeń. **Rola z prawem `CREATE` na bazie
zwykle wystarcza — `btree_gist` jest `trusted` od PostgreSQL 13 — ale nie jest to potwierdzone dla
środowiska docelowego, bo środowisko docelowe nie zostało jeszcze wybrane** (open decision #5 w
wymaganiach; ADR-0008 pkt 5 i 7). Zielony test w CI dowodzi, że mechanizm i migracja są poprawne,
i **nie** dowodzi, że rola aplikacji na docelowej bazie może to wykonać: kontener testowy
(testcontainers) uruchamia rolę nadrzędną.

Jeśli docelowa baza na to nie pozwala, rozszerzenie zakłada raz administrator bazy
(`CREATE EXTENSION btree_gist;`), a migracja przechodzi dzięki `IF NOT EXISTS`. `downgrade()`
rozszerzenia **nie usuwa** — jest obiektem bazy, nie jednej tabeli, i dziedziczą je `exchange_rates`
(ADR-0006) oraz `commercial_terms` (ADR-0003).

## Test / lint

```bash
pytest
ruff check .
```

`pytest` starts a throwaway PostgreSQL container (testcontainers) and runs the migrations
against it, so **Docker must be running**. Set `TEST_DATABASE_URL` to point the suite at a
PostgreSQL you run yourself instead.

## Layout

```
app/
  api/       # FastAPI routers, request dependencies, response shaping, response schemas
  core/      # config, caller identity/permissions, money handling (see core/money.py)
  data/      # the shared access-filtered read path (project_reads.py) + catalogue reads/writes
  db/        # declarative base, engine/session
  domain/    # calculation-independent rules (e.g. scenario readiness)
  models/    # SQLAlchemy models — one module per table
  main.py    # app factory / router registration
migrations/  # Alembic
tests/
```

Money is `Decimal` everywhere; round only through `app.core.money.round_money` (see
`../agents/invariant-guardian.md`, rules 1–3).

Projects are read **only** through `app.data.project_reads` — that function applies the
`project_access` scope filter (ADR-0001 addendum, ADR-0005). A `select(Project)` written
anywhere else bypasses the isolation boundary.

The catalogue (`app.data.catalog`) deliberately has **no** such guard function: its rows belong to
no project, so there is no scope predicate that could be forgotten, and a wrapper named
symmetrically to `project_reads` would imply a filter that is not there (ADR-0001 addendum
2026-09-19, ADR-0005 addendum 2026-09-19). The exception ends at the first catalogue column tying a
row to a project, a business unit or a tenant. `catalog_default_rates.vendor_id` (SC-2-03) is not
that column: a subcontractor is the *counterparty* of a rate, not a subject the caller acts on
behalf of, and no read narrows rates by the caller's relation to a vendor — decided explicitly in
ADR-0001's and ADR-0005's addenda of 2026-09-21, together with the business consequence that
everyone holding `CATALOG_READ` sees every subcontractor's price list. What does **not** move out
of the shaping layer is the personnel-cost gate: `app.api.response_shaping.shape_catalog_rate`
removes `default_cost_rate` for a caller without `PERSONNEL_COSTS_READ` — for a vendor row exactly
as for an internal one.
