from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.holiday_import import (
    API_BASE_URL,
    REQUEST_HEADERS,
    REQUEST_TIMEOUT,
    ImportFailure,
    import_public_holidays,
)
from app.data.scenario_approval import _copy_calendar_days
from app.data.working_calendar import basis_by_location, frozen_basis_by_location
from app.domain.capacity import working_days_in_month
from app.models.approved_snapshot import (
    SNAPSHOT_TABLES,
    ApprovedSnapshotWorkingCalendarDay,
)
from app.models.catalog import (
    WorkingCalendar,
    WorkingCalendarDay,
    WorkingCalendarDayKind,
)
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    make_allocation,
    make_calendar_day,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
)


def _holiday(day: str, name: str, *, global_day: bool = True, types: list[str] | None = None):
    return {
        "date": day,
        "localName": name,
        "name": name,
        "countryCode": "PL",
        "fixed": False,
        "global": global_day,
        "counties": None,
        "launchYear": None,
        "types": types if types is not None else ["Public"],
    }


class RecordingTransport(httpx.BaseTransport):
    def __init__(self, handler):
        self.handler = handler

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self.handler(request)

    def close(self) -> None:
        pass


def _transport(payload: object, *, status: int = 200, headers: dict[str, str] | None = None):
    response_headers = {"Content-Type": "application/json", **(headers or {})}
    body = json.dumps(payload).encode()
    return RecordingTransport(
        lambda request: httpx.Response(
            status, headers=response_headers, stream=httpx.ByteStream(body)
        )
    )


def test_import_maps_rows_collapses_duplicates_and_preserves_manual_conflicts(db_session):
    calendar = make_working_calendar(db_session)
    make_calendar_day(
        db_session,
        calendar,
        day=date(2026, 1, 2),
        kind=WorkingCalendarDayKind.WORKING,
    )
    payload = [
        _holiday("2026-01-01", "New Year"),
        _holiday("2026-01-01", "Another New Year"),
        _holiday("2026-01-02", "Manual wins"),
        _holiday("2026-01-03", "Regional", global_day=False),
        _holiday("2026-01-04", "Observance", types=["Observance"]),
        _holiday(
            "2026-01-05",
            "Type filter has precedence",
            global_day=False,
            types=["Observance"],
        ),
    ]
    report = import_public_holidays(
        db_session,
        calendar.id,
        "PL",
        2026,
        transport=_transport(payload),
    )

    assert report.succeeded
    assert report.received_rows == 6
    assert (
        report.written,
        report.type_skipped,
        report.regional_skipped,
        report.collapsed_duplicates,
        report.conflict_skipped,
    ) == (1, 2, 1, 1, 1)
    assert (
        report.written
        + report.type_skipped
        + report.regional_skipped
        + report.collapsed_duplicates
        + report.conflict_skipped
        == report.received_rows
    )
    assert report.added_days_by_month == {"2026-01": 1}
    imported = db_session.execute(
        sa.select(WorkingCalendarDay).where(
            WorkingCalendarDay.calendar_id == calendar.id,
            WorkingCalendarDay.day == date(2026, 1, 1),
        )
    ).scalar_one()
    assert imported.kind is WorkingCalendarDayKind.NON_WORKING
    assert imported.source == "nager_date"
    assert imported.name == "Another New Year"
    assert imported.country_code == "PL"
    assert imported.year == 2026
    assert report.conflicts[0].existing_kind == WorkingCalendarDayKind.WORKING.value
    assert report.conflicts[0].existing_source == "manual"
    assert report.conflicts[0].incoming_name == "Manual wins"
    assert report.conflicts[0].incoming_country_code == "PL"
    assert report.conflicts[0].incoming_kind == WorkingCalendarDayKind.NON_WORKING.value


def test_import_is_idempotent_reports_stale_rows_and_uses_fixed_egress(
    db_session, monkeypatch
):
    calendar = make_working_calendar(db_session)
    seen: list[httpx.Request] = []
    client_options: dict[str, object] = {}
    original_client = httpx.Client

    def capture_client(*args, **kwargs):
        client_options.update(kwargs)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", capture_client)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(json.dumps([_holiday("2026-01-01", "New Year")]).encode()),
        )

    first = import_public_holidays(
        db_session, calendar.id, "PL", 2026, transport=RecordingTransport(handler)
    )
    second = import_public_holidays(
        db_session, calendar.id, "PL", 2026, transport=RecordingTransport(handler)
    )
    assert (first.written, second.written, second.conflict_skipped) == (1, 0, 1)
    assert str(seen[0].url) == f"{API_BASE_URL}/PublicHolidays/2026/PL"
    assert dict(seen[0].headers)["accept"] == "application/json"
    assert set(REQUEST_HEADERS) == {"Accept", "Accept-Encoding", "User-Agent"}
    assert REQUEST_TIMEOUT.connect == 3.0
    assert REQUEST_TIMEOUT.read == 15.0
    assert client_options["verify"] is True
    assert client_options["follow_redirects"] is False
    assert client_options["trust_env"] is False

    empty = import_public_holidays(
        db_session,
        calendar.id,
        "PL",
        2026,
        transport=RecordingTransport(lambda request: httpx.Response(204)),
    )
    assert empty.succeeded
    assert empty.received_rows == 0
    assert [(row.day, row.name) for row in empty.stale_import_rows] == [
        (date(2026, 1, 1), "New Year")
    ]


def test_cross_country_calendar_import_is_allowed_but_reported(db_session):
    calendar = make_working_calendar(db_session)
    first = import_public_holidays(
        db_session,
        calendar.id,
        "PL",
        2026,
        transport=_transport([_holiday("2026-01-01", "New Year")]),
    )
    german = _holiday("2026-01-02", "New Year")
    german["countryCode"] = "DE"
    second = import_public_holidays(
        db_session,
        calendar.id,
        "DE",
        2026,
        transport=_transport([german]),
    )
    assert first.succeeded and second.succeeded
    assert second.mixed_countries == ("DE", "PL")


def test_invalid_arguments_and_unknown_calendar_do_not_construct_http_client(
    db_session, monkeypatch
):
    def fail_client(*args, **kwargs):
        raise AssertionError("HTTP client must not be constructed")

    monkeypatch.setattr(httpx, "Client", fail_client)
    calendar = make_working_calendar(db_session)
    invalid = import_public_holidays(db_session, calendar.id, "pl", 2026)
    unknown = import_public_holidays(db_session, uuid.uuid4(), "PL", 2026)
    assert invalid.failure is ImportFailure.INVALID_ARGUMENT
    assert unknown.failure is ImportFailure.UNKNOWN_CALENDAR


def test_recorded_nager_pl_2026_response_is_the_ci_fixture(db_session):
    capture = json.loads(
        Path(__file__).with_name("nager_date_pl_2026.json").read_text(encoding="utf-8")
    )
    assert capture["capture_date"] == "2026-10-01"
    assert capture["source_url"] == f"{API_BASE_URL}/PublicHolidays/2026/PL"
    calendar = make_working_calendar(db_session)
    report = import_public_holidays(
        db_session,
        calendar.id,
        "PL",
        2026,
        transport=_transport(capture["response"]),
    )
    assert report.succeeded
    assert report.received_rows == report.written == 14


def test_invalid_late_payload_and_upstream_failures_write_nothing(db_session):
    calendar = make_working_calendar(db_session)
    invalid = import_public_holidays(
        db_session,
        calendar.id,
        "PL",
        2026,
        transport=_transport(
            [_holiday("2026-01-01", "Valid"), _holiday("2027-01-01", "Wrong year")]
        ),
    )
    status = import_public_holidays(
        db_session,
        calendar.id,
        "PL",
        2026,
        transport=_transport({"upstream": "secret"}, status=503),
    )
    rows = db_session.scalar(
        sa.select(sa.func.count()).select_from(WorkingCalendarDay).where(
            WorkingCalendarDay.calendar_id == calendar.id
        )
    )
    assert invalid.failure is ImportFailure.PAYLOAD_INVALID
    assert status.failure is ImportFailure.UNEXPECTED_STATUS
    assert "secret" not in json.dumps(status.as_dict())
    assert rows == 0


def test_redirect_and_oversized_response_are_named_failures(db_session):
    calendar = make_working_calendar(db_session)
    redirected = import_public_holidays(
        db_session,
        calendar.id,
        "PL",
        2026,
        transport=RecordingTransport(
            lambda request: httpx.Response(302, headers={"Location": "https://example.com"})
        ),
    )
    too_large = import_public_holidays(
        db_session,
        calendar.id,
        "PL",
        2026,
        transport=RecordingTransport(
            lambda request: httpx.Response(
                200, stream=httpx.ByteStream(b" " * 2_000_001)
            )
        ),
    )
    assert redirected.failure is ImportFailure.REDIRECT_REFUSED
    assert too_large.failure is ImportFailure.PAYLOAD_TOO_LARGE


def test_holiday_row_count_is_capped(db_session):
    calendar = make_working_calendar(db_session)
    too_many = import_public_holidays(
        db_session,
        calendar.id,
        "PL",
        2026,
        transport=_transport([{}] * 501),
    )
    assert too_many.failure is ImportFailure.PAYLOAD_TOO_LARGE


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (httpx.ConnectTimeout, ImportFailure.TIMEOUT),
        (httpx.ConnectError, ImportFailure.UNREACHABLE),
    ],
)
def test_transport_failures_are_named_and_do_not_write_rows(db_session, exception, expected):
    calendar = make_working_calendar(db_session)

    def fail(request: httpx.Request) -> httpx.Response:
        raise exception("upstream detail", request=request)

    report = import_public_holidays(
        db_session, calendar.id, "PL", 2026, transport=RecordingTransport(fail)
    )
    count = db_session.scalar(
        sa.select(sa.func.count()).select_from(WorkingCalendarDay).where(
            WorkingCalendarDay.calendar_id == calendar.id
        )
    )
    assert report.failure is expected
    assert "upstream detail" not in json.dumps(report.as_dict())
    assert count == 0


def test_malformed_json_is_rejected_without_writes(db_session):
    calendar = make_working_calendar(db_session)
    report = import_public_holidays(
        db_session,
        calendar.id,
        "PL",
        2026,
        transport=RecordingTransport(
            lambda request: httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                stream=httpx.ByteStream(b"{"),
            )
        ),
    )
    assert report.failure is ImportFailure.PAYLOAD_INVALID
    assert db_session.scalar(
        sa.select(sa.func.count()).select_from(WorkingCalendarDay).where(
            WorkingCalendarDay.calendar_id == calendar.id
        )
    ) == 0


def test_content_validation_applies_only_to_rows_that_would_be_imported(db_session):
    calendar = make_working_calendar(db_session)
    skipped = _holiday("2026-01-01", "  ", types=["Observance"])
    report = import_public_holidays(
        db_session,
        calendar.id,
        "PL",
        2026,
        transport=_transport([skipped]),
    )
    assert report.succeeded
    assert report.type_skipped == 1

    retained_but_invalid = _holiday("2026-01-02", "  ")
    invalid = import_public_holidays(
        db_session,
        calendar.id,
        "PL",
        2026,
        transport=_transport([retained_but_invalid]),
    )
    assert invalid.failure is ImportFailure.PAYLOAD_INVALID
    assert db_session.scalar(
        sa.select(sa.func.count()).select_from(WorkingCalendarDay).where(
            WorkingCalendarDay.calendar_id == calendar.id
        )
    ) == 0


def test_database_checks_provenance_without_service_validation(db_session):
    calendar = make_working_calendar(db_session)
    invalid_rows = [
        {
            "source": "unknown",
            "name": None,
            "country_code": None,
            "year": None,
            "day": date(2026, 1, 1),
        },
        {
            "source": "manual",
            "name": "must be imported",
            "country_code": None,
            "year": None,
            "day": date(2026, 1, 2),
        },
        {
            "source": "nager_date",
            "name": "Wrong year",
            "country_code": "PL",
            "year": 2025,
            "day": date(2026, 1, 3),
        },
    ]
    for values in invalid_rows:
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                db_session.execute(
                    sa.insert(WorkingCalendarDay).values(
                        id=uuid.uuid4(),
                        calendar_id=calendar.id,
                        kind=WorkingCalendarDayKind.NON_WORKING,
                        **values,
                    )
                )


def test_imported_rows_feed_capacity_without_http_and_keep_snapshot_provenance(
    db_session, monkeypatch
):
    calendar = make_working_calendar(db_session)
    dims = make_dimension_tuple(db_session, calendar=calendar)
    project = make_project(db_session, name=f"Holiday snapshot {uuid.uuid4()}")
    scenario = make_scenario(db_session, project, name="Draft")
    make_staffing_position(db_session, scenario, dims)
    report = import_public_holidays(
        db_session,
        calendar.id,
        "PL",
        2026,
        transport=_transport([_holiday("2026-01-01", "New Year")]),
    )
    assert report.written == 1

    def fail_client(*args, **kwargs):
        raise AssertionError("calculation path attempted HTTP")

    monkeypatch.setattr(httpx, "Client", fail_client)
    monkeypatch.setattr(httpx, "get", fail_client)
    basis = basis_by_location(db_session, [dims.location_id])[dims.location_id]
    assert working_days_in_month(basis, date(2026, 1, 1)) == 21

    db_session.execute(_copy_calendar_days(scenario.id))
    snapshot = db_session.execute(
        sa.select(ApprovedSnapshotWorkingCalendarDay).where(
            ApprovedSnapshotWorkingCalendarDay.scenario_id == scenario.id
        )
    ).scalar_one()
    assert (snapshot.source, snapshot.name, snapshot.country_code, snapshot.year) == (
        "nager_date",
        "New Year",
        "PL",
        2026,
    )
    assert ApprovedSnapshotWorkingCalendarDay.__table__.c.source.server_default is None
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(
                sa.insert(ApprovedSnapshotWorkingCalendarDay).values(
                    id=uuid.uuid4(),
                    scenario_id=scenario.id,
                    source_calendar_id=calendar.id,
                    day=date(2026, 1, 2),
                    kind=WorkingCalendarDayKind.NON_WORKING,
                )
            )


def test_import_changes_draft_capacity_but_not_approved_snapshot(
    committing_client, engine
):
    with Session(engine) as setup:
        calendar = make_working_calendar(
            setup, name=f"Holiday gate {uuid.uuid4()}", standard_hours_per_day=Decimal("7.50")
        )
        make_calendar_day(
            setup,
            calendar,
            day=date(2026, 1, 2),
            kind=WorkingCalendarDayKind.NON_WORKING,
        )
        dimensions = make_dimension_tuple(setup, calendar=calendar)
        project = make_project(
            setup, name=f"Holiday snapshot {uuid.uuid4()}", accessible_to=(IN_SCOPE_USER,)
        )
        approved = make_scenario(
            setup,
            project,
            name="Approved before import",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            working_calendar="1111100",
            full_time_hours_per_week=Decimal("37.50"),
            currency="PLN",
        )
        draft = make_scenario(
            setup,
            project,
            name="Draft before import",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            working_calendar="1111100",
            full_time_hours_per_week=Decimal("37.50"),
            currency="PLN",
        )
        for scenario in (approved, draft):
            position = make_staffing_position(
                setup,
                scenario,
                dimensions,
                headcount=1,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
            )
            make_allocation(setup, position, period_month=date(2026, 1, 1))
        approved_id = approved.id
        draft_id = draft.id
        project_id = project.id
        calendar_id = calendar.id
        location_id = dimensions.location_id
        setup.commit()

    response = committing_client.post(
        approve_path(project_id, approved_id), headers=as_caller(IN_SCOPE_USER)
    )
    assert response.status_code == 200, response.text

    def snapshot_contents(session: Session) -> dict[str, list[tuple[object, ...]]]:
        contents: dict[str, list[tuple[object, ...]]] = {}
        for table_name in SNAPSHOT_TABLES:
            table = sa.Table(table_name, sa.MetaData(), autoload_with=engine)
            rows = session.execute(
                sa.select(table)
                .where(table.c.scenario_id == approved_id)
                .order_by(table.c.id)
            ).all()
            contents[table_name] = [tuple(row) for row in rows]
        return contents

    with Session(engine) as session:
        before_snapshot = snapshot_contents(session)
        before_frozen = frozen_basis_by_location(session, approved_id)[location_id]
        before_frozen_days = working_days_in_month(before_frozen, date(2026, 1, 1))
        before_live = basis_by_location(session, [location_id])[location_id]
        before_draft_days = working_days_in_month(before_live, date(2026, 1, 1))
        assert before_draft_days == before_frozen_days == 21

    with Session(engine) as session:
        report = import_public_holidays(
            session,
            calendar_id,
            "PL",
            2026,
            transport=_transport([_holiday("2026-01-01", "New Year")]),
        )
        assert report.written == 1

    with Session(engine) as session:
        after_snapshot = snapshot_contents(session)
        after_frozen = frozen_basis_by_location(session, approved_id)[location_id]
        after_frozen_days = working_days_in_month(after_frozen, date(2026, 1, 1))
        after_draft = basis_by_location(session, [location_id])[location_id]
        after_draft_days = working_days_in_month(after_draft, date(2026, 1, 1))
        assert after_snapshot == before_snapshot
        assert after_frozen_days == before_frozen_days == 21
        assert after_draft_days == before_draft_days - 1 == 20
        assert draft_id != approved_id


def test_two_interleaved_imports_use_database_conflict_handling(engine):
    calendar_id = uuid.uuid4()
    with Session(engine) as setup:
        setup.add(
            WorkingCalendar(
                id=calendar_id,
                name=f"Concurrent {calendar_id}",
                standard_hours_per_day=7.5,
                week_pattern="1111100",
            )
        )
        setup.commit()

    barrier = threading.Barrier(2)

    def run_import() -> object:
        def handler(request: httpx.Request) -> httpx.Response:
            barrier.wait(timeout=15)
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                stream=httpx.ByteStream(
                    json.dumps([_holiday("2026-01-01", "New Year")]).encode()
                ),
            )

        with Session(engine) as session:
            return import_public_holidays(
                session,
                calendar_id,
                "PL",
                2026,
                transport=RecordingTransport(handler),
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = list(executor.map(lambda _: run_import(), range(2)))
        assert first.succeeded and second.succeeded
        assert first.written + second.written == 1
        assert first.conflict_skipped + second.conflict_skipped == 1
        with Session(engine) as verify:
            assert verify.scalar(
                sa.select(sa.func.count()).select_from(WorkingCalendarDay).where(
                    WorkingCalendarDay.calendar_id == calendar_id,
                    WorkingCalendarDay.day == date(2026, 1, 1),
                )
            ) == 1
    finally:
        with engine.begin() as connection:
            connection.execute(
                sa.delete(WorkingCalendarDay).where(WorkingCalendarDay.calendar_id == calendar_id)
            )
            connection.execute(
                sa.delete(WorkingCalendar).where(WorkingCalendar.id == calendar_id)
            )
