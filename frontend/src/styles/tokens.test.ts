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
