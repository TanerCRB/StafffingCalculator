import { describe, expect, it } from "vitest";

import indexHtml from "../../index.html?raw";

/**
 * The token layer is the kind of mechanism that fails while still rendering: a hex literal dropped
 * into a component stylesheet looks right on the author's screen and silently forks the palette; a
 * mistyped `var(--sc-colr-blue)` resolves to nothing and renders a transparent background instead
 * of throwing. Neither shows up in a DOM test or a screenshot, so they are checked here, against
 * the stylesheets themselves.
 *
 * The stylesheets are collected with a glob rather than listed by name on purpose — a rule that
 * only covers the files someone remembered to enumerate stops holding the day a new one is added.
 */

/** Glob keys are relative to this file, so the token source is a sibling. */
const TOKENS_PATH = "./tokens.css";

const stylesheets = import.meta.glob("../**/*.css", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Comments are prose about colours; only the declarations are the palette. */
function withoutComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

function source(path: string): string {
  return withoutComments(stylesheets[path]);
}

const paths = Object.keys(stylesheets);
const otherPaths = paths.filter((path) => path !== TOKENS_PATH);
const tokensCss = source(TOKENS_PATH);

const components = import.meta.glob("../**/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** SC-2-05 (ADR-0011): any graphic file under `src/`. None today — the icons come from
 * `@tabler/icons-react` — and the check below holds the day the first one is added. */
const graphics = import.meta.glob("../**/*.svg", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

// --- A very small stylesheet reader ------------------------------------------------------------
// Enough for these files and no more: flat rules, no media queries, no nesting. It is deliberately
// dumb — if the stylesheets ever grow a construct it cannot see, the checks below must be made to
// notice that, not quietly skip it.

interface Rule {
  readonly file: string;
  readonly selector: string;
  readonly declarations: ReadonlyMap<string, string>;
  /** Position in the concatenated cascade — later wins ties. */
  readonly order: number;
}

function parseRules(): Rule[] {
  const rules: Rule[] = [];
  for (const path of paths) {
    const css = source(path);
    expect(css, `${path} uses a construct this reader cannot see`).not.toMatch(/@media|@supports/);
    for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const declarations = new Map<string, string>();
      for (const part of match[2].split(";")) {
        const colon = part.indexOf(":");
        if (colon === -1) continue;
        declarations.set(part.slice(0, colon).trim(), part.slice(colon + 1).trim());
      }
      rules.push({
        file: path,
        selector: match[1].trim().replace(/\s+/g, " "),
        declarations,
        order: rules.length,
      });
    }
  }
  return rules;
}

const rules = parseRules();

/** `[a, b, c]` — ids, class-level parts (classes, attributes, pseudo-classes), element parts.
 * Computed per comma-separated part; a rule is represented by its strongest part. */
function specificity(selector: string): [number, number, number] {
  const parts = selector.split(",").map((part) => {
    const ids = part.match(/#[\w-]+/g)?.length ?? 0;
    const classLevel =
      (part.match(/\.[\w-]+/g)?.length ?? 0) +
      (part.match(/\[[^\]]*\]/g)?.length ?? 0) +
      (part.match(/(?<!:):(?!:)[\w-]+/g)?.length ?? 0);
    const elements =
      (part.match(/(^|[\s>+~])[a-z][\w-]*/g)?.length ?? 0) + (part.match(/::[\w-]+/g)?.length ?? 0);
    return [ids, classLevel, elements] as [number, number, number];
  });
  return parts.reduce((strongest, part) => (isWeaker(strongest, part) ? part : strongest));
}

function isWeaker(a: readonly number[], b: readonly number[]): boolean {
  for (let index = 0; index < 3; index += 1) {
    if (a[index] !== b[index]) return a[index] < b[index];
  }
  return false;
}

// --- Colour maths ------------------------------------------------------------------------------

const tokenValues = new Map(
  [...tokensCss.matchAll(/(--sc-[\w-]+)\s*:\s*([^;]+);/g)].map((match) => [
    match[1],
    match[2].trim(),
  ]),
);

/** Follows `var()` indirection between tokens; returns null for anything that is not a colour
 * (`none`, `transparent`, a shadow, a length). */
function resolveColour(value: string, depth = 0): string | null {
  const trimmed = value.trim();
  if (/^#[0-9a-fA-F]{6}$/.test(trimmed)) return trimmed.toLowerCase();
  const reference = /^var\(\s*(--sc-[\w-]+)\s*\)$/.exec(trimmed);
  if (reference && depth < 5) {
    const target = tokenValues.get(reference[1]);
    return target === undefined ? null : resolveColour(target, depth + 1);
  }
  return null;
}

function channel(byte: number): number {
  const value = byte / 255;
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((offset) => channel(parseInt(hex.slice(offset, offset + 2), 16)));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 2.x contrast ratio, rounded to two decimals so a failure message reads like the review
 * comment that asked for it. */
function contrastRatio(foreground: string, background: string): number {
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return Math.round(((lighter + 0.05) / (darker + 0.05)) * 100) / 100;
}

const AA_NORMAL_TEXT = 4.5;

/** A fresh regex per call — a shared `/g` literal carries `lastIndex` from one file to the next
 * and would let the second offender through. */
function colourLiterals(css: string): string[] {
  return css.match(/#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(/g) ?? [];
}

describe("theme tokens", () => {
  it("keeps every colour literal in the single token source and nowhere else", () => {
    // Contrast: the token file itself must actually hold a palette, so that "no literals anywhere"
    // cannot be satisfied by there being no colours at all — and there must be other stylesheets
    // for the rule to be about something.
    expect(colourLiterals(tokensCss).length).toBeGreaterThan(10);
    expect(otherPaths.length).toBeGreaterThan(0);

    const offenders = otherPaths.filter((path) => colourLiterals(source(path)).length > 0);

    expect(offenders).toEqual([]);
  });

  it("names every token it uses — a stylesheet cannot reference one that does not exist", () => {
    const defined = new Set(
      [...tokensCss.matchAll(/(--sc-[\w-]+)\s*:/g)].map((match) => match[1]),
    );
    expect(defined.size).toBeGreaterThan(20);

    const unknown = new Set<string>();
    for (const path of paths) {
      for (const match of source(path).matchAll(/var\(\s*(--sc-[\w-]+)/g)) {
        if (!defined.has(match[1])) {
          unknown.add(`${path}: ${match[1]}`);
        }
      }
    }

    expect([...unknown]).toEqual([]);
  });

  it("sets type in Manrope and never in all caps, the two rules the brand guide states outright", () => {
    expect(tokensCss).toMatch(/--sc-font-family:\s*"Manrope"/);
    // The font has to actually be requested; a family name nothing delivers is a silent fallback
    // to the system font, on every screen, with nothing broken enough to notice.
    expect(indexHtml).toContain("family=Manrope");

    const uppercased = paths.filter((path) => /text-transform:\s*uppercase/.test(source(path)));
    expect(uppercased).toEqual([]);

    // Outside the token file, a font family may only be named by reference.
    const hardcodedFamilies = otherPaths
      .flatMap((path) =>
        [...source(path).matchAll(/font-family:\s*([^;]+);/g)].map((match) => ({
          path,
          value: match[1].trim(),
        })),
      )
      .filter(({ value }) => !value.startsWith("var(--sc-font-"));
    expect(hardcodedFamilies).toEqual([]);
  });

  it("states every colour by token name — not `white`, not `red`, and not inline in a component", () => {
    // The hex check above is blind to a named colour, and blind to anything a component sets in a
    // `style` prop; both bypass the palette just as completely (Guardian note).
    const COLOUR_PROPERTIES =
      /^(?:color|background|background-color|outline|outline-color|fill|stroke|border(?:-(?:top|right|bottom|left|block|inline))?(?:-color)?)$/;
    const SAFE_WORDS = new Set([
      "none",
      "transparent",
      "inherit",
      "currentcolor",
      "initial",
      "unset",
      "solid",
      "dashed",
      "dotted",
      "auto",
    ]);

    const offenders: string[] = [];
    for (const rule of rules) {
      if (rule.file === TOKENS_PATH) continue;
      for (const [property, value] of rule.declarations) {
        if (!COLOUR_PROPERTIES.test(property)) continue;
        const leftovers = value
          .replace(/var\(\s*--sc-[\w-]+\s*\)/g, " ")
          .split(/\s+/)
          .filter((word) => word !== "" && !SAFE_WORDS.has(word.toLowerCase()) && !/^[\d.]/.test(word));
        if (leftovers.length > 0) {
          offenders.push(`${rule.file} ${rule.selector} { ${property}: ${value} }`);
        }
      }
    }
    expect(offenders).toEqual([]);

    const inlineStyles = Object.entries(components)
      .filter(([, source_]) => source_.includes("style={{"))
      .map(([path]) => path);
    expect(Object.keys(components).length).toBeGreaterThan(0);
    expect(inlineStyles).toEqual([]);
  });

  it("carries no colour in an icon — no `fill`, `stroke` or `color` attribute with a value, and no graphic file with one (SC-2-05, K-29)", () => {
    // ADR-0011, point 2. The two checks above read stylesheets and `style={{`; an icon painted by
    // attribute — `<IconPlus color="#0066ff" />`, `<path fill="#ff5500">` — is neither, and forks
    // the palette just as completely. `@tabler/icons-react` draws in `currentColor` by construction,
    // so the only way a colour gets into one is somebody writing it here.
    const PAINT_ATTRIBUTE = /\b(fill|stroke|color)\s*=\s*\{?\s*["'`]([^"'`]*)["'`]/g;
    const ALLOWED = new Set(["none", "currentcolor", "transparent", "inherit"]);

    const offenders: string[] = [];
    for (const [path, source_] of Object.entries(components)) {
      for (const match of source_.matchAll(PAINT_ATTRIBUTE)) {
        if (!ALLOWED.has(match[2].trim().toLowerCase())) {
          offenders.push(`${path}: ${match[0]}`);
        }
      }
    }
    for (const [path, svg] of Object.entries(graphics)) {
      if (colourLiterals(svg).length > 0 || /\b(fill|stroke)\s*=\s*["'](?!none|currentColor)/i.test(svg)) {
        offenders.push(path);
      }
    }
    expect(offenders).toEqual([]);

    // Contrast: the icons this rule is about exist, so it is not an empty check over a codebase
    // with no icons in it.
    expect(
      Object.values(components).some((source_) => source_.includes('from "@tabler/icons-react"')),
    ).toBe(true);
  });
});

describe("colour contrast", () => {
  /**
   * Reviewer R-01: white on the brand orange is 3.21:1 — it renders perfectly and is unreadable
   * for part of the audience, which is why a screenshot never caught it. The ratios below are
   * computed from the token values, so the palette cannot drift under the claim.
   */

  it("meets WCAG AA for every foreground the stylesheets pair with a background", () => {
    const pairs = rules.flatMap((rule) => {
      const foreground = resolveColour(rule.declarations.get("color") ?? "");
      const background = resolveColour(
        rule.declarations.get("background-color") ?? rule.declarations.get("background") ?? "",
      );
      if (foreground === null || background === null) return [];
      return [{ where: `${rule.file} ${rule.selector}`, foreground, background }];
    });

    // The pairs have to be found, or this test is an elaborate way of iterating over nothing.
    expect(pairs.length).toBeGreaterThan(8);

    const failures = pairs
      .map((pair) => ({ ...pair, ratio: contrastRatio(pair.foreground, pair.background) }))
      .filter((pair) => pair.ratio < AA_NORMAL_TEXT);
    expect(failures).toEqual([]);
  });

  it("meets WCAG AA where the text and its surface are set in different rules", () => {
    // The pairing above only sees a rule that sets both. These are the combinations the cascade
    // produces across rules — the project name on its row, the hover colour on that same row —
    // and they are listed by hand, which is this check's weak spot: a pair nobody lists is a pair
    // nobody checks.
    const declaredPairs = [
      { where: "project name on a row", foreground: "--sc-color-text", background: "--sc-color-surface" },
      { where: "project name, hovered row", foreground: "--sc-color-blue-deep", background: "--sc-color-surface-muted" },
      { where: "project name, selected row", foreground: "--sc-color-blue-deep", background: "--sc-color-status-active-bg" },
      { where: "column headers on the header wash", foreground: "--sc-color-text-muted", background: "--sc-color-surface-muted" },
      { where: "client and period cells", foreground: "--sc-color-text-muted", background: "--sc-color-surface" },
      { where: "client and period cells, hovered row", foreground: "--sc-color-text-muted", background: "--sc-color-surface-muted" },
      { where: "client and period cells, selected row", foreground: "--sc-color-text-muted", background: "--sc-color-status-active-bg" },
      { where: "details panel meta text", foreground: "--sc-color-text-muted", background: "--sc-color-surface-muted" },
      { where: "missing-input list on a scenario card", foreground: "--sc-color-attention-text", background: "--sc-color-surface" },
      { where: "shell subtitle in the topbar", foreground: "--sc-color-text-muted", background: "--sc-color-surface" },
      { where: "breadcrumb trail", foreground: "--sc-color-text-muted", background: "--sc-color-surface" },
      { where: "backend status pill", foreground: "--sc-color-text-muted", background: "--sc-color-surface-muted" },
      { where: "unreachable backend notice", foreground: "--sc-color-attention-text", background: "--sc-color-attention-bg" },
      { where: "rail entry, resting", foreground: "--sc-color-text-muted", background: "--sc-color-surface" },
      { where: "rail entry, hovered", foreground: "--sc-color-text", background: "--sc-color-surface-muted" },
      { where: "rail entry for the current screen", foreground: "--sc-color-status-active-text", background: "--sc-color-status-active-bg" },
      { where: "rail entry not implemented yet", foreground: "--sc-color-text-disabled", background: "--sc-color-disabled-surface" },
      { where: "search placeholder, inactive", foreground: "--sc-color-text-disabled", background: "--sc-color-disabled-surface" },
      // SC-2-02, the catalogue screen. Every pair its stylesheet leaves to the cascade: the count
      // sentence and the panel titles sit on a `.card`, the period sits on a rate row. The withheld
      // cost rate ("Restricted") and dictionary entries no longer leave their colour to the cascade
      // as of SC-2-05 — `.catalog__restricted` carries its own background, and entries sit on a
      // white `.card` — so both are proven by the rule-pair test below instead of listed here (a
      // pair listed against a background nothing renders it on proves nothing; see SC-2-05 R-02).
      { where: "rate count sentence on a panel card", foreground: "--sc-color-text-muted", background: "--sc-color-surface" },
      // K-12: the same sentence's truncated-page state, on the same card.
      { where: "truncated rate count sentence on a panel card", foreground: "--sc-color-attention-text", background: "--sc-color-surface" },
      { where: "effective period cell on a rate row", foreground: "--sc-color-text-muted", background: "--sc-color-surface" },
      // SC-2-03. Same reasoning as the withheld cost rate above: "Internal" is the whole content
      // of the vendor cell on every internal rate, so an unreadable colour empties the cell — and
      // an empty vendor cell is exactly the reading K-09 forbids.
      { where: "internal rate in the vendor cell of a rate row", foreground: "--sc-color-text-muted", background: "--sc-color-surface" },
      { where: "catalogue failure message", foreground: "--sc-color-attention-text", background: "--sc-color-attention-bg" },
      // SC-2-04, the catalogue write forms. Every one of these sits on the form's own muted
      // surface, which is a *different* background from the white the rows above sit on — the same
      // token pair can pass on one and fail on the other, so they are listed separately rather than
      // assumed to be covered by the rows above.
      { where: "field label inside a catalogue form", foreground: "--sc-color-text-muted", background: "--sc-color-surface-muted" },
      { where: "field hint inside a catalogue form", foreground: "--sc-color-text-muted", background: "--sc-color-surface-muted" },
      { where: "stated (not editable) value inside a catalogue form", foreground: "--sc-color-text", background: "--sc-color-surface-muted" },
      { where: "refused save inside a catalogue form", foreground: "--sc-color-attention-text", background: "--sc-color-attention-bg" },
    ];

    const failures = declaredPairs
      .map((pair) => {
        const foreground = resolveColour(`var(${pair.foreground})`);
        const background = resolveColour(`var(${pair.background})`);
        if (foreground === null || background === null) {
          throw new Error(`Not a colour token: ${pair.foreground} / ${pair.background}`);
        }
        return { where: pair.where, ratio: contrastRatio(foreground, background) };
      })
      .filter((pair) => pair.ratio < AA_NORMAL_TEXT);
    expect(failures).toEqual([]);
  });

  it("meets WCAG AA for every colour the catalogue stylesheet sets without a background of its own", () => {
    // QA, SC-2-02. The list above is a list of intentions: it pairs two token names and checks them
    // against each other, and never asks the stylesheet which token it actually applies. Two
    // mutations proved that it cannot fail for this screen — `.catalog__restricted` and
    // `.catalog__cell-period` each set to `var(--sc-color-data-amber)`, 1.75:1 on white, left all
    // the tests green while the declared rows still read `--sc-color-attention-text` and
    // `--sc-color-text-muted`. The withheld cost is the worst case: the refusal word is the entire
    // content of that cell, so a colour nobody can read is a cell that looks empty, which is the
    // one thing gate-1 decision 2 exists to prevent.
    //
    // This check reads the foreground from the rule instead. The surface each rule sits on still
    // has to be stated by hand — the cascade is not reconstructed here — but an unstated rule is a
    // failure rather than a silent omission, which is what the comment above calls this check's
    // weak spot.
    const surfaces: Readonly<Record<string, string>> = {
      // `.catalog__count` sits inside a `.card`; the two cell rules sit on `.catalog__row > td`.
      // Both of those set `--sc-color-surface`, and both set it together with their own colour, so
      // the pairing test above is what keeps them honest.
      ".catalog__count": "--sc-color-surface",
      // K-12: the truncated-page state of the same sentence, on the same `.card` surface.
      ".catalog__count--truncated": "--sc-color-surface",
      ".catalog__cell-period": "--sc-color-surface",
      // SC-2-05: `.catalog__restricted` left this list because it is no longer colour-only — the
      // label now sets `--sc-color-attention-bg` next to its colour (K-30c), so the pairing test
      // above computes it from the rule itself. The two claims below about its colour still hold.
      // SC-2-03: the vendor cell of an internal rate, also on `.catalog__row > td`.
      ".catalog__internal": "--sc-color-surface",
      // SC-2-05: the screen and section descriptions (G-2) sit on the page, outside any card; the
      // dictionary entries are rows on the panel's white `.card`, no longer chips on a wash.
      ".catalog__description": "--sc-color-page",
      ".catalog__entry": "--sc-color-surface",
      // SC-2-04: the three colour-only rules of the write forms. They sit on `.catalog__form`,
      // which sets `--sc-color-surface-muted` and no colour of its own — the one background in this
      // stylesheet that is not white, which is why naming it here rather than inheriting the
      // default matters.
      ".catalog__field-label": "--sc-color-surface-muted",
      ".catalog__field-hint": "--sc-color-surface-muted",
      ".catalog__field-stated": "--sc-color-surface-muted",
    };

    const colourOnly = rules.filter(
      (rule) =>
        rule.file.endsWith("/CatalogScreen.css") &&
        rule.declarations.has("color") &&
        !rule.declarations.has("background-color") &&
        !rule.declarations.has("background"),
    );
    // Found, or this is an elaborate way of iterating over nothing.
    expect(colourOnly.length).toBeGreaterThan(0);
    // Every such rule is accounted for, and nothing is listed that the stylesheet no longer has: a
    // colour added later with no surface named here fails instead of joining the set nobody checks.
    expect(colourOnly.map((rule) => rule.selector).sort()).toEqual(Object.keys(surfaces).sort());

    const failures = colourOnly
      .map((rule) => {
        const declared = rule.declarations.get("color") ?? "";
        const foreground = resolveColour(declared);
        const background = resolveColour(`var(${surfaces[rule.selector]})`);
        if (foreground === null || background === null) {
          throw new Error(`Not a colour: ${rule.selector} { color: ${declared} }`);
        }
        return { where: rule.selector, ratio: contrastRatio(foreground, background) };
      })
      .filter((pair) => pair.ratio < AA_NORMAL_TEXT);
    expect(failures).toEqual([]);

    // And the one cell K-03 hangs on names the same token the declared row above claims, so the
    // list cannot quietly come to describe a screen that no longer exists.
    expect(
      rules.find((rule) => rule.selector === ".catalog__restricted")?.declarations.get("color"),
    ).toBe("var(--sc-color-attention-text)");

    // SC-2-03: the internal-rate state is not painted in the colour this screen spends on a
    // withheld value. Both are "not a number/name", and a reader who has learned that the amber
    // word means "you may not see this" must not meet the same amber word on a row where nothing
    // is being withheld at all.
    const internal = rules
      .find((rule) => rule.selector === ".catalog__internal")
      ?.declarations.get("color");
    expect(internal).toBe("var(--sc-color-text-muted)");
    expect(internal).not.toBe("var(--sc-color-attention-text)");
  });

  // --- SC-2-05, K-29: the same closure, for every stylesheet and for both halves of a pair --------
  //
  // The test above closes one hole for one file. The analyst's K-29 named three it leaves open:
  //
  //   1. It reads only `CatalogScreen.css`. A colour-only rule added to `app.css` or `AppShell.css`
  //      — `color: var(--sc-color-blue)` on the muted surface, 4.31:1 — joins no list and fails
  //      nothing.
  //   2. It only asks "which surface is under this colour". A rule that sets a *surface* and no
  //      colour has the mirror-image problem — which text sits on it — and was answered only by the
  //      hand-written `declaredPairs`, which nothing forces anybody to extend.
  //   3. A rule with `background: none` / `transparent` next to its colour was treated as having a
  //      background of its own, so it fell out of both checks. `.button.catalog__link-button` is one.
  //
  // Both maps below are keyed by file and selector, and both are compared against the stylesheets
  // in full: a new rule with no entry fails, and an entry for a rule that no longer exists fails.
  // The surfaces and foregrounds are still named by hand — the cascade is not reconstructed here —
  // but they are now named for every rule in every stylesheet, not for the ones someone remembered.

  /** `path/to/File.css selector` — the stylesheet's base name keeps the keys readable. */
  function ruleKey(rule: Rule): string {
    return `${rule.file.split("/").pop() ?? rule.file} ${rule.selector}`;
  }

  /** The rule's own background as a colour, or null when it has none (`none`, `transparent`, unset). */
  function ownBackground(rule: Rule): string | null {
    return resolveColour(
      rule.declarations.get("background-color") ?? rule.declarations.get("background") ?? "",
    );
  }

  function checkPairs(pairs: { where: string; foreground: string; background: string }[]) {
    return pairs
      .map((pair) => {
        const foreground = resolveColour(`var(${pair.foreground})`);
        const background = resolveColour(`var(${pair.background})`);
        if (foreground === null || background === null) {
          throw new Error(`Not a colour token: ${pair.foreground} / ${pair.background} (${pair.where})`);
        }
        return { where: pair.where, ratio: contrastRatio(foreground, background) };
      })
      .filter((pair) => pair.ratio < AA_NORMAL_TEXT);
  }

  it("meets WCAG AA for every colour any stylesheet sets without a background of its own, on every surface it is named to sit on", () => {
    // Every surface each rule's text actually lands on — a rule used on two surfaces is listed with
    // both, because the same token can pass on one and fail on the other.
    const surfacesUnder: Readonly<Record<string, readonly string[]>> = {
      // The catalogue (see the test above for the reasoning of each).
      "CatalogScreen.css .catalog__description": ["--sc-color-page"],
      "CatalogScreen.css .catalog__count": ["--sc-color-surface"],
      "CatalogScreen.css .catalog__count--truncated": ["--sc-color-surface"],
      "CatalogScreen.css .catalog__field-label": ["--sc-color-surface-muted"],
      "CatalogScreen.css .catalog__field-hint": ["--sc-color-surface-muted"],
      "CatalogScreen.css .catalog__field-stated": ["--sc-color-surface-muted"],
      "CatalogScreen.css .catalog__cell-period": ["--sc-color-surface"],
      "CatalogScreen.css .catalog__internal": ["--sc-color-surface"],
      // "Edit" on a white rate row, "Rename" on a dictionary panel's white card.
      "CatalogScreen.css .button.catalog__link-button": ["--sc-color-surface"],
      "CatalogScreen.css .catalog__entry": ["--sc-color-surface"],
      // The project list: a row is white, light steel under the pointer, pale blue when selected.
      "ProjectListScreen.css .project-list__client, .project-list__period": [
        "--sc-color-surface",
        "--sc-color-surface-muted",
        "--sc-color-status-active-bg",
      ],
      "ProjectListScreen.css .project-list__name-button": [
        "--sc-color-surface",
        "--sc-color-surface-muted",
        "--sc-color-status-active-bg",
      ],
      "ProjectListScreen.css .project-list__name-button:hover": [
        "--sc-color-surface-muted",
        "--sc-color-status-active-bg",
      ],
      'ProjectListScreen.css .project-list__name-button[aria-pressed="true"]': [
        "--sc-color-status-active-bg",
      ],
      "ProjectListScreen.css .project-list__details-meta, .project-list__details-empty": [
        "--sc-color-surface-muted",
      ],
      "ProjectListScreen.css .project-edit__error": ["--sc-color-surface-muted"],
      "ProjectListScreen.css .project-edit__field": ["--sc-color-surface-muted"],
      "ProjectListScreen.css .project-list__status-filter": ["--sc-color-surface"],
      "ProjectListScreen.css .scenario-card__gaps": ["--sc-color-surface"],
      "ProjectListScreen.css .scenario-card__metric": ["--sc-color-surface"],
      // The shell: the topbar and the rail are both white.
      "AppShell.css .app-shell__subtitle": ["--sc-color-surface"],
      "AppShell.css .app-shell__breadcrumb-list": ["--sc-color-surface"],
      'AppShell.css .app-shell__breadcrumb-list li[aria-current="page"]': ["--sc-color-surface"],
      "AppShell.css .app-shell__nav-item": ["--sc-color-surface"],
      "AppShell.css .app-shell__nav-group-label": ["--sc-color-surface"],
      // The base layer: a placeholder in a live input and in a not-yet-implemented one.
      "app.css .input::placeholder": ["--sc-color-surface", "--sc-color-disabled-surface"],
    };

    const colourOnly = rules.filter(
      (rule) =>
        rule.file !== TOKENS_PATH &&
        resolveColour(rule.declarations.get("color") ?? "") !== null &&
        ownBackground(rule) === null,
    );
    expect(colourOnly.length).toBeGreaterThan(10);
    expect(colourOnly.map(ruleKey).sort()).toEqual(Object.keys(surfacesUnder).sort());

    const failures = checkPairs(
      colourOnly.flatMap((rule) =>
        (surfacesUnder[ruleKey(rule)] ?? []).map((surface) => ({
          where: `${ruleKey(rule)} on ${surface}`,
          foreground: /^var\(\s*(--sc-[\w-]+)\s*\)$/.exec(rule.declarations.get("color") ?? "")?.[1] ?? "",
          background: surface,
        })),
      ),
    );
    expect(failures).toEqual([]);
  });

  it("meets WCAG AA for every text colour named to sit on a surface a stylesheet sets without a colour of its own", () => {
    // The mirror image. An empty list is a statement too — "nothing is written on this" — and it is
    // only allowed with the reason next to it.
    const foregroundsOn: Readonly<Record<string, readonly string[]>> = {
      // The catalogue form: its title and stated values in body text, labels and hints muted.
      "CatalogScreen.css .catalog__form": ["--sc-color-text", "--sc-color-text-muted"],
      // A rate row: names, the muted period and "Internal", the link-style "Edit".
      "CatalogScreen.css .catalog__row > td": [
        "--sc-color-text",
        "--sc-color-text-muted",
        "--sc-color-blue-deep",
      ],
      "ProjectListScreen.css .project-list__row > th, .project-list__row > td": [
        "--sc-color-text",
        "--sc-color-text-muted",
      ],
      "ProjectListScreen.css .project-list__row:hover > th, .project-list__row:hover > td": [
        "--sc-color-text",
        "--sc-color-text-muted",
        "--sc-color-blue-deep",
      ],
      "ProjectListScreen.css .project-list__row--selected > th, .project-list__row--selected > td": [
        "--sc-color-text",
        "--sc-color-text-muted",
        "--sc-color-blue-deep",
      ],
      "ProjectListScreen.css .project-list__row--selected:hover > th, .project-list__row--selected:hover > td":
        ["--sc-color-text", "--sc-color-text-muted", "--sc-color-blue-deep"],
      "ProjectListScreen.css .project-list__details": ["--sc-color-text", "--sc-color-text-muted"],
      "ProjectListScreen.css .scenario-card": [
        "--sc-color-text",
        "--sc-color-text-muted",
        "--sc-color-attention-text",
      ],
      // The orange accent bar on a scenario card: a 4px stripe, no text.
      "ProjectListScreen.css .scenario-card::before": [],
      // The page under every screen: headings and body text, and the catalogue's descriptions.
      "AppShell.css .app-shell": ["--sc-color-text", "--sc-color-text-muted"],
      "AppShell.css .app-shell__topbar": ["--sc-color-text", "--sc-color-text-muted"],
      // The three bars of the tool mark: drawn shapes, no text.
      "AppShell.css .app-shell__brandmark i": [],
      "AppShell.css .app-shell__rail": ["--sc-color-text-muted"],
      // A panel card: body text, muted counts, the truncated count, the link-style "Rename".
      "app.css .card": [
        "--sc-color-text",
        "--sc-color-text-muted",
        "--sc-color-attention-text",
        "--sc-color-blue-deep",
      ],
      // Hover states of buttons whose resting rule sets both halves: the label keeps its colour.
      "app.css .button--primary:hover": ["--sc-color-text"],
      "app.css .button--secondary:hover": ["--sc-color-white"],
      'app.css .button[aria-disabled="true"]:hover': ["--sc-color-text-disabled"],
    };

    const surfaceOnly = rules.filter(
      (rule) =>
        rule.file !== TOKENS_PATH &&
        ownBackground(rule) !== null &&
        resolveColour(rule.declarations.get("color") ?? "") === null,
    );
    expect(surfaceOnly.length).toBeGreaterThan(5);
    expect(surfaceOnly.map(ruleKey).sort()).toEqual(Object.keys(foregroundsOn).sort());

    const failures = checkPairs(
      surfaceOnly.flatMap((rule) =>
        (foregroundsOn[ruleKey(rule)] ?? []).map((foreground) => ({
          where: `${foreground} on ${ruleKey(rule)}`,
          foreground,
          background:
            /^var\(\s*(--sc-[\w-]+)\s*\)$/.exec(
              rule.declarations.get("background-color") ?? rule.declarations.get("background") ?? "",
            )?.[1] ?? "",
        })),
      ),
    );
    expect(failures).toEqual([]);
  });
});

describe("the catalogue stylesheet's reach (SC-2-05, K-26)", () => {
  it("scopes every selector in the catalogue stylesheet to a catalogue class, so it cannot restyle another screen", () => {
    // A stylesheet a component imports is global the moment it loads. A bare `.button--quiet` or
    // `th` rule in CatalogScreen.css would restyle the project list's row actions and the shell as
    // well — with nothing on those screens' own tests to notice, because they do not read this file.
    const catalogueRules = rules.filter((rule) => rule.file.endsWith("/CatalogScreen.css"));
    expect(catalogueRules.length).toBeGreaterThan(20);

    const unscoped = catalogueRules.flatMap((rule) =>
      rule.selector
        .split(",")
        .map((part) => part.trim())
        .filter((part) => !/\.catalog(?:__[\w-]+|--[\w-]+)?(?![\w-])/.test(part))
        .map((part) => `${part} (in "${rule.selector}")`),
    );
    expect(unscoped).toEqual([]);
  });
});

describe("project list layout rules", () => {
  function rulesMatching(selector: string): Rule[] {
    return rules.filter((rule) => rule.selector.includes(selector));
  }

  function declares(selector: string, property: string, value: RegExp): boolean {
    return rulesMatching(selector).some((rule) => value.test(rule.declarations.get(property) ?? ""));
  }

  it("tells every cell that carries a server string how to break it", () => {
    // Reviewer R-02: names are up to 200 characters and need no whitespace, so one unbroken word
    // is a legal answer from the API. Nothing truncates it in the component (see the screen's own
    // test); these are the rules that stop it from pushing the layout apart.
    expect(declares(".project-list__table th", "overflow-wrap", /anywhere|break-word/)).toBe(true);
    expect(declares(".project-list__details", "overflow-wrap", /anywhere|break-word/)).toBe(true);
    expect(declares(".scenario-card__title", "overflow-wrap", /anywhere|break-word/)).toBe(true);

    // The card must not clip what it cannot fit: a title cut off at the card edge disappears
    // without a scrollbar, a tooltip or an ellipsis to hint that anything is missing.
    const cardClips = rulesMatching(".scenario-card").some(
      (rule) => rule.selector === ".scenario-card" && rule.declarations.get("overflow") === "hidden",
    );
    expect(cardClips).toBe(false);
  });

  it("keeps the details panel in view while the list scrolls", () => {
    // Reviewer R-03: with no pagination, selecting a project near the bottom of a long list would
    // otherwise update a panel nobody can see.
    expect(declares(".project-list__details", "position", /sticky/)).toBe(true);
    expect(declares(".project-list__details", "top", /.+/)).toBe(true);
  });

  it("outlines the selected row on all four sides, not only where the cascade happens to allow", () => {
    // Reviewer R-04: the base rules for the outer cells carry two class-level parts, so a
    // `.project-list__row--selected > td` loses to them however late it is written, and the row
    // ends up blue top and bottom, grey down the sides. Checking the winner per side rather than
    // the presence of a declaration is the point — presence was never the problem.
    const affects: Record<string, RegExp> = {
      "border-top-color": /^border(-top)?(-color)?$/,
      "border-bottom-color": /^border(-bottom)?(-color)?$/,
      "border-left-color": /^border(-left)?(-color)?$/,
      "border-right-color": /^border(-right)?(-color)?$/,
    };

    for (const [side, pattern] of Object.entries(affects)) {
      const setters = rules.filter(
        (rule) =>
          rule.selector.includes(".project-list__row") &&
          [...rule.declarations.keys()].some((property) => pattern.test(property)),
      );
      const base = setters.filter((rule) => !rule.selector.includes("--selected")).at(-1);
      const selected = setters.filter((rule) => rule.selector.includes("--selected")).at(-1);

      expect(base, `nothing draws ${side} on an unselected row`).toBeDefined();
      expect(selected, `nothing draws ${side} on the selected row`).toBeDefined();
      if (base === undefined || selected === undefined) continue;

      // The selected rule must actually win: at least as specific, and later in the cascade.
      expect(
        isWeaker(specificity(selected.selector), specificity(base.selector)),
        `${side}: "${selected.selector}" is less specific than "${base.selector}" and loses`,
      ).toBe(false);
      expect(selected.order, `${side}: "${selected.selector}" comes before the rule it overrides`)
        .toBeGreaterThan(base.order);
    }
  });

  it("declares content-visibility: auto with a size estimate on the staffing plan's position and allocation rows (Reviewer R-02, gate 2 of SC-3-04)", () => {
    // NF-03's scale (200 positions × 36 months) can put ~7,200 allocation rows on one card. This is
    // the CSS declaration only — jsdom has no layout engine and does not act on `content-visibility`
    // at all, so no test here can observe a row's layout/paint actually being skipped; that half is
    // for QA to weigh (a mutation deleting these declarations passes every other suite unchanged).
    expect(declares(".staffing-plan__position", "content-visibility", /^auto$/)).toBe(true);
    expect(declares(".staffing-plan__position", "contain-intrinsic-size", /^auto\s/)).toBe(true);
    expect(declares(".staffing-plan__allocation", "content-visibility", /^auto$/)).toBe(true);
    expect(declares(".staffing-plan__allocation", "contain-intrinsic-size", /^auto\s/)).toBe(true);
  });

  it("declares content-visibility: auto with a size estimate on each working-calendar card (Reviewer R-02 of SC-3-06)", () => {
    // Mirrors the test above: a catalogue with many working calendars renders one full
    // `react-day-picker` month grid per card (`WeekPatternCalendar.tsx`), on screen and off. Again
    // the CSS declaration only — jsdom does not act on `content-visibility` at all, so no test here
    // can observe a card's layout/paint actually being skipped; that half is for QA to weigh (a
    // mutation deleting these declarations passes every other suite unchanged).
    expect(declares(".wc__calendar-card", "content-visibility", /^auto$/)).toBe(true);
    expect(declares(".wc__calendar-card", "contain-intrinsic-size", /^auto\s/)).toBe(true);
  });

  it("gives every not-yet-implemented control the same quietened appearance", () => {
    // Reviewer R-05: opacity over a full-strength brand colour still reads as a live call to
    // action. One rule, every variant, so "not yet" cannot look different in two places.
    const disabled = rules.filter((rule) => rule.selector.includes('[aria-disabled="true"]'));
    expect(disabled.length).toBeGreaterThan(0);

    const perVariant = disabled.filter((rule) => /--(primary|secondary|quiet)\[aria-disabled/.test(rule.selector));
    expect(perVariant.map((rule) => rule.selector)).toEqual([]);

    const shared = disabled.find((rule) => rule.selector.includes('.button[aria-disabled="true"]'));
    expect(shared?.declarations.get("background-color")).toBe("var(--sc-color-disabled-surface)");
    expect(shared?.declarations.get("color")).toBe("var(--sc-color-text-disabled)");
    // Not opacity: a translucent brand colour is still the brand colour.
    expect(disabled.some((rule) => rule.declarations.has("opacity"))).toBe(false);
  });
});
