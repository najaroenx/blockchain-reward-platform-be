from datetime import datetime
from typing import Protocol

from marketplace.auth.application.dto import AccessClaims, LoginCommand, SigningPublicKey
from marketplace.auth.application.exceptions import LoginRefusal
from marketplace.auth.domain.entities import (
    AdminCredentials,
    Attempt,
    LoginGrant,
    LoginPolicy,
    MerchantSubject,
)


class PasswordVerifier(Protocol):
    def verify(self, password_hash: str | None, password: str) -> bool: ...


class TokenSigner(Protocol):
    def sign(self, claims: AccessClaims) -> str: ...


class SigningKeyPublisher(Protocol):
    def public_keys(self) -> tuple[SigningPublicKey, ...]: ...


class LoginStore(Protocol):
    def reserve(
        self, command: LoginCommand, now: datetime, policy: LoginPolicy
    ) -> Attempt | LoginRefusal: ...

    def credentials(self, normalized_email: str) -> AdminCredentials | None: ...

    def complete(
        self,
        attempt: Attempt,
        grant: LoginGrant | None,
        now: datetime,
        policy: LoginPolicy,
    ) -> MerchantSubject | LoginRefusal: ...

    def abort(self, attempt: Attempt) -> None: ...
