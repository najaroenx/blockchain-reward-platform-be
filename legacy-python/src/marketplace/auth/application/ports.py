from datetime import datetime
from typing import Protocol

from marketplace.auth.application.dto import StoredSession
from marketplace.auth.domain.entities import Subject


class AdminAccount(Protocol):
    id: str
    merchant_id: str
    password_hash: str
    disabled_at: datetime | None
    merchant_status: str


class AdminAccountReader(Protocol):
    def find_by_email(self, email_normalized: str) -> AdminAccount | None: ...

    def is_disabled(self, admin_id: str) -> bool:
        """True if the account is disabled, or no longer exists."""
        ...

    def touch_last_login(self, admin_id: str) -> None: ...


class AuthSessionStore(Protocol):
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
    ) -> None: ...

    def find_by_token(self, refresh_token: str) -> StoredSession | None: ...

    def rotate(
        self,
        current: StoredSession,
        *,
        next_session_id: str,
        refresh_token: str,
        expires_at: datetime,
        ip_hash: bytes | None,
    ) -> None: ...

    def revoke_family(self, family_id: str, *, reason: str) -> int: ...

    def revoke(self, session_id: str, *, reason: str) -> None: ...

    def revoke_all_for_admin(self, admin_id: str, *, reason: str) -> int: ...

    def list_live_for_admin(self, admin_id: str) -> tuple[StoredSession, ...]: ...


class LoginAttemptTracker(Protocol):
    def is_locked(self, identifier_hash: bytes) -> bool: ...

    def record_failure(self, identifier_hash: bytes, ip_hash: bytes) -> None: ...

    def record_success(self, identifier_hash: bytes, ip_hash: bytes) -> None: ...


class PasswordHasher(Protocol):
    def hash(self, plain: str) -> str: ...

    def verify(self, hashed: str, plain: str) -> bool: ...


class TokenSigner(Protocol):
    """Signs the JWT access token. No production implementation this round: the project intends
    a KMS-backed signer, which is deliberately not built yet (see docs/architecture.md's
    "Authentication" open decision). Tests inject a fake."""

    def sign(self, *, subject: Subject, session_id: str) -> str: ...


class TokenVerifier(Protocol):
    """Verifies an access token and returns the `(subject, session_id)` it carries. Same status as
    `TokenSigner`: no production implementation until the KMS-backed signer exists — a verifier
    for tokens nothing can yet sign would be the other half of an unusable pair."""

    def verify(self, access_token: str) -> tuple[Subject, str]: ...
