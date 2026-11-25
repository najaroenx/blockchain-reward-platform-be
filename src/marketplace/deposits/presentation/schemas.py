from datetime import datetime
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, StringConstraints, field_validator

from marketplace.deposits.domain.entities.deposit import DepositAmount

SATANG_PATTERN = r"^[1-9][0-9]{3,6}$"


# Validates the amount as canonical decimal satang before the use case receives it.
class CreateDeposit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amountSatang: Annotated[str, StringConstraints(strict=True, pattern=SATANG_PATTERN)]

    @field_validator("amountSatang")
    @classmethod
    def within_limit(cls, value: str) -> str:
        DepositAmount(int(value))
        return value


# Returns payment instructions and pending deposit status for a new or replayed request.
class DepositCreated(BaseModel):
    depositId: str
    amountSatang: str
    amountBase: str
    status: Literal["PENDING_PAYMENT"]
    paymentUrl: AnyHttpUrl
    expiresAt: datetime


# Returns the current status of a merchant deposit and the fields reserved for later stages.
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


# Carries the public code and message for a deposit API error.
class ApiErrorDetail(BaseModel):
    code: str
    message: str


# Wraps deposit errors in the JSON envelope documented by the API.
class ApiError(BaseModel):
    error: ApiErrorDetail
