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

/**
 * How many decimal places a displayed amount keeps, for every currency without exception.
 *
 * ADR-0002, addendum 2026-09-19 (SC-2-02): the mirror of today's `round_money`, which quantises to
 * `TWO_PLACES` and takes no currency argument. The decision's "precision depends on the currency"
 * clause is implemented in neither layer, and the frontend must not introduce a currency rule the
 * backend does not know — that would be a second rounding point for the same amount, surfacing as a
 * difference between this screen and a future export rather than as an error. Closing condition:
 * the first catalogue currency whose minor unit is not 2 places (JPY = 0, BHD = 3), which changes
 * `round_money` and this constant together.
 */
const MONEY_FRACTION_DIGITS = 2;
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
 * What this module says about a value it refuses to round. It names the rule that was broken and
 * nothing about the value that broke it — see `roundDecimalString` for why the offending value is
 * deliberately absent from it (SC-1-09, Reviewer R-01).
 */
export const NOT_A_DECIMAL_STRING = "Not a fixed-point decimal string";

/** The one reading of the decimal grammar: the match when `value` is a fixed-point decimal string
 * (at least one digit, on either side of the point), `null` otherwise. */
function parseDecimalString(value: string): RegExpExecArray | null {
  const match = DECIMAL_PATTERN.exec(value.trim());
  if (match === null || ((match[2] ?? "") === "" && (match[3] ?? "") === "")) {
    return null;
  }
  return match;
}

/**
 * Whether `value` is a string `roundDecimalString` accepts — exactly the strings it does not throw
 * on, because both read the grammar through `parseDecimalString` (ADR-0002: one place for it).
 *
 * This is the predicate a shape check at the network boundary uses when a payload that fails it must
 * end in the screen's named read failure rather than in the render boundary (ADR-0010, point 2;
 * SC-4-06, Reviewer R-01). It does not soften the formatter (ADR-0010, point 6): whatever reaches
 * `roundDecimalString` without having been checked still throws.
 */
export function isDecimalString(value: unknown): value is string {
  return typeof value === "string" && parseDecimalString(value) !== null;
}

/**
 * Rounds a fixed-point decimal string to `fractionDigits` places, half away from zero — the same
 * rule as Python's `ROUND_HALF_UP` in `backend/app/core/money.py`.
 *
 * Throws on a value that is not a fixed-point decimal string: a malformed amount is a broken
 * contract, and a screen inventing a plausible number for it is exactly the kind of failure that
 * still renders correctly.
 *
 * **The message carries the rule, never the value** (NF-11, ADR-0010 point 4; Reviewer R-01,
 * SC-1-09). The input here can be a `default_cost_rate` — a personnel cost, the one class of number
 * ADR-0005 keeps from people who may not see it — and this exception is thrown mid render, where
 * React's error-boundary machinery takes it over. React's *production* bundle calls
 * `console.error(error)` for every error a class boundary catches (`logCapturedError` in
 * `react-dom.production.min.js`), unconditionally and before the boundary component gets a say, so
 * no amount of care inside `ScreenErrorBoundary` can keep an interpolated value off the console.
 * The message used to read `Not a fixed-point decimal string: "…"` with the value inside it; the
 * only fix that actually holds is for there to be nothing in the error worth leaking, whoever ends
 * up logging it — React today, a devtools pane, a telemetry hook nobody has written yet.
 *
 * What is lost is a debugging convenience, and it is lost on purpose: the value is in the response
 * body, which is in the network tab, which is behind the same authorisation the value itself is.
 */
export function roundDecimalString(value: string, fractionDigits: number): string {
  const match = parseDecimalString(value);
  if (match === null) {
    throw new Error(NOT_A_DECIMAL_STRING);
  }
  const integerPart = match[2] ?? "";
  const fractionPart = match[3] ?? "";

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

/**
 * Renders an amount the API sent as a fixed-point decimal string, in the currency the API sent
 * with it ("100.0050", "EUR" → "100.01 EUR").
 *
 * This is the entry point for an amount that came from the API; `formatMoney` above takes a JS
 * number and is **not** admissible for one (ADR-0002, addendum 2026-09-19, point 3) — `Number()`
 * would turn "100.005" into 100.00499999999999… and display "100.00" where the backend's
 * ROUND_HALF_UP gives 100.01.
 *
 * The currency is appended as its code rather than a symbol: the catalogue carries ISO-4217 codes,
 * amounts in several currencies sit in one table, and a symbol shared by more than one currency
 * ($, kr) would make two different rows read identically. Rounding here is a lossy projection for
 * display only — never a value fed back as input (ADR-0008, addendum 2026-09-19, points 1-2).
 */
export function formatMoneyString(value: string, currency: string): string {
  return `${roundDecimalString(value, MONEY_FRACTION_DIGITS)} ${currency}`;
}

/**
 * Renders a rate: an amount per unit of time, with both the currency and the unit taken from the
 * response ("100.0050", "EUR", "hour" → "100.01 EUR / hour").
 *
 * The unit is joined here rather than at the call site for the same reason the amount is formatted
 * here: a cell that assembles "… / hour" of its own would keep saying "hour" when F-07 adds daily
 * and monthly rates, and nothing would look broken.
 */
export function formatRatePerUnit(value: string, currency: string, unit: string): string {
  return `${formatMoneyString(value, currency)} / ${unit}`;
}

/**
 * How many decimal places an Outcome-based rule's own amount keeps on screen — the scale of its
 * column, `NUMERIC(14,4)` (`OUTCOME_AMOUNT_SCALE`, backend `app/models/commercial_terms.py`).
 *
 * ADR-0003, addendum 2026-09-25 SC-4-07, point 9 (Q5 = A): a rule parameter is shown **as stored**,
 * not as money rounded to two places — "12.3456" stays "12.3456", and a PM checking what was saved
 * sees exactly that. A computed revenue is still `formatMoneyString`'s (two places).
 */
const RULE_AMOUNT_FRACTION_DIGITS = 4;

/**
 * Renders a commercial rule's stored amount (fixed fee, bonus, unit rate, bounds) with the scale of
 * its column, in the rule's own currency ("12.3456", "PLN" → "12.3456 PLN"). Rounds on the decimal
 * string, never through `Number()` — the same guarantee as `formatMoneyString`.
 */
export function formatRuleAmountString(value: string, currency: string): string {
  return `${roundDecimalString(value, RULE_AMOUNT_FRACTION_DIGITS)} ${currency}`;
}

/**
 * Renders a percentage a rule stores (an outcome category's probability, `NUMERIC(5,2)`) exactly as
 * the server wrote it, with the percent sign ("70.00" → "70.00%"). No rounding and no `Number()`:
 * the value is what the user entered and the server checked to sum to 100.00 (SC-4-03, point 4).
 * Throws on a value that is not a fixed-point decimal string, like every formatter here.
 */
export function formatStoredPercentString(value: string): string {
  if (!isDecimalString(value)) {
    throw new Error(NOT_A_DECIMAL_STRING);
  }
  return `${value}%`;
}
