import { isDecimalString } from "../../lib/money";

export type AssumptionSource = "scenario" | "project" | "organization";
export type AssumptionState = "resolved" | "no_value";

export interface ResolvedAssumption {
  readonly value: string;
  readonly state: AssumptionState;
  readonly source: AssumptionSource | null;
}

export interface ScenarioAssumptions {
  readonly id: string;
  readonly status: "Draft" | "Approved";
  readonly updated_at: string;
  readonly target_margin_percent: ResolvedAssumption;
  readonly overload_threshold_percent: ResolvedAssumption;
}

export interface ScenarioAssumptionResetPreview {
  readonly id: string;
  readonly status: "Draft";
  readonly target_margin_percent: ResolvedAssumption;
  readonly overload_threshold_percent: ResolvedAssumption;
}

export interface ScenarioAssumptionsPatch {
  readonly updated_at: string;
  readonly target_margin_percent?: string | null;
  readonly overload_threshold_percent?: string | null;
}

export interface ScenarioAssumptionOverrides {
  readonly id: string;
  readonly status: "Draft" | "Approved";
  readonly updated_at: string;
  readonly target_margin_percent: string | null;
  readonly overload_threshold_percent: string | null;
}

export interface OrganizationDefaults {
  readonly updated_at: string | null;
  readonly target_margin_percent: string | null;
  readonly overload_threshold_percent: string | null;
}

export interface OrganizationDefaultsPatch {
  readonly updated_at: string | null;
  readonly target_margin_percent?: string | null;
  readonly overload_threshold_percent?: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nullableDecimal(value: unknown): value is string | null {
  return value === null || isDecimalString(value);
}

export function isResolvedAssumption(value: unknown): value is ResolvedAssumption {
  if (!isRecord(value)) return false;
  const validSource = value.source === null || value.source === "scenario" ||
    value.source === "project" || value.source === "organization";
  return validSource && (
    (value.state === "resolved" && (isDecimalString(value.value) || value.value === "n/a") && value.source !== null) ||
    (value.state === "no_value" && value.value === "n/a" && value.source === null)
  );
}

export function isScenarioAssumptions(value: unknown): value is ScenarioAssumptions {
  return isRecord(value) && typeof value.id === "string" &&
    (value.status === "Draft" || value.status === "Approved") && typeof value.updated_at === "string" &&
    isResolvedAssumption(value.target_margin_percent) && isResolvedAssumption(value.overload_threshold_percent);
}

export function isScenarioAssumptionResetPreview(value: unknown): value is ScenarioAssumptionResetPreview {
  return isRecord(value) && typeof value.id === "string" && value.status === "Draft" &&
    isResolvedAssumption(value.target_margin_percent) && isResolvedAssumption(value.overload_threshold_percent);
}

export function isScenarioAssumptionOverrides(value: unknown): value is ScenarioAssumptionOverrides {
  return isRecord(value) && typeof value.id === "string" &&
    (value.status === "Draft" || value.status === "Approved") && typeof value.updated_at === "string" &&
    nullableDecimal(value.target_margin_percent) && nullableDecimal(value.overload_threshold_percent);
}

export function isOrganizationDefaults(value: unknown): value is OrganizationDefaults {
  return isRecord(value) && (typeof value.updated_at === "string" || value.updated_at === null) &&
    nullableDecimal(value.target_margin_percent) && nullableDecimal(value.overload_threshold_percent);
}
