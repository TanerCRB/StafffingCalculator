"""Render the existing whole-scenario result payload as PDF or XLSX (SC-7-06).

This module does not read data or calculate values. Callers pass the payload already produced by
`response_shaping.shape_scenario_results_for_export`, so exports inherit its status snapshots and
field authorization decisions.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from io import BytesIO
from itertools import zip_longest
from typing import Any

import xlsxwriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas

MAX_XLSX_ROWS = 1_048_576


def _json_text(value: Any) -> str:
    """Canonical ASCII JSON, preserving Unicode values as reversible ``\\u`` escapes.

    ReportLab's built-in Courier/Helvetica fonts do not guarantee Polish glyph coverage. Escaping
    non-ASCII values keeps the PDF renderable on every deployment without relying on an OS font;
    the spreadsheet retains the original Unicode strings.
    """
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _walk(value: Any, path: str = "") -> Iterator[tuple[str, str, Any]]:
    """Yield nodes lazily so large arrays do not need a duplicate child list."""
    if isinstance(value, Mapping):
        if path:
            yield path, "object", value
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _walk(child, child_path)
        return
    if isinstance(value, list):
        if path:
            yield path, "array", value
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")
        return
    if value is None:
        kind = "null"
    elif isinstance(value, bool):
        kind = "boolean"
    elif isinstance(value, (int, float)):
        kind = "number"
    else:
        kind = "string"
    if path:
        yield path, kind, value


def render_xlsx(payload: dict[str, Any], generated_at: str) -> bytes:
    """Create an XLSX with typed scalar cells and paths for nested containers."""
    output = BytesIO()
    with xlsxwriter.Workbook(output, {"in_memory": True}) as workbook:
        header = workbook.add_format({"bold": True, "bg_color": "#D9EAF7", "border": 1})
        metadata = workbook.add_worksheet("Export metadata")
        metadata.write_row(0, 0, ["field", "value"], header)
        metadata_rows = [
            ("scenario_id", payload["scenario_id"]),
            ("scenario_status", payload["scenario_status"]),
            ("generated_at_utc", generated_at),
        ]
        for row_index, (key, value) in enumerate(metadata_rows, start=1):
            metadata.write_string(row_index, 0, key)
            metadata.write_string(row_index, 1, str(value))
        metadata.set_column(0, 0, 24)
        metadata.set_column(1, 1, 40)
        metadata.freeze_panes(1, 0)

        details = None
        row_index = MAX_XLSX_ROWS
        sheet_number = 0
        for path, kind, value in _walk(payload):
            if row_index >= MAX_XLSX_ROWS:
                sheet_number += 1
                sheet_name = (
                    "Scenario results"
                    if sheet_number == 1
                    else f"Scenario results {sheet_number}"
                )
                details = workbook.add_worksheet(sheet_name)
                details.write_row(0, 0, ["field_path", "json_type", "json_value"], header)
                details.set_column(0, 0, 58)
                details.set_column(1, 1, 14)
                details.set_column(2, 2, 72)
                details.freeze_panes(1, 0)
                row_index = 1

            # Explicit writes preserve scalar cell types and prevent formula interpretation.
            details.write_string(row_index, 0, path)
            details.write_string(row_index, 1, kind)
            if value is None:
                details.write_blank(row_index, 2, None)
            elif isinstance(value, bool):
                details.write_boolean(row_index, 2, value)
            elif isinstance(value, (int, float)):
                details.write_number(row_index, 2, value)
            elif isinstance(value, (Mapping, list)):
                if value:
                    details.write_blank(row_index, 2, None)
                else:
                    details.write_string(row_index, 2, _json_text(value))
            else:
                details.write_string(row_index, 2, str(value))
            row_index += 1

        if details is not None:
            details.autofilter(0, 0, row_index - 1, 2)
    return output.getvalue()


def _text_chunks(value: str, width: int) -> Iterator[str]:
    """Wrap ASCII text without changing characters or retaining a second copy."""
    if not value:
        yield ""
        return
    for start in range(0, len(value), width):
        yield value[start : start + width]


def render_pdf(payload: dict[str, Any], generated_at: str) -> bytes:
    """Create a paginated PDF summary while consuming result nodes one at a time."""
    output = BytesIO()
    page_size = landscape(A4)
    page_width, page_height = page_size
    pdf = Canvas(output, pagesize=page_size, pageCompression=1)
    pdf.setTitle(f"Scenario results {payload['scenario_id']}")

    margin = 0.45 * inch
    font_size = 7
    line_height = 9
    char_width = font_size * 0.6  # Courier is fixed-width.
    field_width, type_width = 48, 12
    value_width = int((page_width - 2 * margin) / char_width) - field_width - type_width - 4
    type_x = margin + (field_width + 2) * char_width
    value_x = type_x + (type_width + 2) * char_width

    def draw_page_header(first_page: bool) -> float:
        y = page_height - margin
        if first_page:
            pdf.setFont("Helvetica-Bold", 16)
            pdf.drawString(margin, y, "Scenario results")
            y -= 20
            pdf.setFont("Helvetica", 9)
            for label, value in (
                ("scenario_id", payload["scenario_id"]),
                ("scenario_status", payload["scenario_status"]),
                ("generated_at_utc", generated_at),
            ):
                pdf.drawString(margin, y, f"{label}: {value}")
                y -= 13
            y -= 5
        pdf.setFillColor(colors.HexColor("#D9EAF7"))
        pdf.rect(margin, y - 3, page_width - 2 * margin, 13, fill=1, stroke=0)
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 7)
        pdf.drawString(margin, y, "Field path")
        pdf.drawString(type_x, y, "JSON type")
        pdf.drawString(value_x, y, "Value (JSON)")
        pdf.setFont("Courier", font_size)
        return y - 15

    y = draw_page_header(first_page=True)
    for path, kind, value in _walk(payload):
        # Non-empty containers are represented by typed paths and descendant rows. Repeating
        # their complete JSON would create a large unsplittable cell and duplicate all leaf data.
        json_value = (
            ""
            if isinstance(value, (Mapping, list)) and value
            else _json_text(value)
        )
        lines = zip_longest(
            _text_chunks(path, field_width),
            _text_chunks(kind, type_width),
            _text_chunks(json_value, value_width),
            fillvalue="",
        )
        first_line = True
        for path_part, kind_part, value_part in lines:
            if y - line_height < margin:
                pdf.showPage()
                y = draw_page_header(first_page=False)
            pdf.drawString(margin, y, path_part)
            if first_line:
                pdf.drawString(type_x, y, kind_part)
            pdf.drawString(value_x, y, value_part)
            y -= line_height
            first_line = False

    pdf.save()
    return output.getvalue()
