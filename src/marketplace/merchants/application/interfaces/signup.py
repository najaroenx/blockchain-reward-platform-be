from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from marketplace.merchants.domain.entities.signup import Signup, WalletReference


@dataclass(frozen=True)
class SavedSignup:
    digest: bytes
    status: int | None
    body: str | None
    password_hash: str | None = field(repr=False)


class SignupRepository(ABC):
    @abstractmethod
    def probe(self, scope: str, key: str, email: str) -> SavedSignup | None: ...

    @abstractmethod
    def save(
        self,
        signup: Signup,
        password_hash: str,
        wallet: WalletReference,
        now: datetime,
        *,
        scope: str,
        key: str,
        digest: bytes,
    ) -> str | SavedSignup: ...


class SignupCredentialPort(ABC):
    @abstractmethod
    def hash(self, password: str) -> str: ...

    @abstractmethod
    def matches(self, password: str, password_hash: str) -> bool: ...
