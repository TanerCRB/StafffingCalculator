# Frontend

React + TypeScript, Vite. Package manager: pnpm.

## Setup

```bash
pnpm install
cp .env.example .env
```

## Run

```bash
pnpm dev
```

## Test / lint / build

```bash
pnpm test
pnpm lint
pnpm build
```

## Layout

```
src/
  api/
    client.ts        # fetch wrappers, one per backend resource
    contracts/        # API response shapes — the ONLY place they're defined
  features/
    projects/         # project list screen (SC-1-06) + its field-label map
  lib/
    money.ts          # shared money/percent formatting — see money.ts for why
    dates.ts          # shared date / date-range formatting
  App.tsx
```

## Known gaps

- **No translation catalog and no theme tokens yet.** UI strings are English literals in the
  components and there is no stylesheet; introducing an i18n layer or a token set is a product /
  architecture decision, not a side effect of a feature task. Until it exists, a new string goes
  in the component and a new *field-name → label* mapping goes in one module per feature (e.g.
  `features/projects/scenarioInputLabels.ts`), never inline at several call sites.
- **A read that gets no answer within `REQUEST_TIMEOUT_MS` (12 s) fails** with a stated timeout
  message instead of an endless loading state (`src/api/client.ts`). The deadline covers the whole
  read — headers *and* body — and every fetch wrapper in that file goes through
  `readWithDeadline`; a wrapper added outside it has no deadline at all. No retry, no backoff yet
  — the user reloads.
- **`formatMoney` / `formatPercent` still take JS numbers** (R-03). No call site uses them for an
  API amount today — API decimals go through `formatPercentString` / `roundDecimalString`, which
  never leave the string domain (ADR-0002). The number-based pair is the trap to remove when
  money amounts start being displayed (plan block 5/7).
- **Caller identity is a placeholder** (`VITE_CALLER_USER_ID` → `X-Caller-User-Id`), per the
  2026-09-18 addendum to `docs/architecture/decisions/ADR-0005-model-dostepu.md`. It is not
  authentication.
