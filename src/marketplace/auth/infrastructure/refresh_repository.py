from datetime import datetime
from sqlite3 import IntegrityError as SQLiteIntegrityError
from typing import Literal
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from marketplace.auth.application.dto import RefreshCommand
from marketplace.auth.application.exceptions import AuthRefusal
from marketplace.auth.domain.entities import (
    CustomerSubject,
    MerchantSubject,
    NewSession,
    RefreshCandidate,
)
from marketplace.auth.infrastructure.models import (
    AuthEvent,
    AuthSession,
    Customer,
    Merchant,
    MerchantAdmin,
)
from marketplace.auth.infrastructure.transactions import auth_transaction, aware


def duplicate_parent(error: IntegrityError) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == "uq_session_parent":
        return True
    return isinstance(error.orig, SQLiteIntegrityError) and (
        "UNIQUE constraint failed: auth_session.parent_id" in str(error.orig)
    )


class SqlRefreshStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def _revoke_family(
        self,
        session: Session,
        family_id: str,
        now: datetime,
        reason: Literal["ADMIN_REVOKED", "REUSE_DETECTED"],
    ) -> int:
        result = session.connection().execute(
            update(AuthSession)
            .where(AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=reason)
        )
        return result.rowcount

    def revoke_family(self, family_id: str, now: datetime) -> int:
        with auth_transaction(self.sessions, b"auth-family:" + family_id.encode()) as session:
            return self._revoke_family(session, family_id, now, "ADMIN_REVOKED")

    def _compromised(
        self, session: Session, current: AuthSession, now: datetime, ip_hash: bytes
    ) -> AuthRefusal:
        count = self._revoke_family(session, current.family_id, now, "REUSE_DETECTED")
        session.add(
            AuthEvent(
                id="aev_" + uuid4().hex,
                subject_kind=current.subject_kind,
                subject_id=current.merchant_admin_id or current.customer_id,
                kind="REUSE_DETECTED",
                session_id=current.id,
                ip_hash=ip_hash,
                created_at=now,
                metadata_={"familyId": current.family_id, "revokedCount": count},
            )
        )
        return AuthRefusal("SESSION_COMPROMISED")

    def _subject(
        self, session: Session, current: AuthSession, now: datetime
    ) -> MerchantSubject | CustomerSubject | AuthRefusal:
        if current.merchant_admin_id is not None:
            row = session.execute(
                select(MerchantAdmin, Merchant.status)
                .join(Merchant)
                .where(MerchantAdmin.id == current.merchant_admin_id)
                .with_for_update(of=MerchantAdmin)
            ).first()
            if row is None:
                return AuthRefusal("REFRESH_TOKEN_INVALID")
            admin, status = row
            subject: MerchantSubject | CustomerSubject = MerchantSubject(
                admin.id, admin.merchant_id, status
            )
            disabled = admin.disabled_at is not None
        else:
            customer = session.scalar(
                select(Customer).where(Customer.id == current.customer_id).with_for_update()
            )
            if customer is None:
                return AuthRefusal("REFRESH_TOKEN_INVALID")
            subject = CustomerSubject(customer.id)
            disabled = customer.disabled_at is not None
        if disabled:
            return AuthRefusal("REFRESH_TOKEN_REVOKED")
        return subject

    def _check(
        self, session: Session, current: AuthSession | None, now: datetime, ip_hash: bytes
    ) -> MerchantSubject | CustomerSubject | AuthRefusal:
        if current is None:
            return AuthRefusal("REFRESH_TOKEN_INVALID")
        subject = self._subject(session, current, now)
        if isinstance(subject, AuthRefusal):
            return subject
        if current.used_at is not None:
            return self._compromised(session, current, now, ip_hash)
        if current.revoked_at is not None:
            return AuthRefusal("REFRESH_TOKEN_REVOKED")
        if aware(current.expires_at) <= now:
            return AuthRefusal("REFRESH_TOKEN_EXPIRED")
        return subject

    def prepare(self, command: RefreshCommand, now: datetime) -> RefreshCandidate | AuthRefusal:
        with self.sessions() as session:
            family = session.scalar(
                select(AuthSession.family_id).where(
                    AuthSession.refresh_token_hash == command.token_hash
                )
            )
        if family is None:
            return AuthRefusal("REFRESH_TOKEN_INVALID")
        with auth_transaction(self.sessions, b"auth-family:" + family.encode()) as session:
            current = session.scalar(
                select(AuthSession).where(AuthSession.refresh_token_hash == command.token_hash)
            )
            subject = self._check(session, current, now, command.ip_hash)
            if isinstance(subject, AuthRefusal):
                return subject
            assert current is not None
            return RefreshCandidate(current.id, current.family_id, subject, current.user_agent_hash)

    def complete(
        self, candidate: RefreshCandidate, planned: NewSession, ip_hash: bytes, now: datetime
    ) -> AuthRefusal | None:
        scope = b"auth-family:" + candidate.family_id.encode()
        try:
            with auth_transaction(self.sessions, scope) as session:
                current = session.get(AuthSession, candidate.session_id)
                if current is None or current.family_id != candidate.family_id:
                    return AuthRefusal("REFRESH_TOKEN_INVALID")
                subject = self._check(session, current, now, ip_hash)
                if isinstance(subject, AuthRefusal):
                    return subject
                if (
                    type(subject) is not type(candidate.subject)
                    or subject.id != candidate.subject.id
                ):
                    return AuthRefusal("REFRESH_TOKEN_REVOKED")
                if isinstance(subject, MerchantSubject) and (
                    not isinstance(candidate.subject, MerchantSubject)
                    or subject.merchant_id != candidate.subject.merchant_id
                ):
                    return AuthRefusal("REFRESH_TOKEN_REVOKED")
                current.used_at = now
                current.revoked_at = now
                current.revoked_reason = "ROTATED"
                session.flush()
                session.add(
                    AuthSession(
                        id=planned.id,
                        subject_kind=current.subject_kind,
                        merchant_admin_id=current.merchant_admin_id,
                        customer_id=current.customer_id,
                        family_id=current.family_id,
                        refresh_token_hash=planned.refresh_hash,
                        parent_id=current.id,
                        user_agent_hash=candidate.user_agent_hash,
                        ip_hash=ip_hash,
                        created_at=planned.created_at,
                        expires_at=planned.expires_at,
                    )
                )
                session.flush()
                session.add(
                    AuthEvent(
                        id="aev_" + uuid4().hex,
                        subject_kind=current.subject_kind,
                        subject_id=subject.id,
                        kind="REFRESH",
                        session_id=planned.id,
                        ip_hash=ip_hash,
                        created_at=now,
                        metadata_={"parentId": current.id, "familyId": current.family_id},
                    )
                )
            return None
        except IntegrityError as error:
            if not duplicate_parent(error):
                raise
            # The unique index also guards writers outside this family's transaction lock.
            with auth_transaction(self.sessions, scope) as session:
                current = session.get(AuthSession, candidate.session_id)
                if current is None:
                    return AuthRefusal("REFRESH_TOKEN_INVALID")
                return self._compromised(session, current, now, ip_hash)
