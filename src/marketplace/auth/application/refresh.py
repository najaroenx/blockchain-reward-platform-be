from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

from marketplace.auth.application.dto import (
    AccessClaims,
    CustomerAccessClaims,
    RefreshCommand,
    RefreshResult,
)
from marketplace.auth.application.exceptions import AuthFailure, AuthRefusal
from marketplace.auth.application.ports import RefreshStore, TokenSigner
from marketplace.auth.domain.entities import (
    DEFAULT_LOGIN_POLICY,
    LoginPolicy,
    MerchantSubject,
    NewSession,
)


class Refresh:
    def __init__(
        self,
        store: RefreshStore,
        signer: TokenSigner,
        *,
        policy: LoginPolicy = DEFAULT_LOGIN_POLICY,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.signer = signer
        self.policy = policy
        self.clock = clock

    def execute(self, command: RefreshCommand) -> RefreshResult:
        candidate = self.store.prepare(command, self.clock())
        if isinstance(candidate, AuthRefusal):
            raise AuthFailure(candidate)
        now = self.clock()
        refresh_token = token_urlsafe(32)
        planned = NewSession(
            id="ses_" + uuid4().hex,
            family_id=candidate.family_id,
            refresh_hash=sha256(refresh_token.encode()).digest(),
            created_at=now,
            expires_at=now + timedelta(seconds=self.policy.refresh_ttl),
        )
        issued_at = int(now.timestamp())
        expires_at = issued_at + self.policy.access_ttl
        jti = uuid4().hex
        claims: AccessClaims | CustomerAccessClaims
        if isinstance(candidate.subject, MerchantSubject):
            claims = AccessClaims(
                sub=candidate.subject.id,
                mid=candidate.subject.merchant_id,
                sid=planned.id,
                jti=jti,
                iat=issued_at,
                exp=expires_at,
            )
        else:
            claims = CustomerAccessClaims(
                sub=candidate.subject.id,
                sid=planned.id,
                jti=jti,
                iat=issued_at,
                exp=expires_at,
            )
        try:
            access_token = self.signer.sign(claims)
            if not access_token:
                raise ValueError("Signer returned no token")
        except Exception:
            raise AuthFailure(AuthRefusal("AUTH_UNAVAILABLE")) from None
        refusal = self.store.complete(candidate, planned, command.ip_hash, self.clock())
        if refusal:
            raise AuthFailure(refusal)
        return RefreshResult(
            access_token, refresh_token, self.policy.access_ttl, self.policy.refresh_ttl
        )
