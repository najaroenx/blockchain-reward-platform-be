from datetime import datetime
from typing import Annotated, Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator

ShortName = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)
]


class AdminInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=254)]
    password: Annotated[SecretStr, Field(min_length=12, max_length=128)]
    displayName: ShortName

    @field_validator("email")
    @classmethod
    def email_is_valid(cls, value: str) -> str:
        try:
            validate_email(value, check_deliverability=False)
        except EmailNotValidError as error:
            raise ValueError("Invalid email.") from error
        return value


class CreateMerchant(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: ShortName
    admin: AdminInput


class AdminView(BaseModel):
    adminId: str
    email: str


class WalletView(BaseModel):
    address: Annotated[str, StringConstraints(pattern=r"^0x[0-9a-f]{40}$")]
    ready: Literal[False]


class ProvisioningView(BaseModel):
    operationId: str
    status: Literal["PENDING"]


class MerchantCreated(BaseModel):
    merchantId: str
    name: str
    status: Literal["PENDING"]
    createdAt: datetime
    admin: AdminView
    wallet: WalletView
    provisioning: ProvisioningView
