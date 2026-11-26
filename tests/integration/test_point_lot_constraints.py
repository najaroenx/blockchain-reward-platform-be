"""Proves the FEAT-05 database guarantees from migrations 0007 and 0008 that hold on both dialects.

Test numbers follow tests/sql/feat05_acceptance.sql (from reference/feat-05, moved onto the
NewPointToken v3 lots: see docs/reference-findings.md). These run on SQLite. The rules that need
numbers beyond int64 or a trigger run on PostgreSQL only and are proved there by
tests/sql/feat05_acceptance.sql: available balance and the reservation check (T2-T6, T4b, T7b),
the uint256 CHECKs (T11 T12), the balanced point ledger (T16), FEFO (T17), release timing (T18).
Here they are only checked to be emitted in the PostgreSQL DDL.
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
    expiry_notice,
    ledger_entry,
    merchant,
    operation,
    point_ledger_entry,
    point_lot,
    point_lot_balance,
    point_type,
    reservation,
)

NOW = datetime(2026, 11, 18, tzinfo=UTC)


def _insert(database: Database, table: Any, **values: Any) -> None:
    with database.sessions.begin() as session:
        session.execute(table.insert().values(**values))


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    database = Database(database_url)
    with database.sessions.begin() as session:
        session.execute(
            merchant.insert().values(id="mch_1", name="m", status="ACTIVE", activated_at=NOW)
        )
        session.execute(
            point_type.insert().values(
                id="pt_1",
                merchant_id="mch_1",
                name="Lots",
                name_normalized="lots",
                symbol="LOTS",
                business_model=2,
                lot_policy="FEFO_ONLY",
            )
        )
        session.execute(
            operation.insert().values(
                id="op_1",
                actor_scope="merchant:mch_1",
                kind="POINT_TRANSFER",
                subject_id="pt_1",
                payload={},
            )
        )
        session.execute(
            point_lot.insert().values(
                point_type_id="pt_1",
                lot_key="1",
                start_at=NOW - timedelta(days=1),
                expire_at=NOW + timedelta(days=30),
            )
        )
    yield database
    database.close()


def _reservation(id_: str, subject_type: str, subject_id: str) -> dict[str, Any]:
    return {
        "id": id_,
        "operation_id": "op_1",
        "subject_type": subject_type,
        "subject_id": subject_id,
        "amount": "1",
    }


# --- T8 · uq_reservation_exclusive -----------------------------------------------------------


def test_t8_single_item_cannot_be_reserved_twice(database: Database) -> None:
    _insert(database, reservation, **_reservation("rs_1", "COUPON_UNIT", "coupon_1"))

    with pytest.raises(IntegrityError, match="subject_id"):
        _insert(database, reservation, **_reservation("rs_2", "COUPON_UNIT", "coupon_1"))


def test_t8_point_balance_backs_many_open_reservations(database: Database) -> None:
    subject = "pt_1|CUSTOMER|cus_1"
    _insert(database, reservation, **_reservation("rs_1", "POINT_BALANCE", subject))

    _insert(database, reservation, **_reservation("rs_2", "POINT_BALANCE", subject))


def test_t8_released_item_can_be_reserved_again(database: Database) -> None:
    _insert(
        database,
        reservation,
        **_reservation("rs_1", "COUPON_UNIT", "coupon_1"),
        released_at=NOW,
    )

    _insert(database, reservation, **_reservation("rs_2", "COUPON_UNIT", "coupon_1"))


# --- T9 T10 · point_lot window · one balance row per holder per lot -------------------------


def test_t9_lot_must_expire_after_it_starts(database: Database) -> None:
    with pytest.raises(IntegrityError, match="ck_point_lot_window"):
        _insert(database, point_lot, point_type_id="pt_1", lot_key="2", start_at=NOW, expire_at=NOW)


def test_t10_one_balance_row_per_holder_per_lot(database: Database) -> None:
    row = {"point_type_id": "pt_1", "holder_kind": "CUSTOMER", "holder_id": "cus_1", "lot_key": "1"}
    _insert(database, point_lot_balance, **row, amount="5")

    with pytest.raises(IntegrityError, match="point_lot_balance"):
        _insert(database, point_lot_balance, **row, amount="7")


# --- T16 · the point ledger is its own table; ledger_entry is THB only ----------------------


def test_t16_point_ledger_accepts_point_entries(database: Database) -> None:
    common = {"operation_id": "op_1", "point_type_id": "pt_1"}
    _insert(
        database,
        point_ledger_entry,
        id="le_1",
        entry_seq=1,
        holder_kind="INVENTORY",
        holder_id="mch_1",
        direction="DEBIT",
        amount="300",
        **common,
    )
    _insert(
        database,
        point_ledger_entry,
        id="le_2",
        entry_seq=2,
        holder_kind="CUSTOMER",
        holder_id="cus_1",
        lot_key="1",
        direction="CREDIT",
        amount="300",
        **common,
    )


def test_t16_point_ledger_rejects_an_unknown_direction(database: Database) -> None:
    with pytest.raises(IntegrityError, match="ck_point_ledger_direction_enum"):
        _insert(
            database,
            point_ledger_entry,
            id="le_1",
            operation_id="op_1",
            entry_seq=1,
            point_type_id="pt_1",
            holder_kind="CUSTOMER",
            holder_id="cus_1",
            direction="SIDEWAYS",
            amount="1",
        )


def test_t16_thb_ledger_has_no_point_columns() -> None:
    assert {"point_type_id", "holder_kind", "holder_id", "direction"}.isdisjoint(
        ledger_entry.c.keys()
    )


# --- expiry_notice now names a lot balance (was a point_balance_bucket) ---------------------


def test_expiry_notice_once_per_lot_balance_per_threshold(database: Database) -> None:
    holder = {"point_type_id": "pt_1", "holder_kind": "CUSTOMER", "holder_id": "cus_1"}
    _insert(database, point_lot_balance, **holder, lot_key="1", amount="5")
    notice = {**holder, "lot_key": "1", "threshold_days": 7, "channel": "PUSH"}
    _insert(database, expiry_notice, id="en_1", **notice)

    with pytest.raises(IntegrityError, match="expiry_notice"):
        _insert(database, expiry_notice, id="en_2", **notice)


def test_point_balance_bucket_is_gone(database: Database) -> None:
    with database.sessions.begin() as session:
        tables = sa.inspect(session.connection()).get_table_names()
    assert "point_balance_bucket" not in tables
    assert {"point_lot", "point_lot_balance", "point_inventory"} <= set(tables)


# --- PostgreSQL-only parts, checked in the generated DDL ------------------------------------


def test_postgresql_only_parts_are_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    output = io.StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()

    for name in (
        "CREATE FUNCTION f_is_uint256",
        "ck_reservation_amount_uint256",
        "ck_reservation_point_subject",
        "CREATE TRIGGER trg_reservation_release_timing",
        "CREATE CONSTRAINT TRIGGER trg_point_ledger_balanced",
        "CREATE FUNCTION f_available_points",
        "CREATE FUNCTION f_points_fefo",
        "CREATE TRIGGER trg_point_reservation_balance",
        "p_business IS DISTINCT FROM 4",
    ):
        assert name in sql, name
