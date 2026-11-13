"""Contracts for explicit redeem execution; no production runner or finality policy."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from marketplace.coupons.application.dto import RedeemCouponPayload


@dataclass(frozen=True)
class SignedRedeem:
    """Signer adapter must bind chain, recipient, calldata, nonce and signer to the request."""

    raw_tx: bytes
    tx_hash: str
    signer_address: str
    nonce: int


@dataclass(frozen=True)
class ChainLog:
    address: str
    topics: tuple[str, ...]
    data: str
    log_index: int
    tx_hash: str
    block_hash: str


@dataclass(frozen=True)
class RedeemReceipt:
    chain_id: int
    contract_address: str
    tx_hash: str
    block_hash: str
    block_number: int
    mined_at: datetime
    status: Literal[0, 1]
    logs: tuple[ChainLog, ...]
    error_name: str | None = None
    error_origin: Literal["coupon", "callback", "unknown"] = "unknown"
    error_reason: str | None = None


class RedeemChain(Protocol):
    def verify(self, payload: RedeemCouponPayload) -> None:
        """Verify deployment identity, callback wiring and current signer rights, or refuse."""
        ...

    def prepare(self, payload: RedeemCouponPayload) -> SignedRedeem:
        """Sign exactly redeemFrom(wallet, typeId, 1); reserve a nonce without broadcasting."""
        ...

    def broadcast(self, transaction: SignedRedeem) -> None:
        """Submit those exact bytes; duplicate delivery must preserve the transaction hash."""
        ...

    def receipt(self, tx_hash: str) -> RedeemReceipt | None:
        """Return only a receipt eligible under the caller's explicit confirmation policy."""
        ...


class ReceiptCorrelationError(ValueError):
    """The receipt cannot prove the expected operation result."""
