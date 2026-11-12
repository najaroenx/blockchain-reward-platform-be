import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException

from marketplace.coupons.application.dto import AuthenticatedWallet, RedeemCouponCommand
from marketplace.coupons.application.exceptions import RedeemErrorCode, RedeemFailure
from marketplace.coupons.application.use_cases import RedeemCoupons
from marketplace.coupons.presentation.errors import error_response, openapi_error_responses
from marketplace.coupons.presentation.schemas import (
    RedeemCouponRequest,
    RedeemCouponResponse,
)


@dataclass(frozen=True)
class RedeemDependencies:
    """Authentication can be wired before deployment resolution and durable admission."""

    authenticate: Callable[[str], AuthenticatedWallet]
    redeemer: RedeemCoupons | None = None


class RedeemRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                missing_key = any(
                    item["loc"] == ("header", "Idempotency-Key") and item["type"] == "missing"
                    for item in error.errors()
                )
                return error_response(
                    RedeemErrorCode.IDEMPOTENCY_KEY_REQUIRED
                    if missing_key
                    else RedeemErrorCode.VALIDATION_FAILED
                )
            except RedeemFailure as error:
                return error_response(error.code, retry_after=error.retry_after)
            except HTTPException as error:
                return error_response(
                    RedeemErrorCode.VALIDATION_FAILED
                    if error.status_code == 400
                    else RedeemErrorCode.INTERNAL_ERROR
                )
            except Exception as error:
                logging.getLogger("marketplace.coupons").error(
                    "redeem_request_failed", extra={"exception_type": type(error).__name__}
                )
                return error_response(RedeemErrorCode.INTERNAL_ERROR)

        return handle


def create_router(dependencies: RedeemDependencies | None) -> APIRouter:
    router = APIRouter(prefix="/v1/coupons", tags=["coupons"], route_class=RedeemRoute)
    bearer = HTTPBearer(auto_error=False)

    def configured_dependencies() -> RedeemDependencies:
        if dependencies is None:
            raise RedeemFailure(RedeemErrorCode.REDEEM_UNAVAILABLE)
        return dependencies

    def authenticated_wallet(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    ) -> AuthenticatedWallet:
        if credentials is None:
            raise RedeemFailure(RedeemErrorCode.UNAUTHENTICATED)
        return configured_dependencies().authenticate(credentials.credentials)

    def configured_redeemer() -> RedeemCoupons:
        redeemer = configured_dependencies().redeemer
        if redeemer is None:
            raise RedeemFailure(RedeemErrorCode.REDEEM_UNAVAILABLE)
        return redeemer

    @router.post(
        "/redeem",
        status_code=202,
        response_model=RedeemCouponResponse,
        operation_id="redeemCoupon",
        responses=openapi_error_responses(),
        description=(
            "Admit a request for one coupon using a verified user wallet. "
            "PENDING acknowledges committed admission, not redemption on chain. "
            "Returns 503 until verified auth, deployment, and durable admission adapters are wired."
        ),
    )
    def redeem(
        body: RedeemCouponRequest,
        wallet: Annotated[AuthenticatedWallet, Depends(authenticated_wallet)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
        ],
    ) -> RedeemCouponResponse:
        redeemer = configured_redeemer()
        result = redeemer.execute(
            RedeemCouponCommand(
                wallet=wallet, type_id=int(body.typeId), idempotency_key=idempotency_key
            )
        )
        return RedeemCouponResponse(operationId=result.operation_id, typeId=str(result.type_id))

    return router
