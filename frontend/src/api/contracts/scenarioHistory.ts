import { isDecimalString } from "../../lib/money";

/** Read contract for `GET /projects/{project_id}/scenarios/{scenario_id}/history` (SC-8-02).
 * Mirrors `backend/app/api/schemas/scenario_history.py`. This is a per-scenario record: no
 * relationship to another draft or approved scenario is implied by this response. */

export interface ScenarioHistoryInputs {
  start_date: string | null;
  end_date: string | null;
  working_calendar: string | null;
  full_time_hours_per_week: string | null;
  currency: string | null;
  target_margin_percent: string | null;
  overload_threshold_percent: string | null;
}

export interface ScenarioApprovalEvent {
  action_type: "scenario_approved";
  performed_by: string;
  performed_by_verified: false;
  created_at: string;
}

export interface HistoryOrganizationDefaults {
  target_margin_percent: string | null;
  overload_threshold_percent: string | null;
}

export interface HistoryWorkingCalendar {
  source_calendar_id: string;
  source_location_id: string;
  name: string;
  standard_hours_per_day: string;
  week_pattern: string;
}

export interface HistoryWorkingCalendarDay {
  source_calendar_id: string;
  day: string;
  source: string;
  name: string | null;
  country_code: string | null;
  year: number | null;
  kind: string;
}

export interface HistoryAbsenceType {
  source_absence_type_id: string;
  name: string;
  generates_cost: boolean;
  generates_revenue: boolean;
  is_statutory_leave: boolean;
}

export interface HistoryAbsenceBudget {
  source_calendar_id: string;
  source_engagement_type_id: string;
  budget_days: string;
  unit: string;
  effective_from: string;
  effective_to: string;
}

export interface HistoryCatalogRate {
  source_rate_id: string;
  source_role_id: string;
  source_seniority_id: string;
  source_location_id: string;
  source_engagement_type_id: string;
  source_vendor_id: string | null;
  default_selling_rate: string;
  currency: string;
  unit: string;
  effective_from: string;
  effective_to: string | null;
  surcharge_percent: string;
  includes_surcharge: boolean;
  /** Both fields are omitted unless both personnel-cost gates pass (ADR-0005). */
  cost_rate_unit?: string;
  default_cost_rate?: string;
}

export interface HistoryExchangeRate {
  source_rate_id: string;
  source_scope: string;
  source_project_id: string | null;
  source_scenario_id: string | null;
  source_currency: string;
  target_currency: string;
  effective_from: string;
  effective_to: string | null;
  rate: string;
  source: string;
}

export interface HistorySnapshotPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApprovedScenarioSnapshot {
  organization_defaults: HistoryOrganizationDefaults | null;
  working_calendars: HistorySnapshotPage<HistoryWorkingCalendar>;
  working_calendar_days: HistorySnapshotPage<HistoryWorkingCalendarDay>;
  absence_types: HistorySnapshotPage<HistoryAbsenceType>;
  absence_budgets: HistorySnapshotPage<HistoryAbsenceBudget>;
  catalog_rates: HistorySnapshotPage<HistoryCatalogRate>;
  exchange_rates: HistorySnapshotPage<HistoryExchangeRate>;
}

export type SnapshotCollectionKey = Exclude<keyof ApprovedScenarioSnapshot, "organization_defaults">;

export interface ScenarioHistory {
  scenario_id: string;
  name: string;
  status: "Draft" | "Approved";
  updated_at: string;
  inputs: ScenarioHistoryInputs;
  approval_event: ScenarioApprovalEvent | null;
  approved_snapshot: ApprovedScenarioSnapshot | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return typeof value === "string" || value === null;
}

function hasStrings(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return keys.every((key) => typeof value[key] === "string");
}

function hasNullableStrings(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return keys.every((key) => isNullableString(value[key]));
}

function isInputs(value: unknown): value is ScenarioHistoryInputs {
  return isRecord(value) &&
    isNullableString(value.start_date) && isNullableString(value.end_date) &&
    isNullableString(value.working_calendar) && nullableDecimal(value.full_time_hours_per_week) &&
    isNullableString(value.currency) && nullableDecimal(value.target_margin_percent) &&
    nullableDecimal(value.overload_threshold_percent);
}

function nullableDecimal(value: unknown): value is string | null {
  return value === null || isDecimalString(value);
}

function isApprovalEvent(value: unknown): value is ScenarioApprovalEvent {
  return isRecord(value) && value.action_type === "scenario_approved" &&
    typeof value.performed_by === "string" && value.performed_by_verified === false &&
    typeof value.created_at === "string";
}

function isOrganizationDefaults(value: unknown): value is HistoryOrganizationDefaults {
  return isRecord(value) && nullableDecimal(value.target_margin_percent) &&
    nullableDecimal(value.overload_threshold_percent);
}

function isSnapshotPage(value: unknown, isItem: (item: unknown) => boolean): value is HistorySnapshotPage<unknown> {
  return isRecord(value) && Array.isArray(value.items) && value.items.every(isItem) &&
    typeof value.total === "number" && Number.isInteger(value.total) && value.total >= 0 &&
    typeof value.limit === "number" && Number.isInteger(value.limit) && value.limit > 0 &&
    typeof value.offset === "number" && Number.isInteger(value.offset) && value.offset >= 0;
}

function isWorkingCalendar(row: unknown): boolean {
  return isRecord(row) && hasStrings(row, ["source_calendar_id", "source_location_id", "name", "week_pattern"]) && isDecimalString(row.standard_hours_per_day);
}

function isWorkingCalendarDay(row: unknown): boolean {
  return isRecord(row) && hasStrings(row, ["source_calendar_id", "day", "source", "kind"]) &&
    hasNullableStrings(row, ["name", "country_code"]) && (typeof row.year === "number" || row.year === null);
}

function isAbsenceType(row: unknown): boolean {
  return isRecord(row) && hasStrings(row, ["source_absence_type_id", "name"]) &&
    typeof row.generates_cost === "boolean" && typeof row.generates_revenue === "boolean" &&
    typeof row.is_statutory_leave === "boolean";
}

function isAbsenceBudget(row: unknown): boolean {
  return isRecord(row) && hasStrings(row, ["source_calendar_id", "source_engagement_type_id", "unit", "effective_from", "effective_to"]) && isDecimalString(row.budget_days);
}

function isCatalogRate(row: unknown): boolean {
  return isRecord(row) && hasStrings(row, ["source_rate_id", "source_role_id", "source_seniority_id", "source_location_id", "source_engagement_type_id", "currency", "unit", "effective_from"]) &&
    isDecimalString(row.default_selling_rate) && isDecimalString(row.surcharge_percent) &&
    hasNullableStrings(row, ["source_vendor_id", "effective_to"]) && typeof row.includes_surcharge === "boolean" &&
    ((row.default_cost_rate === undefined && row.cost_rate_unit === undefined) ||
      (isDecimalString(row.default_cost_rate) && typeof row.cost_rate_unit === "string"));
}

function isExchangeRate(row: unknown): boolean {
  return isRecord(row) && hasStrings(row, ["source_rate_id", "source_scope", "source_currency", "target_currency", "effective_from", "source"]) && isDecimalString(row.rate) &&
    hasNullableStrings(row, ["source_project_id", "source_scenario_id", "effective_to"]);
}

function isSnapshot(value: unknown): value is ApprovedScenarioSnapshot {
  if (!isRecord(value) ||
      !(value.organization_defaults === null || isOrganizationDefaults(value.organization_defaults))) {
    return false;
  }
  return isSnapshotPage(value.working_calendars, isWorkingCalendar) &&
    isSnapshotPage(value.working_calendar_days, isWorkingCalendarDay) &&
    isSnapshotPage(value.absence_types, isAbsenceType) &&
    isSnapshotPage(value.absence_budgets, isAbsenceBudget) &&
    isSnapshotPage(value.catalog_rates, isCatalogRate) &&
    isSnapshotPage(value.exchange_rates, isExchangeRate);
}

/** Validate the complete boundary shape and the approved/draft pairing before UI rendering. */
export function isScenarioHistory(value: unknown, scenarioId: string): value is ScenarioHistory {
  if (!isRecord(value) || value.scenario_id !== scenarioId || typeof value.name !== "string" ||
      (value.status !== "Draft" && value.status !== "Approved") || typeof value.updated_at !== "string" ||
      !isInputs(value.inputs) || !(value.approval_event === null || isApprovalEvent(value.approval_event)) ||
      !(value.approved_snapshot === null || isSnapshot(value.approved_snapshot))) {
    return false;
  }
  return value.status === "Approved"
    ? value.approved_snapshot !== null
    : value.approval_event === null && value.approved_snapshot === null;
}
