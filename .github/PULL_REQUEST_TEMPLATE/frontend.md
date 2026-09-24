<!--
Gate 2. A human approves the merge — this template exists so they approve
with proof in hand, not from the author's description.

Leave an unfilled field empty and write why. An entry of "not applicable" without justification is
worse than empty, because it looks like it was checked.

This is the version for `frontend/`. The backend has its own, in PULL_REQUEST_TEMPLATE.md, with an
invariant list about money handling, access control, and migrations.

Refs, not Closes: merging this PR must leave the Issue open, waiting for gate 3
(`state:evidence`). The gate-3 documentation PR is the one that says "Closes #<N>".
-->

Refs #

## What is changing and why

<!-- Why, not just what. One to three sentences. -->

## Definition of done — proof

<!--
Quote the _Definition of done:_ line from the story or from a phase in the frontend roadmap and point to
what satisfies it: a test name, an artifact, a command result. Code that exists but has no passing
test does not check off the task.

A screenshot is not proof. It shows one theme, one locale, one data state, and one
window width — and it is usually the other half of each pair that breaks.
-->

- Condition:
- Proof:

## Mutation

<!--
Required if the PR carries a strong claim: that a screen does NOT show something, that it denies,
that it does not miscalculate, that it does not drop the locale. Remove the mechanism, confirm that
the test actually fails, and add a line to docs/architecture/capabilities.md.

The mutation should target the layer the criterion is about. Removing a key from the translation
catalog is not a mutation for a claim computed by the date library.
-->

- Removed mechanism:
- Result:
- Line in docs/architecture/capabilities.md:

## Contrast test

<!--
An assertion without contrast also passes when the mechanism blocks everything. A screen rendering
an empty list satisfies "no items outside scope are visible" flawlessly and forever.

What case proves that the mechanism does not simply always deny? A 200 response gives a table where
a 403 gives a screen with no buttons.
-->

## Invariant Guardian report

<!-- Verdict PASS / STOP. On STOP — what was done with the findings. -->

## Dependency on the backend

<!--
What this PR takes from the backend (a new/changed endpoint or response field), and how you
verified it actually looks that way — the backend PR/commit it depends on, not just "the contract
is in src/api/contracts/."
-->

## Invariants affected by this change

<!--
Check what the change affects. Unchecked = not applicable. They all share one trait: breaking
them still renders correctly. Nothing throws an exception, so the only defense is this list and a
test.
-->

- [ ] New color — through a theme token, no hex outside the centralized source
- [ ] New API response type — only in `src/api/contracts/`, nowhere else
- [ ] New UI text — in the translation catalog, not in the component
- [ ] New money/percentage display — through the shared formatting helper, not `toFixed()` by hand
- [ ] New zero-denominator metric shown — renders "Not applicable", not `0%`/`NaN%`/blank
- [ ] A set spanning multiple currencies — no single combined total without a warning (F-09/F-10)
- [ ] New personnel-cost field/column — absent (not masked) for a user without that permission
- [ ] New permission-gated screen/section — denied preflight renders no action buttons, no flash of data
- [ ] New menu item or tab without a route — `disabled` with a tooltip, not hidden

## Out of scope for this PR

<!-- What is deliberately left for later, and where that is recorded. -->
