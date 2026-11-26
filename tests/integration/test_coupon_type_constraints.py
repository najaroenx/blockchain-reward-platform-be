"""Proves the FEAT-07 database guarantees from migration 0010 that hold on both dialects.

Test numbers follow tests/sql/feat07_acceptance.sql (from reference/feat-07, aligned to the Coupon
ABI: see docs/reference-findings.md). These run on SQLite. The PostgreSQL-only parts are checked
here only in the generated DDL; their behaviour is proved by tests/sql/feat07_acceptance.sql
against a real PostgreSQL:

  - trg_coupon_type_merchant_active (T2), trg_coupon_chain_immutable (T13),
    trg_coupon_live_has_chain (T20), the issued_count triggers and their guard (T7 T8b T21b T21c),
  - ck_code_ref_derived (T6, sha256), ck_coupon_type_face_value_uint256 (T19) and
    ck_coupon_type_chain_type_id_uint256 (T13b), which call PostgreSQL functions.

On SQLite nothing keeps issued_count up to date, so the supply tests here write it directly: they
prove the CHECK, not the counting.
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
from marketplace.infrastructure.legacy_schema import coupon_type, merchant, operation

LATER = datetime.now(UTC) + timedelta(days=30)


def _coupon_type(id_: str, name: str, **values: Any) -> dict[str, Any]:
    # name_normalized is written in SQL, the way the application must write it.
    return {
        "id": id_,
        "merchant_id": "mch_1",
        "name": name,
        "name_normalized": sa.func.lower(sa.func.trim(name)),
        "face_value": "1",
        "total_supply": 10,
        "expires_at": LATER,
        **values,
    }


def _insert(database: Database, table: Any, **values: Any) -> None:
    with database.sessions.begin() as session:
        session.execute(table.insert().values(**values))


def _deploy(id_: str, **values: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "actor_scope": "merchant:mch_1",
        "kind": "COUPON_TYPE_CREATE",
        "subject_id": "ct_1",
        "payload": {},
        **values,
    }


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    database = Database(database_url)
    _insert(
        database,
        merchant,
        id="mch_1",
        name="test merchant",
        status="ACTIVE",
        activated_at=datetime.now(UTC),
    )
    yield database
    database.close()


# --- T3 T4 · one live name per merchant -----------------------------------------------------


def test_t3_same_name_ignoring_case_and_outer_spaces_is_rejected(database: Database) -> None:
    _insert(database, coupon_type, **_coupon_type("ct_1", "Discount 100"))

    with pytest.raises(IntegrityError, match="name_normalized"):
        _insert(database, coupon_type, **_coupon_type("ct_2", "  discount 100 "))


def test_t3_name_normalized_must_be_derived_from_the_name(database: Database) -> None:
    row = _coupon_type("ct_1", "Discount 100", name_normalized="something else")

    with pytest.raises(IntegrityError, match="ck_coupon_type_name_normalized"):
        _insert(database, coupon_type, **row)


def test_t4_failed_deploy_releases_the_name(database: Database) -> None:
    _insert(database, coupon_type, **_coupon_type("ct_1", "Discount 100", status="FAILED"))

    _insert(database, coupon_type, **_coupon_type("ct_2", "Discount 100"))


def test_t4_other_merchants_may_use_the_same_name(database: Database) -> None:
    _insert(
        database, merchant, id="mch_2", name="m2", status="ACTIVE", activated_at=datetime.now(UTC)
    )
    _insert(database, coupon_type, **_coupon_type("ct_1", "Discount 100"))

    _insert(database, coupon_type, **_coupon_type("ct_2", "Discount 100", merchant_id="mch_2"))


# --- T5 · validity window -------------------------------------------------------------------


def test_t5_starts_at_defaults_to_creation(database: Database) -> None:
    _insert(database, coupon_type, **_coupon_type("ct_1", "Discount 100"))

    with database.sessions.begin() as session:
        starts_at, created_at = session.execute(
            sa.select(coupon_type.c.starts_at, coupon_type.c.created_at)
        ).one()
    assert starts_at == created_at


def test_t5_expiry_before_the_start_is_rejected(database: Database) -> None:
    row = _coupon_type("ct_1", "Discount 100", starts_at=LATER + timedelta(days=1))

    with pytest.raises(IntegrityError, match="ck_coupon_type_window"):
        _insert(database, coupon_type, **row)


# --- T7 T21d · supply -----------------------------------------------------------------------


def test_t7_issued_count_cannot_pass_the_supply(database: Database) -> None:
    _insert(database, coupon_type, **_coupon_type("ct_1", "Discount 100", total_supply=3))

    with (
        pytest.raises(IntegrityError, match="ck_coupon_type_within_supply"),
        database.sessions.begin() as session,
    ):
        session.execute(coupon_type.update().values(issued_count=4))


def test_t21d_supply_cannot_drop_under_what_was_issued(database: Database) -> None:
    _insert(
        database,
        coupon_type,
        **_coupon_type("ct_1", "Discount 100", total_supply=3, issued_count=3),
    )

    with (
        pytest.raises(IntegrityError, match="ck_coupon_type_within_supply"),
        database.sessions.begin() as session,
    ):
        session.execute(coupon_type.update().values(total_supply=2))


# --- T15 · one unfinished deploy per coupon type --------------------------------------------


def test_t15_second_unfinished_deploy_of_a_coupon_type_is_rejected(database: Database) -> None:
    _insert(database, operation, **_deploy("op_1"))

    with pytest.raises(IntegrityError, match="subject_id"):
        _insert(database, operation, **_deploy("op_2"))


def test_t15_deploy_can_be_retried_after_the_previous_one_reverted(database: Database) -> None:
    _insert(database, operation, **_deploy("op_1", status="CHAIN_REVERTED"))

    _insert(database, operation, **_deploy("op_2"))


def test_t15_a_point_deploy_and_a_coupon_deploy_do_not_collide(database: Database) -> None:
    _insert(database, operation, **_deploy("op_1"))

    _insert(database, operation, **_deploy("op_2", kind="POINT_TYPE_CREATE"))


# --- PostgreSQL-only parts, checked in the generated DDL ------------------------------------


def test_postgresql_only_parts_are_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    output = io.StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()

    for name in (
        "ck_coupon_type_face_value_uint256",
        "ck_coupon_type_chain_type_id_uint256",
        "ck_code_ref_derived",
        "CREATE TRIGGER trg_coupon_type_merchant_active",
        "CREATE TRIGGER trg_coupon_chain_immutable",
        "CREATE CONSTRAINT TRIGGER trg_coupon_live_has_chain",
        "CREATE TRIGGER trg_coupon_issued_insert",
        "CREATE TRIGGER trg_coupon_issued_update",
        "CREATE TRIGGER trg_coupon_issued_delete",
        "CREATE TRIGGER trg_coupon_issued_count_guard",
    ):
        assert name in sql, name
