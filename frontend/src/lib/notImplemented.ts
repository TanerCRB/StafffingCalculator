/**
 * The one place that says how a control which is rendered but wired to nothing behaves.
 *
 * F-13: a user may see the shape of the product ahead of its implementation. Such a control is
 * rendered, keyboard reachable and announced — `aria-disabled` rather than `disabled`, so it keeps
 * its place in the tab order — and it carries a tooltip that starts with the same words everywhere.
 *
 * This lived inside `features/projects/ProjectListScreen.tsx` while it had exactly one user. The
 * shell's navigation rail is the second (SC-2-02 is not merged), and two copies of a convention are
 * how "Not implemented yet" starts being worded two ways and tested in one place only.
 */

/** The opening of every such tooltip. Tests match on it rather than on a whole sentence. */
export const NOT_IMPLEMENTED_PREFIX = "Not implemented yet";

/** `reason` names what has to exist first — a task identifier or the story that owns it. */
export function notImplementedHint(reason: string): string {
  return `${NOT_IMPLEMENTED_PREFIX} — ${reason}`;
}

/**
 * The placeholder handler for every such control. It does nothing, on purpose, and it is named so
 * that the next developer sees a placeholder to replace rather than a mechanism to add code next
 * to. Nothing here protects anything: a real handler hung on the same button would run,
 * `aria-disabled` or not — the block, when these actions exist, is the server's.
 */
export function handleNotYetImplemented(): void {
  // Intentionally empty — see the comment above.
}
