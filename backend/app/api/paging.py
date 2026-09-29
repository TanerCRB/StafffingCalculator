"""Turning raw `limit`/`offset` query strings into a validated page - after scope (ADR-0017).

ADR-0017, point 8: on a scoped resource the scope check must run **before** the paging parameters
are validated, and that is not the framework's default order (FastAPI validates a query parameter
declared `int` before the handler body ever runs). So a paged endpoint declares `limit`/`offset` as
`str | None`, resolves scope first, and only then calls `validated_page` - a scenario outside the
caller's scope answers the same `404` whatever the parameters say, or whether they parse at all.

A violation is a `422` in the list-of-objects `detail` shape FastAPI/Pydantic answers everywhere
else (`{"type", "loc", "msg", "input", "ctx"?}`), naming the field and never a row value (NF-11),
and never a silent clamp (ADR-0017, point 7).
"""

from typing import Any

from fastapi import HTTPException, status


def _violation(
    field: str, *, error_type: str, message: str, raw: str, ctx: dict[str, int] | None = None
) -> HTTPException:
    detail: dict[str, Any] = {
        "type": error_type,
        "loc": ["query", field],
        "msg": message,
        "input": raw,
    }
    if ctx is not None:
        detail["ctx"] = ctx
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=[detail])


def _parse(raw: str | None, field: str) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise _violation(
            field,
            error_type="int_parsing",
            message="Input should be a valid integer, unable to parse string as an integer",
            raw=raw,
        ) from None


def validated_page(
    limit: str | None,
    offset: str | None,
    *,
    default_limit: int,
    max_limit: int,
    max_offset: int,
) -> tuple[int | None, int]:
    """`(limit, offset)` for a list read - or a `422`.

    `None, None` (neither named) is the whole list: `(None, 0)`. Naming only one fills the other
    from its default - `default_limit` for the limit, `0` for the offset.
    """
    if limit is None and offset is None:
        return None, 0
    parsed_limit = _parse(limit, "limit")
    parsed_offset = _parse(offset, "offset")
    effective_limit = default_limit if parsed_limit is None else parsed_limit
    effective_offset = 0 if parsed_offset is None else parsed_offset
    for field, value, raw, low, high in (
        ("limit", effective_limit, limit, 1, max_limit),
        ("offset", effective_offset, offset, 0, max_offset),
    ):
        shown = raw if raw is not None else str(value)
        if value < low:
            raise _violation(
                field,
                error_type="greater_than_equal",
                message=f"Input should be greater than or equal to {low}",
                raw=shown,
                ctx={"ge": low},
            )
        if value > high:
            raise _violation(
                field,
                error_type="less_than_equal",
                message=f"Input should be less than or equal to {high}",
                raw=shown,
                ctx={"le": high},
            )
    return effective_limit, effective_offset
