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
  data/      # the shared access-filtered read path (project_reads.py)
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
