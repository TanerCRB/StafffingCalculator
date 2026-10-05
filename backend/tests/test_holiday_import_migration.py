import os
import uuid
from datetime import date

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from tests.conftest import BACKEND_ROOT, make_working_calendar


def test_existing_calendar_day_gets_manual_provenance(database_url, engine):
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    with Session(engine) as session:
        calendar = make_working_calendar(session, name=f"B5 legacy {uuid.uuid4()}")
        calendar_id = calendar.id
        session.commit()

    command.downgrade(config, "a91c4e7d2b60")
    legacy_day_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO working_calendar_day (id, calendar_id, day, kind, created_at) "
                "VALUES (:id, :calendar_id, :day, 'non_working', now())"
            ),
            {"id": legacy_day_id, "calendar_id": calendar_id, "day": date(2026, 1, 2)},
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT source, name, country_code, year "
                "FROM working_calendar_day WHERE id=:id"
            ),
            {"id": legacy_day_id},
        ).one()
    assert row == ("manual", None, None, None)
