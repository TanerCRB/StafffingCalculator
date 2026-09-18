// The single money-formatting entry point — see agents/developer-frontend.md,
// "Money/percentage values render through one shared formatting function". Never call
// toFixed()/manual string building on a money value at the call site.

export const NOT_APPLICABLE = "Not applicable";

/** Renders a ratio (margin, markup) that the backend may report as "n/a" for a zero
 * denominator. Mirrors backend/app/core/money.py's NOT_APPLICABLE sentinel — never render
 * that case as 0%, NaN%, or a blank cell. */
export function formatPercent(value: number | "n/a"): string {
  if (value === "n/a") return NOT_APPLICABLE;
  return `${value.toFixed(2)}%`;
}

export function formatMoney(value: number, currency: string): string {
  return new Intl.NumberFormat("en", { style: "currency", currency }).format(value);
}

// --- Fixed-point decimal strings (ADR-0002) ---------------------------------------------------
// Amounts and ratios cross the API boundary as fixed-point *strings*, never as JSON floats, so
// that the precision the storage layer keeps is not lost on the way to the screen. Turning such a
// string into a JS number to display it throws that guarantee away at the last step:
// Number("1.005") is 1.00499999999999989…, so toFixed(2) renders "1.00" where the backend's
// ROUND_HALF_UP (backend/app/core/money.py) gives "1.01". The functions below therefore round on
// the decimal representation itself — no Number(), no parseFloat(), at any stage.

const PERCENT_FRACTION_DIGITS = 2;
const DECIMAL_PATTERN = /^([+-]?)(\d*)(?:\.(\d*))?$/;

function incrementDigits(digits: string): string {
  const out = digits.split("");
  for (let index = out.length - 1; index >= 0; index -= 1) {
    if (out[index] === "9") {
      out[index] = "0";
    } else {
      out[index] = String(Number(out[index]) + 1);
      return out.join("");
    }
  }
  return `1${out.join("")}`;
}

function stripLeadingZeros(digits: string): string {
  const stripped = digits.replace(/^0+/, "");
  return stripped === "" ? "0" : stripped;
}

/**
 * Rounds a fixed-point decimal string to `fractionDigits` places, half away from zero — the same
 * rule as Python's `ROUND_HALF_UP` in `backend/app/core/money.py`.
 *
 * Throws on a value that is not a fixed-point decimal string: a malformed amount is a broken
 * contract, and a screen inventing a plausible number for it is exactly the kind of failure that
 * still renders correctly.
 */
export function roundDecimalString(value: string, fractionDigits: number): string {
  const match = DECIMAL_PATTERN.exec(value.trim());
  const integerPart = match?.[2] ?? "";
  const fractionPart = match?.[3] ?? "";
  if (match === null || (integerPart === "" && fractionPart === "")) {
    throw new Error(`Not a fixed-point decimal string: ${JSON.stringify(value)}`);
  }

  const kept = fractionPart.slice(0, fractionDigits).padEnd(fractionDigits, "0");
  const dropped = fractionPart.slice(fractionDigits);
  // Half-up: a first dropped digit of 5 or more rounds away from zero, an exact half included.
  const roundsAway = dropped.charAt(0) >= "5" && dropped.charAt(0) <= "9";

  let digits = `${integerPart === "" ? "0" : integerPart}${kept}`;
  if (roundsAway) {
    digits = incrementDigits(digits);
  }

  const cut = digits.length - fractionDigits;
  const roundedInteger = stripLeadingZeros(digits.slice(0, cut));
  const roundedFraction = digits.slice(cut);
  const isZero = /^0*$/.test(digits);
  const sign = match[1] === "-" && !isZero ? "-" : "";

  return fractionDigits > 0
    ? `${sign}${roundedInteger}.${roundedFraction}`
    : `${sign}${roundedInteger}`;
}

/**
 * Renders a percentage the API sent as a fixed-point decimal string ("12.500" → "12.50%"), or the
 * backend's zero-denominator sentinel as an explicit label. This is the entry point a component
 * uses for an API percentage; `formatPercent` above is for values that are already JS numbers and
 * must never be fed an API string.
 */
export function formatPercentString(value: string): string {
  if (value === "n/a") {
    return NOT_APPLICABLE;
  }
  return `${roundDecimalString(value, PERCENT_FRACTION_DIGITS)}%`;
}
