// User-facing wording for the additional-cost section (same text-catalog convention as
// commercialTermsText.ts; this app does not yet have a locale-switching translation framework).

export const ADDITIONAL_COSTS_HEADING = "Additional costs";
export const ADD_COST = "Add cost";
export const EDIT_COST = "Edit";
export const REMOVE_COST = "Remove";
export const CANCEL = "Cancel";
export const SAVE = "Save cost";
export const SAVING = "Saving cost…";
export const CATEGORY = "Category";
export const SELECT_CATEGORY = "Select a category";
export const AMOUNT = "Amount";
export const CURRENCY = "Currency";
export const TYPE = "Period type";
export const ONE_OFF = "One-off";
export const RECURRING = "Recurring";
export const START_MONTH = "Start month";
export const END_MONTH = "End month";
export const FUNDING_SOURCE = "Charge basis";
export const INTERNAL = "Internal";
export const REBILLED_TO_CLIENT = "Rebilled to client";
export const PERIOD = "Period";
export const NO_COSTS = "No additional costs are recorded for this scenario.";
export const READ_LOADING = "Loading additional costs";
export const READ_DENIED = "Additional costs could not be read with the current access.";
export const READ_NOT_FOUND = "This scenario's additional costs are unavailable.";
export const READ_FAILED = "Additional costs could not be loaded.";
export const READ_AGAIN = "Read additional costs again";
export const CATEGORIES_LOADING = "Loading cost categories";
export const CATEGORIES_UNAVAILABLE = "Cost categories could not be loaded; a new cost cannot be added.";
export const CATEGORY_REQUIRED = "Select a cost category.";
export const WRITE_DENIED = "Not saved — the server denied this change.";
export const WRITE_REFUSED = "Not saved — the server refused this change.";
export const WRITE_UNRESOLVED =
  "Unresolved — the server did not give a readable answer. Retry this create to reuse the same key, or read the list again.";
export const WRITING = "This cost is being saved…";
export const SAVED = "Saved. The row below is the server's response.";
export const REMOVED = "Removed.";
export const INVALID = "Not saved — check the fields and try again.";

export const COST_TYPE_LABELS = {
  one_off: ONE_OFF,
  recurring: RECURRING,
} as const;

export const FUNDING_SOURCE_LABELS = {
  internal: INTERNAL,
  rebilled_to_client: REBILLED_TO_CLIENT,
} as const;
