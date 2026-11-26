import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from marketplace.auth.application.dto import LoginResult, RefreshResult, SessionSummary
from marketplace.auth.application.exceptions import (
    AccountLocked,
    InvalidCredentials,
    RefreshTokenExpired,
    RefreshTokenInvalid,
    RefreshTokenRevoked,
    SessionCompromised,
)
from marketplace.auth.application.ports import (
    AdminAccountReader,
    AuthSessionStore,
    LoginAttemptTracker,
    PasswordHasher,
    TokenSigner,
)
from marketplace.auth.domain.entities import Subject, normalize_email

ACCESS_TTL_SECONDS = 900
REFRESH_TTL_SECONDS = 60 * 60 * 24 * 30

# A syntactically valid argon2id PHC string with no real credential behind it. `login` always
# runs `hasher.verify` against *some* hash, even when no account matches the email, so that the
# "no such email" and "wrong password" paths take the same time and the endpoint can't be used to
# enumerate registered emails. `PasswordHasher.verify` must never raise on a malformed hash.
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)


def _short_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _new_refresh_token() -> str:
    """256 bits from a CSPRNG, url-safe. Never stored as presented — only its sha256."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class LoginContext:
    ip_hash: bytes
    user_agent_hash: bytes | None = None


class AuthService:
    def __init__(
        self,
        accounts: AdminAccountReader,
        sessions: AuthSessionStore,
        attempts: LoginAttemptTracker,
        hasher: PasswordHasher,
        signer: TokenSigner,
    ) -> None:
        self.accounts = accounts
        self.sessions = sessions
        self.attempts = attempts
        self.hasher = hasher
        self.signer = signer

    def login(self, email: str, password: str, ctx: LoginContext) -> LoginResult:
        email_normalized = normalize_email(email)
        # Hashed here, not passed in: lockout lookups must key on the same normalized form used
        # to find the account, or a case/whitespace variant would dodge the lockout entirely.
        identifier_hash = hashlib.sha256(email_normalized.encode()).digest()

        if self.attempts.is_locked(identifier_hash):
            raise AccountLocked

        account = self.accounts.find_by_email(email_normalized)

        # Verify runs whether or not an account was found, against a dummy hash if not, so a
        # missing email takes the same time as a wrong password (see _DUMMY_PASSWORD_HASH).
        hash_to_check = account.password_hash if account is not None else _DUMMY_PASSWORD_HASH
        password_ok = self.hasher.verify(hash_to_check, password)
        allowed = account is not None and password_ok and account.disabled_at is None

        if not allowed:
            self.attempts.record_failure(identifier_hash, ctx.ip_hash)
            raise InvalidCredentials
        assert account is not None

        session_id = _short_id("ses")
        family_id = _short_id("fam")
        refresh_token = _new_refresh_token()
        expires_at = datetime.now(UTC) + timedelta(seconds=REFRESH_TTL_SECONDS)

        self.sessions.create(
            session_id=session_id,
            admin_id=account.id,
            family_id=family_id,
            refresh_token=refresh_token,
            expires_at=expires_at,
            user_agent_hash=ctx.user_agent_hash,
            ip_hash=ctx.ip_hash,
        )
        self.attempts.record_success(identifier_hash, ctx.ip_hash)
        self.accounts.touch_last_login(account.id)

        subject = Subject(kind="MERCHANT_ADMIN", id=account.id, merchant_id=account.merchant_id)
        access_token = self.signer.sign(subject=subject, session_id=session_id)
        return LoginResult(
            subject=subject,
            merchant_status=account.merchant_status,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=ACCESS_TTL_SECONDS,
        )

    def refresh(self, presented_token: str, *, ip_hash: bytes | None) -> RefreshResult:
        current = self.sessions.find_by_token(presented_token)
        if current is None:
            raise RefreshTokenInvalid

        # Presented a second time: only possible if someone else now holds this token too.
        # Revoking the whole family, not just this request, is the point — see docs/architecture.md
        # and migration 0002's `uq_session_parent` index, which is what actually enforces the
        # "rotates into exactly one child" half of this guarantee. The revocation must survive
        # even though this method also raises: whatever provides the session this use case runs
        # on must catch this (and commit) inside its own session scope, the way `provide_catalog`
        # in main.py catches `SQLAlchemyError` before the `with ... sessions()` block exits — not
        # let it propagate past that scope, or the write rolls back with it.
        if current.used_at is not None:
            revoked_count = self.sessions.revoke_family(current.family_id, reason="REUSE_DETECTED")
            raise SessionCompromised(current.family_id, revoked_count)

        if current.revoked_at is not None:
            raise RefreshTokenRevoked
        if current.expires_at <= datetime.now(UTC):
            raise RefreshTokenExpired

        assert current.merchant_admin_id is not None

        # FEAT-03 T14: a disabled account keeps no live session. `login` already refuses it, but
        # a refresh token issued before the account was disabled would otherwise keep minting
        # access tokens for up to REFRESH_TTL_SECONDS. Revoke every session of the account, not
        # only this family, so its other devices stop too. Like the reuse branch above, the
        # caller must catch the exception inside its session scope so the revocation commits.
        if self.accounts.is_disabled(current.merchant_admin_id):
            self.sessions.revoke_all_for_admin(current.merchant_admin_id, reason="ADMIN_REVOKED")
            raise RefreshTokenRevoked
        next_session_id = _short_id("ses")
        next_refresh_token = _new_refresh_token()
        expires_at = datetime.now(UTC) + timedelta(seconds=REFRESH_TTL_SECONDS)

        self.sessions.rotate(
            current,
            next_session_id=next_session_id,
            refresh_token=next_refresh_token,
            expires_at=expires_at,
            ip_hash=ip_hash,
        )

        subject = Subject(kind="MERCHANT_ADMIN", id=current.merchant_admin_id)
        access_token = self.signer.sign(subject=subject, session_id=next_session_id)
        return RefreshResult(
            subject=subject,
            access_token=access_token,
            refresh_token=next_refresh_token,
            expires_in=ACCESS_TTL_SECONDS,
        )

    def logout(self, session_id: str) -> None:
        self.sessions.revoke(session_id, reason="LOGOUT")

    def revoke_all_for(self, admin_id: str, *, reason: str = "ADMIN_REVOKED") -> int:
        return self.sessions.revoke_all_for_admin(admin_id, reason=reason)

    def list_sessions(
        self, admin_id: str, *, current_session_id: str
    ) -> tuple[SessionSummary, ...]:
        return tuple(
            SessionSummary(
                session_id=s.id,
                current=s.id == current_session_id,
                created_at=s.created_at.isoformat(),
                expires_at=s.expires_at.isoformat(),
            )
            for s in self.sessions.list_live_for_admin(admin_id)
        )
