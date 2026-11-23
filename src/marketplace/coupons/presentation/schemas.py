"""HTTP shapes for single-coupon redeem admission."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from marketplace.coupons.application.exceptions import RedeemErrorCode

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
