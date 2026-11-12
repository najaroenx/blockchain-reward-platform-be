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
from marketplace.infrastructure.legacy_schema import customer, merchant

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
        session.execute(
            customer.insert().values(
                id="cus_1", phone_e164="+66812345678", phone_verified_at=datetime.now(UTC)
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
