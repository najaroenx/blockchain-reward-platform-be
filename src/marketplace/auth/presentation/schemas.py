from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, SecretStr

from marketplace.auth.application.exceptions import AuthErrorCode
from marketplace.auth.domain.entities.auth import MerchantStatus, normalize_email


def normalized_email(value: object) -> object:
    return normalize_email(value) if isinstance(value, str) else value


# Validates sign-in input and keeps the submitted password in a secret field.
class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    email: Annotated[EmailStr, BeforeValidator(normalized_email), Field(max_length=254)]
    password: SecretStr = Field(min_length=1, max_length=256)


# Exposes the merchant administrator identity and merchant status to a login client.
class SubjectResponse(BaseModel):
    kind: Literal["MERCHANT_ADMIN"] = "MERCHANT_ADMIN"
    id: str
    merchantId: str
    merchantStatus: MerchantStatus


# Shapes the login response with an access token, expiry, and authenticated subject.
class LoginResponse(BaseModel):
    accessToken: str = Field(min_length=1, repr=False)
    tokenType: Literal["Bearer"] = "Bearer"
    expiresIn: Literal[900] = 900
    subject: SubjectResponse


# Carries the public auth error code, message, and request ID.
class ErrorDetail(BaseModel):
    code: AuthErrorCode
    message: str
    requestId: str


# Wraps authentication errors in the JSON format returned by auth routes.
class ErrorResponse(BaseModel):
    error: ErrorDetail
