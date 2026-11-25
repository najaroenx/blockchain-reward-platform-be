from hashlib import sha256

from marketplace.merchants.application.interfaces.wallets import WalletProvider
from marketplace.merchants.domain.entities.signup import WalletReference


class MockWalletProvider(WalletProvider):
    def reserve(self, request_ref: str) -> WalletReference:
        address = sha256(f"merchant-mock-wallet:{request_ref}".encode()).digest()[-20:]
        return WalletReference(address, f"mock-kms://merchant/{request_ref}")
