"""HTTP shapes for coupon-type drafts and single-coupon redeem admission."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from marketplace.coupons.application.exceptions import RedeemErrorCode
from marketplace.coupons.domain.entities.coupon import CouponTypeDraft


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
    def from_domain(cls, coupon_type: CouponTypeDraft) -> "CouponTypeResponse":
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


UINT256_MAX = 2**256 - 1


def coupon_type_id(value: str) -> str:
    if int(value) > UINT256_MAX:
        raise ValueError("must be a canonical uint256 decimal string greater than zero")
    return value


CouponTypeId = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=78,
        pattern=r"^[1-9][0-9]{0,77}$",
        description=f"Canonical decimal uint256 string in the range 1 to {UINT256_MAX}.",
        examples=["1", str(UINT256_MAX)],
        json_schema_extra={"x-uint256-maximum": str(UINT256_MAX)},
    ),
    AfterValidator(coupon_type_id),
]


class RedeemCouponRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, json_schema_extra={"examples": [{"typeId": "1"}]}
    )

    typeId: CouponTypeId


class RedeemCouponResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={
            "required": ["operationId", "status", "typeId", "amount"],
            "examples": [
                {"operationId": "op_example", "status": "PENDING", "typeId": "1", "amount": "1"}
            ],
        },
    )

    operationId: str = Field(min_length=1)
    status: Literal["PENDING"] = "PENDING"
    typeId: CouponTypeId
    amount: Literal["1"] = "1"


class RedeemError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: RedeemErrorCode
    message: str


class RedeemErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: RedeemError
