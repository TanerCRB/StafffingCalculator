"""Operator-run Nager.Date holiday import (SC-3-08, ADR-0020)."""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.catalog import WorkingCalendar, WorkingCalendarDay, WorkingCalendarDayKind

API_BASE_URL = "https://date.nager.at/api/v3"
MAX_RESPONSE_BYTES = 2_000_000
MAX_HOLIDAY_ROWS = 500
REQUEST_TIMEOUT = httpx.Timeout(connect=3.0, read=15.0, write=3.0, pool=3.0)
REQUEST_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "identity",
    "User-Agent": "StafffingCalculator-holiday-import/1.0",
}


class ImportFailure(StrEnum):
    INVALID_ARGUMENT = "invalid_argument"
    UNKNOWN_CALENDAR = "unknown_calendar"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    UNEXPECTED_STATUS = "unexpected_status"
    REDIRECT_REFUSED = "redirect_refused"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    PAYLOAD_INVALID = "payload_invalid"
    DATABASE_ERROR = "database_error"


@dataclass(frozen=True)
class StaleImportRow:
    day: date
    name: str


@dataclass(frozen=True)
class ConflictRow:
    day: date
    incoming_kind: str
    incoming_name: str
    incoming_source: str
    incoming_country_code: str
    incoming_year: int
    existing_kind: str
    existing_source: str
    existing_name: str | None


@dataclass(frozen=True)
class HolidayImportReport:
    calendar_id: uuid.UUID
    country_code: str
    year: int
    received_rows: int = 0
    written: int = 0
    type_skipped: int = 0
    regional_skipped: int = 0
    collapsed_duplicates: int = 0
    conflict_skipped: int = 0
    conflicts: tuple[ConflictRow, ...] = ()
    stale_import_rows: tuple[StaleImportRow, ...] = ()
    added_days_by_month: dict[str, int] | None = None
    mixed_countries: tuple[str, ...] = ()
    failure: ImportFailure | None = None

    @property
    def succeeded(self) -> bool:
        return self.failure is None

    def as_dict(self) -> dict[str, Any]:
        report = asdict(self)
        report["calendar_id"] = str(self.calendar_id)
        report["stale_import_rows"] = [
            {"day": row.day.isoformat(), "name": row.name} for row in self.stale_import_rows
        ]
        report["conflicts"] = [
            {
                "day": row.day.isoformat(),
                "incoming_kind": row.incoming_kind,
                "incoming_name": row.incoming_name,
                "incoming_source": row.incoming_source,
                "incoming_country_code": row.incoming_country_code,
                "incoming_year": row.incoming_year,
                "existing_kind": row.existing_kind,
                "existing_source": row.existing_source,
                "existing_name": row.existing_name,
            }
            for row in self.conflicts
        ]
        report["failure"] = self.failure.value if self.failure else None
        report["succeeded"] = self.succeeded
        return report


class _ImportProblem(Exception):
    def __init__(self, failure: ImportFailure) -> None:
        self.failure = failure


def _valid_request(country_code: object, year: object) -> bool:
    return (
        isinstance(country_code, str)
        and re.fullmatch(r"[A-Z]{2}", country_code, flags=re.ASCII) is not None
        and type(year) is int
        and 1 <= year <= 9999
    )


def _load_payload(
    country_code: str,
    year: int,
    transport: httpx.BaseTransport | None,
) -> list[dict[str, Any]]:
    url = f"{API_BASE_URL}/PublicHolidays/{year}/{country_code}"
    with httpx.Client(
        transport=transport,
        timeout=REQUEST_TIMEOUT,
        follow_redirects=False,
        verify=True,
        trust_env=False,
        headers=REQUEST_HEADERS,
    ) as client:
        try:
            with client.stream("GET", url) as response:
                if 300 <= response.status_code < 400:
                    raise _ImportProblem(ImportFailure.REDIRECT_REFUSED)
                if response.status_code == 204:
                    return []
                if response.status_code != 200:
                    raise _ImportProblem(ImportFailure.UNEXPECTED_STATUS)
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise _ImportProblem(ImportFailure.PAYLOAD_INVALID)
                content_length = response.headers.get("content-length")
                if content_length and content_length.isdecimal():
                    if len(content_length) > len(str(MAX_RESPONSE_BYTES)) or int(
                        content_length
                    ) > MAX_RESPONSE_BYTES:
                        raise _ImportProblem(ImportFailure.PAYLOAD_TOO_LARGE)
                body = bytearray()
                for chunk in response.iter_raw():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise _ImportProblem(ImportFailure.PAYLOAD_TOO_LARGE)
        except httpx.TimeoutException as error:
            raise _ImportProblem(ImportFailure.TIMEOUT) from error
        except httpx.HTTPError as error:
            raise _ImportProblem(ImportFailure.UNREACHABLE) from error

    try:
        payload = json.loads(body)
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        TypeError,
        ValueError,
        RecursionError,
    ) as error:
        raise _ImportProblem(ImportFailure.PAYLOAD_INVALID) from error
    if not isinstance(payload, list) or len(payload) > MAX_HOLIDAY_ROWS:
        failure = (
            ImportFailure.PAYLOAD_TOO_LARGE
            if isinstance(payload, list)
            else ImportFailure.PAYLOAD_INVALID
        )
        raise _ImportProblem(failure)
    return payload


def _validate_payload(
    rows: list[dict[str, Any]], country_code: str, year: int
) -> list[tuple[date, str, bool, list[str]]]:
    validated: list[tuple[date, str, bool, list[str]]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise _ImportProblem(ImportFailure.PAYLOAD_INVALID)
        raw_day = row.get("date")
        name = row.get("name")
        country = row.get("countryCode")
        global_holiday = row.get("global")
        types = row.get("types")
        if (
            not isinstance(raw_day, str)
            or not isinstance(name, str)
            or not isinstance(country, str)
            or not isinstance(global_holiday, bool)
            or not isinstance(types, list)
            or any(not isinstance(kind, str) for kind in types)
        ):
            raise _ImportProblem(ImportFailure.PAYLOAD_INVALID)
        try:
            holiday_day = date.fromisoformat(raw_day)
        except ValueError as error:
            raise _ImportProblem(ImportFailure.PAYLOAD_INVALID) from error
        if (
            holiday_day.isoformat() != raw_day
            or holiday_day.year != year
            or country != country_code
        ):
            raise _ImportProblem(ImportFailure.PAYLOAD_INVALID)
        validated.append((holiday_day, name, global_holiday, types))
    return validated


def _failed_report(
    calendar_id: uuid.UUID,
    country_code: str,
    year: int,
    failure: ImportFailure,
    received_rows: int = 0,
) -> HolidayImportReport:
    return HolidayImportReport(
        calendar_id=calendar_id,
        country_code=country_code if isinstance(country_code, str) else "",
        year=year if type(year) is int else 0,
        received_rows=received_rows,
        failure=failure,
    )


def import_public_holidays(
    session: Session,
    calendar_id: uuid.UUID,
    country_code: str,
    year: int,
    *,
    transport: httpx.BaseTransport | None = None,
) -> HolidayImportReport:
    """Fetch and validate one country/year before writing any database rows."""
    if not _valid_request(country_code, year):
        return _failed_report(calendar_id, country_code, year, ImportFailure.INVALID_ARGUMENT)

    try:
        calendar_exists = session.get(WorkingCalendar, calendar_id) is not None
    except SQLAlchemyError:
        session.rollback()
        return _failed_report(calendar_id, country_code, year, ImportFailure.DATABASE_ERROR)
    if not calendar_exists:
        return _failed_report(calendar_id, country_code, year, ImportFailure.UNKNOWN_CALENDAR)

    try:
        payload = _load_payload(country_code, year, transport)
        validated = _validate_payload(payload, country_code, year)
    except _ImportProblem as error:
        return _failed_report(calendar_id, country_code, year, error.failure)
    except httpx.TimeoutException:
        return _failed_report(calendar_id, country_code, year, ImportFailure.TIMEOUT)
    except httpx.HTTPError:
        return _failed_report(calendar_id, country_code, year, ImportFailure.UNREACHABLE)

    received_days = {row[0] for row in validated}
    retained = sorted(
        (row for row in validated if "Public" in row[3] and row[2]),
        key=lambda row: (row[0], row[1]),
    )
    if any(
        not row[1].strip()
        or len(row[1]) > 200
        or any(ord(char) < 32 or ord(char) == 127 for char in row[1])
        for row in retained
    ):
        return _failed_report(
            calendar_id, country_code, year, ImportFailure.PAYLOAD_INVALID, len(validated)
        )
    type_skipped = sum("Public" not in row[3] for row in validated)
    regional_skipped = sum("Public" in row[3] and not row[2] for row in validated)
    by_day: dict[date, tuple[date, str, bool, list[str]]] = {}
    for row in retained:
        by_day.setdefault(row[0], row)
    collapsed_duplicates = len(retained) - len(by_day)
    candidates = list(by_day.values())

    try:
        with session.begin_nested():
            inserted_days: set[date] = set()
            if candidates:
                statement = (
                    pg_insert(WorkingCalendarDay)
                    .values(
                        [
                            {
                                "id": uuid.uuid4(),
                                "calendar_id": calendar_id,
                                "day": day,
                                "kind": WorkingCalendarDayKind.NON_WORKING,
                                "source": "nager_date",
                                "name": name,
                                "country_code": country_code,
                                "year": year,
                            }
                            for day, name, _global, _types in candidates
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=["calendar_id", "day"])
                    .returning(WorkingCalendarDay.day)
                )
                inserted_days = set(session.execute(statement).scalars().all())

            rows_for_period = session.execute(
                sa.select(WorkingCalendarDay.day, WorkingCalendarDay.name)
                .where(
                    WorkingCalendarDay.calendar_id == calendar_id,
                    WorkingCalendarDay.source == "nager_date",
                    WorkingCalendarDay.country_code == country_code,
                    WorkingCalendarDay.year == year,
                )
                .order_by(WorkingCalendarDay.day)
            ).all()
            stale = tuple(
                StaleImportRow(day, name or "")
                for day, name in rows_for_period
                if day not in received_days
            )
            conflict_days = {day for day, *_ in candidates} - inserted_days
            candidate_by_day = {row[0]: row for row in candidates}
            conflict_rows = tuple(
                ConflictRow(
                    day=day,
                    incoming_kind=WorkingCalendarDayKind.NON_WORKING.value,
                    incoming_name=candidate_by_day[day][1],
                    incoming_source="nager_date",
                    incoming_country_code=country_code,
                    incoming_year=year,
                    existing_kind=kind.value,
                    existing_source=source,
                    existing_name=name,
                )
                for day, kind, source, name in session.execute(
                    sa.select(
                        WorkingCalendarDay.day,
                        WorkingCalendarDay.kind,
                        WorkingCalendarDay.source,
                        WorkingCalendarDay.name,
                    )
                    .where(
                        WorkingCalendarDay.calendar_id == calendar_id,
                        WorkingCalendarDay.day.in_(conflict_days),
                    )
                    .order_by(WorkingCalendarDay.day)
                ).all()
            )
            all_imported_countries = tuple(
                sorted(
                    session.execute(
                        sa.select(WorkingCalendarDay.country_code)
                        .where(
                            WorkingCalendarDay.calendar_id == calendar_id,
                            WorkingCalendarDay.source == "nager_date",
                        )
                        .distinct()
                    ).scalars()
                )
            )
            month_counts = Counter(day.strftime("%Y-%m") for day in inserted_days)
            report = HolidayImportReport(
                calendar_id=calendar_id,
                country_code=country_code,
                year=year,
                received_rows=len(validated),
                written=len(inserted_days),
                type_skipped=type_skipped,
                regional_skipped=regional_skipped,
                collapsed_duplicates=collapsed_duplicates,
                conflict_skipped=len(conflict_rows),
                conflicts=conflict_rows,
                stale_import_rows=stale,
                added_days_by_month=dict(sorted(month_counts.items())),
                mixed_countries=all_imported_countries if len(all_imported_countries) > 1 else (),
            )
        session.commit()
        return report
    except SQLAlchemyError:
        session.rollback()
        return _failed_report(
            calendar_id, country_code, year, ImportFailure.DATABASE_ERROR, len(validated)
        )
