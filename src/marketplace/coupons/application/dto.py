"""Coupon-type draft messages, and redeem messages shared by the HTTP boundary and
admission adapters.
"""

from dataclasses import dataclass, field
from typing import Literal

from marketplace.coupons.domain.entities.coupon import CouponTypeDraft


# Carries validated coupon draft fields from the HTTP schema to the creation use case.
@dataclass(frozen=True)
class CreateCouponType:
    name: str
    start_date: int
    expire_date: int
    face_value: int
    total_supply: int


# Returns a page of saved drafts with the total needed by the HTTP response.
@dataclass(frozen=True)
class CouponTypePage:
    items: tuple[CouponTypeDraft, ...]
    total: int


@dataclass(frozen=True)
class AuthenticatedWallet:
    """Wallet ownership already verified by the authentication adapter."""

    user_id: str
    wallet_address: str


# Combines a verified wallet, coupon type ID, and idempotency key for redeem admission.
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


# Defines the deployment, wallet, coupon type, and fixed amount to persist for execution.
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
