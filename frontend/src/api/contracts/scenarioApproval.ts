/** Response contract for POST /projects/{project_id}/scenarios/{scenario_id}/approve. */
export interface ScenarioApproval {
  readonly id: string;
  readonly status: "Approved";
  readonly snapshot: {
    readonly working_calendars: number;
    readonly working_calendar_days: number;
    readonly absence_types: number;
    readonly absence_budgets: number;
    readonly organization_defaults: number;
  };
}
