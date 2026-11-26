"""Proves the FEAT-04 database guarantees from migration 0006 that hold on both dialects.

Test numbers follow tests/sql/feat04_acceptance.sql (from reference/feat-04, aligned to the
NewPointToken v3 contract: see docs/reference-findings.md). These run on SQLite. The PostgreSQL-only
parts are checked here only in the generated DDL; their behaviour is proved by
tests/sql/feat04_acceptance.sql against a real PostgreSQL:

  - ck_point_type_symbol_format (T3), a regex,
  - trg_point_type_merchant_active (T2) and trg_point_chain_immutable (T9 T9b), triggers.
"""

import io
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from marketplace.infrastructure.database import Database
from marketplace.infrastructure.legacy_schema import (
    merchant,
    operation,
    point_type,
    point_type_chain,
)

NOW = datetime(2026, 11, 18, tzinfo=UTC)


def _seed_merchant(database: Database) -> None:
    with database.sessions.begin() as session:
        session.execute(
            merchant.insert().values(
                id="mch_1", name="test merchant", status="ACTIVE", activated_at=datetime.now(UTC)
            )
        )


def _point_type(id_: str, name: str, symbol: str, **values: Any) -> dict[str, Any]:
    # name_normalized is written in SQL, the way the application must write it.
    return {
        "id": id_,
        "merchant_id": "mch_1",
        "name": name,
        "name_normalized": sa.func.lower(sa.func.trim(name)),
        "symbol": symbol,
        "business_model": 2,
        "lot_policy": "FEFO_ONLY",
        **values,
    }


def _insert(database: Database, table: Any, **values: Any) -> None:
    with database.sessions.begin() as session:
        session.execute(table.insert().values(**values))


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    database = Database(database_url)
    _seed_merchant(database)
    yield database
    database.close()


# --- T4 · decimals = 18 ---------------------------------------------------------------------


def test_t4_decimals_default_to_18_and_nothing_else_is_accepted(database: Database) -> None:
    _insert(database, point_type, **_point_type("pt_1", "Happy Points", "HAPPY"))
    with database.sessions.begin() as session:
        assert session.execute(sa.select(point_type.c.decimals)).scalar_one() == 18

    with pytest.raises(IntegrityError, match="ck_point_type_decimals_fixed"):
        _insert(database, point_type, **_point_type("pt_2", "Two", "TWO", decimals=2))


# --- T5 T6 T7 · uq_point_symbol · uq_point_name · FAILED releases ----------------------------


def test_t5_same_symbol_in_one_merchant_is_rejected(database: Database) -> None:
    _insert(database, point_type, **_point_type("pt_1", "Happy Points", "HAPPY"))

    with pytest.raises(IntegrityError, match="symbol"):
        _insert(database, point_type, **_point_type("pt_2", "Other Name", "HAPPY"))


def test_t6_same_name_ignoring_case_and_outer_spaces_is_rejected(database: Database) -> None:
    _insert(database, point_type, **_point_type("pt_1", "Happy Points", "HAPPY"))

    with pytest.raises(IntegrityError, match="name_normalized"):
        _insert(database, point_type, **_point_type("pt_2", "  HAPPY points ", "HAPPY2"))


def test_t6_name_normalized_must_be_derived_from_the_name(database: Database) -> None:
    with pytest.raises(IntegrityError, match="ck_point_type_name_normalized"):
        _insert(
            database,
            point_type,
            **_point_type("pt_1", "Happy Points", "HAPPY", name_normalized="something else"),
        )


def test_t7_failed_deploy_releases_name_and_symbol(database: Database) -> None:
    _insert(database, point_type, **_point_type("pt_1", "Happy Points", "HAPPY", status="FAILED"))

    _insert(database, point_type, **_point_type("pt_2", "Happy Points", "HAPPY"))


# --- T8 · uq_point_deploy_inflight -----------------------------------------------------------


def _deploy(id_: str) -> dict[str, Any]:
    return {
        "id": id_,
        "actor_scope": "merchant:mch_1",
        "kind": "POINT_TYPE_CREATE",
        "subject_id": "pt_1",
        "payload": {},
    }


def test_t8_second_unfinished_deploy_of_a_point_type_is_rejected(database: Database) -> None:
    _insert(database, operation, **_deploy("op_1"))

    with pytest.raises(IntegrityError, match="subject_id"):
        _insert(database, operation, **_deploy("op_2"))


def test_t8_deploy_can_be_retried_after_the_previous_one_reverted(database: Database) -> None:
    _insert(database, operation, **_deploy("op_1"), status="CHAIN_REVERTED")

    _insert(database, operation, **_deploy("op_2"))


# --- T11 · business configuration -----------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (1, {}),  # A without a window
        (1, {"program_start_at": NOW, "program_expire_at": NOW}),  # A whose window is empty
        (2, {"lot_policy": "USER_SELECT"}),  # B is FEFO only
        (3, {"lot_policy": "FEFO_ONLY"}),  # C is user-select only
        (4, {}),  # D needs a policy
    ],
)
def test_t11_incomplete_or_inconsistent_configuration_is_rejected(
    database: Database, model: int, values: dict[str, Any]
) -> None:
    row = _point_type("pt_1", "Happy Points", "HAPPY", business_model=model, lot_policy=None)
    with pytest.raises(IntegrityError, match="ck_point_type_business_config"):
        _insert(database, point_type, **{**row, **values})


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (
            1,
            {
                "program_start_at": datetime.now(UTC),
                "program_expire_at": datetime.now(UTC) + timedelta(days=60),
            },
        ),
        (2, {"lot_policy": "FEFO_ONLY"}),
        (3, {"lot_policy": "USER_SELECT"}),
        (4, {"lot_policy": "USER_SELECT"}),
    ],
)
def test_t11_each_business_model_accepts_its_own_configuration(
    database: Database, model: int, values: dict[str, Any]
) -> None:
    row = _point_type("pt_1", "Happy Points", "HAPPY", business_model=model, lot_policy=None)
    _insert(database, point_type, **{**row, **values})


def test_t11_chain_configuration_needs_its_block_and_tx(database: Database) -> None:
    _insert(database, point_type, **_point_type("pt_1", "Happy Points", "HAPPY"))
    chain_row = {
        "point_type_id": "pt_1",
        "contract_address": "0x" + "aa" * 20,
        "deployed_block": 1001,
        "deployed_tx_hash": "0x" + "ab" * 32,
    }
    # Deployed but not configured yet (Draft): allowed.
    _insert(database, point_type_chain, **chain_row)

    with (
        pytest.raises(IntegrityError, match="ck_point_chain_configured"),
        database.sessions.begin() as session,
    ):
        session.execute(
            point_type_chain.update()
            .where(point_type_chain.c.point_type_id == "pt_1")
            .values(business_model=2, lot_policy="FEFO_ONLY")
        )


# --- PostgreSQL-only parts, checked in the generated DDL ------------------------------------


def test_postgresql_only_parts_are_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    output = io.StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()

    for name in (
        "ck_point_type_symbol_format",
        "CREATE TRIGGER trg_point_type_merchant_active",
        "CREATE TRIGGER trg_point_chain_immutable",
        "OR NEW.deployed_block IS DISTINCT FROM OLD.deployed_block",
    ):
        assert name in sql, name
