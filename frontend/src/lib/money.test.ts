import { describe, expect, it } from "vitest";

import { NOT_APPLICABLE, formatPercent, formatPercentString, roundDecimalString } from "./money";

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
