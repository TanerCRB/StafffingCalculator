import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CatalogRate, DimensionEntry } from "../../api/contracts/catalog";
import { CatalogScreen } from "./CatalogScreen";

/**
 * SC-2-05, K-30 — the mockup's visual properties (`Wymagania/prototyp/screens/15-catalog*.png`),
 * checked on the elements that carry them.
 *
 * jsdom lays nothing out, so this cannot say where anything *ends up*; that is what the before/after
 * screenshots of K-31 are for, judged by a person at gate 2. What it can say — and what a check of
 * the stylesheet alone cannot — is that the declaration is on a rule that **matches the element**:
 * a `grid-template-columns` under a selector the screen never renders is a dead proof. So every
 * element below is found by role and name first, and only then are the rules that match it read.
 *
 * The rules are read as text from every stylesheet under `src/` and the winner per property is
 * picked by specificity, then source order — the order the files are globbed in, which is the order
 * the application loads them (every screen's stylesheet through `App`, then `app.css`; `main.tsx`).
 * Values are compared as the declarations spell them (`var(--sc-…)`), and resolved through
 * `tokens.css` only where two sizes have to be compared with each other.
 *
 * Which of these already held on `main` and which are the change is recorded in the PR, per
 * sub-point (the analyst's K-30: "zmiana" is red on the branch point, "już spełnione" green on both).
 */

// --- Reading the rules that match an element --------------------------------------------------

const stylesheets = import.meta.glob("../../**/*.css", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

interface Rule {
  readonly selector: string;
  readonly declarations: ReadonlyMap<string, string>;
  readonly order: number;
}

const rules: Rule[] = Object.values(stylesheets).flatMap((css) =>
  [...css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => {
    const declarations = new Map<string, string>();
    for (const part of match[2].split(";")) {
      const colon = part.indexOf(":");
      if (colon !== -1) {
        declarations.set(part.slice(0, colon).trim(), part.slice(colon + 1).trim());
      }
    }
    return { selector: match[1].trim().replace(/\s+/g, " "), declarations, order: 0 };
  }),
).map((rule, order) => ({ ...rule, order }));

function specificity(part: string): number {
  const ids = part.match(/#[\w-]+/g)?.length ?? 0;
  const classLevel =
    (part.match(/\.[\w-]+/g)?.length ?? 0) +
    (part.match(/\[[^\]]*\]/g)?.length ?? 0) +
    (part.match(/(?<!:):(?!:)[\w-]+/g)?.length ?? 0);
  const elements = part.match(/(^|[\s>+~])[a-z][\w-]*/g)?.length ?? 0;
  return ids * 10000 + classLevel * 100 + elements;
}

/** The strongest part of the rule's selector list that matches `element`, or -1. */
function matchStrength(element: Element, rule: Rule): number {
  let strongest = -1;
  for (const part of rule.selector.split(",").map((piece) => piece.trim())) {
    try {
      if (element.matches(part)) strongest = Math.max(strongest, specificity(part));
    } catch {
      // A pseudo-element (`::before`, `::placeholder`) is not something an element "matches".
    }
  }
  return strongest;
}

/** The declared value that wins for `property` on `element`, as written, or undefined. */
function declared(element: Element, property: string): string | undefined {
  let winner: { strength: number; order: number; value: string } | undefined;
  for (const rule of rules) {
    const value = rule.declarations.get(property);
    if (value === undefined) continue;
    const strength = matchStrength(element, rule);
    if (strength < 0) continue;
    if (
      winner === undefined ||
      strength > winner.strength ||
      (strength === winner.strength && rule.order > winner.order)
    ) {
      winner = { strength, order: rule.order, value };
    }
  }
  return winner?.value;
}

const tokens = new Map(
  [...Object.entries(stylesheets).find(([path]) => path.endsWith("/tokens.css"))![1].matchAll(
    /(--sc-[\w-]+)\s*:\s*([^;]+);/g,
  )].map((match) => [match[1], match[2].trim()]),
);

/** `var(--sc-font-size-lg)` → 18 (px, at the 16px root the rem tokens are written against). */
function fontSizePx(element: Element): number {
  const value = declared(element, "font-size") ?? "";
  const token = /^var\((--sc-[\w-]+)\)$/.exec(value)?.[1];
  const resolved = token === undefined ? value : (tokens.get(token) ?? "");
  const rem = /^([\d.]+)rem$/.exec(resolved);
  if (rem === null) throw new Error(`Not a rem font size: ${value} → ${resolved}`);
  return Number(rem[1]) * 16;
}

// --- Fixtures ----------------------------------------------------------------------------------

const MARKER = "2026-09-20T09:00:00+00:00";

const DICTIONARIES: Readonly<Record<string, DimensionEntry[]>> = {
  roles: [{ id: "a1", name: "Backend engineer", updated_at: MARKER }],
  seniorities: [{ id: "b1", name: "Senior", updated_at: MARKER }],
  locations: [{ id: "c1", name: "Poland", updated_at: MARKER }],
  "engagement-types": [{ id: "d1", name: "Contractor", updated_at: MARKER }],
  vendors: [{ id: "f1", name: "Acme Technology", updated_at: MARKER }],
};

const RATE: CatalogRate = {
  id: "e1",
  role_id: "a1",
  seniority_id: "b1",
  location_id: "c1",
  engagement_type_id: "d1",
  vendor_id: null,
  default_cost_rate: null,
  default_selling_rate: "260.00",
  currency: "PLN",
  unit: "hour",
  effective_from: "2026-09-01",
  effective_to: "2027-02-28",
  updated_at: MARKER,
};

async function renderReady() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const path = new URL(url).pathname;
      if (path === "/catalog/rates") {
        return { ok: true, status: 200, json: async () => ({ rates: [RATE, { ...RATE, id: "e2", default_cost_rate: "180.00" }], total: 2 }) };
      }
      const entries = DICTIONARIES[path.replace("/catalog/dimensions/", "")];
      return { ok: true, status: 200, json: async () => ({ entries }) };
    }),
  );
  render(<CatalogScreen />);
  await screen.findByRole("table");
}

const DICTIONARY_SECTIONS = ["Roles", "Seniorities", "Locations", "Engagement types", "Vendors"];
const ADD_BUTTONS = [
  "Add default rate",
  "Add role",
  "Add seniority",
  "Add location",
  "Add engagement type",
  "Add vendor",
];

/** Only a decorative icon: an `<svg>` hidden from assistive technology, and nothing else added. */
function expectDecorativeIcon(button: HTMLElement) {
  const icons = button.querySelectorAll("svg");
  expect(icons, `${button.textContent}: one icon`).toHaveLength(1);
  expect(icons[0]).toHaveAttribute("aria-hidden", "true");
  expect(icons[0].querySelector("title")).toBeNull();
}

describe("the catalogue screen's visual properties from the mockup (SC-2-05, K-30)", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("(a) puts the rates table full width above the dictionaries, and the dictionaries in two equal columns in the mockup's order", async () => {
    await renderReady();

    const rates = screen.getByRole("region", { name: "Default rates" });
    const sections = DICTIONARY_SECTIONS.map((name) => screen.getByRole("region", { name }));
    const grid = sections[0].parentElement as HTMLElement;

    // One container holds exactly the five panels, in the order that fills a two-column grid row by
    // row as Roles | Seniorities, Locations | Engagement types, Vendors.
    expect([...grid.children]).toEqual(sections);
    expect(declared(grid, "display")).toBe("grid");
    expect(declared(grid, "grid-template-columns")).toBe("repeat(2, minmax(0, 1fr))");

    // The rates come first and are not in the grid.
    expect(rates.compareDocumentPosition(grid) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(grid.contains(rates)).toBe(false);
  });

  it("(b) gives the rates table a light-steel header with muted column names, hairlines between rows, and right-aligned tabular amounts", async () => {
    await renderReady();

    const table = screen.getByRole("table");
    expect(declared(table, "border-collapse")).toBe("collapse");

    const header = screen.getByRole("columnheader", { name: "Role" });
    expect(declared(header, "background-color")).toBe("var(--sc-color-surface-muted)");
    expect(declared(header, "color")).toBe("var(--sc-color-text-muted)");

    // One hairline under the header and under every row but the last — the footnote below the
    // table draws the line under that one, so the box does not end in a double rule.
    const [head, ...rows] = within(table).getAllByRole("row");
    for (const cell of [...within(head).getAllByRole("columnheader"), ...rows.slice(0, -1).flatMap((row) => within(row).getAllByRole("cell"))]) {
      expect(declared(cell, "border-bottom")).toBe("1px solid var(--sc-color-border)");
    }
    expect(rows.length).toBeGreaterThan(1);

    const amount = within(table).getAllByRole("cell", { name: "260.00 PLN / hour" })[0];
    expect(declared(amount, "text-align")).toBe("right");
    expect(declared(amount, "font-variant-numeric")).toBe("tabular-nums");
    for (const name of ["Default selling rate", "Default cost rate"]) {
      expect(declared(screen.getByRole("columnheader", { name }), "text-align")).toBe("right");
    }
  });

  it("(c) shows a withheld cost rate as a label with the attention surface and text, still as the word", async () => {
    await renderReady();

    const label = within(screen.getByRole("table")).getByText("Restricted");
    expect(declared(label, "background-color")).toBe("var(--sc-color-attention-bg)");
    expect(declared(label, "color")).toBe("var(--sc-color-attention-text)");
    expect(label.textContent).toBe("Restricted");
  });

  it("(d) makes every Add control primary — the product's orange — with a decorative plus, and Edit and Rename link-style with a decorative pencil", async () => {
    await renderReady();

    for (const name of ADD_BUTTONS) {
      const button = screen.getByRole("button", { name });
      // G-8: the product's primary, not the mockup's blue.
      expect(declared(button, "background-color"), name).toBe("var(--sc-color-orange)");
      expect(declared(button, "color"), name).toBe("var(--sc-color-text)");
      expectDecorativeIcon(button);
    }

    const quiet = [
      ...screen.getAllByRole("button", { name: /^Edit the default rate for / }),
      ...screen.getAllByRole("button", { name: /^Rename / }),
    ];
    expect(quiet).toHaveLength(7);
    for (const button of quiet) {
      expect(declared(button, "background-color")).toBe("transparent");
      expect(declared(button, "border-color")).toBe("transparent");
      expect(declared(button, "color")).toBe("var(--sc-color-blue-deep)");
      expectDecorativeIcon(button);
    }
  });

  it("(e) sizes the screen title above the section headings above the panel headings, in Manrope only", async () => {
    await renderReady();

    const title = screen.getByRole("heading", { level: 2, name: "Roles & rates" });
    const section = screen.getByRole("heading", { name: "Default rates" });
    const dimensions = screen.getByRole("heading", { name: "Dimensions" });
    const panel = screen.getByRole("heading", { name: "Roles" });

    expect(fontSizePx(title)).toBeGreaterThan(fontSizePx(section));
    expect(fontSizePx(dimensions)).toBe(fontSizePx(section));
    expect(fontSizePx(section)).toBeGreaterThan(fontSizePx(panel));

    // No rule that reaches any element of this screen names a typeface other than the token.
    const families = new Set<string>();
    for (const element of screen.getByRole("region", { name: "Roles & rates" }).querySelectorAll("*")) {
      const family = declared(element, "font-family");
      if (family !== undefined) families.add(family);
    }
    expect([...families].filter((family) => family !== "var(--sc-font-family)")).toEqual([]);
  });

  it("(f) draws each panel with a 1px border and the mockup's radius, and a hairline between entries", async () => {
    await renderReady();

    for (const name of DICTIONARY_SECTIONS) {
      const panel = screen.getByRole("region", { name });
      expect(declared(panel, "border"), name).toBe("1px solid var(--sc-color-border)");
      expect(declared(panel, "border-radius"), name).toBe("var(--sc-radius-md)");
      for (const item of within(panel).getAllByRole("listitem")) {
        expect(declared(item, "border-top"), name).toBe("1px solid var(--sc-color-border)");
      }
    }
  });

  it("(g) opens the dictionary form on a muted, bordered, rounded surface with Cancel before Save at the right", async () => {
    await renderReady();
    fireEvent.click(screen.getByRole("button", { name: "Add role" }));

    const form = screen.getByRole("form", { name: "Add an entry to the roles dictionary" });
    expect(declared(form, "background-color")).toBe("var(--sc-color-surface-muted)");
    expect(declared(form, "border")).toBe("1px solid var(--sc-color-border)");
    expect(declared(form, "border-radius")).toBe("var(--sc-radius-md)");

    const cancel = within(form).getByRole("button", { name: "Cancel" });
    const save = within(form).getByRole("button", { name: "Save new entry" });
    // G-9: the DOM order is the visual order — no `row-reverse` making the two disagree.
    expect(cancel.compareDocumentPosition(save) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const actions = cancel.parentElement as HTMLElement;
    expect(actions).toBe(save.parentElement);
    expect(declared(actions, "justify-content")).toBe("flex-end");
    expect(declared(actions, "flex-direction")).toBeUndefined();
  });
});
