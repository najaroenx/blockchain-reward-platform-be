from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from re import fullmatch
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from marketplace.deposits.application.use_cases.deposits import Deposits
from marketplace.deposits.domain.entities.deposit import DepositAmount
from marketplace.deposits.domain.exceptions import DepositError, DepositFailure, DepositNotFound
from marketplace.deposits.presentation.schemas import (
    ApiError,
    CreateDeposit,
    DepositCreated,
    DepositView,
)

DEPOSIT_ID_PATTERN = r"dep_[A-Za-z0-9]{6,}"


ERRORS = {
    "VALIDATION_FAILED": (400, "Enter a canonical amount from 2000 to 5000000 satang."),
    "IDEMPOTENCY_KEY_REQUIRED": (400, "Idempotency-Key is required."),
    "IDEMPOTENCY_KEY_INVALID": (400, "Idempotency-Key must be 8 to 255 characters."),
    "UNAUTHENTICATED": (401, "A valid merchant access token is required."),
    "DEPOSIT_NOT_FOUND": (404, "Deposit was not found."),
    "IDEMPOTENCY_KEY_REUSED": (409, "Idempotency-Key was used for a different amount."),
    "IDEMPOTENCY_KEY_IN_PROGRESS": (409, "This request is still in progress."),
    "DEPOSIT_UNAVAILABLE": (503, "Deposit service is unavailable."),
    "PAYMENT_UNAVAILABLE": (503, "Payment instructions are unavailable."),
}


# Carries deposit endpoint failures such as missing credentials to the error handler.
class RouteFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def error_response(code: str) -> JSONResponse:
    status, message = ERRORS[code]
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}, headers=headers
    )


# Maps invalid requests and deposit failures into the public HTTP error envelope.
class DepositRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                for item in error.errors():
                    if item["loc"] == ("header", "Idempotency-Key"):
                        return error_response(
                            "IDEMPOTENCY_KEY_REQUIRED"
                            if item["type"] == "missing"
                            else "IDEMPOTENCY_KEY_INVALID"
                        )
                return error_response("VALIDATION_FAILED")
            except DepositNotFound:
                return error_response("DEPOSIT_NOT_FOUND")
            except (RouteFailure, DepositFailure) as error:
                return error_response(error.code)

        return handle


def create_router(
    authenticate: Callable[[str], str | None] | None,
    provide_deposits: Callable[[Request], Deposits],
) -> APIRouter:
    router = APIRouter(prefix="/v1/deposits", tags=["deposits"], route_class=DepositRoute)
    bearer = HTTPBearer(auto_error=False)

    def merchant(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    ) -> str:
        if credentials is None:
            raise RouteFailure("UNAUTHENTICATED")
        if authenticate is None:
            raise RouteFailure(DepositError.DEPOSIT_UNAVAILABLE)
        merchant_id = authenticate(credentials.credentials)
        if merchant_id is None:
            raise RouteFailure("UNAUTHENTICATED")
        return merchant_id

    @router.post(
        "",
        status_code=201,
        operation_id="createDeposit",
        response_model=DepositCreated,
        responses={
            200: {
                "model": DepositCreated,
                "description": "Replay of the saved payment instructions.",
            },
            400: {"model": ApiError},
            401: {"model": ApiError},
            409: {"model": ApiError},
            503: {"model": ApiError},
            "4XX": {"model": ApiError},
        },
    )
    def create(
        body: CreateDeposit,
        merchant_id: Annotated[str, Depends(merchant)],
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=255)],
        deposits: Annotated[Deposits, Depends(provide_deposits)],
    ) -> Response:
        response, replay = deposits.create(
            merchant_id=merchant_id,
            key=key,
            amount=DepositAmount(int(body.amountSatang)),
        )
        DepositCreated.model_validate_json(response)
        return Response(response, status_code=200 if replay else 201, media_type="application/json")

    @router.get(
        "/{depositId}",
        operation_id="getDeposit",
        response_model=DepositView,
        responses={
            401: {"model": ApiError},
            404: {"model": ApiError},
            503: {"model": ApiError},
            "4XX": {"model": ApiError},
        },
    )
    def get(
        depositId: str,
        merchant_id: Annotated[str, Depends(merchant)],
        deposits: Annotated[Deposits, Depends(provide_deposits)],
    ) -> DepositView:
        if not fullmatch(DEPOSIT_ID_PATTERN, depositId):
            raise RouteFailure("DEPOSIT_NOT_FOUND")
        deposit = deposits.get(merchant_id=merchant_id, deposit_id=depositId)
        return DepositView(
            depositId=deposit.id,
            amountSatang=str(deposit.amount_satang),
            amountBase=deposit.amount_base,
            status=deposit.status_at(datetime.now(UTC)),
            createdAt=deposit.created_at,
        )

    return router
