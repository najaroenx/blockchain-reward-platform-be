"""Proves the FEAT-11 database guarantees from migration 0014 that hold on both dialects.

Test numbers follow tests/sql/feat11_acceptance.sql (from reference/feat-11, aligned to the Coupon,
Marketplace and Vault ABIs: see docs/reference-findings.md). Here: an escrow row is one Vault
escrow and one purchase (T9), and it is closed exactly when nothing remains (T18). The money rules
(T11 T15-T17), redeem admission (T13) and REDEEMED being final (T14) are PostgreSQL triggers and are
checked here only in the generated DDL; tests/sql/feat11_acceptance.sql proves their behaviour.
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
    coupon_type,
    escrow,
    listing,
    merchant,
    operation,
    purchase,
    wallet,
)


def _insert(database: Database, table: Any, **values: Any) -> None:
    with database.sessions.begin() as session:
        session.execute(table.insert().values(**values))


def _purchase(database: Database, id_: str) -> None:
    _insert(
        database,
        listing,
        id=f"lst_{id_}",
        seller_kind="MERCHANT",
        seller_id="mch_1",
        coupon_type_id="ct_1",
        unit_price="80",
        quantity=1,
    )
    _insert(
        database,
        operation,
        id=f"op_{id_}",
        actor_scope="customer:cus_1",
        kind="LISTING_BUY",
        subject_id=f"lst_{id_}",
        payload={},
    )
    _insert(
        database,
        purchase,
        id=id_,
        listing_id=f"lst_{id_}",
        buyer_kind="CUSTOMER",
        buyer_id="cus_1",
        seller_kind="MERCHANT",
        seller_id="mch_1",
        unit_price="80",
        quantity=1,
        total_price="80",
        fee_bps=0,
        fee_amount="0",
        net_to_seller="80",
        operation_id=f"op_{id_}",
    )


def _escrow(id_: str, purchase_id: str, chain_escrow_id: str, **values: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "chain_token_id": "7",
        "contract_address": "0x" + "fa" * 20,
        "seller_wallet_id": "wal_s",
        "buyer_wallet_id": "wal_b",
        "total_amount": "80",
        "remaining_amount": "80",
        "coupons_locked": 1,
        "chain_escrow_id": chain_escrow_id,
        "purchase_id": purchase_id,
        **values,
    }


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    database = Database(database_url)
    now = datetime.now(UTC)
    _insert(database, merchant, id="mch_1", name="m", status="ACTIVE", activated_at=now)
    _insert(
        database,
        coupon_type,
        id="ct_1",
        merchant_id="mch_1",
        name="c",
        name_normalized=sa.func.lower(sa.func.trim("c")),
        face_value="100",
        total_supply=10,
        expires_at=now + timedelta(days=30),
    )
    for id_, kind, owner, byte in (
        ("wal_s", "MERCHANT", "mch_1", "11"),
        ("wal_b", "CUSTOMER", "cus_1", "22"),
    ):
        _insert(
            database,
            wallet,
            id=id_,
            address="0x" + byte * 20,
            key_ref=f"kms/{id_}",
            owner_kind=kind,
            owner_id=owner,
        )
    _purchase(database, "pur_1")
    _purchase(database, "pur_2")
    yield database
    database.close()


# --- T9 · one row per Vault escrow, one escrow per purchase ---------------------------------


def test_t9_a_vault_escrow_id_is_recorded_once(database: Database) -> None:
    _insert(database, escrow, **_escrow("esc_1", "pur_1", "501"))

    with pytest.raises(IntegrityError, match="chain_escrow_id"):
        _insert(database, escrow, **_escrow("esc_2", "pur_2", "501"))


def test_t9_a_purchase_has_one_escrow(database: Database) -> None:
    _insert(database, escrow, **_escrow("esc_1", "pur_1", "501"))

    with pytest.raises(IntegrityError, match="purchase_id"):
        _insert(database, escrow, **_escrow("esc_2", "pur_1", "502"))


def test_t9b_escrows_of_one_coupon_type_coexist(database: Database) -> None:
    _insert(database, escrow, **_escrow("esc_1", "pur_1", "501"))
    _insert(database, escrow, **_escrow("esc_2", "pur_2", "502"))


# --- T18 · closed exactly when nothing remains ----------------------------------------------


@pytest.mark.parametrize(("remaining", "active"), [("80", False), ("0", True)])
def test_t18_closed_exactly_when_empty(database: Database, remaining: str, active: bool) -> None:
    row = _escrow("esc_1", "pur_1", "501", remaining_amount=remaining, active=active)

    with pytest.raises(IntegrityError, match="ck_escrow_closed"):
        _insert(database, escrow, **row)


def test_t18_an_empty_escrow_is_closed(database: Database) -> None:
    _insert(
        database, escrow, **_escrow("esc_1", "pur_1", "501", remaining_amount="0", active=False)
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
        "ck_escrow_remaining_le_total",
        "CREATE TRIGGER trg_escrow_terms",
        "CREATE TRIGGER trg_escrow_progress",
        "CREATE CONSTRAINT TRIGGER trg_escrow_matches_ledger",
        "CREATE TRIGGER trg_redeem_admissible",
        "CREATE TRIGGER trg_coupon_redeemed_final",
        "CREATE CONSTRAINT TRIGGER trg_redeemed_coupon_paid",
    ):
        assert name in sql, name
