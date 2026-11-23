from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import func, inspect, select, update
from sqlalchemy.exc import IntegrityError

from marketplace.auth.infrastructure.models import AuthSession, Customer, Merchant, MerchantAdmin
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
    for expected in (2, 0):
        with auth_db.sessions.begin() as session:
            result = session.execute(
                update(AuthSession)
                .where(AuthSession.family_id == "family", AuthSession.revoked_at.is_(None))
                .values(revoked_at=NOW, revoked_reason="ADMIN_REVOKED")
            )
            assert result.rowcount == expected
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


@pytest.mark.parametrize(
    "fields",
    [
        {"subject_kind": "CUSTOMER"},
        {"subject_kind": "MERCHANT_ADMIN", "merchant_admin_id": None, "customer_id": "cus"},
    ],
)
def test_t6_subject_kind_matches_the_single_owner(auth_db: Database, fields: dict) -> None:
    with (
        pytest.raises(IntegrityError, match="ck_subject_matches"),
        auth_db.sessions.begin() as session,
    ):
        session.add(new_session("wrong-kind", **fields))


def test_t7_session_token_columns_store_only_hashes(auth_db: Database) -> None:
    names = {column["name"] for column in inspect(auth_db.engine).get_columns("auth_session")}
    assert {name for name in names if "token" in name} == {"refresh_token_hash"}
