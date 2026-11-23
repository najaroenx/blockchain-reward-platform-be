from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from marketplace.coupons.domain.entities import CouponType


class CreateCouponTypeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    start_date: int = Field(ge=0)
    expire_date: int = Field(ge=0)
    face_value: int = Field(gt=0)
    total_supply: int = Field(gt=0)

    @model_validator(mode="after")
    def start_before_expire(self) -> "CreateCouponTypeRequest":
        # Mirrors BaseCoupon.sol's InvalidDateRange revert, so a bad request fails fast
        # instead of being discovered only once a future phase submits it on-chain.
        if self.start_date >= self.expire_date:
            raise ValueError("start_date must be before expire_date")
        return self


class CouponTypeResponse(BaseModel):
    id: str
    name: str
    start_date: int
    expire_date: int
    face_value: int
    total_supply: int
    chain_type_id: int | None
    issued_count: int
    remaining_supply: int
    created_at: datetime

    @classmethod
    def from_domain(cls, coupon_type: CouponType) -> "CouponTypeResponse":
        # No coupon type reaches chain in this phase (docs/architecture.md), so issued/
        # remaining always reduce to these constants until a later phase adds chain reads.
        issued_count = 0
        return cls(
            id=coupon_type.id,
            name=coupon_type.name,
            start_date=coupon_type.start_date,
            expire_date=coupon_type.expire_date,
            face_value=coupon_type.face_value,
            total_supply=coupon_type.total_supply,
            chain_type_id=coupon_type.chain_type_id,
            issued_count=issued_count,
            remaining_supply=coupon_type.total_supply - issued_count,
            created_at=coupon_type.created_at,
        )


class PageResponse(BaseModel):
    items: list[CouponTypeResponse]
    total: int
    limit: int
    offset: int
