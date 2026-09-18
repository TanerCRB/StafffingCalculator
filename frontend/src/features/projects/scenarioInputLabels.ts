// `missing_inputs` arrives as backend attribute names (`working_calendar`, …) — see
// backend/app/domain/scenario_readiness.py, REQUIRED_SCENARIO_INPUTS. The screen must name the
// gaps to a project manager (F-01), so the names are translated here, in one place.
//
// Note: this is NOT a translation catalog — no locale mechanism exists in this repository yet
// (see frontend/README.md). It is a single-source field-name → English-label map, so a component
// never builds one of its own.

export const MISSING_INPUT_LABELS: Readonly<Record<string, string>> = {
  start_date: "Start date",
  end_date: "End date",
  working_calendar: "Working calendar",
  full_time_hours_per_week: "Full-time hours per week",
  currency: "Currency",
  target_margin_percent: "Target margin",
};

/** A readable label for one missing input. An input the backend adds later still renders
 * readably (underscores removed) rather than disappearing from the list — a gap the user is not
 * told about is worse than an inelegant label. */
export function missingInputLabel(name: string): string {
  const known = MISSING_INPUT_LABELS[name];
  if (known !== undefined) {
    return known;
  }
  const spaced = name.replace(/_/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
