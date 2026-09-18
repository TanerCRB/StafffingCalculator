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
  lib/
    money.ts          # shared money/percent formatting — see money.ts for why
  App.tsx
```
