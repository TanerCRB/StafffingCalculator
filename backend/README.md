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
uvicorn app.main:app --reload
```

## Test / lint

```bash
pytest
ruff check .
```

## Layout

```
app/
  api/       # FastAPI routers, one module per resource
  core/      # config, money handling (Decimal, rounding — see core/money.py)
  main.py    # app factory / router registration
tests/
```

No database yet — see `docs/PLAN.md`, task `SC-1-01`, for the first task that adds persistence.
Money is `Decimal` everywhere; round only through `app.core.money.round_money` (see
`../agents/invariant-guardian.md`, rules 1–3).
