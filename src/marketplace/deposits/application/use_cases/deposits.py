import json
from datetime import UTC, datetime
from hashlib import sha256

from marketplace.deposits.application.exceptions import (
    PaymentProviderError,
    PaymentReferenceConflict,
)
from marketplace.deposits.application.interfaces.deposits import DepositAdmission, DepositRepository
from marketplace.deposits.application.interfaces.payments import PaymentProvider
from marketplace.deposits.domain.entities.deposit import DepositAmount, DepositRecord
from marketplace.deposits.domain.exceptions import DepositError, DepositFailure, DepositNotFound
from marketplace.idempotency import fingerprint


class Deposits:
    def __init__(self, repository: DepositRepository, provider: PaymentProvider | None) -> None:
        self.repository = repository
        self.provider = provider

    def create(self, *, merchant_id: str, key: str, amount: DepositAmount) -> tuple[str, bool]:
        scope = f"merchant:{merchant_id}:POST:/v1/deposits"
        digest = fingerprint({"amountSatang": str(amount.satang)})
        saved = self.repository.replay(scope, key, digest)
        if saved is not None:
            return saved, True
        if self.provider is None:
            raise DepositFailure(DepositError.PAYMENT_UNAVAILABLE)

        reference = sha256(json.dumps([scope, key], ensure_ascii=True).encode()).hexdigest()
        now = datetime.now(UTC)
        try:
            instructions = self.provider.prepare(reference, amount.satang)
            instructions.validate(now)
        except PaymentReferenceConflict:
            raise DepositFailure(DepositError.IDEMPOTENCY_KEY_REUSED) from None
        except (PaymentProviderError, OSError, ValueError):
            raise DepositFailure(DepositError.PAYMENT_UNAVAILABLE) from None
        deposit = DepositAdmission(
            f"dep_{reference}", merchant_id, amount, self.provider.name, instructions, now
        )
        return self.repository.save(deposit, scope=scope, key=key, digest=digest)

    def get(self, *, merchant_id: str, deposit_id: str) -> DepositRecord:
        deposit = self.repository.find(merchant_id=merchant_id, deposit_id=deposit_id)
        if deposit is None:
            raise DepositNotFound
        return deposit
