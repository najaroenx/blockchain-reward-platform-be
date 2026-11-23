from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal
from unicodedata import normalize

from fastapi import APIRouter, Depends, Header, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from marketplace.infrastructure.jwt_auth import MerchantIdentity
from marketplace.point_types.store import POINT_DECIMALS, PointTypeFailure, SqlPointTypes


class CreatePointType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=60)]
    symbol: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Z][A-Z0-9]{1,10}$")]
    decimals: Annotated[int, Field(strict=True, ge=POINT_DECIMALS, le=POINT_DECIMALS)]

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("Name cannot contain a NUL character")
            return normalize("NFC", value).strip()
        return value


class PointTypeRequested(CreatePointType):
    pointTypeId: str
    status: Literal["REQUESTED"]
    operationId: str


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


ERRORS = {
    "VALIDATION_FAILED": (400, "Enter a valid point type name, symbol, and decimals value of 18."),
    "IDEMPOTENCY_KEY_REQUIRED": (400, "Idempotency-Key is required."),
    "IDEMPOTENCY_KEY_INVALID": (400, "Idempotency-Key must be 8 to 255 characters."),
    "UNAUTHENTICATED": (401, "A valid merchant access token is required."),
    "FORBIDDEN": (403, "An enabled administrator of this merchant is required."),
    "MERCHANT_NOT_ACTIVE": (403, "The merchant must be active."),
    "IDEMPOTENCY_KEY_REUSED": (409, "Idempotency-Key was used for a different request."),
    "IDEMPOTENCY_KEY_IN_PROGRESS": (409, "This request is still in progress."),
    "NAME_TAKEN": (409, "This merchant already has a point type with this name."),
    "SYMBOL_TAKEN": (409, "This merchant already has a point type with this symbol."),
    "MERCHANT_WALLET_NOT_READY": (422, "The active merchant does not have a ready wallet."),
    "POINT_TYPE_UNAVAILABLE": (503, "Point type admission is unavailable."),
}


def _error_response(code: str) -> JSONResponse:
    status, message = ERRORS[code]
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status, headers=headers
    )


class PointTypeRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                for item in error.errors():
                    if item["loc"] == ("header", "Idempotency-Key"):
                        return _error_response(
                            "IDEMPOTENCY_KEY_REQUIRED"
                            if item["type"] == "missing"
                            else "IDEMPOTENCY_KEY_INVALID"
                        )
                return _error_response("VALIDATION_FAILED")
            except PointTypeFailure as error:
                return _error_response(error.code)

        return handle


def create_router(authenticate: Callable[[str], MerchantIdentity | None] | None) -> APIRouter:
    router = APIRouter(prefix="/v1/point-types", tags=["point types"], route_class=PointTypeRoute)
    bearer = HTTPBearer(auto_error=False)

    def merchant(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    ) -> MerchantIdentity:
        if credentials is None:
            raise PointTypeFailure("UNAUTHENTICATED")
        if authenticate is None:
            raise PointTypeFailure("POINT_TYPE_UNAVAILABLE")
        identity = authenticate(credentials.credentials)
        if identity is None:
            raise PointTypeFailure("UNAUTHENTICATED")
        return identity

    @router.post(
        "",
        status_code=202,
        operation_id="createPointType",
        response_model=PointTypeRequested,
        responses={
            202: {
                "description": "Request queued. Replays return the original acknowledgement.",
                "headers": {
                    "Location": {
                        "schema": {"type": "string"},
                        "description": "Operation status URL.",
                    }
                },
            },
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def create(
        request: Request,
        body: CreatePointType,
        identity: Annotated[MerchantIdentity, Depends(merchant)],
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=255)],
    ) -> Response:
        if not key.strip():
            raise PointTypeFailure("IDEMPOTENCY_KEY_INVALID")
        store = SqlPointTypes(request.app.state.database.engine)
        operation_id, response = store.create(
            identity=identity, key=key, name=body.name, symbol=body.symbol
        )
        return Response(
            response,
            status_code=202,
            media_type="application/json",
            headers={"Location": f"/v1/operations/{operation_id}", "Cache-Control": "no-store"},
        )

    return router
