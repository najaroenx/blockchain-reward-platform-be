"""Redeem messages shared by the HTTP boundary and admission adapters."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class AuthenticatedWallet:
    """Wallet ownership already verified by the authentication adapter."""

    user_id: str
    wallet_address: str


@dataclass(frozen=True)
class RedeemCouponCommand:
    wallet: AuthenticatedWallet
    type_id: int
    idempotency_key: str


@dataclass(frozen=True)
class CouponDeployment:
    """Deployment verified and selected by a trusted configuration adapter."""

    chain_id: int
    contract_address: str


@dataclass(frozen=True)
class RedeemCouponPayload:
    chain_id: int
    coupon_contract_address: str
    wallet_address: str
    type_id: int
    amount: Literal[1] = field(default=1, init=False)


@dataclass(frozen=True)
class RedeemCouponResult:
    """Acknowledgement returned only after admission commits or replays."""

    operation_id: str
    type_id: int
    status: Literal["PENDING"] = field(default="PENDING", init=False)
    amount: Literal[1] = field(default=1, init=False)
    response_body: str | None = field(default=None, repr=False)
