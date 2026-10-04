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
