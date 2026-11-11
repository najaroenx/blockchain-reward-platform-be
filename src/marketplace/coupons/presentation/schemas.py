"""Draft HTTP shapes for #224; no redeem endpoint is registered yet."""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def coupon_type_id(value: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]{0,77}", value) or int(value) >= 2**256:
        raise ValueError("must be a canonical uint256 decimal string greater than zero")
    return value


CouponTypeId = Annotated[
    str, Field(strict=True, min_length=1, max_length=78), AfterValidator(coupon_type_id)
]


class RedeemCouponRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    typeId: CouponTypeId


class RedeemCouponResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operationId: str = Field(min_length=1)
    status: Literal["PENDING"] = "PENDING"
    typeId: CouponTypeId
    amount: Literal["1"] = "1"
