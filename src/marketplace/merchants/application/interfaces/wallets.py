from abc import ABC, abstractmethod

from marketplace.merchants.domain.entities.signup import WalletReference


# Defines the port for reserving a public wallet address and key reference under a stable request
# reference.
class WalletProvider(ABC):
    @abstractmethod
    def reserve(self, request_ref: str) -> WalletReference:
        """Reserve or return the public wallet reference for this request."""
