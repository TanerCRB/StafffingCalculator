/** API contract for scenario-owned additional costs (ADR-0014, SC-5-13). */

export const ADDITIONAL_COST_TYPES = ["one_off", "recurring"] as const;
export type AdditionalCostType = (typeof ADDITIONAL_COST_TYPES)[number];

export const ADDITIONAL_COST_FUNDING_SOURCES = ["internal", "rebilled_to_client"] as const;
export type AdditionalCostFundingSource = (typeof ADDITIONAL_COST_FUNDING_SOURCES)[number];

export interface AdditionalCostRead {
  id: string;
  category_id: string;
  category_name: string;
  position_id: string | null;
  risk_id: string | null;
  amount: string;
  currency: string;
  cost_type: AdditionalCostType;
  start_month: string;
  end_month: string | null;
  funding_source: AdditionalCostFundingSource;
  updated_at: string;
}

export interface ScenarioAdditionalCosts {
  scenario_id: string;
  scenario_status: "Draft" | "Approved";
  costs: AdditionalCostRead[];
}

export interface AdditionalCostCreateRequest {
  category_id: string;
  amount: string;
  currency: string;
  cost_type: AdditionalCostType;
  start_month: string;
  end_month: string | null;
  funding_source: AdditionalCostFundingSource;
}

export interface AdditionalCostEditRequest {
  updated_at: string;
  category_id?: string;
  amount?: string;
  currency?: string;
  cost_type?: AdditionalCostType;
  start_month?: string;
  end_month?: string | null;
  funding_source?: AdditionalCostFundingSource;
}

export interface AdditionalCostDeleteRequest {
  updated_at: string;
}
