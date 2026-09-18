import { describe, expect, it } from "vitest";

import { NOT_APPLICABLE, formatPercent } from "./money";

describe("formatPercent", () => {
  it("renders a numeric ratio with two decimals and a percent sign", () => {
    expect(formatPercent(30)).toBe("30.00%");
  });

  it("renders the backend's n/a sentinel as an explicit label, not 0% or NaN%", () => {
    expect(formatPercent("n/a")).toBe(NOT_APPLICABLE);
  });
});
