"""Proves the FEAT-14 database guarantees from migration 0017 that hold on both dialects.

Test numbers follow tests/sql/feat14_acceptance.sql (from reference/feat-14, adapted to the
delivered contracts and with the gaps of the pack closed: see docs/reference-findings.md). Here:
one live block per height while orphaned ones are kept (T26, T26b); the projections carry no
column that takes the time of applying, and every holding names the event and block it is as of
(T23); a code is redeemed on chain once (T22). The lease, the hash chain, the cursor and
commit-time checks, the projector and replay are PostgreSQL triggers and functions, and are
checked here only in the generated DDL; tests/sql/feat14_acceptance.sql proves their behaviour.
"""

import hashlib
import io
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from marketplace.infrastructure.database import Database
from marketplace.infrastructure.legacy_schema import (
    chain_event,
    coupon_code,
    coupon_holding,
    coupon_redemption,
    coupon_type,
    merchant,
    point_balance,
    raw_block,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _hash(text: str) -> str:
    return "0x" + hashlib.sha256(text.encode()).hexdigest()


def _insert(database: Database, table: Any, **values: Any) -> None:
    with database.sessions.begin() as session:
        session.execute(table.insert().values(**values))


def _block(number: int, label: str = "block", **values: Any) -> dict[str, Any]:
    return {
        "block_hash": _hash(f"{label}-{number}"),
        "block_number": number,
        "parent_hash": _hash(f"block-{number - 1}"),
        "timestamp": NOW,
        **values,
    }


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    database = Database(database_url)
    yield database
    database.close()


# --- T26 T26b · one live block per height; orphaned blocks are kept --------------------------


def test_t26b_a_second_live_block_at_one_height_is_rejected(database: Database) -> None:
    _insert(database, raw_block, **_block(45))

    with pytest.raises(IntegrityError, match="block_number"):
        _insert(database, raw_block, **_block(45, "fork"))


def test_t26_the_winning_block_is_stored_beside_the_orphaned_one(database: Database) -> None:
    _insert(database, raw_block, **_block(45, orphaned_at=NOW))

    _insert(database, raw_block, **_block(45, "fork"))


# --- T23 · projections are as of a block, never as of the time they were applied -------------


@pytest.mark.parametrize("table", [coupon_holding, point_balance, coupon_redemption])
def test_t23_projections_have_no_apply_time_column(table: sa.Table) -> None:
    for column in table.columns:
        assert column.name not in {"applied_at", "updated_at"}, column
        default = str(getattr(column.server_default, "arg", "")).lower()
        assert "now" not in default and "current_timestamp" not in default, column


def test_t23_a_holding_names_its_event_and_block() -> None:
    for name in ("chain_event_id", "as_of_block", "as_of_time"):
        assert coupon_holding.c[name].nullable is False, name


# --- T22 · a code is redeemed on chain once ---------------------------------------------------


def test_t22_a_code_has_one_chain_redemption(database: Database) -> None:
    _insert(database, merchant, id="mch_a", name="A", status="ACTIVE", activated_at=NOW)
    _insert(
        database,
        coupon_type,
        id="ct_1",
        merchant_id="mch_a",
        name="c",
        name_normalized="c",
        face_value="1",
        total_supply=10,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )
    _insert(
        database,
        coupon_code,
        id="cc_1",
        coupon_type_id="ct_1",
        code_hash=_hash("code"),
        code_ref=_hash("cc_1"),
    )
    _insert(database, raw_block, **_block(30))
    _insert(
        database,
        chain_event,
        id="ce_1",
        block_number=30,
        block_hash=_hash("block-30"),
        tx_hash=_hash("tx"),
        log_index=0,
        address="0x" + "aa" * 20,
        topic0=_hash("topic"),
        topics=[],
        data=b"\x00",
        block_timestamp=NOW,
    )
    redemption = {
        "coupon_code_id": "cc_1",
        "redeemer_kind": "EXTERNAL",
        "redeemer_id": "0x" + "bb" * 20,
        "chain_event_id": "ce_1",
        "as_of_block": 30,
        "as_of_time": NOW,
    }
    _insert(database, coupon_redemption, **redemption)

    with pytest.raises(IntegrityError, match="coupon_code_id|coupon_redemption"):
        _insert(database, coupon_redemption, **redemption)


# --- PostgreSQL-only parts, checked in the generated DDL ------------------------------------


def test_postgresql_only_parts_are_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    output = io.StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()

    for name in (
        "CREATE FUNCTION f_finality_depth(",
        "CREATE TRIGGER trg_indexer_cursor_guard",
        "CONSTRAINT = 'trg_cursor_rewind'",
        "CREATE TRIGGER trg_block_links_to_parent",
        "CREATE TRIGGER trg_raw_block_guard",
        "CREATE TRIGGER trg_event_matches_block",
        "CREATE TRIGGER trg_chain_event_guard",
        "CREATE CONSTRAINT TRIGGER trg_event_consistent",
        "CREATE CONSTRAINT TRIGGER trg_block_consistent",
        "CREATE CONSTRAINT TRIGGER trg_cursor_consistent",
        "CREATE TRIGGER trg_reorg_log_facts",
        "CREATE FUNCTION f_apply_event(",
        "CREATE FUNCTION f_replay_projections(",
        "CREATE FUNCTION f_handle_reorg(",
        "CREATE FUNCTION f_mark_finalized(",
        "pg_advisory_xact_lock(hashtext('indexer'))",
        "ck_point_balance_amount_uint256",
        "ck_wallet_address_lower",
    ):
        assert name in sql, name
