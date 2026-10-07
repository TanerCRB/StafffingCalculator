import { ApiError, RequestTimeoutError } from "../../api/client";

export const SCENARIO_CREATE_MESSAGES = {
  heading: "Create a scenario",
  nameLabel: "Scenario name",
  create: "Create scenario",
  creating: "Creating scenario…",
  created: "Scenario created. Its draft and missing-input state are shown below.",
  denied: "Scenario was not created — you do not have permission to create scenarios.",
  notFound: "Scenario was not created — the project was not found.",
  invalid: "Scenario was not created — enter a name containing a non-whitespace character.",
  unresolved: "The create result is unresolved. Refresh the project list before deciding whether to try again.",
  refresh: "Refresh project list",
  failed: "Scenario was not created — the server could not complete the request.",
  unknownSuccess: "The server reported success with a response this screen could not read. The result is unresolved; refresh the project list.",
} as const;

export function describeScenarioCreateFailure(error: unknown): string {
  if (error instanceof RequestTimeoutError) return SCENARIO_CREATE_MESSAGES.unresolved;
  if (!(error instanceof ApiError)) return SCENARIO_CREATE_MESSAGES.failed;
  if (error.status >= 200 && error.status < 300) return SCENARIO_CREATE_MESSAGES.unknownSuccess;
  switch (error.status) {
    case 401:
    case 403:
      return SCENARIO_CREATE_MESSAGES.denied;
    case 404:
      return SCENARIO_CREATE_MESSAGES.notFound;
    case 409:
      // The endpoint defines two named and materially different conflicts. Keep the server's
      // exact statement so an archived-project refusal cannot be presented as a name conflict.
      return error.detail ?? SCENARIO_CREATE_MESSAGES.failed;
    case 422:
      return SCENARIO_CREATE_MESSAGES.invalid;
    default:
      return SCENARIO_CREATE_MESSAGES.failed;
  }
}
