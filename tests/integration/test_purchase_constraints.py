"""Proves the FEAT-10 database guarantees from migration 0013 that hold on both dialects.

Test numbers follow tests/sql/feat10_acceptance.sql (from reference/feat-10, aligned to the
Marketplace and Vault ABIs: see docs/reference-findings.md). The ledger rules that count money
(T13 T14 T14b at commit, T18 T18b, the THB reservation balance, the purchase arithmetic and terms)
are PostgreSQL triggers and are checked here only in the generated DDL; their behaviour is proved
by tests/sql/feat10_acceptance.sql against a real PostgreSQL.
"""

import io
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from marketplace.infrastructure.database import Database
from marketplace.infrastructure.legacy_schema import ledger_entry, operation, thb_balance


def _insert(database: Database, table: Any, **values: Any) -> None:
    with database.sessions.begin() as session:
        session.execute(table.insert().values(**values))


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    database = Database(database_url)
    _insert(
        database,
        operation,
        id="op_1",
        actor_scope="system:psp",
        kind="THB_MINT",
        payload={},
    )
    yield database
    database.close()


# --- ESCROW · THB locked in the Vault for one purchase --------------------------------------


def test_escrow_is_a_ledger_account(database: Database) -> None:
    _insert(database, thb_balance, account="ESCROW", account_id="ESCROW:pur_1", amount="160")
    _insert(
        database,
        ledger_entry,
        id="le_1",
        operation_id="op_1",
        entry_seq=1,
        account="ESCROW",
        account_id="ESCROW:pur_1",
        amount="160",
    )


# --- T12b · an account_id names its account -------------------------------------------------


@pytest.mark.parametrize("account_id", ["cus_1", "MERCHANT_THB:cus_1", "CUSTOMER_THB:"])
def test_t12b_account_id_must_start_with_its_account(database: Database, account_id: str) -> None:
    with pytest.raises(IntegrityError, match="ck_thb_balance_account_id"):
        _insert(database, thb_balance, account="CUSTOMER_THB", account_id=account_id, amount="0")
    with pytest.raises(IntegrityError, match="ck_ledger_entry_account_id"):
        _insert(
            database,
            ledger_entry,
            id="le_1",
            operation_id="op_1",
            entry_seq=1,
            account="CUSTOMER_THB",
            account_id=account_id,
            amount="1",
        )


# --- T33 · only ISSUANCE may be negative ----------------------------------------------------


@pytest.mark.parametrize("account", ["CUSTOMER_THB", "MERCHANT_THB", "PLATFORM_THB", "ESCROW"])
def test_t33_only_issuance_may_be_negative(database: Database, account: str) -> None:
    with pytest.raises(IntegrityError, match="ck_thb_balance_sign"):
        _insert(database, thb_balance, account=account, account_id=f"{account}:x", amount="-1")


def test_t33_issuance_is_negative_by_design(database: Database) -> None:
    _insert(database, thb_balance, account="ISSUANCE", account_id="ISSUANCE:thb", amount="-500")


# --- PostgreSQL-only parts, checked in the generated DDL ------------------------------------


def test_postgresql_only_parts_are_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    output = io.StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()

    for name in (
        "ck_ledger_entry_amount",
        "ck_thb_balance_amount",
        "ck_reservation_thb_subject",
        "CREATE TRIGGER trg_thb_reservation_balance",
        "CREATE TRIGGER trg_buy_admissible",
        "CREATE TRIGGER trg_purchase_math",
        "CREATE TRIGGER trg_purchase_terms",
        "CREATE TRIGGER trg_listing_terms_fixed",
        "CREATE TRIGGER trg_ledger_append_only",
        "CREATE CONSTRAINT TRIGGER trg_ledger_balanced",
        "CREATE CONSTRAINT TRIGGER trg_balance_matches_ledger\n  AFTER INSERT ON ledger_entry",
    ):
        assert name in sql, name
