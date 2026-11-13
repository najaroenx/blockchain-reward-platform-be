from datetime import datetime
from typing import Protocol

from marketplace.auth.application.dto import AccessClaims, LoginCommand
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
    """#182 supplies signing, issuer/audience configuration, keys, and algorithm policy."""

    def sign(self, claims: AccessClaims) -> str: ...


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
