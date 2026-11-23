from dataclasses import dataclass

from marketplace.coupons.domain.entities import CouponType


@dataclass(frozen=True)
class CreateCouponType:
    name: str
    start_date: int
    expire_date: int
    face_value: int
    total_supply: int


@dataclass(frozen=True)
class CouponTypePage:
    items: tuple[CouponType, ...]
    total: int
