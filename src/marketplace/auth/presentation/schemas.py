from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, SecretStr

from marketplace.auth.application.exceptions import LoginErrorCode
from marketplace.auth.domain.entities import MerchantStatus, normalize_email


def normalized_email(value: object) -> object:
    return normalize_email(value) if isinstance(value, str) else value


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    email: Annotated[EmailStr, BeforeValidator(normalized_email), Field(max_length=254)]
    password: SecretStr = Field(min_length=1, max_length=256)


class SubjectResponse(BaseModel):
    kind: Literal["MERCHANT_ADMIN"] = "MERCHANT_ADMIN"
    id: str
    merchantId: str
    merchantStatus: MerchantStatus


class LoginResponse(BaseModel):
    accessToken: str = Field(min_length=1, repr=False)
    tokenType: Literal["Bearer"] = "Bearer"
    expiresIn: Literal[900] = 900
    subject: SubjectResponse


class ErrorDetail(BaseModel):
    code: LoginErrorCode
    message: str
    requestId: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
