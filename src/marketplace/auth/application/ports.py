from datetime import datetime
from typing import Protocol

from marketplace.auth.application.dto import (
    AccessClaims,
    CustomerAccessClaims,
    LoginCommand,
    RefreshCommand,
    SigningPublicKey,
)
from marketplace.auth.application.exceptions import AuthRefusal
from marketplace.auth.domain.entities import (
    AdminCredentials,
    Attempt,
    LoginGrant,
    LoginPolicy,
    MerchantSubject,
    NewSession,
    RefreshCandidate,
)


class PasswordVerifier(Protocol):
    def verify(self, password_hash: str | None, password: str) -> bool: ...


class TokenSigner(Protocol):
    def sign(self, claims: AccessClaims | CustomerAccessClaims) -> str: ...


class SigningKeyPublisher(Protocol):
    def public_keys(self) -> tuple[SigningPublicKey, ...]: ...


class RefreshStore(Protocol):
    def prepare(self, command: RefreshCommand, now: datetime) -> RefreshCandidate | AuthRefusal: ...

    def complete(
        self, candidate: RefreshCandidate, planned: NewSession, ip_hash: bytes, now: datetime
    ) -> AuthRefusal | None: ...


class LoginStore(Protocol):
    def reserve(
        self, command: LoginCommand, now: datetime, policy: LoginPolicy
    ) -> Attempt | AuthRefusal: ...

    def credentials(self, normalized_email: str) -> AdminCredentials | None: ...

    def complete(
        self,
        attempt: Attempt,
        grant: LoginGrant | None,
        now: datetime,
        policy: LoginPolicy,
    ) -> MerchantSubject | AuthRefusal: ...

    def abort(self, attempt: Attempt) -> None: ...
