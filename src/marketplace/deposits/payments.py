from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Protocol


@dataclass(frozen=True)
class PaymentInstructions:
    reference: str
    url: str
    expires_at: datetime


class PaymentProviderError(Exception):
    pass


class PaymentReferenceConflict(PaymentProviderError):
    pass


class PaymentProvider(Protocol):
    name: str

    def prepare(self, reference: str, amount_satang: int) -> PaymentInstructions:
        """Reuse a checkout by reference and reject a changed amount."""
        ...


class MockDepositPayments:
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
