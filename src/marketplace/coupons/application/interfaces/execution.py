"""Contracts for explicit redeem execution; no production runner or finality policy."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from marketplace.coupons.application.dto import RedeemCouponPayload
from marketplace.coupons.application.exceptions import RedeemErrorCode


@dataclass(frozen=True)
class SignedRedeem:
    """Signer adapter must bind chain, recipient, calldata, nonce and signer to the request."""

    raw_tx: bytes
    tx_hash: str
    signer_address: str
    nonce: int


# Carries a mined log and its transaction and block identifiers into receipt correlation.
@dataclass(frozen=True)
class ChainLog:
    address: str
    topics: tuple[str, ...]
    data: str
    log_index: int
    tx_hash: str
    block_hash: str


# Carries mined transaction evidence and decoded error details to the redeem projector.
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


# Defines the chain adapter steps for deployment checks, signing, broadcast, and receipt reads.
class RedeemChain(ABC):
    @abstractmethod
    def verify(self, payload: RedeemCouponPayload) -> None:
        """Verify deployment identity, callback wiring and current signer rights, or refuse."""
        ...

    @abstractmethod
    def prepare(self, payload: RedeemCouponPayload) -> SignedRedeem:
        """Sign exactly redeemFrom(wallet, typeId, 1); reserve a nonce without broadcasting."""
        ...

    @abstractmethod
    def broadcast(self, transaction: SignedRedeem) -> None:
        """Submit those exact bytes; duplicate delivery must preserve the transaction hash."""
        ...

    @abstractmethod
    def receipt(self, tx_hash: str) -> RedeemReceipt | None:
        """Return only a receipt eligible under the caller's explicit confirmation policy."""
        ...


class ReceiptCorrelationError(ValueError):
    """The receipt cannot prove the expected operation result."""


# Defines the persistence boundary for applying a redeem receipt to operation state.
class RedeemProjectionStore(ABC):
    @abstractmethod
    def project(self, operation_id: str, receipt: RedeemReceipt) -> dict[str, object]: ...


# Extends receipt projection with durable signed transaction storage and refusal handling.
class RedeemExecutionStore(RedeemProjectionStore):
    @abstractmethod
    def payload(self, operation_id: str) -> RedeemCouponPayload: ...

    @abstractmethod
    def transaction(self, operation_id: str) -> SignedRedeem | None: ...

    @abstractmethod
    def remember(
        self, operation_id: str, payload: RedeemCouponPayload, signed: SignedRedeem
    ) -> SignedRedeem: ...

    @abstractmethod
    def submitted(self, operation_id: str) -> None: ...

    @abstractmethod
    def result(self, operation_id: str) -> dict[str, object] | None: ...

    @abstractmethod
    def refuse(self, operation_id: str, code: RedeemErrorCode) -> dict[str, object]: ...
