from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

from marketplace.auth.application.dto import AccessClaims, LoginCommand, LoginResult
from marketplace.auth.application.exceptions import AuthFailure, AuthRefusal
from marketplace.auth.application.ports import LoginStore, PasswordVerifier, TokenSigner
from marketplace.auth.domain.entities import (
    DEFAULT_LOGIN_POLICY,
    LoginGrant,
    LoginPolicy,
    NewSession,
)


class Login:
    def __init__(
        self,
        store: LoginStore,
        hasher: PasswordVerifier,
        signer: TokenSigner,
        *,
        policy: LoginPolicy = DEFAULT_LOGIN_POLICY,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.hasher = hasher
        self.signer = signer
        self.policy = policy
        self.clock = clock

    def execute(self, command: LoginCommand) -> LoginResult:
        attempt = self.store.reserve(command, self.clock(), self.policy)
        if isinstance(attempt, AuthRefusal):
            raise AuthFailure(attempt)
        credentials = self.store.credentials(command.normalized_email)
        verified = self.hasher.verify(
            credentials.password_hash if credentials else None, command.password
        )
        candidate = credentials if verified and credentials and not credentials.disabled else None
        now = self.clock()
        grant = None
        access_token = ""
        refresh_token = ""
        if candidate:
            refresh_token = token_urlsafe(32)
            planned = NewSession(
                id="ses_" + uuid4().hex,
                family_id="fam_" + uuid4().hex,
                refresh_hash=sha256(refresh_token.encode()).digest(),
                created_at=now,
                expires_at=now + timedelta(seconds=self.policy.refresh_ttl),
            )
            try:
                access_token = self.signer.sign(
                    AccessClaims(
                        sub=candidate.subject.id,
                        mid=candidate.subject.merchant_id,
                        sid=planned.id,
                        jti=uuid4().hex,
                        iat=int(now.timestamp()),
                        exp=int(now.timestamp()) + self.policy.access_ttl,
                    )
                )
                if not access_token:
                    raise AuthFailure(AuthRefusal("AUTH_UNAVAILABLE"))
            except Exception:
                self.store.abort(attempt)
                raise AuthFailure(AuthRefusal("AUTH_UNAVAILABLE")) from None
            grant = LoginGrant(candidate, planned)
        subject = self.store.complete(attempt, grant, self.clock(), self.policy)
        if isinstance(subject, AuthRefusal):
            raise AuthFailure(subject)
        return LoginResult(
            access_token=access_token,
            refresh_token=refresh_token,
            subject=subject,
            expires_in=self.policy.access_ttl,
            refresh_max_age=self.policy.refresh_ttl,
        )
