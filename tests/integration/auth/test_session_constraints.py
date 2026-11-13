from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Barrier

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from marketplace.auth.infrastructure.models import (
    AuthSession,
    Customer,
    LoginAttempt,
    Merchant,
    MerchantAdmin,
)
from marketplace.auth.infrastructure.refresh_repository import SqlRefreshStore
from marketplace.auth.infrastructure.transactions import auth_transaction
from marketplace.infrastructure.database import Database

NOW = datetime(2026, 11, 12, tzinfo=UTC)


@pytest.fixture
def auth_db(database_url: str):
    database = Database(database_url)
    with database.sessions.begin() as session:
        session.add_all(
            [Merchant(id="mch", name="Merchant"), Customer(id="cus", tel_hash=b"x" * 32)]
        )
        session.flush()
        session.add(
            MerchantAdmin(
                id="adm",
                merchant_id="mch",
                email="owner@example.test",
                email_normalized="owner@example.test",
                password_hash="$argon2id$fixture",
                display_name="Owner",
            )
        )
    yield database
    database.close()


def new_session(id: str, **overrides) -> AuthSession:
    values = dict(
        id=id,
        subject_kind="MERCHANT_ADMIN",
        merchant_admin_id="adm",
        family_id="family",
        refresh_token_hash=sha256(id.encode()).digest(),
        created_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    values.update(overrides)
    return AuthSession(**values)


def test_t1_refresh_hash_cannot_be_reused(auth_db: Database) -> None:
    with auth_db.sessions.begin() as session:
        session.add(new_session("first"))
    with (
        pytest.raises(IntegrityError, match="auth_session.refresh_token_hash|uq_session_token"),
        auth_db.sessions.begin() as session,
    ):
        session.add(new_session("duplicate", refresh_token_hash=sha256(b"first").digest()))


def test_t2_only_one_child_may_rotate_a_parent(auth_db: Database) -> None:
    with auth_db.sessions.begin() as session:
        session.add(new_session("parent"))
    with auth_db.sessions.begin() as session:
        session.add(new_session("child1", parent_id="parent"))
    with (
        pytest.raises(IntegrityError, match="auth_session.parent_id|uq_session_parent"),
        auth_db.sessions.begin() as session,
    ):
        session.add(new_session("child2", parent_id="parent"))


def test_t3_family_revocation_leaves_no_live_member(auth_db: Database) -> None:
    with auth_db.sessions.begin() as session:
        session.add_all(
            [new_session("first"), new_session("second"), new_session("other", family_id="other")]
        )
    store = SqlRefreshStore(auth_db.sessions)
    assert store.revoke_family("family", NOW) == 2
    assert store.revoke_family("family", NOW) == 0
    with auth_db.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.family_id == "family", AuthSession.revoked_at.is_(None))
            )
            == 0
        )
        assert session.get(AuthSession, "other").revoked_at is None


@pytest.mark.parametrize("fields", [{"revoked_at": NOW}, {"revoked_reason": "LOGOUT"}])
def test_t4_revocation_requires_both_timestamp_and_reason(auth_db: Database, fields: dict) -> None:
    with (
        pytest.raises(IntegrityError, match="ck_revoke_has_reason"),
        auth_db.sessions.begin() as session,
    ):
        session.add(new_session("bad-revocation", **fields))


@pytest.mark.parametrize("fields", [{"merchant_admin_id": None}, {"customer_id": "cus"}])
def test_t5_session_requires_exactly_one_owner(auth_db: Database, fields: dict) -> None:
    with (
        pytest.raises(IntegrityError, match="ck_one_subject|ck_subject_matches"),
        auth_db.sessions.begin() as session,
    ):
        session.add(new_session("bad-owner", **fields))


def test_t8_database_has_no_plaintext_access_or_refresh_token_columns(auth_db: Database) -> None:
    inspector = inspect(auth_db.engine)
    for table in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns(table)}
        assert "access_token" not in columns
        assert "refresh_token" not in columns
    columns = {column["name"] for column in inspector.get_columns("login_attempt")}
    assert not columns & {"email", "ip", "ip_address", "password"}


def test_foreign_key_owner_is_enforced(auth_db: Database) -> None:
    with (
        pytest.raises(IntegrityError, match="FOREIGN KEY|foreign key"),
        auth_db.sessions.begin() as session,
    ):
        session.add(new_session("nonexistent-owner", merchant_admin_id="missing"))


@pytest.mark.parametrize("expiry", [NOW, NOW - timedelta(seconds=1)])
def test_t9_expiry_must_follow_creation(auth_db: Database, expiry: datetime) -> None:
    with (
        pytest.raises(IntegrityError, match="ck_expiry_after_create"),
        auth_db.sessions.begin() as session,
    ):
        session.add(new_session("bad-expiry", expires_at=expiry))


@pytest.mark.parametrize("password", ["$2b$10$abcdefghijklmnopqrstuv", "plaintext-password"])
def test_t10_bcrypt_and_plaintext_passwords_are_rejected(auth_db: Database, password: str) -> None:
    with (
        pytest.raises(IntegrityError, match="ck_admin_password_argon2id"),
        auth_db.sessions.begin() as session,
    ):
        session.add(
            MerchantAdmin(
                id="bad-admin",
                merchant_id="mch",
                email="bad@example.test",
                email_normalized="bad@example.test",
                password_hash=password,
                display_name="Invalid",
            )
        )


@pytest.mark.parametrize("success,code", [(True, "BAD_CREDENTIALS"), (False, None)])
def test_t11_attempt_success_and_failure_code_are_consistent(
    auth_db: Database, success: bool, code: str | None
) -> None:
    with (
        pytest.raises(IntegrityError, match="ck_failure_code"),
        auth_db.sessions.begin() as session,
    ):
        session.add(
            LoginAttempt(
                id="bad-attempt",
                identifier_hash=sha256(b"id").digest(),
                ip_hash=sha256(b"ip").digest(),
                succeeded=success,
                failure_code=code,
            )
        )


def test_t12_attempt_identifiers_are_only_hashes(auth_db: Database) -> None:
    import re

    columns = inspect(auth_db.engine).get_columns("login_attempt")
    identifying = [
        column["name"]
        for column in columns
        if re.search(r"email|ip|identifier|tel|phone", column["name"])
    ]
    assert identifying and all(name.endswith("_hash") for name in identifying)


@pytest.mark.parametrize("kind", ["admin", "customer"])
def test_t14_disabling_owner_revokes_all_sessions_and_blocks_new_live_rows(
    auth_db: Database, kind: str
) -> None:
    owner_fields = (
        {}
        if kind == "admin"
        else {"subject_kind": "CUSTOMER", "merchant_admin_id": None, "customer_id": "cus"}
    )
    with auth_db.sessions.begin() as session:
        session.add_all(
            [
                new_session("first", **owner_fields),
                new_session("second", family_id="other", **owner_fields),
            ]
        )
    with auth_db.sessions.begin() as session:
        owner = session.get(
            MerchantAdmin if kind == "admin" else Customer, "adm" if kind == "admin" else "cus"
        )
        owner.disabled_at = NOW
    with auth_db.sessions() as session:
        rows = session.scalars(select(AuthSession)).all()
        assert all(
            row.revoked_at is not None and row.revoked_reason == "ADMIN_REVOKED" for row in rows
        )
    with (
        pytest.raises(IntegrityError, match="ck_session_owner_enabled"),
        auth_db.sessions.begin() as session,
    ):
        session.add(new_session("new-disabled-owner-session", **owner_fields))
    with (
        pytest.raises(IntegrityError, match="ck_session_owner_enabled"),
        auth_db.sessions.begin() as session,
    ):
        row = session.get(AuthSession, "first")
        row.revoked_at = None
        row.revoked_reason = None


def test_session_cannot_be_reassigned_to_a_disabled_owner(auth_db: Database) -> None:
    with auth_db.sessions.begin() as session:
        session.add(new_session("active"))
        session.add(
            MerchantAdmin(
                id="disabled",
                merchant_id="mch",
                email="disabled@example.test",
                email_normalized="disabled@example.test",
                password_hash="$argon2id$fixture",
                display_name="Disabled",
                disabled_at=NOW,
            )
        )
    with (
        pytest.raises(IntegrityError, match="ck_session_owner_enabled"),
        auth_db.sessions.begin() as session,
    ):
        session.get(AuthSession, "active").merchant_admin_id = "disabled"


def test_t14_concurrent_session_inserts_and_account_disable_leave_no_live_sessions(
    auth_db: Database,
) -> None:
    start = Barrier(21)

    def insert(index: int) -> str:
        start.wait(timeout=10)
        try:
            with auth_transaction(auth_db.sessions, f"test-insert:{index}".encode()) as session:
                session.add(new_session("concurrent-" + str(index)))
            return "created"
        except IntegrityError as error:
            assert "ck_session_owner_enabled" in str(error.orig)
            return "disabled"

    def disable() -> str:
        start.wait(timeout=10)
        with auth_transaction(auth_db.sessions, b"test-disable") as session:
            session.get(MerchantAdmin, "adm").disabled_at = NOW
        return "account-disabled"

    with ThreadPoolExecutor(max_workers=21) as pool:
        requests = [pool.submit(insert, index) for index in range(20)]
        disabled = pool.submit(disable)
        results = [request.result(timeout=15) for request in requests]
        assert disabled.result(timeout=15) == "account-disabled"
    assert len(results) == 20 and set(results) <= {"created", "disabled"}
    with auth_db.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.revoked_at.is_(None))
            )
            == 0
        )
