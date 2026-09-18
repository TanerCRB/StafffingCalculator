# Verified capabilities registry

Answers exactly one question: **what does the system actually do, and what proves it** — kept
separate from `decisions/`, because an accepted architectural decision means only an approved
direction, not working code (see `FrameworkDoc.md`, section 7). Rows are added only in a
documentation commit made by the human (gate 3) — the Developer and QA roles **propose** rows in
their reports, ready to paste; they do not write here directly.

Evidence column uses exactly one of four values:

- **mutation-checked test** — a test exists, and a mutation was run against the mechanism it
  guards, and recorded in the mutation log below.
- **test, no mutation** — a test exists and is green, but no mutation has been run against it yet.
  This is weaker proof than it looks — see `FrameworkDoc.md`, section 6.
- **manual verification, `<date>`** — checked by hand once; will go stale silently if nothing
  re-checks it.
- **no evidence** — documented and possibly implemented, but nothing here proves it works. This is
  the honest default for a new capability row, not a flaw in the registry.

## Capabilities

| Capability | Requirement ref | Evidence | Reference |
|---|---|---|---|

*(empty — first rows land once the first task through the full process reaches gate 3)*

## Mutation log

| Date | Task | Removed mechanism | Result |
|---|---|---|---|

*(empty)*
