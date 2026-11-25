from datetime import datetime
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, StringConstraints, field_validator

from marketplace.deposits.domain.entities.deposit import DepositAmount

SATANG_PATTERN = r"^[1-9][0-9]{3,6}$"


class CreateDeposit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amountSatang: Annotated[str, StringConstraints(strict=True, pattern=SATANG_PATTERN)]

    @field_validator("amountSatang")
    @classmethod
    def within_limit(cls, value: str) -> str:
        DepositAmount(int(value))
        return value


class DepositCreated(BaseModel):
    depositId: str
    amountSatang: str
    amountBase: str
    status: Literal["PENDING_PAYMENT"]
    paymentUrl: AnyHttpUrl
    expiresAt: datetime


class DepositView(BaseModel):
    depositId: str
    amountSatang: str
    amountBase: str
    status: Literal["PENDING_PAYMENT", "EXPIRED"]
    createdAt: datetime
    confirmedAt: None = None
    mintedAt: None = None
    mint: None = None
    reversal: None = None


class ApiErrorDetail(BaseModel):
    code: str
    message: str


class ApiError(BaseModel):
    error: ApiErrorDetail
