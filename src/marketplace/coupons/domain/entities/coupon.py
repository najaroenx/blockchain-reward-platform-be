"""Coupon read and draft models.

CouponType/CouponHolding are adapted from docs/legacy/0001_typescript_reference.sql and
describe indexed on-chain state (BaseCoupon.getCouponData); they do not authorize or
execute redemption. CouponTypeDraft is unrelated to that chain state: it is a backend-only
record of a requested coupon type that has not reached chain (chain_type_id is always None
until a later phase resolves deployment/signing — see docs/architecture.md).
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CouponType:
    """On-chain type data; dates are Unix seconds and counters are exact integers.

    Redemption increases total_redeemed without decreasing total_supply.
    Ordinary burns decrease total_supply instead.
    """

    chain_id: int
    contract_address: str
    type_id: int
    name: str
    starts_at: int
    expires_at: int
    total_supply: int
    total_redeemed: int
    as_of_block: int


@dataclass(frozen=True)
class CouponHolding:
    """Indexed balance, not proof of API wallet ownership or current redeemability."""

    chain_id: int
    contract_address: str
    type_id: int
    holder_address: str
    quantity: int
    as_of_block: int


# Represents a coupon type saved by the backend before a chain type has been linked.
@dataclass(frozen=True)
class CouponTypeDraft:
    id: str
    name: str
    start_date: int
    expire_date: int
    face_value: int
    total_supply: int
    chain_type_id: int | None
    created_at: datetime
