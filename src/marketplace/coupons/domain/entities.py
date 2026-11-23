"""Coupon read models adapted from docs/legacy/0001_typescript_reference.sql.

CouponType combines the legacy type/chain identity with BaseCoupon.getCouponData.
CouponHolding represents ERC-1155 balance per wallet/type, without per-code ownership.
Both describe indexed state; they do not authorize or execute redemption.
"""

from dataclasses import dataclass


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
