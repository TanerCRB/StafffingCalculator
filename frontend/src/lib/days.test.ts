import { describe, expect, it } from "vitest";

import daysSource from "./days.ts?raw";
import { NOT_A_DECIMAL_STRING } from "./money";
import { formatBudgetDaysString } from "./days";

/**
 * SC-3-06 (Issue #140) — `budget_days` renders only through this shared formatter, the fourth named
 * class of decimal value after money, percentage and hours (ADR-0002, addendum 2026-09-27).
 *
 * The mutation this file is written against, mirroring `hours.test.ts`: a call site swapping
 * `formatBudgetDaysString` for `Number(value).toFixed(2)` (float imprecision), or for
 * `formatHoursString` outright (the wrong unit suffix, "20.00 h" for a day count).
 */

describe("formatBudgetDaysString", () => {
  it("rounds ROUND_HALF_UP on the .005 boundary, matching the backend, where a JS float rounds down", () => {
    expect(formatBudgetDaysString("20.005")).toBe("20.01 days");
    expect(formatBudgetDaysString("2.675")).toBe("2.68 days");
  });

  it("leaves a value that needs no rounding exactly as the backend sent it, with the days suffix", () => {
    expect(formatBudgetDaysString("20.00")).toBe("20.00 days");
    expect(formatBudgetDaysString("0.00")).toBe("0.00 days");
    expect(formatBudgetDaysString("5")).toBe("5.00 days");
  });

  it("refuses a value that is not a fixed-point decimal string instead of inventing a number", () => {
    expect(() => formatBudgetDaysString("twenty")).toThrow(/fixed-point decimal string/);
    expect(() => formatBudgetDaysString("")).toThrow(/fixed-point decimal string/);
  });

  it("refuses the sentinel n/a rather than rendering it as a number or as text of its own", () => {
    expect(() => formatBudgetDaysString("n/a")).toThrow(NOT_A_DECIMAL_STRING);
  });
});

describe("days.ts is not softened, and calls no second implementation of the decimal grammar, and never the hours suffix", () => {
  const code = daysSource.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");

  it("catches nothing, so a malformed value leaves this module as an exception", () => {
    expect(code).not.toMatch(/\bcatch\b/);
    expect(code).not.toMatch(/\btry\s*\{/);
  });

  it("rounds only through the shared money.ts primitive — no Number()/toFixed() of its own", () => {
    expect(code).not.toMatch(/\bNumber\(/);
    expect(code).not.toMatch(/\.toFixed\(/);
    expect(code).toContain('from "./money"');
    expect(code).toContain("roundDecimalString(value, BUDGET_DAYS_FRACTION_DIGITS)");
  });

  it("never suffixes its output with the hours unit, and does not import hours.ts", () => {
    // The mutation this criterion (K-0x of Issue #140, ADR-0002 addendum 2026-09-27) is written
    // against: reusing `formatHoursString` for `budget_days` prints "20.00 h" for a day count.
    expect(code).not.toMatch(/from ["']\.\/hours["']/);
    expect(code).not.toContain('" h"');
    expect(code).not.toMatch(/`\$\{[^}]*\}\s*h`/);
  });
});
