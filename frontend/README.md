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
    projects/         # project list screen (SC-1-06) + its field-label map + its stylesheet
  styles/
    tokens.css        # colour / type / spacing tokens — the ONLY file with a colour literal
    app.css           # shell, and the primitives more than one feature needs (card, button, badge)
  lib/
    money.ts          # shared money/percent formatting — see money.ts for why
    dates.ts          # shared date / date-range formatting
  App.tsx
```

## Styling

Colour, type, spacing and radius live in `src/styles/tokens.css` and nowhere else. A new colour is
a new token there — **never** a literal, and never a named colour or a `style={{…}}` prop in a
component. `src/styles/tokens.test.ts` reads the stylesheets themselves and fails the suite on a
colour literal outside the token file, on a colour value that is not a `var(--sc-…)`, on a
`var(--sc-…)` that no token defines, on an inline style in a `.tsx`, and on any `text-transform:
uppercase` (the brand guide forbids all-caps type).

The same file computes **WCAG contrast ratios from the token values** and requires 4.5:1 for every
foreground/background pair a rule declares, plus a hand-listed set of pairs the cascade produces
across rules. This is why the label on the orange button is black, not white (3.21:1 → 6.55:1) and
why a project name hovers to `--sc-color-blue-deep` rather than `--sc-color-blue` on a light-steel
row. Pairs that nobody listed and that no single rule declares are still unchecked.

The palette and the typeface come from `Wymagania/UI/globallogic_style_guide-v3.docx`; everything
else in the token file (neutrals, type scale, spacing) is this app's own. One brand rule rides
along with the tokens and **nothing tests it** — orange (`--sc-color-orange`) may sit on white
only, never on light steel; blue (`--sc-color-blue`) is the accent for light-steel surfaces.

A control that is rendered but not yet implemented (`aria-disabled="true"`) gets one appearance
regardless of variant: the muted surface and muted text of `--sc-color-disabled-surface` /
`--sc-color-text-disabled`, never a brand colour at reduced opacity, which reads as a live call to
action that happens to be broken.

Manrope is requested with a `<link>` in `index.html`, not an `@import` in CSS: the link starts the
font download in parallel with the bundle, while an `@import` is only discovered once the CSS has
been parsed. The family name itself stays a token; the tag is delivery, not definition.

## Known gaps

- **No translation catalog yet.** UI strings are English literals in the components; introducing an
  i18n layer is a product / architecture decision, not a side effect of a feature task. Until it
  exists, a new string goes in the component and a new *field-name → label* mapping goes in one
  module per feature (e.g. `features/projects/scenarioInputLabels.ts`), never inline at several
  call sites.
- **Search, Filters and Add project on the project list are visual placeholders.** They are
  rendered, focusable and `aria-disabled` with a tooltip, and wired to nothing: no client-side
  filtering (search/filter is a separate story — Issue #3, out of scope 2) and no call to
  `POST /projects`, even though the endpoint exists (SC-1-01) — that screen is a separate task.
  Same pattern as the row controls (View/Edit/Copy/Archive/Add scenario).
- **No visual-regression test, and none planned.** Pixel fidelity to `Wymagania/UI/Project
  List.jpeg` is explicitly out of scope (Issue #3, out of scope 1); the mockup is a reference. The
  layout is proven only to the extent that the DOM tests read the same text and controls as before.
- **jsdom applies the stylesheets but does not lay them out.** It resolves no custom property
  (`getComputedStyle` returns the literal `var(--sc-color-blue)`) and knows nothing of
  `overflow-wrap`, sticky positioning or wrapping. Every layout claim in
  `src/styles/tokens.test.ts` is therefore checked against the stylesheet *text* and its cascade
  order, not against a rendered box: the rule is proven to be written, not to have the effect it
  is written for.
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
