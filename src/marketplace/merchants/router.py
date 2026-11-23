from collections.abc import Callable, Coroutine
from datetime import datetime
from threading import Lock
from time import monotonic
from typing import Annotated, Any, Literal
from unicodedata import normalize

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    ValidationError,
    field_validator,
)

from marketplace.merchants.store import Signup, SignupCredentials, SignupFailure, SqlMerchantSignup
from marketplace.merchants.wallets import WalletProvider

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


ERRORS = {
    "VALIDATION_FAILED": (400, "Invalid merchant registration."),
    "IDEMPOTENCY_KEY_REQUIRED": (400, "Idempotency-Key is required."),
    "EMAIL_TAKEN": (409, "That email is already registered."),
    "IDEMPOTENCY_KEY_IN_PROGRESS": (409, "This request is still in progress."),
    "IDEMPOTENCY_KEY_REUSED": (409, "Idempotency-Key was used with different registration data."),
    "RATE_LIMITED": (429, "Too many registration requests."),
    "WALLET_UNAVAILABLE": (503, "Wallet reservation is unavailable."),
    "SIGNUP_UNAVAILABLE": (503, "Merchant registration is unavailable."),
}


def error_response(code: str, *, retry_after: int | None = None) -> JSONResponse:
    status, message = ERRORS[code]
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}, headers=headers
    )


class SignupRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                if any(
                    item["loc"] == ("header", "Idempotency-Key") and item["type"] == "missing"
                    for item in error.errors()
                ):
                    return error_response("IDEMPOTENCY_KEY_REQUIRED")
                return error_response("VALIDATION_FAILED")
            except SignupFailure as error:
                return error_response(error.code)
            except ValidationError:
                return error_response("SIGNUP_UNAVAILABLE")

        return handle


class SignupRateLimit:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._minute = -1
        self._counts: dict[str, int] = {}
        self._lock = Lock()

    def retry_after(self, address: str) -> int | None:
        with self._lock:
            minute, seconds = divmod(int(monotonic()), 60)
            if minute != self._minute:
                self._counts.clear()
                self._minute = minute
            count = self._counts.get(address, 0)
            if count >= self.per_minute or (count == 0 and len(self._counts) >= 4096):
                return 60 - seconds
            self._counts[address] = count + 1
            return None


def create_router(wallets: WalletProvider | None, per_minute: int) -> APIRouter:
    router = APIRouter(prefix="/v1/merchants", tags=["merchants"], route_class=SignupRoute)
    limiter = SignupRateLimit(per_minute)
    credentials = SignupCredentials()

    @router.post(
        "",
        operation_id="createMerchant",
        status_code=201,
        response_model=MerchantCreated,
        responses={
            201: {"headers": {"Location": {"schema": {"type": "string"}}}},
            400: {"description": "Invalid input or missing idempotency key."},
            409: {"description": "Email or idempotency conflict."},
            429: {
                "description": "Registration rate limit reached.",
                "headers": {"Retry-After": {"schema": {"type": "integer"}}},
            },
            503: {"description": "Wallet reservation or signup storage is unavailable."},
            "4XX": {"description": "Client error."},
        },
    )
    def signup(
        request: Request,
        body: CreateMerchant,
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=255)],
    ) -> Response:
        retry_after = limiter.retry_after(request.client.host if request.client else "")
        if retry_after is not None:
            return error_response("RATE_LIMITED", retry_after=retry_after)
        store = SqlMerchantSignup(request.app.state.database.engine, credentials, wallets)
        saved = store.admit(
            key,
            Signup(
                name=normalize("NFC", body.name),
                email=body.admin.email,
                email_normalized=normalize("NFC", body.admin.email).lower(),
                display_name=normalize("NFC", body.admin.displayName),
                password=body.admin.password.get_secret_value(),
            ),
        )
        created = MerchantCreated.model_validate_json(saved)
        return Response(
            saved,
            media_type="application/json",
            status_code=201,
            headers={
                "Location": f"/v1/merchants/{created.merchantId}",
                "Cache-Control": "no-store",
            },
        )

    return router
