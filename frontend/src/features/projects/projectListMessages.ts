/** Feature-local copy catalog for the project search and paging controls. */
export const PROJECT_LIST_MESSAGES = {
  searchPlaceholder: "Search name, client, or owner",
  statusLabel: "Status",
  statusAll: "All statuses",
  resetFilters: "Reset filters",
  noProjects: "No projects to show.",
  noMatches: "No projects match your search and filters.",
  updating: "Updating projects…",
  retry: "Try again",
  paginationLabel: "Project list pages",
  page: (current: number, count: number) => `Page ${current} of ${count}`,
  noPages: "No pages",
  previousPage: "Previous page",
  nextPage: "Next page",
  previous: "Previous",
  next: "Next",
} as const;

export const PROJECT_COPY_MESSAGES = {
  copying: "Copying project…",
  denied: "Project not copied — you do not have permission to copy this project.",
  timedOut: "The copy request timed out. Check the project list before trying again.",
  refused: "Project not copied — the server refused the copy.",
  reconciling: "Project copied. Updating the server project list…",
  copiedNotOnPage: "The project was copied, but its row is on another page. Use the project list pagination to locate it.",
  unsupportedModel: (detail: string) => detail,
} as const;

export const PROJECT_ARCHIVE_MESSAGES = {
  confirmation: (name: string) => `Archive ${name}? This action cannot be undone. The project will remain visible as Archived.`,
  archiving: "Archiving project…",
  reconciling: "Project archived. Refreshing the project list…",
  unresolved: "The archive result is unknown. Refreshing the project list to check.",
  refused: "Project was not archived. Its status has not changed.",
} as const;
