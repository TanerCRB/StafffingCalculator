import { roundDecimalString } from "./money";

/** Renders an FTE fraction (1 = one full-time equivalent) without converting through Number. */
export function formatFteString(value: string): string {
  return `${roundDecimalString(value, 2)} FTE`;
}
