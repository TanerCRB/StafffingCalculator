"""Import one Nager.Date country/year into an existing working calendar.

Run from ``backend/``::

    python -m scripts.import_public_holidays --calendar-id <uuid> --country-code PL --year 2026
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from app.data.holiday_import import import_public_holidays
from app.db.session import get_sessionmaker


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar-id")
    parser.add_argument("--country-code")
    parser.add_argument("--year")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    if not args.calendar_id or not args.country_code or not args.year:
        print(json.dumps({"failure": "invalid_argument", "succeeded": False}, sort_keys=True))
        print("Import failed: invalid_argument", file=sys.stderr)
        return 1
    try:
        calendar_id = uuid.UUID(args.calendar_id)
    except ValueError:
        print(json.dumps({"failure": "invalid_argument", "succeeded": False}, sort_keys=True))
        print("Import failed: invalid_argument", file=sys.stderr)
        return 1
    with get_sessionmaker()() as session:
        report = import_public_holidays(
            session, calendar_id, args.country_code, args.year
        )
    print(json.dumps(report.as_dict(), ensure_ascii=True, sort_keys=True))
    if not report.succeeded:
        print(f"Import failed: {report.failure.value}", file=sys.stderr)
        return 1
    if report.received_rows == 0:
        print("Nothing received.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
