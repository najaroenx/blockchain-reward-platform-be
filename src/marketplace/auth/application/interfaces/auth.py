from abc import ABC, abstractmethod
from datetime import datetime

from marketplace.auth.application.dto import (
    AccessClaims,
    CustomerAccessClaims,
    LoginCommand,
    RefreshCommand,
    SigningPublicKey,
)
from marketplace.auth.application.exceptions import AuthRefusal
from marketplace.auth.domain.entities.auth import (
    AdminCredentials,
    Attempt,
    LoginGrant,
    LoginPolicy,
    MerchantSubject,
    NewSession,
    RefreshCandidate,
)


class PasswordVerifier(ABC):
    @abstractmethod
    def verify(self, password_hash: str | None, password: str) -> bool: ...


class TokenSigner(ABC):
    @abstractmethod
    def sign(self, claims: AccessClaims | CustomerAccessClaims) -> str: ...


class SigningKeyPublisher(ABC):
    @abstractmethod
    def public_keys(self) -> tuple[SigningPublicKey, ...]: ...


class RefreshStore(ABC):
    @abstractmethod
    def prepare(self, command: RefreshCommand, now: datetime) -> RefreshCandidate | AuthRefusal: ...

    @abstractmethod
    def complete(
        self, candidate: RefreshCandidate, planned: NewSession, ip_hash: bytes, now: datetime
    ) -> AuthRefusal | None: ...


class LoginStore(ABC):
    @abstractmethod
    def reserve(
        self, command: LoginCommand, now: datetime, policy: LoginPolicy
    ) -> Attempt | AuthRefusal: ...

    @abstractmethod
    def credentials(self, normalized_email: str) -> AdminCredentials | None: ...

    @abstractmethod
    def complete(
        self,
        attempt: Attempt,
        grant: LoginGrant | None,
        now: datetime,
        policy: LoginPolicy,
    ) -> MerchantSubject | AuthRefusal: ...

    @abstractmethod
    def abort(self, attempt: Attempt) -> None: ...
