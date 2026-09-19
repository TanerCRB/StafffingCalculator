"""Reading the mapped column values of a row, minus the ones a copy must not inherit.

Extracted from `app.data.project_writes` (where it was `_values_to_copy`) when the staffing copier
of SC-3-01 needed the same thing. One implementation, in a module neither copy path imports the
other through: `project_writes` holds `SCENARIO_CHILD_COPIERS` and therefore imports the staffing
copier, so the staffing module cannot import back from it.

Why reflection rather than a hand-written field list, in every copy path that uses this: a column
added to a table later is then copied **by default** instead of being silently left behind on the
copy — the failure ADR-0004 (addendum 2026-09-18, point 4) names. What keeps reflection from being
silent in the other direction is a per-table drift guard in the tests: the mapped attributes must
equal the copied set plus the explicitly excluded set, so a new column fails a test until somebody
decides which side it is on.
"""

import sqlalchemy as sa


def values_to_copy(instance: object, *, excluded: frozenset[str]) -> dict[str, object]:
    """Mapped column values of `instance`, minus the excluded attribute names."""
    mapper = sa.inspect(type(instance))
    return {
        attribute.key: getattr(instance, attribute.key)
        for attribute in mapper.column_attrs
        if attribute.key not in excluded
    }
