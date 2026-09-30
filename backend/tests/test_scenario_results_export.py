"""SC-7-06 — result exports preserve the response tree and personnel-cost access gate."""

from __future__ import annotations

import base64
import re
import uuid
import zipfile
import zlib
from io import BytesIO
from xml.etree import ElementTree

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import scenario_results_export
from app.api.scenario_results_export import render_pdf, render_xlsx
from app.core.identity import Permission
from app.models import CatalogDefaultRate
from tests.conftest import caller_holding
from tests.test_personnel_cost import _approve
from tests.test_scenario_results import _ensure_statutory_bypass, _full_scenario, results_path

EVERYTHING = frozenset(Permission)
WITHOUT_PERSONNEL_COSTS_READ = EVERYTHING - {Permission.PERSONNEL_COSTS_READ}

_XLSX_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _workbook_strings(content: bytes) -> list[str]:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.itertext())
        for node in root.findall("x:si", _XLSX_NS)
    ]


def _xlsx_details(content: bytes) -> dict[str, tuple[str, str | None]]:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        strings = _workbook_strings(content)
        worksheet_names = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            ),
            key=lambda name: int(re.search(r"sheet(\d+)", name).group(1)),
        )
        roots = [ElementTree.fromstring(archive.read(name)) for name in worksheet_names]

    rows: dict[str, tuple[str, str | None]] = {}
    for root in roots:
        for row in root.findall(".//x:sheetData/x:row", _XLSX_NS):
            cells = {cell.attrib["r"][:1]: cell for cell in row.findall("x:c", _XLSX_NS)}
            path_cell = cells.get("A")
            if path_cell is None:
                continue

            def cell_value(cell: ElementTree.Element | None) -> str | None:
                if cell is None:
                    return None
                value = cell.findtext("x:v", namespaces=_XLSX_NS)
                if value is None:
                    return None
                if cell.attrib.get("t") == "s":
                    return strings[int(value)]
                return value

            path = cell_value(path_cell)
            kind = cell_value(cells.get("B"))
            value = cell_value(cells.get("C"))
            if path is not None and kind is not None and path != "field_path":
                rows[path] = (kind, value)
    return rows


def _xlsx_metadata(content: bytes) -> dict[str, str | None]:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        strings = _workbook_strings(content)
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))

    metadata: dict[str, str | None] = {}
    for row in root.findall(".//x:sheetData/x:row", _XLSX_NS):
        cells = {cell.attrib["r"][:1]: cell for cell in row.findall("x:c", _XLSX_NS)}
        key_cell, value_cell = cells.get("A"), cells.get("B")
        if key_cell is None or value_cell is None:
            continue
        key_index = int(key_cell.findtext("x:v", namespaces=_XLSX_NS) or "0")
        value_index = int(value_cell.findtext("x:v", namespaces=_XLSX_NS) or "0")
        metadata[strings[key_index]] = strings[value_index]
    return metadata


def _pdf_text(content: bytes) -> bytes:
    decoded: list[bytes] = []
    offset = 0
    while (marker := content.find(b"stream\n", offset)) >= 0:
        start = marker + len(b"stream\n")
        end = content.find(b"endstream", start)
        if end < 0:
            break
        stream = content[start:end]
        try:
            stream = stream.rstrip(b"\r\n")
            if stream.endswith(b"~>"):
                stream = base64.a85decode(stream[:-2])
            try:
                stream = zlib.decompress(stream)
            except zlib.error:
                pass
            decoded.append(stream)
        except ValueError:
            pass
        offset = end + len(b"endstream")
    return b"\n".join(decoded)


def _export(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID, suffix: str):
    return client.get(f"{results_path(project_id, scenario_id)}/export.{suffix}")


def test_xlsx_preserves_scalar_kinds_null_n_a_and_unicode() -> None:
    payload = {
        "scenario_id": "scenario-1",
        "scenario_status": "draft",
        "revenue": {"amount": "12.30", "state": "calculated"},
        "profit": "n/a",
        "genuine_null": None,
        "count": 2,
        "enabled": True,
        "label": "Zażółć",
        "empty_object": {},
    }

    rows = _xlsx_details(render_xlsx(payload, "2026-09-30T12:00:00Z"))

    assert rows["count"] == ("number", "2")
    assert rows["enabled"] == ("boolean", "1")
    assert rows["genuine_null"] == ("null", None)
    assert rows["profit"] == ("string", "n/a")
    assert rows["revenue.amount"] == ("string", "12.30")
    assert rows["label"] == ("string", "Zażółć")
    assert rows["empty_object"] == ("object", "{}")


def test_pdf_contains_identity_status_and_reversible_unicode_values() -> None:
    payload = {
        "scenario_id": "scenario-2",
        "scenario_status": "Draft",
        "label": "Zażółć",
    }

    content = render_pdf(payload, "2026-09-30T12:00:00Z")
    text = _pdf_text(content)

    assert content.startswith(b"%PDF-")
    assert b"scenario-2" in text
    assert b"scenario_status" in text and b"Draft" in text
    assert b"generated_at_utc" in text and b"2026-09-30T12:00:00Z" in text
    assert b"label" in text and b"Za\\\\u017c\\\\u00f3\\\\u0142\\\\u0107" in text


@pytest.mark.parametrize(
    ("suffix", "media_type"),
    [
        ("pdf", "application/pdf"),
        (
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    ],
)
def test_exports_include_scenario_identity_status_timestamp_and_result_fields(
    client: TestClient,
    db_session: Session,
    suffix: str,
    media_type: str,
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(db_session, name=f"Export-{suffix}")

    with caller_holding(*EVERYTHING):
        response = _export(client, project.id, scenario.id, suffix)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(media_type)
    assert response.headers["cache-control"] == "no-store"
    assert f"scenario-{scenario.id}-results.{suffix}" in response.headers[
        "content-disposition"
    ]

    if suffix == "xlsx":
        metadata = _xlsx_metadata(response.content)
        rows = _xlsx_details(response.content)
        assert metadata["scenario_id"] == str(scenario.id)
        assert metadata["scenario_status"] == "Draft"
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", metadata["generated_at_utc"] or ""
        )
        assert "revenue.amount" in rows
        assert rows["personnel_cost.amount"] == ("string", "12000.00")
        assert "personnel_cost.assumptions_used" in rows
        assert "profit" in rows
    else:
        text = _pdf_text(response.content)
        assert response.content.startswith(b"%PDF-")
        assert str(scenario.id).encode() in text
        assert b"scenario_status" in text and b"Draft" in text
        assert b"generated_at_utc" in text
        assert b"revenue.amount" in text
        assert b"personnel_cost.amount" in text and b"12000.00" in text
        assert b"assumptions_used" in text
        assert b"profit" in text


@pytest.mark.parametrize(
    ("suffix", "media_type"),
    [
        ("pdf", "application/pdf"),
        (
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    ],
)
@pytest.mark.parametrize("closed_gate", ["global_permission", "project_flag"])
def test_exports_omit_only_gated_personnel_cost_values_when_either_gate_is_closed(
    client: TestClient,
    db_session: Session,
    suffix: str,
    media_type: str,
    closed_gate: str,
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, _ = _full_scenario(
        db_session,
        name=f"ExportGate-{suffix}-{closed_gate}",
        cost_visible=closed_gate != "project_flag",
    )
    permissions = (
        WITHOUT_PERSONNEL_COSTS_READ if closed_gate == "global_permission" else EVERYTHING
    )

    with caller_holding(*permissions):
        response = _export(client, project.id, scenario.id, suffix)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(media_type)
    if suffix == "xlsx":
        rows = _xlsx_details(response.content)
        paths = set(rows)
        text_values = " ".join(value or "" for _, value in rows.values())
    else:
        text_values = _pdf_text(response.content).decode("ascii")
        paths = {
            path
            for path in (
                "personnel_cost.amount",
                "personnel_cost.assumptions_used",
                "personnel_cost.paid_absence_amount",
                "included_cost",
                "profit",
                "margin",
                "markup",
            )
            if f"({path})".encode() in text_values.encode()
        }

    assert not (
        paths
        & {
        "personnel_cost.amount",
        "personnel_cost.assumptions_used",
        "personnel_cost.paid_absence_amount",
        "included_cost",
        "profit",
        "margin",
        "markup",
        }
    )
    assert "12000.00" not in text_values
    assert "6000.00" not in text_values

    if suffix == "xlsx":
        assert "personnel_cost.state" in paths
        assert "personnel_cost.currency" in paths
        assert "revenue.amount" in paths
        assert "additional_cost.amount" in paths
    else:
        assert b"personnel_cost.state" in text_values.encode()
        assert b"personnel_cost.currency" in text_values.encode()
        assert b"revenue.amount" in text_values.encode()
        assert b"additional_cost.amount" in text_values.encode()


@pytest.mark.parametrize("suffix", ["pdf", "xlsx"])
def test_approved_exports_keep_snapshot_values_after_catalog_changes(
    client: TestClient,
    db_session: Session,
    suffix: str,
) -> None:
    _ensure_statutory_bypass(db_session)
    project, scenario, dimensions = _full_scenario(db_session, name=f"ApprovedExport-{suffix}")
    rate = db_session.scalar(
        sa.select(CatalogDefaultRate).where(
            CatalogDefaultRate.role_id == dimensions.role_id,
            CatalogDefaultRate.seniority_id == dimensions.seniority_id,
            CatalogDefaultRate.location_id == dimensions.location_id,
            CatalogDefaultRate.engagement_type_id == dimensions.engagement_type_id,
        )
    )
    assert rate is not None
    _approve(client, project.id, scenario.id)

    with caller_holding(*EVERYTHING):
        original = _export(client, project.id, scenario.id, suffix)
    assert original.status_code == 200, original.text

    db_session.execute(
        sa.update(CatalogDefaultRate)
        .where(CatalogDefaultRate.id == rate.id)
        .values(default_cost_rate="999.0000")
    )
    db_session.flush()
    db_session.expire_all()

    with caller_holding(*EVERYTHING):
        after_catalog_change = _export(client, project.id, scenario.id, suffix)
    assert after_catalog_change.status_code == 200, after_catalog_change.text

    if suffix == "xlsx":
        original_rows = _xlsx_details(original.content)
        changed_rows = _xlsx_details(after_catalog_change.content)
        assert _xlsx_metadata(original.content)["scenario_status"] == "Approved"
        assert _xlsx_metadata(after_catalog_change.content)["scenario_status"] == "Approved"
        assert original_rows["personnel_cost.amount"] == changed_rows["personnel_cost.amount"]
        assert original_rows["personnel_cost.amount"] == ("string", "12000.00")
    else:
        original_text = _pdf_text(original.content)
        changed_text = _pdf_text(after_catalog_change.content)
        assert b"scenario_status" in original_text and b"Approved" in original_text
        assert b"scenario_status" in changed_text and b"Approved" in changed_text
        assert b"personnel_cost.amount" in original_text and b"12000.00" in original_text
        assert b"personnel_cost.amount" in changed_text and b"12000.00" in changed_text
        assert b"999.0000" not in changed_text


def test_xlsx_rolls_detail_rows_to_additional_sheets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scenario_results_export, "MAX_XLSX_ROWS", 4)
    payload = {
        "scenario_id": "scenario-rollover",
        "scenario_status": "Draft",
        "values": [1, 2, 3, 4, 5],
    }

    content = render_xlsx(payload, "2026-09-30T12:00:00Z")

    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = [
            name
            for name in archive.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        ]
    assert len(names) >= 3
    rows = _xlsx_details(content)
    assert all(f"values[{index}]" in rows for index in range(5))



def test_pdf_paginates_large_nested_result_without_repeating_container_json() -> None:
    payload = {
        "scenario_id": "scenario-large-pdf",
        "scenario_status": "Draft",
        "assumptions": [{"label": f"assumption-{index}"} for index in range(1200)],
    }

    content = render_pdf(payload, "2026-09-30T12:00:00Z")
    text = _pdf_text(content)

    assert content.startswith(b"%PDF-")
    assert b"assumptions[1199].label" in text
    assert b"assumption-1199" in text



def test_walk_yields_container_before_iterating_its_children() -> None:
    class IterationProbe(list[int]):
        iterated = False

        def __iter__(self):
            self.iterated = True
            return super().__iter__()

    payload = IterationProbe([1, 2, 3])
    nodes = scenario_results_export._walk(payload, "items")

    assert next(nodes) == ("items", "array", payload)
    assert not payload.iterated
    assert next(nodes) == ("items[0]", "number", 1)
    assert payload.iterated
