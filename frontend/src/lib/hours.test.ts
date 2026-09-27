import { describe, expect, it } from "vitest";

import hoursSource from "./hours.ts?raw";
import { NOT_A_DECIMAL_STRING } from "./money";
import { formatHoursString } from "./hours";

/**
 * SC-3-04 (Issue #135), K-01 — hours render only through this shared formatter.
 *
 * The mutation this file is written against: a call site swapping `formatHoursString` for
 * `Number(value).toFixed(2)`. `Number("7.005").toFixed(2)` is `"7.00"` (float imprecision), where
 * the backend's `ROUND_HALF_UP` (`NUMERIC(10,2)`) gives `"7.01"` — the same defect `money.test.ts`
 * proves against for money and percentages, proved here for hours (ADR-0002, addendum 2026-09-26).
 */

describe("formatHoursString", () => {
  it("rounds ROUND_HALF_UP on the .005 boundary, matching the backend, where a JS float rounds down", () => {
    expect(formatHoursString("7.005")).toBe("7.01 h");
    expect(formatHoursString("2.675")).toBe("2.68 h");
  });

  it("leaves a value that needs no rounding exactly as the backend sent it", () => {
    expect(formatHoursString("12.500")).toBe("12.50 h");
    expect(formatHoursString("0.00")).toBe("0.00 h");
    expect(formatHoursString("160")).toBe("160.00 h");
  });

  it("refuses a value that is not a fixed-point decimal string instead of inventing a number", () => {
    expect(() => formatHoursString("twelve")).toThrow(/fixed-point decimal string/);
    expect(() => formatHoursString("")).toThrow(/fixed-point decimal string/);
  });

  it("refuses the sentinel n/a rather than rendering it as a number or as text of its own", () => {
    // `formatHoursString` takes no sentinel branch (see its own docstring): a caller must branch on
    // the field's named state first and never call this function on "n/a" at all. A component that
    // forgot to branch and passed the sentinel straight through must not get a silently rendered
    // value back.
    expect(() => formatHoursString("n/a")).toThrow(NOT_A_DECIMAL_STRING);
  });
});

describe("hours.ts is not softened, and calls no second implementation of the decimal grammar", () => {
  const code = hoursSource.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");

  it("catches nothing, so a malformed value leaves this module as an exception", () => {
    expect(code).not.toMatch(/\bcatch\b/);
    expect(code).not.toMatch(/\btry\s*\{/);
    // The detector is not vacuous.
    const softened = 'function f(v) { try { return g(v); } catch { return "0.00 h"; } }';
    expect(softened).toMatch(/\bcatch\b/);
  });

  it("rounds only through the shared money.ts primitive — no Number()/toFixed() of its own", () => {
    expect(code).not.toMatch(/\bNumber\(/);
    expect(code).not.toMatch(/\.toFixed\(/);
    expect(code).toContain('from "./money"');
    expect(code).toContain("roundDecimalString(value, HOURS_FRACTION_DIGITS)");
    // The detector is not vacuous.
    const notSoftened = 'export function f(v) { return Number(v).toFixed(2); }';
    expect(notSoftened).toMatch(/\bNumber\(/);
    expect(notSoftened).toMatch(/\.toFixed\(/);
  });
});
