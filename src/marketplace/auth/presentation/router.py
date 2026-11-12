from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from marketplace.auth.application.dto import LoginCommand
from marketplace.auth.application.exceptions import LoginFailure, LoginRefusal
from marketplace.auth.application.ports import PasswordVerifier, TokenSigner
from marketplace.auth.application.use_cases import Login
from marketplace.auth.domain.entities import LoginPolicy
from marketplace.auth.presentation.schemas import (
    ErrorDetail,
    ErrorResponse,
    LoginRequest,
    LoginResponse,
    SubjectResponse,
)


@dataclass(frozen=True)
class AuthDependencies:
    hasher: PasswordVerifier
    signer: TokenSigner
    policy: LoginPolicy = LoginPolicy()
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)


ERRORS = {
    "INVALID_CREDENTIALS": (401, "อีเมลหรือรหัสผ่านไม่ถูกต้อง"),
    "ACCOUNT_LOCKED": (423, "บัญชีถูกล็อกชั่วคราว"),
    "RATE_LIMITED": (429, "ส่งคำขอถี่เกินกำหนด"),
    "AUTH_UNAVAILABLE": (503, "ระบบยืนยันตัวตนยังไม่พร้อมใช้งาน"),
    "VALIDATION_FAILED": (400, "ข้อมูลคำขอไม่ถูกต้อง"),
}


def failure_response(refusal: LoginRefusal) -> JSONResponse:
    status, message = ERRORS[refusal.code]
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    if refusal.retry_after is not None:
        headers["Retry-After"] = str(refusal.retry_after)
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(
            error=ErrorDetail(code=refusal.code, message=message, requestId="req_" + uuid4().hex)
        ).model_dump(),
        headers=headers,
    )


class LoginRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except LoginFailure as error:
                return failure_response(error.refusal)
            except RequestValidationError:
                return failure_response(LoginRefusal("VALIDATION_FAILED"))
            except HTTPException as error:
                return failure_response(
                    LoginRefusal(
                        "VALIDATION_FAILED" if error.status_code == 400 else "AUTH_UNAVAILABLE"
                    )
                )
            except Exception:
                return failure_response(LoginRefusal("AUTH_UNAVAILABLE"))

        return handle


def create_router(provider: Callable[[Request], Login]) -> APIRouter:
    router = APIRouter(prefix="/v1/auth", tags=["authentication"], route_class=LoginRoute)

    @router.post(
        "/login",
        operation_id="merchantLogin",
        response_model=LoginResponse,
        responses={
            **{status: {"model": ErrorResponse} for status in (400, 401, 423, 429, 503)},
            "200": {
                "description": "Authenticated. Refresh token is sent only in a cookie.",
                "headers": {
                    "Set-Cookie": {
                        "schema": {"type": "string"},
                        "description": (
                            "refreshToken; HttpOnly; Secure; SameSite=Strict; Path=/v1/auth"
                        ),
                    }
                },
            },
            "default": {
                "model": ErrorResponse,
                "description": "Failures use the login error envelope.",
            },
        },
        description=(
            "Verify merchant-admin credentials. Refresh tokens are sent only in a secure "
            "HttpOnly cookie. Signing requires the #182 TokenSigner adapter."
        ),
    )
    def login(
        body: LoginRequest,
        request: Request,
        response: Response,
        service: Annotated[Login, Depends(provider)],
    ) -> LoginResponse:
        peer = request.client.host if request.client else "unknown-peer"
        user_agent = request.headers.get("user-agent")
        result = service.execute(
            LoginCommand(
                normalized_email=str(body.email),
                password=body.password.get_secret_value(),
                ip_hash=sha256(peer.encode()).digest(),
                user_agent_hash=sha256(user_agent.encode()).digest() if user_agent else None,
            )
        )
        response.set_cookie(
            "refreshToken",
            result.refresh_token,
            max_age=result.refresh_max_age,
            path="/v1/auth",
            secure=True,
            httponly=True,
            samesite="strict",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return LoginResponse(
            accessToken=result.access_token,
            subject=SubjectResponse(
                id=result.subject.id,
                merchantId=result.subject.merchant_id,
                merchantStatus=result.subject.merchant_status,
            ),
        )

    return router
