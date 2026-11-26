"""Proves, at the database layer, the FEAT-01 chain-safety constraints added in migration 0003.

T6 `uq_provision_inflight`, T9 `uq_signer_nonce` and T10 `uq_step_txhash` run here against SQLite,
which supports the same partial unique indexes as PostgreSQL. T7 `ck_outbox_topic_version` is
PostgreSQL-only, so it is checked here in the generated PostgreSQL DDL; its behaviour is proved by
tests/sql/feat01_acceptance.sql against a real PostgreSQL. Test numbers follow that file.
"""

import io
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from marketplace.infrastructure.database import Database
from marketplace.infrastructure.legacy_schema import (
    merchant,
    operation,
    operation_step,
    outbox,
    signer,
)

SIGNER = "0x" + "ac" * 20
TX_A = "0x" + "ab" * 32
TX_B = "0x" + "cd" * 32


def _seed(database: Database) -> None:
    with database.sessions.begin() as session:
        session.execute(merchant.insert().values(id="mch_1", name="test merchant"))
        session.execute(signer.insert().values(address=SIGNER, key_ref="kms/signer-1"))
        session.execute(
            operation.insert().values(
                id="op_1",
                actor_scope="anon:x",
                kind="MERCHANT_PROVISION",
                subject_id="mch_1",
                payload={},
            )
        )
        session.execute(
            outbox.insert().values(
                id="obx_1", operation_id="op_1", topic="merchant.provision.v1", payload={}
            )
        )


def _step(id_: str, index: int, **values: Any) -> dict[str, Any]:
    return {"id": id_, "operation_id": "op_1", "step_index": index, "name": id_, **values}


def _submitted(id_: str, index: int, *, nonce: int | None, tx_hash: str) -> dict[str, Any]:
    return _step(
        id_,
        index,
        raw_tx=b"\x02\xf8",
        status="SUBMITTED",
        signer_address=SIGNER if nonce is not None else None,
        nonce=nonce,
        tx_hash=tx_hash,
    )


def _insert(database: Database, table: Any, **values: Any) -> None:
    with database.sessions.begin() as session:
        session.execute(table.insert().values(**values))


# --- T6 · uq_provision_inflight -------------------------------------------------------------


def test_t6_second_unfinished_provision_for_same_merchant_is_rejected(database_url: str) -> None:
    database = Database(database_url)
    _seed(database)

    with pytest.raises(IntegrityError, match="subject_id"):
        _insert(
            database,
            operation,
            id="op_2",
            actor_scope="anon:x",
            kind="MERCHANT_PROVISION",
            subject_id="mch_1",
            payload={},
        )
    database.close()


def test_t6_provision_can_be_retried_after_the_previous_one_reverted(database_url: str) -> None:
    database = Database(database_url)
    _seed(database)
    with database.sessions.begin() as session:
        session.execute(
            operation.update().where(operation.c.id == "op_1").values(status="CHAIN_REVERTED")
        )

    _insert(
        database,
        operation,
        id="op_2",
        actor_scope="anon:x",
        kind="MERCHANT_PROVISION",
        subject_id="mch_1",
        payload={},
    )
    database.close()


def test_t6_other_operation_kinds_on_same_subject_are_unaffected(database_url: str) -> None:
    database = Database(database_url)
    _seed(database)

    _insert(
        database,
        operation,
        id="op_2",
        actor_scope="anon:x",
        kind="POINT_TYPE_CREATE",
        subject_id="mch_1",
        payload={},
    )
    database.close()


# --- T9 · uq_signer_nonce -------------------------------------------------------------------


def test_t9_same_signer_cannot_reuse_a_nonce(database_url: str) -> None:
    database = Database(database_url)
    _seed(database)
    _insert(database, operation_step, **_submitted("st_1", 1, nonce=41, tx_hash=TX_A))

    with pytest.raises(IntegrityError, match="nonce"):
        _insert(database, operation_step, **_submitted("st_2", 2, nonce=41, tx_hash=TX_B))
    database.close()


def test_t9_unsigned_steps_without_nonce_do_not_collide(database_url: str) -> None:
    database = Database(database_url)
    _seed(database)

    _insert(database, operation_step, **_step("st_1", 1))
    _insert(database, operation_step, **_step("st_2", 2))
    database.close()


# --- T10 · uq_step_txhash -------------------------------------------------------------------


def test_t10_same_tx_hash_cannot_back_two_steps(database_url: str) -> None:
    database = Database(database_url)
    _seed(database)
    _insert(database, operation_step, **_submitted("st_1", 1, nonce=41, tx_hash=TX_A))

    # A different step name and index, so only the tx_hash can collide (not uq_step_name).
    with pytest.raises(IntegrityError, match="tx_hash"):
        _insert(database, operation_step, **_submitted("st_dup", 3, nonce=None, tx_hash=TX_A))
    database.close()


# --- T7 · ck_outbox_topic_version -----------------------------------------------------------


def test_t7_versioned_topic_check_is_emitted_for_postgresql_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    output = io.StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()

    assert "ck_outbox_topic_version" in sql
    assert r"topic ~ '\.v[0-9]+$'" in sql
    for index in ("uq_provision_inflight", "uq_signer_nonce", "uq_step_txhash"):
        assert f"CREATE UNIQUE INDEX {index}" in sql
