<!--
Gate 2. A human approves the merge — this template exists so they approve
with proof in hand, not from the author's description.

Leave an unfilled field empty and write why. An entry of "not applicable" without justification is
worse than empty, because it looks like it was checked.

Backend (Python/FastAPI) version. The frontend variant has its own invariant list — see
PULL_REQUEST_TEMPLATE-frontend.md in this directory.

Refs, not Closes: merging this PR must leave the Issue open, waiting for gate 3
(`state:evidence`). The gate-3 documentation PR is the one that says "Closes #<N>".
-->

Refs #

## What is changing and why

<!-- Why, not just what. One to three sentences. -->

## Definition of done — proof

<!--
Quote the *Definition of done:* line from the story and point to what satisfies it: a test class
name, a query, an artifact. Code that exists but has no passing test does not check off the task.
-->

- Condition:
- Proof:

## Mutation

<!--
Required if the PR carries a strong claim (access control, money correctness, a database
constraint, version immutability). Remove the mechanism, confirm that the test actually fails, and
add a line to docs/architecture/capabilities.md.
-->

- Removed mechanism:
- Result:
- Line in docs/architecture/capabilities.md:

## Contrast test

<!--
A negative test without contrast also passes when the mechanism blocks everything.
What case proves that the mechanism does not simply always deny?
-->

## Invariant Guardian report

<!-- Verdict PASS / STOP. On STOP — what was done with the findings. -->

## Invariants affected by this change

<!--
Check what the change affects. Unchecked = not applicable. Numbers refer to
agents/invariant-guardian.md.
-->

- [ ] New/changed monetary calculation — `Decimal` end-to-end, no `float` (rule 1)
- [ ] New rounding point — explicit rule named, no unrounded value crosses a boundary (rule 2)
- [ ] New zero-denominator metric — returns explicit "n/a", not 0/null/NaN (rule 3)
- [ ] New endpoint on project/scenario data — declares its permission, denies by default (rule 4)
- [ ] Personnel-cost field in a response — stripped server-side, not just hidden in UI (rule 5)
- [ ] New export/server interface reading project data — reuses the shared authorization check (rule 6)
- [ ] Write to a calculation — rejected at data-access layer when the version is approved (rule 7)
- [ ] Report/export logic — reads rates/calendars/rules from the saved version's snapshot (rule 8)
- [ ] New/changed rate or cost table — effective-date overlap rejected at write time (rule 13)
- [ ] New time column — timezone-aware, or a date-only type, never naive datetime (rule 15)
- [ ] New document in `docs/architecture/decisions/` — added to the publication list

## Out of scope for this PR

<!-- What is deliberately left for later, and where that is recorded. -->
