from datetime import datetime, timedelta
from hashlib import sha256
from math import ceil
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from marketplace.auth.application.dto import LoginCommand
from marketplace.auth.application.exceptions import AuthRefusal
from marketplace.auth.domain.entities import (
    AdminCredentials,
    Attempt,
    LoginGrant,
    LoginPolicy,
    MerchantSubject,
)
from marketplace.auth.infrastructure.models import (
    AuthEvent,
    AuthSession,
    LoginAttempt,
    LoginLockout,
    Merchant,
    MerchantAdmin,
)
from marketplace.auth.infrastructure.transactions import auth_transaction, aware


class SqlLoginStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def reserve(
        self, command: LoginCommand, now: datetime, policy: LoginPolicy
    ) -> Attempt | AuthRefusal:
        identifier = sha256(command.normalized_email.encode()).digest()
        with auth_transaction(self.sessions, b"login-ip:" + command.ip_hash) as session:
            since = now - timedelta(seconds=policy.ip_window)
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(LoginAttempt)
                    .where(LoginAttempt.ip_hash == command.ip_hash, LoginAttempt.created_at > since)
                )
                or 0
            )
            if count >= policy.ip_limit:
                oldest = session.scalar(
                    select(func.min(LoginAttempt.created_at)).where(
                        LoginAttempt.ip_hash == command.ip_hash, LoginAttempt.created_at > since
                    )
                )
                retry = (
                    max(
                        1,
                        ceil(
                            (
                                aware(oldest) + timedelta(seconds=policy.ip_window) - now
                            ).total_seconds()
                        ),
                    )
                    if oldest
                    else policy.ip_window
                )
                return AuthRefusal("RATE_LIMITED", retry)
            ticket = Attempt(
                "la_" + uuid4().hex, identifier, command.ip_hash, command.user_agent_hash, now
            )
            record = LoginAttempt(
                id=ticket.id,
                identifier_hash=identifier,
                ip_hash=command.ip_hash,
                succeeded=False,
                failure_code="PENDING",
                created_at=now,
            )
            session.add(record)
            lock = session.get(LoginLockout, identifier)
            if lock and aware(lock.locked_until) > now:
                record.failure_code = "ACCOUNT_LOCKED"
                return AuthRefusal(
                    "ACCOUNT_LOCKED", max(1, ceil((aware(lock.locked_until) - now).total_seconds()))
                )
            return ticket

    def credentials(self, normalized_email: str) -> AdminCredentials | None:
        with self.sessions() as session:
            row = session.execute(
                select(MerchantAdmin, Merchant.status)
                .join(Merchant)
                .where(MerchantAdmin.email_normalized == normalized_email)
            ).first()
            if row is None:
                return None
            admin, status = row
            return AdminCredentials(
                MerchantSubject(admin.id, admin.merchant_id, status),
                admin.password_hash,
                admin.disabled_at is not None,
            )

    def complete(
        self,
        attempt: Attempt,
        grant: LoginGrant | None,
        now: datetime,
        policy: LoginPolicy,
    ) -> MerchantSubject | AuthRefusal:
        with auth_transaction(
            self.sessions, b"login-identifier:" + attempt.identifier_hash
        ) as session:
            record = session.get(LoginAttempt, attempt.id)
            assert record is not None
            lock = session.get(LoginLockout, attempt.identifier_hash)
            if lock and aware(lock.locked_until) > now:
                record.failure_code = "ACCOUNT_LOCKED"
                return AuthRefusal(
                    "ACCOUNT_LOCKED", max(1, ceil((aware(lock.locked_until) - now).total_seconds()))
                )
            candidate = grant.credentials if grant else None
            current = (
                session.execute(
                    select(MerchantAdmin, Merchant.status)
                    .join(Merchant)
                    .where(MerchantAdmin.id == candidate.subject.id)
                    .with_for_update(of=MerchantAdmin)
                ).first()
                if candidate
                else None
            )
            if (
                candidate is None
                or current is None
                or current[0].disabled_at is not None
                or current[0].password_hash != candidate.password_hash
                or current[0].merchant_id != candidate.subject.merchant_id
                or sha256(current[0].email_normalized.encode()).digest() != attempt.identifier_hash
            ):
                record.failure_code = "BAD_CREDENTIALS"
                record.counts_for_lockout = True
                session.flush()
                since = now - timedelta(seconds=policy.lockout_seconds)
                count = (
                    session.scalar(
                        select(func.count())
                        .select_from(LoginAttempt)
                        .where(
                            LoginAttempt.identifier_hash == attempt.identifier_hash,
                            LoginAttempt.failure_code == "BAD_CREDENTIALS",
                            LoginAttempt.counts_for_lockout.is_(True),
                            LoginAttempt.created_at > since,
                        )
                    )
                    or 0
                )
                if count >= policy.lockout_threshold:
                    if lock is None:
                        lock = LoginLockout(identifier_hash=attempt.identifier_hash)
                        session.add(lock)
                    lock.locked_until = now + timedelta(seconds=policy.lockout_seconds)
                    lock.attempt_count = count
                    lock.updated_at = now
                elif lock:
                    session.delete(lock)
                return AuthRefusal("INVALID_CREDENTIALS")
            assert grant is not None
            planned_session = grant.session
            admin, status = current
            session.add(
                AuthSession(
                    id=planned_session.id,
                    subject_kind="MERCHANT_ADMIN",
                    merchant_admin_id=admin.id,
                    family_id=planned_session.family_id,
                    refresh_token_hash=planned_session.refresh_hash,
                    user_agent_hash=attempt.user_agent_hash,
                    ip_hash=attempt.ip_hash,
                    created_at=planned_session.created_at,
                    expires_at=planned_session.expires_at,
                )
            )
            session.flush()
            record.succeeded = True
            record.failure_code = None
            admin.last_login_at = now
            session.execute(
                update(LoginAttempt)
                .where(LoginAttempt.identifier_hash == attempt.identifier_hash)
                .values(counts_for_lockout=False)
            )
            session.execute(
                delete(LoginLockout).where(LoginLockout.identifier_hash == attempt.identifier_hash)
            )
            session.add(
                AuthEvent(
                    id="aev_" + uuid4().hex,
                    subject_kind="MERCHANT_ADMIN",
                    subject_id=admin.id,
                    kind="LOGIN_OK",
                    session_id=planned_session.id,
                    ip_hash=attempt.ip_hash,
                    created_at=now,
                )
            )
            return MerchantSubject(admin.id, admin.merchant_id, status)

    def abort(self, attempt: Attempt) -> None:
        with auth_transaction(
            self.sessions, b"login-identifier:" + attempt.identifier_hash
        ) as session:
            session.execute(
                update(LoginAttempt)
                .where(LoginAttempt.id == attempt.id)
                .values(failure_code="AUTH_UNAVAILABLE")
            )
