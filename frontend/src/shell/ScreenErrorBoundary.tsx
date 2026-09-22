import { Component, createRef, type ReactNode } from "react";

/**
 * SC-1-09, ADR-0010 — the application's one render-error boundary, mounted by `AppShell` around the
 * screen slot.
 *
 * Until this existed, a single exception thrown while a screen rendered unmounted the whole React
 * tree: no topbar, no breadcrumb, no rail, no message, and no way back other than reloading the
 * tab. That is the failure this component exists to convert into a stated one.
 *
 * Six properties are the point of it, and each is proven separately (K-02..K-04, K-07; the last two
 * by Reviewer R-02 and R-04 of 2026-09-22):
 *
 *   * **It is the last resort, never the first line.** A response that violates the contract is
 *     stopped in `api/client.ts` (`isProjectListItemShape`) and ends in the screen's own named read
 *     failure. Anything reaching here is an exception nobody anticipated, and it is stated as such
 *     — a different sentence, giving different advice (ADR-0010, points 2 and 5).
 *   * **It catches render-phase exceptions and nothing else.** React error boundaries do not see
 *     errors thrown in event handlers, in `setTimeout`/`Promise` callbacks, in effects that run
 *     after commit, or during server rendering. A crash in a click handler still reaches the
 *     browser's global handler, and this component will not have been involved (ADR-0010, point 1).
 *   * **A caught crash does not outlive the screen that caused it** (K-04). `resetKey` is the
 *     identifier of the screen the shell currently mounts; when it changes, the boundary clears
 *     itself, so navigating the rail to a healthy screen mounts that screen for real rather than
 *     leaving the fallback standing over it.
 *   * **The fallback renders nothing derived from the payload** (K-03, NF-11). The caught error is
 *     not stored: `getDerivedStateFromError` returns a flag and discards its argument. Nothing in
 *     this module calls `console`, and nothing reports the error anywhere: diagnostics has no
 *     receiver in this repository and no decision about what may go into one (Issue #43, out of
 *     scope 3).
 *     *Known limitation, corrected (Reviewer R-01, SC-1-09):* React logs the error it hands a class
 *     boundary — `console.error(error)`, in `logCapturedError`, wired unconditionally for any
 *     component that defines `getDerivedStateFromError`. It does so **in the production bundle as
 *     well as the development one** (`react-dom.production.min.js`, React 18.3.1), and it does so
 *     before this component is consulted, so there is no version of this file that can prevent it.
 *     What keeps a protected amount off that console is therefore not this boundary's discretion but
 *     the absence of the amount from the error: `lib/money.ts` throws
 *     `"Not a fixed-point decimal string"` with no value interpolated into it. This boundary's own
 *     promise is narrower than it used to read — it adds nothing to what React reports, and it
 *     reports nothing itself.
 *   * **There is a way out from where the user is** (K-04, gate-1 decision, gap 3). "Try again"
 *     re-renders the screen from the same place in the tree. Because the crashed subtree was
 *     unmounted when the fallback replaced it, that re-render is a fresh mount: the screen's
 *     effects run and its reads are issued again. A crash caused by one bad response therefore
 *     clears itself once the server answers differently, without a page reload. The control that
 *     did it is removed from the DOM by its own success, so the shell is told to put focus back on
 *     the remounted screen's heading (`onRetry`, Reviewer R-02) — the same landing place a rail
 *     navigation uses, rather than `document.body`.
 *   * **A retry that keeps failing says so** (Reviewer R-04). The button is offered for ever, but
 *     the guidance beside it stops pretending the next press is likely to be different once two
 *     consecutive retries have crashed again with no successful render in between. The counter is
 *     reset by a render that works and by a navigation — repetition, not a lifetime total.
 */

/**
 * The one sentence this application shows for a screen that crashed while rendering.
 *
 * Distinct from every other failure sentence on both screens, and not a substring of any of them in
 * either direction (K-07; the same rule ADR-0009 point 5 states for the write endings). It has to
 * be: "the server refused", "the server was too slow" and "this application has a defect" lead to
 * three different next actions, and a shared sentence would give all three the same one.
 *
 * It names no cause, because it has none to name: the error is deliberately not kept (see above).
 */
export const SCREEN_CRASH_MESSAGE =
  "This screen stopped working and was closed so the rest of the application keeps running. " +
  "Nothing else on this page was affected; if this keeps happening, report it.";

/**
 * What the fallback says once retrying has stopped being plausible advice (Reviewer R-04).
 *
 * It replaces the sentence above rather than joining it: the plain one ends in "if this keeps
 * happening, report it", which is precisely the situation the user is now in, and repeating it
 * under a button that has already failed twice is the application declining to notice. The button
 * stays — the cause may still clear on its own, and disabling the one way out would be a worse
 * answer than an honest one.
 */
export const SCREEN_CRASH_PERSISTENT_MESSAGE =
  "This screen has failed again every time it was tried, so another attempt is unlikely to help on " +
  "its own. Reloading the page, or coming back to it later, is the next thing worth trying; if it " +
  "still fails, report it.";

/**
 * How many retries must crash again, with no successful render in between, before the fallback
 * changes its advice. Two: the first failed retry is ordinary bad luck, the second is a pattern.
 */
export const SCREEN_CRASH_PERSISTENT_AFTER_RETRIES = 2;

/**
 * The repeat, said out loud for a screen reader (Reviewer R-04).
 *
 * `role="alert"` announces a live region when its *content changes*. A retry that crashes again puts
 * the same words back, so an assistive technology has nothing to notice and a blind user gets
 * silence where a sighted one sees the message flash. This sentence changes on every repeat, which
 * is what makes the announcement happen again; it is visually hidden because a sighted user watched
 * the screen do it.
 *
 * Words rather than a changing `key` on the alert element. Re-keying would force an announcement
 * too, but by re-mounting the whole fallback — the retry button included — and it would announce the
 * same sentence again rather than say what actually happened.
 */
export function screenCrashRepeatNotice(failedRetries: number): string {
  return `Retry ${failedRetries} did not help.`;
}

/** The label of the control that re-mounts the crashed screen from the same place. */
export const SCREEN_CRASH_RETRY_LABEL = "Try again";

interface ScreenErrorBoundaryProps {
  /**
   * Which screen the shell currently mounts. A change means the user navigated, and a crash
   * belonging to the screen they left must not follow them (K-04).
   */
  readonly resetKey: string;
  /**
   * Called once a retry has been committed, whatever it produced (Reviewer R-02).
   *
   * The boundary knows *when* the screen was remounted and nothing about where focus should land;
   * the shell owns that convention already (`AppShell`'s focus effect, Reviewer R-03 of SC-2-02)
   * and owns the element to search inside. Handing the moment upwards keeps one focus convention in
   * the application rather than a second one invented here.
   */
  readonly onRetry?: () => void;
  readonly children: ReactNode;
}

interface ScreenErrorBoundaryState {
  /**
   * A flag, and only a flag. The caught error is not held anywhere: a fallback that cannot reach a
   * value cannot leak one, which is a stronger guarantee than a fallback that holds the error and
   * is careful with it (K-03, NF-11).
   */
  readonly crashed: boolean;
  /** The `resetKey` the current `crashed` value belongs to. */
  readonly resetKey: string;
  /**
   * How many retries have been pressed since the last render that worked (Reviewer R-04).
   *
   * Counted here rather than in a `componentDidCatch`, which this component deliberately does not
   * have: that hook is the one a reporter hangs off, and growing one to keep a tally would be a
   * place where logging looks considered and forgotten. Incrementing on the press and clearing on a
   * commit that rendered the screen gives the same number out of the two moments this component
   * already has.
   */
  readonly failedRetries: number;
}

export class ScreenErrorBoundary extends Component<
  ScreenErrorBoundaryProps,
  ScreenErrorBoundaryState
> {
  constructor(props: ScreenErrorBoundaryProps) {
    super(props);
    this.state = { crashed: false, resetKey: props.resetKey, failedRetries: 0 };
  }

  /** The error is the argument this method does not take. See `ScreenErrorBoundaryState.crashed`. */
  static getDerivedStateFromError(): Partial<ScreenErrorBoundaryState> {
    return { crashed: true };
  }

  static getDerivedStateFromProps(
    props: ScreenErrorBoundaryProps,
    state: ScreenErrorBoundaryState,
  ): Partial<ScreenErrorBoundaryState> | null {
    if (props.resetKey === state.resetKey) {
      return null;
    }
    // The shell mounts a different screen than the one that crashed. Clearing here rather than in
    // an effect matters: it happens in the same render that brings the new screen in, so the new
    // screen is mounted directly, and the fallback never flashes over it (K-04).
    //
    // The repeat count goes with it: it describes one screen's refusal to render, and carrying it
    // to the next screen would open that screen's first crash with advice about a history it has
    // none of (Reviewer R-04).
    return { crashed: false, resetKey: props.resetKey, failedRetries: 0 };
  }

  // No `componentDidCatch`. It is the hook a reporter would hang off, and there is nothing to
  // report to (Issue #43, out of scope 3); an empty one would read as a place where logging was
  // considered and forgotten.

  /**
   * A commit happened. If it rendered the screen rather than the fallback, whatever was wrong is
   * over and the retry tally is spent (Reviewer R-04).
   *
   * This runs only for renders React actually committed — a retry whose screen threw again never
   * reaches it, because that render was unwound rather than committed, which is exactly what makes
   * "consecutive, with nothing working in between" the thing being counted.
   */
  componentDidUpdate(): void {
    if (!this.state.crashed && this.state.failedRetries !== 0) {
      this.setState({ failedRetries: 0 });
    }
  }

  /**
   * The fallback's own control, so focus has somewhere to go when a retry crashes again (Reviewer
   * R-02).
   *
   * It is needed because React does not keep the node: a retry that throws again re-renders the
   * fallback into a *new* DOM element — the subtree was already being replaced by the screen when
   * the throw unwound it — so the button under the user's finger is not the button that comes back.
   * Measured, not assumed: without this the focus lands on `document.body`.
   */
  private readonly retryRef = createRef<HTMLButtonElement>();

  private readonly retry = () => {
    this.setState(
      (previous) => ({ crashed: false, failedRetries: previous.failedRetries + 1 }),
      // After the commit, not during the click: whatever the retry produced is in the DOM by now.
      // Either way the control that held keyboard focus has been removed by its own press, and
      // without this the browser drops focus to `document.body` — the same defect Reviewer R-03
      // found on the rail in SC-2-02, reproduced by a new control (R-02).
      () => {
        if (this.state.crashed) {
          // It crashed again. The way out is still the only thing on screen, so focus belongs on
          // it — not on a heading that does not exist, and not on the body.
          this.retryRef.current?.focus();
          return;
        }
        this.props.onRetry?.();
      },
    );
  };

  render(): ReactNode {
    if (!this.state.crashed) {
      return this.props.children;
    }

    const { failedRetries } = this.state;
    const persistent = failedRetries >= SCREEN_CRASH_PERSISTENT_AFTER_RETRIES;

    return (
      // `role="alert"`, not the `role="status"` the screens use for their read failures: this is
      // the one message that means the application itself misbehaved, and it appears without the
      // user having asked for anything.
      <div className="app-shell__screen-error" role="alert">
        <p className="app-shell__screen-error-message">
          {persistent ? SCREEN_CRASH_PERSISTENT_MESSAGE : SCREEN_CRASH_MESSAGE}
        </p>
        {/* The repeat, for a reader that cannot see the message flash. See
            `screenCrashRepeatNotice`: the words have to change, or the live region announces
            nothing the second time. */}
        {failedRetries > 0 && (
          <span className="visually-hidden">{screenCrashRepeatNotice(failedRetries)}</span>
        )}
        <button
          ref={this.retryRef}
          type="button"
          className="button button--secondary"
          onClick={this.retry}
        >
          {SCREEN_CRASH_RETRY_LABEL}
        </button>
      </div>
    );
  }
}
