import hashlib
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from marketplace.auth.application.dto import LoginResult
from marketplace.auth.application.exceptions import (
    AccountLocked,
    InvalidCredentials,
    RefreshTokenExpired,
    RefreshTokenInvalid,
    RefreshTokenRevoked,
    SessionCompromised,
    TokenVerificationError,
)
from marketplace.auth.application.ports import TokenVerifier
from marketplace.auth.application.use_cases import AuthService, LoginContext
from marketplace.auth.domain.entities import Subject
from marketplace.auth.presentation.schemas import (
    ErrorDetail,
    ErrorResponse,
    LoginRequest,
    LoginResponse,
    RefreshResponse,
    SessionsResponse,
    SessionView,
    SubjectResponse,
)

REFRESH_COOKIE = "refresh_token"
COOKIE_PATH = "/v1/auth"


def _ip_hash(request: Request) -> bytes:
    host = request.client.host if request.client else "unknown"
    return hashlib.sha256(host.encode()).digest()


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, request_id=""))
    return JSONResponse(body.model_dump(), status_code=status_code)


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE, token, httponly=True, secure=True, samesite="strict", path=COOKIE_PATH
    )


def _login_response(result: LoginResult) -> LoginResponse:
    return LoginResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        subject=SubjectResponse(
            kind=result.subject.kind,
            id=result.subject.id,
            merchant_id=result.subject.merchant_id,
            merchant_status=result.merchant_status,
        ),
    )


def create_router(
    service_provider: Callable[[Request], Iterator[AuthService]],
    verifier_provider: Callable[[Request], TokenVerifier],
) -> APIRouter:
    router = APIRouter(prefix=COOKIE_PATH, tags=["auth"])

    def require_subject(
        request: Request, verifier: Annotated[TokenVerifier, Depends(verifier_provider)]
    ) -> tuple[Subject, str]:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        try:
            return verifier.verify(header[len("bearer ") :])
        except TokenVerificationError as error:
            raise HTTPException(status_code=401, detail="invalid or expired token") from error

    @router.post("/login", response_model=None)
    def login(
        body: LoginRequest,
        request: Request,
        response: Response,
        service: Annotated[AuthService, Depends(service_provider)],
    ) -> LoginResponse | JSONResponse:
        ctx = LoginContext(ip_hash=_ip_hash(request))
        try:
            result = service.login(body.email, body.password, ctx)
        except AccountLocked:
            return _error(423, "ACCOUNT_LOCKED", "ลองใหม่อีกครั้งใน 15 นาที")
        except InvalidCredentials:
            return _error(401, "INVALID_CREDENTIALS", "อีเมลหรือรหัสผ่านไม่ถูกต้อง")
        _set_refresh_cookie(response, result.refresh_token)
        return _login_response(result)

    @router.post("/refresh", response_model=None)
    def refresh(
        request: Request,
        response: Response,
        service: Annotated[AuthService, Depends(service_provider)],
        refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
    ) -> RefreshResponse | JSONResponse:
        if refresh_token is None:
            return _error(401, "REFRESH_TOKEN_INVALID", "ไม่พบ token นี้")
        try:
            result = service.refresh(refresh_token, ip_hash=_ip_hash(request))
        except RefreshTokenInvalid:
            return _error(401, "REFRESH_TOKEN_INVALID", "ไม่พบ token นี้")
        except RefreshTokenExpired:
            return _error(401, "REFRESH_TOKEN_EXPIRED", "เกิน 30 วัน")
        except RefreshTokenRevoked:
            return _error(401, "REFRESH_TOKEN_REVOKED", "ถูกเพิกถอนแล้ว")
        except SessionCompromised:
            return _error(
                401,
                "SESSION_COMPROMISED",
                "session นี้ถูกยกเลิกเพื่อความปลอดภัย กรุณาเข้าสู่ระบบใหม่",
            )
        _set_refresh_cookie(response, result.refresh_token)
        return RefreshResponse(access_token=result.access_token, expires_in=result.expires_in)

    @router.post("/logout", status_code=204)
    def logout(
        response: Response,
        service: Annotated[AuthService, Depends(service_provider)],
        subject_session: Annotated[tuple[Subject, str], Depends(require_subject)],
    ) -> None:
        _, session_id = subject_session
        service.logout(session_id)
        response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)

    @router.get("/sessions")
    def list_sessions(
        service: Annotated[AuthService, Depends(service_provider)],
        subject_session: Annotated[tuple[Subject, str], Depends(require_subject)],
    ) -> SessionsResponse:
        subject, session_id = subject_session
        summaries = service.list_sessions(subject.id, current_session_id=session_id)
        return SessionsResponse(
            sessions=[
                SessionView(
                    session_id=s.session_id,
                    current=s.current,
                    created_at=s.created_at,
                    expires_at=s.expires_at,
                )
                for s in summaries
            ]
        )

    @router.delete("/sessions", status_code=204)
    def revoke_all_sessions(
        service: Annotated[AuthService, Depends(service_provider)],
        subject_session: Annotated[tuple[Subject, str], Depends(require_subject)],
    ) -> None:
        subject, _ = subject_session
        service.revoke_all_for(subject.id)

    return router
