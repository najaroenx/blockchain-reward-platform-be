"""Proves, at the database layer, the two guarantees restored in migration 0002 this round:
`uq_session_parent` (a token rotates into exactly one child) and `ck_one_subject` (a session
belongs to exactly one subject). These must hold without any application code involved — see
migrations/versions/0002_legacy_schema_port.py and src/marketplace/auth/infrastructure/models.py.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketplace.auth.infrastructure.models import AuthSession, MerchantAdmin
from marketplace.infrastructure.database import Database
from marketplace.infrastructure.legacy_schema import customer, merchant, otp_challenge

PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$aaaa"


def _future() -> datetime:
    return datetime.now(UTC) + timedelta(days=30)


def _seed_admin(session: Session) -> str:
    session.execute(merchant.insert().values(id="mch_1", name="test merchant"))
    session.add(
        MerchantAdmin(
            id="adm_1",
            merchant_id="mch_1",
            email="owner@shop.co.th",
            email_normalized="owner@shop.co.th",
            password_hash=PASSWORD_HASH,
            display_name="Owner",
        )
    )
    session.flush()
    return "adm_1"


def test_second_rotation_of_same_parent_is_rejected(database_url: str) -> None:
    database = Database(database_url)
    with database.sessions.begin() as session:
        admin_id = _seed_admin(session)
        session.add(
            AuthSession(
                id="ses_1",
                subject_kind="MERCHANT_ADMIN",
                merchant_admin_id=admin_id,
                family_id="fam_1",
                refresh_token_hash="0x" + "a" * 64,
                expires_at=_future(),
            )
        )
        session.add(
            AuthSession(
                id="ses_2",
                subject_kind="MERCHANT_ADMIN",
                merchant_admin_id=admin_id,
                family_id="fam_1",
                refresh_token_hash="0x" + "b" * 64,
                parent_id="ses_1",
                expires_at=_future(),
            )
        )

    with pytest.raises(IntegrityError), database.sessions.begin() as session:
        session.add(
            AuthSession(
                id="ses_evil",
                subject_kind="MERCHANT_ADMIN",
                merchant_admin_id=admin_id,
                family_id="fam_1",
                refresh_token_hash="0x" + "c" * 64,
                parent_id="ses_1",
                expires_at=_future(),
            )
        )
        session.flush()


def test_session_with_two_owners_is_rejected(database_url: str) -> None:
    database = Database(database_url)
    with database.sessions.begin() as session:
        admin_id = _seed_admin(session)
        now = datetime.now(UTC)
        # FEAT-02 F7 (migration 0004): a customer exists only through a verified challenge.
        session.execute(
            otp_challenge.insert().values(
                id="ch_1",
                phone_e164="+66812345678",
                code_hash="0x" + "f" * 64,
                pepper_ref="kms://otp-pepper/v1",
                status="VERIFIED",
                created_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(minutes=4),
                verified_at=now,
            )
        )
        session.execute(
            customer.insert().values(
                id="cus_1",
                phone_e164="+66812345678",
                phone_verified_at=now,
                verified_challenge_id="ch_1",
            )
        )

    with pytest.raises(IntegrityError), database.sessions.begin() as session:
        session.add(
            AuthSession(
                id="ses_bad",
                subject_kind="MERCHANT_ADMIN",
                merchant_admin_id=admin_id,
                customer_id="cus_1",
                family_id="fam_x",
                refresh_token_hash="0x" + "d" * 64,
                expires_at=_future(),
            )
        )
        session.flush()


def test_session_with_no_owner_is_rejected(database_url: str) -> None:
    database = Database(database_url)
    with pytest.raises(IntegrityError), database.sessions.begin() as session:
        session.add(
            AuthSession(
                id="ses_orphan",
                subject_kind="MERCHANT_ADMIN",
                family_id="fam_y",
                refresh_token_hash="0x" + "e" * 64,
                expires_at=_future(),
            )
        )
        session.flush()


def test_unknown_revoke_reason_is_rejected(database_url: str) -> None:
    """FEAT-03 T4b (migration 0005): revoked_reason is one of the six known reasons."""
    database = Database(database_url)
    with database.sessions.begin() as session:
        admin_id = _seed_admin(session)

    with (
        pytest.raises(IntegrityError, match="ck_auth_session_revoked_reason_enum"),
        database.sessions.begin() as session,
    ):
        session.add(
            AuthSession(
                id="ses_typo",
                subject_kind="MERCHANT_ADMIN",
                merchant_admin_id=admin_id,
                family_id="fam_z",
                refresh_token_hash="0x" + "f" * 64,
                expires_at=_future(),
                revoked_at=datetime.now(UTC),
                revoked_reason="REUSED",
            )
        )
        session.flush()
