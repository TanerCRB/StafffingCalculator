"""SC-4-02, K-06 (database half) — the shape of a Fixed Price rule is enforced by the database.

Every write here is raw SQL or the ORM, never a request schema: the claim of K-06 is that the
**database** refuses the wrong row, so a path through Pydantic would prove only that Pydantic
refused first. Every refusal is asserted **by constraint name** (and SQLSTATE), so it cannot be
some other constraint that happened to fail.

- the composite foreign key, in both directions: a Fixed Price details row of a T&M rule, and a T&M
  details row of a Fixed Price rule — each with its accepted contrast;
- `CHECK (agreed_price >= 0)` — `-0.0001` refused, `0` accepted (D-5: AC-05 reachable);
- the names of the existing constraints survive the migration that widened the discriminator
  (`ck_commercial_terms_model_type_known`, `ck_tm_terms_model_type_is_tm`,
  `fk_tm_terms_commercial_terms_model_type`, `uq_commercial_terms_id_model_type`), and the widened
  CHECK still refuses a model with no details table;
- the migration runs both ways.

Real PostgreSQL, real migration (ADR-0001).
"""

import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.commercial_terms import (
    DETAIL_COLUMNS_NOT_COPIED_BY_MODEL,
    DETAIL_TABLE_BY_MODEL,
    REVENUE_BY_MODEL,
)
from app.models.commercial_terms import (
    AGREED_PRICE_NON_NEGATIVE_EXPRESSION,
    FIXED_PRICE_MODEL_TYPE_EXPRESSION,
    FIXED_PRICE_TYPE_AGREEMENT_FOREIGN_KEY,
    MODEL_TYPE_KNOWN_EXPRESSION,
    MODEL_TYPES,
    TYPE_AGREEMENT_FOREIGN_KEY,
)
from tests.conftest import (
    BACKEND_ROOT,
    IN_SCOPE_USER,
    make_commercial_terms,
    make_fixed_price_terms,
    make_outcome_terms,
    make_project,
    make_scenario,
    make_story_points_terms,
)

MIGRATION_REVISION = "b8f2d6a41c93"
PREVIOUS_REVISION = "c4d7e2a9b1f6"
"""The single head of `main` this migration was re-parented onto at the sync of 2026-09-28
(SC-2-06). It does not touch the discriminator CHECK; the list `downgrade` restores is the one the
latest CHECK-shaping migration before this one created (`PREVIOUS_CHECK_MIGRATION_PATH`)."""
MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "b8f2d6a41c93_create_fixed_price_terms.py"
)
PREVIOUS_CHECK_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "b9e3c7a1f264_create_outcome_terms_and_widen_the_model_type_check.py"
)


def _migration(path: Path = MIGRATION_PATH, name: str = "sc_4_02_migration") -> ModuleType:
    """Import the migration by path — `migrations/versions` is not a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sqlstate_and_constraint(error: IntegrityError) -> tuple[str | None, str | None]:
    diagnostics = error.orig.diag  # type: ignore[union-attr]
    return diagnostics.sqlstate, diagnostics.constraint_name


def _scenario(session: Session, name: str = "Baseline"):
    project = make_project(session, name=f"Aurora {name}", accessible_to=(IN_SCOPE_USER,))
    return make_scenario(session, project, name=name)


def _insert_fixed_price_details(
    session: Session, rule_id: object, *, price: str = "150000", model_type: str = "fixed_price"
) -> None:
    session.execute(
        sa.text(
            "INSERT INTO fixed_price_terms"
            " (commercial_terms_id, model_type, agreed_price, currency)"
            " VALUES (:id, :model_type, CAST(:price AS numeric), 'PLN')"
        ),
        {"id": rule_id, "model_type": model_type, "price": price},
    )


# --- K-06: the composite foreign key, both directions ---------------------------------------------


def test_k_06_a_fixed_price_details_row_of_a_tm_rule_is_refused_by_the_composite_foreign_key(
    db_session: Session,
) -> None:
    """K-06 — `fixed_price_terms` pointing at a T&M rule is refused by
    `fk_fixed_price_terms_commercial_terms_model_type`.

    Contrast first, same transaction: a Fixed Price details row of a Fixed Price rule is accepted.
    Mutation named by the criterion: the foreign key on `commercial_terms_id` alone — the mismatched
    row is then accepted and `pytest.raises` fails.
    """
    fixed_price_rule = make_fixed_price_terms(db_session, _scenario(db_session), agreed_price=None)
    _insert_fixed_price_details(db_session, fixed_price_rule.id)  # the contrast: accepted

    tm_rule = make_commercial_terms(db_session, _scenario(db_session, "T&M"), with_details=False)
    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            _insert_fixed_price_details(db_session, tm_rule.id)

    assert _sqlstate_and_constraint(refused.value) == (
        "23503",
        FIXED_PRICE_TYPE_AGREEMENT_FOREIGN_KEY,
    )


def test_k_06_a_tm_details_row_of_a_fixed_price_rule_is_refused_by_the_tm_foreign_key(
    db_session: Session,
) -> None:
    """K-06, the other direction (FP-2) — now reachable without staging a schema change: a real
    Fixed Price rule exists, and a `tm_terms` row pointing at it is refused by the **existing**
    `fk_tm_terms_commercial_terms_model_type`, which survived the migration unchanged.

    Contrast: the same `tm_terms` insert for a T&M rule is accepted.
    """
    tm_rule = make_commercial_terms(db_session, _scenario(db_session, "T&M"), with_details=False)
    db_session.execute(
        sa.text(
            "INSERT INTO tm_terms (commercial_terms_id, model_type)"
            " VALUES (:id, 'time_and_material')"
        ),
        {"id": tm_rule.id},
    )  # the contrast: accepted

    fixed_price_rule = make_fixed_price_terms(db_session, _scenario(db_session), agreed_price=None)
    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "INSERT INTO tm_terms (commercial_terms_id, model_type)"
                    " VALUES (:id, 'time_and_material')"
                ),
                {"id": fixed_price_rule.id},
            )

    assert _sqlstate_and_constraint(refused.value) == ("23503", TYPE_AGREEMENT_FOREIGN_KEY)


def test_k_06_a_fixed_price_details_row_cannot_claim_another_model(db_session: Session) -> None:
    """The neighbouring refusal: a `fixed_price_terms` row saying `time_and_material` — which would
    agree with a T&M rule by *claiming* to be one — is refused by its own CHECK, by name."""
    tm_rule = make_commercial_terms(db_session, _scenario(db_session), with_details=False)

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            _insert_fixed_price_details(db_session, tm_rule.id, model_type="time_and_material")

    assert _sqlstate_and_constraint(refused.value) == (
        "23514",
        "ck_fixed_price_terms_model_type_is_fixed_price",
    )


def test_k_06_both_type_agreement_keys_are_composite_in_the_catalog_of_the_database(
    db_session: Session,
) -> None:
    """K-06, structurally — each foreign key spans both columns on both sides, as migrated."""
    rows = db_session.execute(
        sa.text(
            "SELECT c.conname, c.conrelid::regclass::text, c.confrelid::regclass::text,"
            " ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(n, i)"
            "       JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n"
            "       ORDER BY k.i)::text[],"
            " ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(n, i)"
            "       JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.n"
            "       ORDER BY k.i)::text[]"
            " FROM pg_constraint c WHERE c.conname = ANY(:names) AND c.contype = 'f'"
        ),
        {"names": [TYPE_AGREEMENT_FOREIGN_KEY, FIXED_PRICE_TYPE_AGREEMENT_FOREIGN_KEY]},
    ).all()
    found = {row[0]: tuple(row[1:3]) + (list(row[3]), list(row[4])) for row in rows}

    assert found == {
        TYPE_AGREEMENT_FOREIGN_KEY: (
            "tm_terms",
            "commercial_terms",
            ["commercial_terms_id", "model_type"],
            ["id", "model_type"],
        ),
        FIXED_PRICE_TYPE_AGREEMENT_FOREIGN_KEY: (
            "fixed_price_terms",
            "commercial_terms",
            ["commercial_terms_id", "model_type"],
            ["id", "model_type"],
        ),
    }


# --- K-06: the price's lower bound (D-5) ----------------------------------------------------------


def test_k_06_a_negative_agreed_price_is_refused_by_the_check_and_zero_is_accepted(
    db_session: Session,
) -> None:
    """K-06 / D-5 — `-0.0001` refused by `ck_fixed_price_terms_agreed_price_non_negative`; `0` is
    accepted (AC-05: a zero revenue is reachable for Fixed Price). Mutation: `> 0` — the zero
    contrast fails; the CHECK removed — the negative row is accepted."""
    zero_rule = make_fixed_price_terms(db_session, _scenario(db_session, "Zero"), agreed_price=None)
    _insert_fixed_price_details(db_session, zero_rule.id, price="0")  # accepted

    negative_rule = make_fixed_price_terms(
        db_session, _scenario(db_session, "Negative"), agreed_price=None
    )
    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            _insert_fixed_price_details(db_session, negative_rule.id, price="-0.0001")

    assert _sqlstate_and_constraint(refused.value) == (
        "23514",
        "ck_fixed_price_terms_agreed_price_non_negative",
    )


def test_k_06_the_price_and_its_currency_are_required_and_the_currency_is_an_upper_case_code(
    db_session: Session,
) -> None:
    """The price columns are `NOT NULL`, and the currency carries the catalogue's two rules — a
    Fixed Price details row without a price or currency is unwritable by any path, not only the
    API."""
    rule = make_fixed_price_terms(db_session, _scenario(db_session), agreed_price=None)

    refusals = []
    for statement in (
        "INSERT INTO fixed_price_terms (commercial_terms_id, currency) VALUES (:id, 'PLN')",
        "INSERT INTO fixed_price_terms (commercial_terms_id, agreed_price) VALUES (:id, 1)",
        "INSERT INTO fixed_price_terms (commercial_terms_id, agreed_price, currency)"
        " VALUES (:id, 1, 'pln')",
        "INSERT INTO fixed_price_terms (commercial_terms_id, agreed_price, currency)"
        " VALUES (:id, 1, 'PL')",
    ):
        with pytest.raises(IntegrityError) as refused:
            with db_session.begin_nested():
                db_session.execute(sa.text(statement), {"id": rule.id})
        refusals.append(_sqlstate_and_constraint(refused.value))

    assert refusals[:2] == [("23502", None), ("23502", None)]
    assert refusals[2:] == [
        ("23514", "ck_fixed_price_terms_currency_is_upper"),
        ("23514", "ck_fixed_price_terms_currency_iso4217"),
    ]


# --- K-06: the existing names survive, the widened CHECK still closes the discriminator -----------


def test_k_06_the_names_of_the_existing_constraints_survive_the_migration(
    db_session: Session,
) -> None:
    """K-06 — every constraint SC-4-01 created on the rule and on `tm_terms` exists at head under
    its original name, and the discriminator CHECK — dropped and re-added in the migration — is the
    widened one. A migration that re-created the CHECK under an autogenerated name (or left the old
    one next to a new one) fails here."""
    rows = dict(
        db_session.execute(
            sa.text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conrelid IN ('commercial_terms'::regclass, 'tm_terms'::regclass)"
            )
        ).all()
    )

    assert {
        "pk_commercial_terms",
        "fk_commercial_terms_scenario_id",
        # SC-4-05 (sync of 2026-09-28): `uq_commercial_terms_scenario_id` is now a partial unique
        # *index* (not a `pg_constraint` row — asserted by name below), and `scope_ref` brought its
        # composite foreign key.
        "fk_commercial_terms_scope_ref_scenario_id",
        "uq_commercial_terms_id_model_type",
        "ck_commercial_terms_model_type_known",
        "pk_tm_terms",
        "ck_tm_terms_model_type_is_tm",
        "fk_tm_terms_commercial_terms_model_type",
    } == set(rows)
    indexes = set(
        db_session.execute(
            sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'commercial_terms'")
        ).scalars()
    )
    assert "uq_commercial_terms_scenario_id" in indexes
    known = rows["ck_commercial_terms_model_type_known"]
    assert "'time_and_material'" in known and "'fixed_price'" in known
    # SC-4-04's and SC-4-03's values survive this widening: the CHECK grows, it is not replaced by a
    # narrower one.
    assert "'story_points'" in known
    assert "'outcome_based'" in known
    assert "'time_and_material'" in rows["ck_tm_terms_model_type_is_tm"]
    assert "'fixed_price'" not in rows["ck_tm_terms_model_type_is_tm"]


def test_k_06_the_widened_discriminator_still_refuses_a_model_without_a_details_table(
    db_session: Session,
) -> None:
    """`ck_commercial_terms_model_type_known` admits the four models with a details table and
    nothing else (ADR-0003, point 2) — all four accepted as contrast, `not_a_model` (a value that
    will never be a model; until the sync of 2026-09-28 this was `outcome_based`, real since
    SC-4-03) refused by name."""
    make_commercial_terms(db_session, _scenario(db_session, "T&M"), with_details=False)
    make_story_points_terms(db_session, _scenario(db_session, "SP"), with_details=False)
    make_outcome_terms(db_session, _scenario(db_session, "OB"), with_details=False)
    make_fixed_price_terms(db_session, _scenario(db_session, "FP"), agreed_price=None)

    other = _scenario(db_session, "Unknown model")
    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "INSERT INTO commercial_terms (id, scenario_id, model_type)"
                    " VALUES (gen_random_uuid(), :scenario_id, 'not_a_model')"
                ),
                {"scenario_id": other.id},
            )

    assert _sqlstate_and_constraint(refused.value) == (
        "23514",
        "ck_commercial_terms_model_type_known",
    )


# --- drift guards ---------------------------------------------------------------------------------


def test_the_newest_discriminator_migration_and_the_model_agree_on_every_sql_expression() -> None:
    """The drift guard of the **newest** migration shaping `ck_commercial_terms_model_type_known` —
    one rule for block 4 (human decision, Issue #66, 2026-09-25): the model's discriminator CHECK
    equals the copy in the migration that last widened it (today `b8f2d6a41c93`; the same agreement
    `test_commercial_terms_schema.py` asserts through `LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH`). The
    next model's task moves this guard to its own migration; older migrations are pinned to the
    literal they created
    (`test_commercial_terms_schema.py::test_the_model_and_the_migration_agree_…` for `e7b41c9d2a58`,
    `test_story_points_terms.py::test_the_model_and_the_d2f6a91c4b58_…` for `d2f6a91c4b58`,
    `test_outcome_terms_schema.py::test_the_model_and_the_outcome_migration_agree_…` for
    `b9e3c7a1f264`). The list `downgrade` restores is exactly the one `b9e3c7a1f264` created.

    Also: the two new CHECKs and the foreign key name are spelled once each on both sides."""
    migration = _migration()
    previous = _migration(PREVIOUS_CHECK_MIGRATION_PATH, "sc_4_03_migration_for_sc_4_02")

    assert migration._MODEL_TYPE_KNOWN_EXPRESSION == MODEL_TYPE_KNOWN_EXPRESSION
    assert migration._PREVIOUS_MODEL_TYPE_KNOWN_EXPRESSION == previous._MODEL_TYPE_KNOWN_EXPRESSION
    assert migration._FIXED_PRICE_MODEL_TYPE_EXPRESSION == FIXED_PRICE_MODEL_TYPE_EXPRESSION
    assert migration._AGREED_PRICE_NON_NEGATIVE_EXPRESSION == AGREED_PRICE_NON_NEGATIVE_EXPRESSION
    assert migration._TYPE_AGREEMENT_FOREIGN_KEY == FIXED_PRICE_TYPE_AGREEMENT_FOREIGN_KEY


def test_every_model_has_a_details_table_a_revenue_branch_and_a_copy_rule() -> None:
    """ADR-0003, points 2 and 9, with the copier's registry (SC-4-02): the four registries agree."""
    assert (
        set(REVENUE_BY_MODEL)
        == set(MODEL_TYPES)
        == set(DETAIL_TABLE_BY_MODEL)
        == set(DETAIL_COLUMNS_NOT_COPIED_BY_MODEL)
        == {"time_and_material", "story_points", "outcome_based", "fixed_price"}
    )


# --- the migration runs both ways -----------------------------------------------------------------


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_the_fixed_price_migration_downgrades_and_upgrades_again(
    engine: Engine, alembic_config: Config
) -> None:
    """`downgrade()` of `b8f2d6a41c93` really runs: the table goes, the discriminator narrows back
    to what `b9e3c7a1f264` left — `time_and_material`, `story_points` and `outcome_based` — under
    the same name, and `upgrade` restores both.

    Not an acceptance criterion — the repository's practice since SC-2-03. The starting revision is
    read, not hard-coded; `command.upgrade(..., "head")` runs in `finally`, because `engine` is
    session-scoped and every other test reads the schema this one leaves.
    """

    def revision() -> str:
        with engine.connect() as connection:
            return connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()

    def state() -> tuple[bool, str]:
        with engine.connect() as connection:
            exists = connection.execute(
                sa.text("SELECT to_regclass('fixed_price_terms') IS NOT NULL")
            ).scalar_one()
            check = connection.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conname = 'ck_commercial_terms_model_type_known'"
                )
            ).scalar_one()
        return exists, check

    before = revision()
    try:
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        assert revision() == PREVIOUS_REVISION
        exists, check = state()
        assert not exists
        assert "'fixed_price'" not in check
        assert "'time_and_material'" in check and "'story_points'" in check
        assert "'outcome_based'" in check
    finally:
        command.upgrade(alembic_config, "head")

    assert revision() == before
    exists, check = state()
    assert exists
    assert "'fixed_price'" in check and "'story_points'" in check and "'outcome_based'" in check


def test_the_fixed_price_migration_downgrade_refuses_while_a_fixed_price_rule_exists(
    engine: Engine, alembic_config: Config
) -> None:
    """R-02 of the SC-4-02 review (2026-09-28) — the downgrade never deletes a scenario's rule. With
    a committed Fixed Price rule and its price row in the database, `downgrade()` of `b8f2d6a41c93`
    is refused by the database (re-adding the narrower CHECK over an existing `fixed_price` rule),
    the revision stays at head, and the rule and its price row are untouched — the `DROP TABLE` is
    rolled back with the rest of the migration's transaction. The pattern of
    `tests/test_sc_4_03_merge_qa.py::test_merge_contrast_an_outcome_rule_makes_the_downgrade_refuse_and_loses_nothing`.

    Contrast (no Fixed Price rule → the downgrade and the upgrade run through):
    `test_the_fixed_price_migration_downgrades_and_upgrades_again` above.

    Mutation killed: a downgrade that first deletes the `fixed_price` rules to get through (the
    first version of this migration) — the downgrade then succeeds and the rule is gone."""

    def one(sql: str, **params: object) -> object:
        with engine.connect() as connection:
            return connection.execute(sa.text(sql), params).scalar_one()

    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="FP downgrade", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Baseline")
        rule = make_fixed_price_terms(setup, scenario)
        ids = {"rule": rule.id, "scenario": scenario.id, "project": project.id}
        setup.commit()

    before = one("SELECT version_num FROM alembic_version")
    try:
        with pytest.raises(sa.exc.IntegrityError):
            command.downgrade(alembic_config, PREVIOUS_REVISION)
        assert one("SELECT version_num FROM alembic_version") == before
        assert one("SELECT to_regclass('fixed_price_terms') IS NOT NULL") is True
        assert (
            one("SELECT model_type FROM commercial_terms WHERE id = :id", id=ids["rule"])
            == "fixed_price"
        )
        assert (
            one(
                "SELECT agreed_price::text || '|' || currency FROM fixed_price_terms"
                " WHERE commercial_terms_id = :id",
                id=ids["rule"],
            )
            == "150000.0000|PLN"
        )
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM fixed_price_terms WHERE commercial_terms_id = :id"),
                {"id": ids["rule"]},
            )
            connection.execute(
                sa.text("DELETE FROM commercial_terms WHERE id = :id"), {"id": ids["rule"]}
            )
            connection.execute(
                sa.text("DELETE FROM project_access WHERE project_id = :id"),
                {"id": ids["project"]},
            )
            connection.execute(
                sa.text("DELETE FROM scenarios WHERE id = :id"), {"id": ids["scenario"]}
            )
            connection.execute(
                sa.text("DELETE FROM projects WHERE id = :id"), {"id": ids["project"]}
            )
