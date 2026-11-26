"""Proves the FEAT-12 database guarantees from migration 0015 that hold on both dialects.

Test numbers follow tests/sql/feat12_acceptance.sql (from reference/feat-12, with the gaps of the
pack closed: see docs/reference-findings.md). Here: one unfinished mint per deposit (T27), one
deposit per confirming webhook (T7c). The money rules (exact satang conversion, status and its
evidence, what makes a mint right, ISSUANCE backed by a real deposit, the reversal outcome against
the computed balance) are PostgreSQL triggers and are checked here only in the generated DDL;
tests/sql/feat12_acceptance.sql proves their behaviour.
"""

import io
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from marketplace.infrastructure.database import Database
from marketplace.infrastructure.legacy_schema import operation, psp_webhook, thb_deposit


def _insert(database: Database, table: Any, **values: Any) -> None:
    with database.sessions.begin() as session:
        session.execute(table.insert().values(**values))


def _mint(id_: str, deposit_id: str, **values: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "actor_scope": "system:minter",
        "kind": "THB_MINT",
        "subject_id": deposit_id,
        "payload": {},
        **values,
    }


def _deposit(id_: str, **values: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "holder_kind": "CUSTOMER",
        "holder_id": "cus_1",
        "provider": "psp",
        "provider_ref": f"ref_{id_}",
        "amount_satang": 10000,
        "amount_base": "100000000000000000000",
        "expires_at": datetime.now(UTC) + timedelta(days=1),
        **values,
    }


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    database = Database(database_url)
    _insert(
        database,
        psp_webhook,
        id="wh_1",
        provider="psp",
        provider_event_id="evt_1",
        event_type="charge.complete",
        raw_body=b"{}",
        signature="sig",
        signature_ok=True,
    )
    yield database
    database.close()


# --- T27 · one unfinished mint per deposit --------------------------------------------------


def test_t27_second_unfinished_mint_of_a_deposit_is_rejected(database: Database) -> None:
    _insert(database, operation, **_mint("op_1", "dep_1"))

    with pytest.raises(IntegrityError, match="subject_id"):
        _insert(database, operation, **_mint("op_2", "dep_1"))


def test_t27_a_reverted_mint_can_be_retried(database: Database) -> None:
    _insert(database, operation, **_mint("op_1", "dep_1", status="CHAIN_REVERTED"))

    _insert(database, operation, **_mint("op_2", "dep_1"))


# --- T7c · one deposit per confirming webhook -----------------------------------------------


def test_t7c_a_webhook_confirms_one_deposit(database: Database) -> None:
    now = datetime.now(UTC)
    confirmed = {"status": "CONFIRMED", "webhook_id": "wh_1", "confirmed_at": now}
    _insert(database, thb_deposit, **_deposit("dep_1", **confirmed))

    with pytest.raises(IntegrityError, match="webhook_id"):
        _insert(database, thb_deposit, **_deposit("dep_2", **confirmed))


# --- PostgreSQL-only parts, checked in the generated DDL ------------------------------------


def test_postgresql_only_parts_are_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    output = io.StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()

    for name in (
        "ck_thb_deposit_base_uint256",
        "CREATE TRIGGER trg_psp_webhook_immutable",
        "CREATE TRIGGER trg_deposit_amount_exact",
        "CREATE TRIGGER trg_deposit_status_transition",
        "CREATE TRIGGER trg_mint_requires_confirmed_deposit",
        "CREATE TRIGGER trg_issuance_only_from_mint",
        "CREATE CONSTRAINT TRIGGER trg_issuance_backed",
        "CREATE TRIGGER trg_reversal_outcome_matches_balance",
        "v_available := f_available_thb(",
    ):
        assert name in sql, name
