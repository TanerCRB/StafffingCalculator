"""SC-1-11, K-06 — a copy of a scenario carries its delivery segments (ADR-0016; ADR-0004, addendum
2026-09-25 SC-1-11, point 4; AC-02).

The simplest cascade entry this repository has: a segment has no child or grandchild of its own
(ADR-0016, point 9), so `copy_scenario_delivery_segments` needs no old-to-new id mapping, unlike the
staffing or additional-cost aggregates. There is no endpoint over this table itself (ADR-0016, point
8), so every copy here goes through the one entry point an API caller has today,
`POST /projects/{id}/copy`, which drives the same `copy_scenario`/`SCENARIO_CHILD_COPIERS` mechanism
`app.data.scenario_duplication` (SC-6-01) will later reach a second way.
"""

import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.data import project_writes
from app.data.project_writes import SCENARIO_CHILD_COPIERS
from app.data.scenario_delivery_segment import (
    SEGMENT_COLUMNS_NOT_COPIED,
    copy_scenario_delivery_segments,
)
from app.models import Scenario, ScenarioDeliverySegment, ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    make_project,
    make_scenario,
    make_scenario_delivery_segment,
)

MAR = date(2026, 3, 1)
HEADERS = as_caller(IN_SCOPE_USER)

COPIED_SEGMENT_FIELDS = ("name",)
"""Spelled out here rather than derived from the model: the drift guard below compares the two, so
a column added to `scenario_delivery_segment` later fails it until somebody decides which side it is
on."""


def _copy_project(client: TestClient, project_id: uuid.UUID) -> uuid.UUID:
    response = client.post(f"/projects/{project_id}/copy", headers=HEADERS)
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def _only_scenario(session: Session, project_id: uuid.UUID) -> Scenario:
    session.expire_all()
    return session.execute(
        sa.select(Scenario).where(Scenario.project_id == project_id)
    ).scalar_one()


def _segments_of(session: Session, scenario_id: uuid.UUID) -> list[ScenarioDeliverySegment]:
    session.expire_all()
    return list(
        session.execute(
            sa.select(ScenarioDeliverySegment)
            .where(ScenarioDeliverySegment.scenario_id == scenario_id)
            .order_by(ScenarioDeliverySegment.name)
        ).scalars()
    )


def test_k_06_a_copy_carries_every_segment_with_a_new_id_and_the_same_name(
    client: TestClient, db_session: Session
) -> None:
    """K-06 — two segments on the source; the copy has two segments, neither sharing a source id,
    each with the source's own name, pointing at the copy's own `scenario_id`. The source is
    unchanged."""
    project = make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    discovery = make_scenario_delivery_segment(db_session, scenario, name="Discovery")
    build = make_scenario_delivery_segment(db_session, scenario, name="Build")
    source_ids = {discovery.id, build.id}

    copy = _only_scenario(db_session, _copy_project(client, project.id))
    copied = _segments_of(db_session, copy.id)

    assert [segment.name for segment in copied] == ["Build", "Discovery"]
    assert {segment.id for segment in copied}.isdisjoint(source_ids)
    assert {segment.scenario_id for segment in copied} == {copy.id}

    source_after = _segments_of(db_session, scenario.id)
    assert {segment.id for segment in source_after} == source_ids
    assert [segment.name for segment in source_after] == ["Build", "Discovery"]


def test_k_06_canary_removing_the_registry_entry_leaves_the_copy_with_no_segment(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K-06's canary — red on its own when `copy_scenario_delivery_segments` is taken out of
    `SCENARIO_CHILD_COPIERS`: the copy then has zero segments although the source has one."""
    project = make_project(db_session, name="No copier", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    make_scenario_delivery_segment(db_session, scenario, name="Discovery")

    monkeypatch.setattr(
        project_writes,
        "SCENARIO_CHILD_COPIERS",
        tuple(
            copier
            for copier in project_writes.SCENARIO_CHILD_COPIERS
            if copier is not copy_scenario_delivery_segments
        ),
    )

    copy = _only_scenario(db_session, _copy_project(client, project.id))
    assert _segments_of(db_session, copy.id) == [], (
        "the segment was copied even though its registry entry was removed — the test's mutation "
        "hook is not wired to the copy path actually used"
    )


def test_k_06_a_copy_of_an_approved_scenario_is_a_draft_with_its_segments_and_the_source_stays(
    client: TestClient, db_session: Session
) -> None:
    """K-06 with an **approved** source (ADR-0004): the copy is a draft carrying the segment; the
    source is still approved, with its own original row untouched."""
    project = make_project(db_session, name="Approved source", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", status=ScenarioStatus.APPROVED)
    source_segment = make_scenario_delivery_segment(db_session, scenario, name="Discovery")

    copy = _only_scenario(db_session, _copy_project(client, project.id))

    assert copy.status == ScenarioStatus.DRAFT
    copied = _segments_of(db_session, copy.id)
    assert len(copied) == 1
    assert copied[0].id != source_segment.id
    assert copied[0].name == "Discovery"
    assert db_session.get(Scenario, scenario.id).status == ScenarioStatus.APPROVED
    remaining_source = _segments_of(db_session, scenario.id)
    assert [segment.id for segment in remaining_source] == [source_segment.id]


def test_a_scenario_with_no_segment_copies_zero_segments(
    client: TestClient, db_session: Session
) -> None:
    """The uncontrived empty case: a draft with no segment at all copies cleanly to zero, not an
    error and not a phantom row."""
    project = make_project(db_session, name="No segment", accessible_to=(IN_SCOPE_USER,))
    make_scenario(db_session, project, name="Baseline")

    copy = _only_scenario(db_session, _copy_project(client, project.id))
    assert _segments_of(db_session, copy.id) == []


def test_the_segment_copier_is_a_registered_entry_of_the_cascade() -> None:
    """ADR-0004, addendum 2026-09-25 SC-1-11, point 4 — registered once, as its own entry."""
    assert SCENARIO_CHILD_COPIERS.count(copy_scenario_delivery_segments) == 1


def test_every_scenario_delivery_segment_column_is_either_copied_or_explicitly_excluded() -> None:
    """The drift guard of `app.data.column_copy` — a column added to `scenario_delivery_segment`
    later fails here until it is named as copied (and so compared in K-06) or excluded with a
    reason in `SEGMENT_COLUMNS_NOT_COPIED`."""
    mapped = {attribute.key for attribute in sa.inspect(ScenarioDeliverySegment).column_attrs}

    assert mapped == set(COPIED_SEGMENT_FIELDS) | SEGMENT_COLUMNS_NOT_COPIED
    assert set(COPIED_SEGMENT_FIELDS).isdisjoint(SEGMENT_COLUMNS_NOT_COPIED)
