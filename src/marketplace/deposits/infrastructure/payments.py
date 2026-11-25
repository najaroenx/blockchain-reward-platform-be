from datetime import UTC, datetime, timedelta
from threading import Lock

from marketplace.deposits.application.exceptions import PaymentReferenceConflict
from marketplace.deposits.application.interfaces.payments import PaymentProvider
from marketplace.deposits.domain.entities.deposit import PaymentInstructions


# Provides local checkout instructions and reuses them when the reference and amount match.
class MockDepositPayments(PaymentProvider):
    name = "mock"

    def __init__(self) -> None:
        self._lock = Lock()
        self._intents: dict[str, tuple[int, PaymentInstructions]] = {}

    def prepare(self, reference: str, amount_satang: int) -> PaymentInstructions:
        with self._lock:
            if reference not in self._intents:
                self._intents[reference] = (
                    amount_satang,
                    PaymentInstructions(
                        reference=reference,
                        url=f"https://payments.invalid/deposits/{reference}",
                        expires_at=datetime.now(UTC) + timedelta(hours=24),
                    ),
                )
            amount, instructions = self._intents[reference]
            if amount != amount_satang:
                raise PaymentReferenceConflict
            return instructions
