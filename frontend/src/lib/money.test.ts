import { describe, expect, it } from "vitest";

import moneySource from "./money.ts?raw";
import {
  NOT_APPLICABLE,
  NOT_A_DECIMAL_STRING,
  formatMoneyString,
  formatPercent,
  formatPercentString,
  formatRatePerUnit,
  roundDecimalString,
} from "./money";

describe("formatPercent", () => {
  it("renders a numeric ratio with two decimals and a percent sign", () => {
    expect(formatPercent(30)).toBe("30.00%");
  });

  it("renders the backend's n/a sentinel as an explicit label, not 0% or NaN%", () => {
    expect(formatPercent("n/a")).toBe(NOT_APPLICABLE);
  });
});

describe("formatPercentString", () => {
  it("rounds a decimal string half up, the way the backend does, where a JS float rounds down", () => {
    // Number("1.005") is 1.00499999999999989…, so a float path renders "1.00" here. The backend's
    // ROUND_HALF_UP (backend/app/core/money.py) gives 1.01, and so must the screen (ADR-0002).
    expect(formatPercentString("1.005")).toBe("1.01%");
    expect(formatPercentString("2.675")).toBe("2.68%");
  });

  it("leaves a value that needs no rounding exactly as the backend sent it", () => {
    // Contrast: the case that converts losslessly through a float, which is why it could never
    // have caught the bug above on its own.
    expect(formatPercentString("12.500")).toBe("12.50%");
    expect(formatPercentString("0.00")).toBe("0.00%");
    expect(formatPercentString("100")).toBe("100.00%");
  });

  it("rounds a negative value away from zero, like the backend's ROUND_HALF_UP", () => {
    expect(formatPercentString("-1.005")).toBe("-1.01%");
    expect(formatPercentString("-0.001")).toBe("0.00%");
  });

  it("renders the n/a sentinel as an explicit label, not 0%", () => {
    expect(formatPercentString("n/a")).toBe(NOT_APPLICABLE);
  });

  it("refuses a value that is not a fixed-point decimal string instead of inventing a number", () => {
    expect(() => formatPercentString("twelve")).toThrow(/fixed-point decimal string/);
    expect(() => formatPercentString("")).toThrow(/fixed-point decimal string/);
  });
});

describe("formatMoneyString", () => {
  it("rounds an amount half up, the way the backend's round_money does, where a JS float rounds down", () => {
    // Number("100.005") is 100.00499999999999…, so a float path renders "100.00" here while
    // backend/app/core/money.py gives 100.01 (ADR-0002).
    expect(formatMoneyString("100.005", "EUR")).toBe("100.01 EUR");
    expect(formatMoneyString("-0.005", "EUR")).toBe("-0.01 EUR");
  });

  it("keeps two places for every currency, because round_money takes no currency argument", () => {
    // ADR-0002, addendum 2026-09-19: the "precision depends on the currency" clause is implemented
    // in neither layer. A frontend that quantised JPY to zero places would disagree with every
    // amount the backend computes, and nothing would throw. Closing condition is named there.
    expect(formatMoneyString("1234.5", "JPY")).toBe("1234.50 JPY");
    expect(formatMoneyString("1.005", "BHD")).toBe("1.01 BHD");
  });

  it("keeps digits a JS number could not hold, and pads a value shorter than the scale", () => {
    expect(formatMoneyString("9007199254740993.004", "PLN")).toBe("9007199254740993.00 PLN");
    expect(formatMoneyString("7", "USD")).toBe("7.00 USD");
  });

  it("refuses a value that is not a fixed-point decimal string instead of inventing an amount", () => {
    expect(() => formatMoneyString("a lot", "EUR")).toThrow(/fixed-point decimal string/);
  });
});

describe("formatRatePerUnit", () => {
  it("takes the currency and the unit from the response, joining them in one place", () => {
    // A call site that assembled "… / hour" of its own would keep saying "hour" when F-07 adds
    // daily and monthly rates, and nothing would look broken.
    expect(formatRatePerUnit("100.005", "EUR", "hour")).toBe("100.01 EUR / hour");
    expect(formatRatePerUnit("100.005", "PLN", "day")).toBe("100.01 PLN / day");
  });
});

describe("the formatters are not softened (SC-1-09, K-06; ADR-0002; ADR-0010, point 6)", () => {
  /** The module's own text, comments removed — prose about throwing is not code that catches. */
  const code = moneySource.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");

  it("catches nothing anywhere, so a malformed value leaves this module as an exception", () => {
    // The mutation this exists against is a one-liner, and a plausible one: wrap the throw in a
    // `try`/`catch` and return "0.00%" so the screen "does not break". It would trade a stated
    // failure for a number nobody sent — the error that renders correctly, on the one kind of value
    // this application exists to get right. The boundary added in SC-1-09 does not license it: a
    // backstop for an exception is not a reason to stop raising one (ADR-0010, point 6).
    expect(code).not.toMatch(/\bcatch\b/);
    expect(code).not.toMatch(/\btry\s*\{/);
    // The detector is not vacuous: it finds the softening it is looking for.
    const softened = 'function f(v) { try { return g(v); } catch { return "0.00%"; } }';
    expect(softened).toMatch(/\bcatch\b/);
    expect(softened).toMatch(/\btry\s*\{/);
    // And the module was actually read, rather than being an empty string the two checks above
    // would pass against for ever.
    expect(code).toContain("export function roundDecimalString");
  });

  it("throws out of every public entry point a screen calls, and returns no substitute value", () => {
    for (const malformed of ["abc", "12,50", "1.2.3", " ", "12%"]) {
      expect(() => roundDecimalString(malformed, 2)).toThrow(/fixed-point decimal string/);
      expect(() => formatPercentString(malformed)).toThrow(/fixed-point decimal string/);
      expect(() => formatMoneyString(malformed, "EUR")).toThrow(/fixed-point decimal string/);
    }
  });

  it("still formats a value the contract did carry validly", () => {
    // The contrast K-06 asks for, next to the refusals: strictness here is not the module refusing
    // to work. "1.005" is the value a float path gets wrong, so this is also the assertion that
    // would die if the strictness were "achieved" by throwing on everything.
    expect(formatPercentString("1.005")).toBe("1.01%");
    expect(formatMoneyString("100.005", "EUR")).toBe("100.01 EUR");
    expect(roundDecimalString("1.005", 2)).toBe("1.01");
  });
});

describe("the refusal names the rule and not the value (Reviewer R-01, SC-1-09; NF-11)", () => {
  /**
   * Shaped like the thing that must not leak: a personnel cost (`default_cost_rate`, ADR-0005) that
   * arrived malformed. The amount and the marker are separable on purpose — a message carrying only
   * the digits leaks exactly as much as one carrying the whole string.
   */
  const MALFORMED_COST = "1234.56-NF11-SENTINEL";
  const AMOUNT_IN_IT = "1234.56";

  /** Every public entry point a screen can reach the formatter through. */
  const CALLS: readonly { readonly what: string; readonly call: () => string }[] = [
    { what: "roundDecimalString", call: () => roundDecimalString(MALFORMED_COST, 2) },
    { what: "formatMoneyString", call: () => formatMoneyString(MALFORMED_COST, "EUR") },
    { what: "formatPercentString", call: () => formatPercentString(MALFORMED_COST) },
    { what: "formatRatePerUnit", call: () => formatRatePerUnit(MALFORMED_COST, "EUR", "hour") },
  ];

  function thrownBy(call: () => string): Error {
    try {
      call();
    } catch (error) {
      return error as Error;
    }
    throw new Error("the formatter returned instead of throwing");
  }

  it("still refuses a malformed amount — the value is gone from the message, not the throw", () => {
    // The positive control, and the reason this pair is two assertions rather than one: a formatter
    // that stopped throwing would satisfy "the message does not contain the amount" perfectly, by
    // rendering a number nobody sent (ADR-0002, ADR-0010 point 6, K-06).
    for (const { what, call } of CALLS) {
      expect(call, what).toThrow(Error);
      expect(thrownBy(call).message, what).toBe(NOT_A_DECIMAL_STRING);
    }
  });

  it("puts nothing from the malformed value into the error React's production build logs", () => {
    // React's *production* bundle calls `console.error(error)` for every error a class boundary
    // catches — `logCapturedError` in `react-dom.production.min.js`, wired unconditionally for any
    // component with `getDerivedStateFromError`, and reached before `ScreenErrorBoundary` gets a
    // say. The boundary discards the error it is handed, and that is still not enough: what React
    // logs is the `Error` object itself. So the guarantee has to hold in the object, not in its
    // handling — which is why this assertion lives here and not in the boundary's suite.
    //
    // The mutation it exists against is the code that shipped: ``throw new Error(`Not a fixed-point
    // decimal string: ${JSON.stringify(value)}`)``.
    for (const { what, call } of CALLS) {
      const error = thrownBy(call);
      expect(error.message, what).not.toContain(MALFORMED_COST);
      expect(error.message, what).not.toContain(AMOUNT_IN_IT);
      expect(error.stack ?? "", what).not.toContain(MALFORMED_COST);
      expect(error.stack ?? "", what).not.toContain(AMOUNT_IN_IT);
    }

    // The detector is not vacuous: the message it *used* to produce is caught by the same checks.
    const asItWas = new Error(`${NOT_A_DECIMAL_STRING}: ${JSON.stringify(MALFORMED_COST)}`);
    expect(asItWas.message).toContain(MALFORMED_COST);
    expect(asItWas.message).toContain(AMOUNT_IN_IT);
  });

  it("builds no throw message out of a value at all, anywhere in the module", () => {
    // Source-level, because the runtime checks above can only see the throw sites a test happens to
    // reach. A second refusal added later — a currency this module declines to format, say — is the
    // obvious place for the interpolation to come back.
    const code = moneySource.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
    expect(code).not.toMatch(/throw new Error\(`/);
    expect(code).not.toMatch(/throw new Error\([^)]*\$\{/);
    // Read, not empty — and the one throw it does have is the named constant.
    expect(code).toContain(`throw new Error(NOT_A_DECIMAL_STRING)`);
    // The detector finds the interpolation it is looking for.
    expect("throw new Error(`Not a decimal: ${value}`);").toMatch(/throw new Error\(`/);
  });
});

describe("roundDecimalString", () => {
  it("carries across every digit when rounding up", () => {
    expect(roundDecimalString("9.999", 2)).toBe("10.00");
    expect(roundDecimalString("0.999", 0)).toBe("1");
    expect(roundDecimalString("999999999999999999.995", 2)).toBe("1000000000000000000.00");
  });

  it("keeps digits a JS number could not hold", () => {
    // 9007199254740993 is not representable as a JS number; a float path would corrupt it.
    expect(roundDecimalString("9007199254740993.004", 2)).toBe("9007199254740993.00");
  });
});
