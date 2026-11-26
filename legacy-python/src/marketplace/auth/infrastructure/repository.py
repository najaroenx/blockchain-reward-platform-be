import hashlib
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketplace.auth.application.dto import StoredSession
from marketplace.auth.infrastructure.models import (
    AuthEvent,
    AuthSession,
    LoginAttempt,
    LoginLockout,
    MerchantAdmin,
)
from marketplace.infrastructure.legacy_schema import merchant

LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW_SECONDS = 900

# Revoke reasons that end a session map 1:1 to an audit-event kind. ROTATED has no event of its
# own — the child row's own LOGIN_OK/REFRESH event already records the rotation.
_EVENT_KIND_FOR_REVOKE = {
    "LOGOUT": "LOGOUT",
    "REUSE_DETECTED": "REUSE_DETECTED",
    "ADMIN_REVOKED": "ADMIN_REVOKED",
    "PASSWORD_CHANGED": "PASSWORD_CHANGED",
}


def _record_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _hash_hex(raw: bytes) -> str:
    """This project's convention for hash-shaped columns: lowercase `0x` + 64 hex chars."""
    return "0x" + raw.hex()


def _sha256_hex(value: str) -> str:
    return _hash_hex(hashlib.sha256(value.encode()).digest())


def _aware(value: datetime) -> datetime:
    """SQLite's `DateTime(timezone=True)` drops tzinfo on read regardless of what was written —
    this project stores every timestamp as UTC (`TS` in models.py), so a naive value read back is
    UTC, not local time. PostgreSQL round-trips tzinfo correctly; this is a no-op there."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class _AdminAccountRow(NamedTuple):
    id: str
    merchant_id: str
    password_hash: str
    disabled_at: datetime | None
    merchant_status: str


class SqlAdminAccountReader:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_email(self, email_normalized: str) -> _AdminAccountRow | None:
        row = self.session.execute(
            select(
                MerchantAdmin.id,
                MerchantAdmin.merchant_id,
                MerchantAdmin.password_hash,
                MerchantAdmin.disabled_at,
                merchant.c.status,
            )
            .join(merchant, merchant.c.id == MerchantAdmin.merchant_id)
            .where(MerchantAdmin.email_normalized == email_normalized)
        ).first()
        return _AdminAccountRow(*row) if row is not None else None

    def is_disabled(self, admin_id: str) -> bool:
        disabled_at = self.session.execute(
            select(MerchantAdmin.disabled_at).where(MerchantAdmin.id == admin_id)
        ).first()
        return disabled_at is None or disabled_at[0] is not None

    def touch_last_login(self, admin_id: str) -> None:
        admin = self.session.get(MerchantAdmin, admin_id)
        if admin is not None:
            admin.last_login_at = datetime.now(UTC)
        self.session.flush()


def _to_stored(row: AuthSession) -> StoredSession:
    return StoredSession(
        id=row.id,
        subject_kind=row.subject_kind,  # type: ignore[arg-type]
        merchant_admin_id=row.merchant_admin_id,
        family_id=row.family_id,
        created_at=_aware(row.created_at),
        expires_at=_aware(row.expires_at),
        used_at=_aware(row.used_at) if row.used_at is not None else None,
        revoked_at=_aware(row.revoked_at) if row.revoked_at is not None else None,
    )


class SqlAuthSessionStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        session_id: str,
        admin_id: str,
        family_id: str,
        refresh_token: str,
        expires_at: datetime,
        user_agent_hash: bytes | None,
        ip_hash: bytes | None,
    ) -> None:
        self.session.add(
            AuthSession(
                id=session_id,
                subject_kind="MERCHANT_ADMIN",
                merchant_admin_id=admin_id,
                family_id=family_id,
                refresh_token_hash=_sha256_hex(refresh_token),
                user_agent_hash=user_agent_hash,
                ip_hash=ip_hash,
                expires_at=expires_at,
            )
        )
        self._record_event("LOGIN_OK", "MERCHANT_ADMIN", admin_id, session_id, ip_hash)
        self.session.flush()

    def find_by_token(self, refresh_token: str) -> StoredSession | None:
        row = self.session.scalar(
            select(AuthSession).where(AuthSession.refresh_token_hash == _sha256_hex(refresh_token))
        )
        return _to_stored(row) if row is not None else None

    def rotate(
        self,
        current: StoredSession,
        *,
        next_session_id: str,
        refresh_token: str,
        expires_at: datetime,
        ip_hash: bytes | None,
    ) -> None:
        row = self.session.get(AuthSession, current.id)
        assert row is not None
        row.used_at = datetime.now(UTC)
        row.revoked_at = datetime.now(UTC)
        row.revoked_reason = "ROTATED"

        # `uq_session_parent` (migrations/versions/0002_legacy_schema_port.py) is what actually
        # rejects a second rotation of the same parent — this INSERT is where that would surface,
        # not a pre-check here.
        self.session.add(
            AuthSession(
                id=next_session_id,
                subject_kind=row.subject_kind,
                merchant_admin_id=row.merchant_admin_id,
                customer_id=row.customer_id,
                family_id=row.family_id,
                refresh_token_hash=_sha256_hex(refresh_token),
                parent_id=row.id,
                user_agent_hash=row.user_agent_hash,
                ip_hash=ip_hash,
                expires_at=expires_at,
            )
        )
        self._record_event(
            "REFRESH",
            row.subject_kind,
            row.merchant_admin_id or row.customer_id,
            next_session_id,
            ip_hash,
        )
        self.session.flush()

    def revoke_family(self, family_id: str, *, reason: str) -> int:
        rows = self.session.scalars(
            select(AuthSession).where(
                AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None)
            )
        ).all()
        now = datetime.now(UTC)
        for row in rows:
            row.revoked_at = now
            row.revoked_reason = reason
        if rows:
            first = rows[0]
            owner = first.merchant_admin_id or first.customer_id
            self._record_revoke_event(reason, first.subject_kind, owner)
        self.session.flush()
        return len(rows)

    def revoke(self, session_id: str, *, reason: str) -> None:
        row = self.session.get(AuthSession, session_id)
        if row is None or row.revoked_at is not None:
            return
        row.revoked_at = datetime.now(UTC)
        row.revoked_reason = reason
        owner = row.merchant_admin_id or row.customer_id
        self._record_revoke_event(reason, row.subject_kind, owner)
        self.session.flush()

    def revoke_all_for_admin(self, admin_id: str, *, reason: str) -> int:
        rows = self.session.scalars(
            select(AuthSession).where(
                AuthSession.merchant_admin_id == admin_id, AuthSession.revoked_at.is_(None)
            )
        ).all()
        now = datetime.now(UTC)
        for row in rows:
            row.revoked_at = now
            row.revoked_reason = reason
        if rows:
            self._record_revoke_event(reason, "MERCHANT_ADMIN", admin_id)
        self.session.flush()
        return len(rows)

    def _record_revoke_event(
        self, reason: str, subject_kind: str | None, subject_id: str | None
    ) -> None:
        kind = _EVENT_KIND_FOR_REVOKE.get(reason)
        if kind is not None:
            self._record_event(kind, subject_kind, subject_id, None, None)

    def list_live_for_admin(self, admin_id: str) -> tuple[StoredSession, ...]:
        rows = self.session.scalars(
            select(AuthSession).where(
                AuthSession.merchant_admin_id == admin_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > datetime.now(UTC),
            )
        ).all()
        return tuple(_to_stored(row) for row in rows)

    def _record_event(
        self,
        kind: str,
        subject_kind: str | None,
        subject_id: str | None,
        session_id: str | None,
        ip_hash: bytes | None,
    ) -> None:
        self.session.add(
            AuthEvent(
                id=_record_id("aev"),
                subject_kind=subject_kind,
                subject_id=subject_id,
                kind=kind,
                session_id=session_id,
                ip_hash=ip_hash,
                event_metadata={},
            )
        )


class SqlLoginAttemptTracker:
    def __init__(self, session: Session) -> None:
        self.session = session

    def is_locked(self, identifier_hash: bytes) -> bool:
        lock = self.session.get(LoginLockout, _hash_hex(identifier_hash))
        return lock is not None and _aware(lock.locked_until) > datetime.now(UTC)

    def record_failure(self, identifier_hash: bytes, ip_hash: bytes) -> None:
        hashed = _hash_hex(identifier_hash)
        self.session.add(
            LoginAttempt(
                id=_record_id("la"),
                identifier_hash=hashed,
                ip_hash=_hash_hex(ip_hash),
                succeeded=False,
                failure_code="BAD_CREDENTIALS",
            )
        )
        self.session.flush()

        since = datetime.now(UTC) - timedelta(seconds=LOCKOUT_WINDOW_SECONDS)
        fails = self.session.scalar(
            select(func.count())
            .select_from(LoginAttempt)
            .where(
                LoginAttempt.identifier_hash == hashed,
                LoginAttempt.succeeded.is_(False),
                LoginAttempt.created_at > since,
            )
        )
        if fails is not None and fails >= LOCKOUT_THRESHOLD:
            locked_until = datetime.now(UTC) + timedelta(seconds=LOCKOUT_WINDOW_SECONDS)
            existing = self.session.get(LoginLockout, hashed)
            if existing is None:
                self.session.add(
                    LoginLockout(
                        identifier_hash=hashed, locked_until=locked_until, attempt_count=fails
                    )
                )
            else:
                existing.locked_until = locked_until
                existing.attempt_count = fails
        self.session.flush()

    def record_success(self, identifier_hash: bytes, ip_hash: bytes) -> None:
        hashed = _hash_hex(identifier_hash)
        self.session.add(
            LoginAttempt(
                id=_record_id("la"),
                identifier_hash=hashed,
                ip_hash=_hash_hex(ip_hash),
                succeeded=True,
            )
        )
        existing = self.session.get(LoginLockout, hashed)
        if existing is not None:
            self.session.delete(existing)
        self.session.flush()
