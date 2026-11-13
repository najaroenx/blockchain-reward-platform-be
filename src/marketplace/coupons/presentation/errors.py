from dataclasses import dataclass
from typing import Any

from fastapi.responses import JSONResponse

from marketplace.coupons.application.exceptions import RedeemErrorCode
from marketplace.coupons.presentation.schemas import RedeemError, RedeemErrorResponse


@dataclass(frozen=True)
class ErrorSpec:
    status: int
    message: str


ERRORS = {
    RedeemErrorCode.IDEMPOTENCY_KEY_REQUIRED: ErrorSpec(400, "Idempotency-Key is required."),
    RedeemErrorCode.VALIDATION_FAILED: ErrorSpec(400, "Invalid redeem request."),
    RedeemErrorCode.UNAUTHENTICATED: ErrorSpec(401, "A valid access token is required."),
    RedeemErrorCode.WALLET_FORBIDDEN: ErrorSpec(
        403, "No authorized wallet is available for this user."
    ),
    RedeemErrorCode.COUPON_TYPE_NOT_FOUND: ErrorSpec(404, "Coupon type was not found."),
    RedeemErrorCode.IDEMPOTENCY_KEY_IN_PROGRESS: ErrorSpec(
        409, "This request is still in progress."
    ),
    RedeemErrorCode.IDEMPOTENCY_KEY_REUSED: ErrorSpec(
        409, "Idempotency-Key was used for another request."
    ),
    RedeemErrorCode.INSUFFICIENT_BALANCE: ErrorSpec(422, "Coupon balance is insufficient."),
    RedeemErrorCode.COUPON_NOT_STARTED: ErrorSpec(422, "Coupon redemption has not started."),
    RedeemErrorCode.COUPON_EXPIRED: ErrorSpec(422, "Coupon has expired."),
    RedeemErrorCode.NO_REDEEM_CODE_AVAILABLE: ErrorSpec(422, "No redeem code is available."),
    RedeemErrorCode.RATE_LIMITED: ErrorSpec(429, "Too many requests."),
    RedeemErrorCode.REDEEM_UNAVAILABLE: ErrorSpec(
        503, "Redeem admission adapters are not configured."
    ),
    RedeemErrorCode.SIGNER_UNAUTHORIZED: ErrorSpec(503, "The backend signer is not authorized."),
    RedeemErrorCode.CONTRACT_CALL_FAILED: ErrorSpec(
        503, "The contract call could not be completed."
    ),
    RedeemErrorCode.ADMISSION_UNAVAILABLE: ErrorSpec(503, "Admission storage is unavailable."),
    RedeemErrorCode.INTERNAL_ERROR: ErrorSpec(500, "The request could not be completed."),
}


def error_response(code: RedeemErrorCode, *, retry_after: int | None = None) -> JSONResponse:
    spec = ERRORS[code]
    headers = {}
    if spec.status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if code == RedeemErrorCode.RATE_LIMITED and retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=spec.status,
        content=RedeemErrorResponse(error=RedeemError(code=code, message=spec.message)).model_dump(
            mode="json"
        ),
        headers=headers,
    )


def openapi_error_responses() -> dict[int | str, dict[str, Any]]:
    responses: dict[int | str, dict[str, Any]] = {}
    for code, spec in ERRORS.items():
        response = responses.setdefault(
            spec.status,
            {
                "model": RedeemErrorResponse,
                "description": (
                    "Contract business rejection before admission; input validation uses HTTP 400."
                    if spec.status == 422
                    else "Redeem request error."
                ),
                "content": {"application/json": {"examples": {}}},
            },
        )
        response["content"]["application/json"]["examples"][code.value] = {
            "value": {"error": {"code": code.value, "message": spec.message}}
        }
    responses[401]["headers"] = {
        "WWW-Authenticate": {"schema": {"type": "string"}, "description": "Bearer challenge."}
    }
    responses[429]["headers"] = {
        "Retry-After": {
            "schema": {"type": "string"},
            "description": "Optional delay in seconds supplied by the rate limiter.",
        }
    }
    return responses
