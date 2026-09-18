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
