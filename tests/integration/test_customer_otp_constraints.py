"""Proves the FEAT-02 database guarantees from migration 0004 that hold on both dialects.

Test numbers follow tests/sql/feat02_acceptance.sql (from reference/feat-02). These run on SQLite,
like the rest of the suite. The PostgreSQL-only parts of 0004 are checked here only in the generated
DDL; their behaviour is proved by tests/sql/feat02_acceptance.sql against a real PostgreSQL:

  - the E.164 / kms:// regex CHECKs (T2 T3 T5 T11),
  - the triggers trg_otp_challenge_guard (T24 T25), trg_customer_from_verified_challenge (T28 T30)
    and trg_customer_guard (T31),
  - fk_customer_verified_challenge (T27): SQLite only enforces foreign keys with
    `PRAGMA foreign_keys=ON`, which this project does not turn on.
"""

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from marketplace.infrastructure.database import Database
from marketplace.infrastructure.legacy_schema import customer, operation, otp_challenge

PHONE = "+66819990001"


def _challenge(id_: str, phone: str = PHONE, **values: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": id_,
        "phone_e164": phone,
        "code_hash": "0x" + "c" * 64,
        "pepper_ref": "kms://otp-pepper/v1",
        "created_at": now - timedelta(seconds=10),
        "expires_at": now + timedelta(minutes=5),
        **values,
    }


def _verified(id_: str, phone: str = PHONE) -> dict[str, Any]:
    return _challenge(id_, phone, status="VERIFIED", attempts=1, verified_at=datetime.now(UTC))


def _customer(id_: str, challenge_id: str | None, phone: str = PHONE) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": id_,
        "phone_e164": phone,
        "phone_verified_at": datetime.now(UTC),
    }
    if challenge_id is not None:
        values["verified_challenge_id"] = challenge_id
    return values


def _insert(database: Database, table: Any, **values: Any) -> None:
    with database.sessions.begin() as session:
        session.execute(table.insert().values(**values))


# --- T14 · uq_challenge_live ----------------------------------------------------------------


def test_t14_second_pending_challenge_for_same_phone_is_rejected(database_url: str) -> None:
    database = Database(database_url)
    _insert(database, otp_challenge, **_challenge("ch_1"))

    with pytest.raises(IntegrityError, match="phone_e164"):
        _insert(database, otp_challenge, **_challenge("ch_2"))
    database.close()


def test_t14_new_challenge_allowed_once_the_previous_one_is_no_longer_pending(
    database_url: str,
) -> None:
    database = Database(database_url)
    _insert(database, otp_challenge, **_challenge("ch_1", status="EXPIRED"))

    _insert(database, otp_challenge, **_challenge("ch_2"))
    database.close()


# --- T26 · customer.verified_challenge_id NOT NULL ------------------------------------------


def test_t26_customer_without_a_challenge_is_rejected(database_url: str) -> None:
    database = Database(database_url)

    with pytest.raises(IntegrityError, match="verified_challenge_id"):
        _insert(database, customer, **_customer("cus_1", challenge_id=None))
    database.close()


# --- T29 · T30 · uq_customer_challenge ------------------------------------------------------


def test_t29_customer_registers_through_a_verified_challenge(database_url: str) -> None:
    database = Database(database_url)
    _insert(database, otp_challenge, **_verified("ch_1"))

    _insert(database, customer, **_customer("cus_1", "ch_1"))
    database.close()


def test_t30_one_challenge_cannot_register_two_customers(database_url: str) -> None:
    database = Database(database_url)
    _insert(database, otp_challenge, **_verified("ch_1"))
    _insert(database, customer, **_customer("cus_1", "ch_1"))

    # A different phone, so only the challenge can collide (not customer.phone_e164).
    with pytest.raises(IntegrityError, match="verified_challenge_id"):
        _insert(database, customer, **_customer("cus_2", "ch_1", phone="+66819990002"))
    database.close()


# --- T36 · uq_provision_inflight widened to CUSTOMER_PROVISION ------------------------------


def _provision(id_: str, kind: str, subject: str) -> dict[str, Any]:
    return {
        "id": id_,
        "actor_scope": "system:x",
        "kind": kind,
        "subject_id": subject,
        "payload": {},
    }


def test_t36_second_unfinished_customer_provision_is_rejected(database_url: str) -> None:
    database = Database(database_url)
    _insert(database, operation, **_provision("op_1", "CUSTOMER_PROVISION", "cus_1"))

    with pytest.raises(IntegrityError, match="subject_id"):
        _insert(database, operation, **_provision("op_2", "CUSTOMER_PROVISION", "cus_1"))
    database.close()


def test_t36_merchant_and_customer_provisions_do_not_collide(database_url: str) -> None:
    database = Database(database_url)
    _insert(database, operation, **_provision("op_1", "CUSTOMER_PROVISION", "same_id"))

    _insert(database, operation, **_provision("op_2", "MERCHANT_PROVISION", "same_id"))
    database.close()


# --- PostgreSQL-only parts, checked in the generated DDL ------------------------------------


def test_postgresql_only_checks_and_triggers_are_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    output = io.StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()

    for name in (
        "ck_customer_phone_e164",
        "ck_otp_challenge_phone_e164",
        "ck_phone_throttle_phone_e164",
        "ck_otp_challenge_pepper_ref",
        "ck_wallet_kms_key_ref",
        "fk_customer_verified_challenge",
        "CREATE TRIGGER trg_otp_challenge_guard",
        "CREATE TRIGGER trg_customer_from_verified_challenge",
        "CREATE TRIGGER trg_customer_guard",
        "CREATE UNIQUE INDEX uq_challenge_live",
    ):
        assert name in sql, name
