from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from marketplace.deposits.domain.entities.deposit import (
    DepositAmount,
    DepositRecord,
    PaymentInstructions,
)


# Carries a prepared payment and validated deposit into the repository's atomic save.
@dataclass(frozen=True)
class DepositAdmission:
    id: str
    merchant_id: str
    amount: DepositAmount
    provider: str
    instructions: PaymentInstructions
    created_at: datetime


# Defines replay lookup, atomic admission, and reads scoped to the owning merchant.
class DepositRepository(ABC):
    @abstractmethod
    def replay(self, scope: str, key: str, digest: bytes) -> str | None: ...

    @abstractmethod
    def save(
        self, deposit: DepositAdmission, *, scope: str, key: str, digest: bytes
    ) -> tuple[str, bool]: ...

    @abstractmethod
    def find(self, *, merchant_id: str, deposit_id: str) -> DepositRecord | None: ...
