from collections.abc import Callable
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from marketplace.auth.application.exceptions import (
    AccountLocked,
    InvalidCredentials,
    RefreshTokenRevoked,
    SessionCompromised,
)
from marketplace.auth.application.ports import TokenSigner
from marketplace.auth.application.use_cases import AuthService, LoginContext
from marketplace.auth.infrastructure.hashing import Argon2PasswordHasher
from marketplace.auth.infrastructure.models import AuthSession, MerchantAdmin
from marketplace.auth.infrastructure.repository import (
    LOCKOUT_THRESHOLD,
    SqlAdminAccountReader,
    SqlAuthSessionStore,
    SqlLoginAttemptTracker,
)
from marketplace.infrastructure.database import Database

PASSWORD = "correct-horse-battery-staple"
IP = b"\x01" * 32


def _build_service(session: Session, signer: TokenSigner) -> AuthService:
    return AuthService(
        SqlAdminAccountReader(session),
        SqlAuthSessionStore(session),
        SqlLoginAttemptTracker(session),
        Argon2PasswordHasher(),
        signer,
    )


def _seed_admin(session: Session, seed_merchant: Callable[[Session], None]) -> str:
    seed_merchant(session)
    session.add(
        MerchantAdmin(
            id="adm_1",
            merchant_id="mch_1",
            email="owner@shop.co.th",
            email_normalized="owner@shop.co.th",
            password_hash=Argon2PasswordHasher().hash(PASSWORD),
            display_name="Owner",
        )
    )
    session.flush()
    return "adm_1"


def test_login_issues_tokens_and_creates_a_session(database_url, signer, seed_merchant) -> None:
    database = Database(database_url)
    with database.sessions.begin() as session:
        _seed_admin(session, seed_merchant)

    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        result = service.login("owner@shop.co.th", PASSWORD, LoginContext(ip_hash=IP))

    assert result.subject.id == "adm_1"
    assert result.merchant_status == "PENDING"
    assert result.access_token.startswith("MERCHANT_ADMIN:adm_1:")


def test_unknown_email_and_wrong_password_raise_the_same_error(
    database_url, signer, seed_merchant
) -> None:
    database = Database(database_url)
    with database.sessions.begin() as session:
        _seed_admin(session, seed_merchant)

    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        with pytest.raises(InvalidCredentials):
            service.login("nobody@shop.co.th", "whatever", LoginContext(ip_hash=IP))

    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        with pytest.raises(InvalidCredentials):
            service.login("owner@shop.co.th", "wrong-password", LoginContext(ip_hash=IP))


def test_account_locks_after_threshold_failures(database_url, signer, seed_merchant) -> None:
    database = Database(database_url)
    with database.sessions.begin() as session:
        _seed_admin(session, seed_merchant)

    for _ in range(LOCKOUT_THRESHOLD):
        with database.sessions.begin() as session:
            service = _build_service(session, signer)
            with pytest.raises(InvalidCredentials):
                service.login("owner@shop.co.th", "wrong-password", LoginContext(ip_hash=IP))

    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        with pytest.raises(AccountLocked):
            service.login("owner@shop.co.th", PASSWORD, LoginContext(ip_hash=IP))


def test_refresh_rotates_the_token(database_url, signer, seed_merchant) -> None:
    database = Database(database_url)
    with database.sessions.begin() as session:
        _seed_admin(session, seed_merchant)

    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        login_result = service.login("owner@shop.co.th", PASSWORD, LoginContext(ip_hash=IP))

    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        refreshed = service.refresh(login_result.refresh_token, ip_hash=IP)

    assert refreshed.refresh_token != login_result.refresh_token
    assert refreshed.subject.id == "adm_1"


def test_replaying_a_used_refresh_token_revokes_the_whole_family(
    database_url, signer, seed_merchant
) -> None:
    database = Database(database_url)
    with database.sessions.begin() as session:
        _seed_admin(session, seed_merchant)

    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        login_result = service.login("owner@shop.co.th", PASSWORD, LoginContext(ip_hash=IP))

    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        refreshed = service.refresh(login_result.refresh_token, ip_hash=IP)

    # The old token has already rotated once — presenting it again looks like theft. The
    # exception is caught *inside* the session scope, same as `provide_catalog` in main.py: the
    # revocation this raises from must still commit, so the session must not see an uncaught
    # exception and roll it back.
    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        with pytest.raises(SessionCompromised):
            service.refresh(login_result.refresh_token, ip_hash=IP)

    # The whole family is gone, including the child that was issued moments ago.
    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        with pytest.raises(RefreshTokenRevoked):
            service.refresh(refreshed.refresh_token, ip_hash=IP)


def test_logout_revokes_only_that_session(database_url, signer, seed_merchant) -> None:
    database = Database(database_url)
    with database.sessions.begin() as session:
        _seed_admin(session, seed_merchant)

    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        login_result = service.login("owner@shop.co.th", PASSWORD, LoginContext(ip_hash=IP))
    session_id = login_result.access_token.rsplit(":", 1)[-1]

    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        service.logout(session_id)

    with pytest.raises(RefreshTokenRevoked), database.sessions.begin() as session:
        service = _build_service(session, signer)
        service.refresh(login_result.refresh_token, ip_hash=IP)


def test_refresh_of_a_disabled_account_revokes_all_its_sessions(
    database_url, signer, seed_merchant
) -> None:
    """FEAT-03 T14: no live session survives for a disabled account."""
    database = Database(database_url)
    with database.sessions.begin() as session:
        _seed_admin(session, seed_merchant)

    # Two devices, two families.
    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        phone = service.login("owner@shop.co.th", PASSWORD, LoginContext(ip_hash=IP))
        laptop = service.login("owner@shop.co.th", PASSWORD, LoginContext(ip_hash=IP))

    with database.sessions.begin() as session:
        session.execute(
            sa.update(MerchantAdmin)
            .where(MerchantAdmin.id == "adm_1")
            .values(disabled_at=datetime.now(UTC))
        )

    # Caught inside the session scope, so the revocation commits (see the reuse test above).
    with database.sessions.begin() as session:
        service = _build_service(session, signer)
        with pytest.raises(RefreshTokenRevoked):
            service.refresh(phone.refresh_token, ip_hash=IP)

    with database.sessions.begin() as session:
        rows = session.execute(
            sa.select(AuthSession.revoked_reason).where(AuthSession.merchant_admin_id == "adm_1")
        ).all()
        assert [reason for (reason,) in rows] == ["ADMIN_REVOKED", "ADMIN_REVOKED"]

        service = _build_service(session, signer)
        with pytest.raises(RefreshTokenRevoked):
            service.refresh(laptop.refresh_token, ip_hash=IP)
