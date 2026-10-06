// User-facing approval text lives in one feature catalog, following the existing *Text.ts modules.

export const SCENARIO_APPROVAL_TEXT = {
  approve: "Approve scenario",
  saving: "Approving scenario…",
  approved: "Approved. Its saved values are frozen; duplicate this scenario to make further changes.",
  denied: "Approval was refused. The scenario remains unapproved.",
  conflict: "The scenario changed before approval completed. Refresh the project and review its current status.",
  alreadyApproved: "This scenario is already approved. Its values are frozen; duplicate it to make further changes.",
  timedOut: "Approval outcome is unresolved because the server did not answer in time. Refresh the project to check its status.",
  failed: "Approval outcome is unresolved. The server may have completed the request. Refresh the project to check the scenario’s current status.",
} as const;
