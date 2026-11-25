from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from unicodedata import normalize

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError

from marketplace.merchants.application.use_cases.signup import SignupMerchant
from marketplace.merchants.domain.entities.signup import Signup
from marketplace.merchants.domain.exceptions import SignupFailure, SignupRateLimited
from marketplace.merchants.presentation.schemas import CreateMerchant, MerchantCreated

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
            except SignupRateLimited as error:
                return error_response("RATE_LIMITED", retry_after=error.retry_after)
            except SignupFailure as error:
                return error_response(error.code)
            except ValidationError:
                return error_response("SIGNUP_UNAVAILABLE")

        return handle


def create_router(provide_signup: Callable[[Request], SignupMerchant]) -> APIRouter:
    router = APIRouter(prefix="/v1/merchants", tags=["merchants"], route_class=SignupRoute)

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
        signup_merchant: Annotated[SignupMerchant, Depends(provide_signup)],
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=255)],
    ) -> Response:
        saved = signup_merchant.execute(
            request.client.host if request.client else "",
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
