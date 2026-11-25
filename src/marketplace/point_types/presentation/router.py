from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from marketplace.identity import MerchantIdentity
from marketplace.point_types.application.use_cases.create import CreatePointTypeRequest
from marketplace.point_types.domain.entities.point_type import PointTypeDraft
from marketplace.point_types.domain.exceptions import PointTypeFailure
from marketplace.point_types.presentation.schemas import (
    CreatePointType,
    ErrorResponse,
    PointTypeRequested,
)

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


# Passes validated HTTP input to the point type use case and maps expected failures to responses.
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


def create_router(
    authenticate: Callable[[str], MerchantIdentity | None] | None,
    provide_create: Callable[[Request], CreatePointTypeRequest],
) -> APIRouter:
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
        body: CreatePointType,
        use_case: Annotated[CreatePointTypeRequest, Depends(provide_create)],
        identity: Annotated[MerchantIdentity, Depends(merchant)],
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=255)],
    ) -> Response:
        operation_id, response = use_case.execute(
            identity=identity, key=key, draft=PointTypeDraft(body.name, body.symbol, body.decimals)
        )
        return Response(
            response,
            status_code=202,
            media_type="application/json",
            headers={"Location": f"/v1/operations/{operation_id}", "Cache-Control": "no-store"},
        )

    return router
