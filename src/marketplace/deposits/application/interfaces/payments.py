from abc import ABC, abstractmethod

from marketplace.deposits.domain.entities.deposit import PaymentInstructions


# Defines how the use case obtains or reuses payment instructions under a stable reference.
class PaymentProvider(ABC):
    name: str

    @abstractmethod
    def prepare(self, reference: str, amount_satang: int) -> PaymentInstructions:
        """Reuse a checkout by reference and reject a changed amount."""
