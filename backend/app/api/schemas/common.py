"""Annotated types shared by every request/response schema at the API boundary.

Extracted from `app.api.schemas.project` when the catalogue schemas (SC-2-01) needed the same three
(`DecimalString`, `NonEmptyName`, `Iso4217Code`). They are boundary rules, not project rules — a
second local copy of `DecimalString` in particular is how one endpoint starts serialising money as a
JSON float while the tests of another keep passing.
"""

from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer, StringConstraints

DecimalString = Annotated[Decimal, PlainSerializer(lambda value: format(value, "f"), str)]
"""Decimals cross the API boundary as fixed-point strings, never as JSON floats — a float would
lose exactly the precision NF-01/ADR-0002 require the storage layer to keep.

`format(value, "f")` and not `str(value)`: Pydantic's own Decimal serialisation is `str`, which
renders `Decimal("1.85E+2")` as `"1.85E+2"`. A consumer parsing that as a decimal is not wrong, but
one reading it as a display value is, and the two are indistinguishable in the payload."""

NonEmptyName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]

Iso4217Code = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
"""ISO-4217 alphabetic code, uppercase, validated by shape and not against a closed list:
ADR-0006 keeps currencies an open list (a string, not a database enum) so that adding one needs
no migration. Rejected rather than silently upper-cased — a request that means `eur` is a client
bug worth surfacing, and normalizing input on the way in is how two spellings of one currency
end up in the same column."""
