from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol


@dataclass(frozen=True)
class WalletReference:
    address: bytes
    key_ref: str


class WalletProviderError(Exception):
    pass


class WalletProvider(Protocol):
    def reserve(self, request_ref: str) -> WalletReference:
        """Reserve or return the public wallet reference for this request."""
        ...


class MockWalletProvider:
    def reserve(self, request_ref: str) -> WalletReference:
        address = sha256(f"merchant-mock-wallet:{request_ref}".encode()).digest()[-20:]
        return WalletReference(address, f"mock-kms://merchant/{request_ref}")
