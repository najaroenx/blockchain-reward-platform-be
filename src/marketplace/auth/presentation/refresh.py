import re
from collections.abc import Callable, Coroutine
from hashlib import sha256
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel, Field

from marketplace.auth.application.dto import RefreshCommand
from marketplace.auth.application.exceptions import AuthFailure, AuthRefusal
from marketplace.auth.application.refresh import Refresh
from marketplace.auth.presentation.cookies import clear_refresh_cookie, set_refresh_cookie
from marketplace.auth.presentation.router import AuthRoute
from marketplace.auth.presentation.schemas import ErrorResponse


class RefreshResponse(BaseModel):
    accessToken: str = Field(min_length=1, repr=False)
    tokenType: Literal["Bearer"] = "Bearer"
    expiresIn: Literal[900] = 900


class RefreshRoute(AuthRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            response = await handler(request)
            if response.status_code == 401:
                clear_refresh_cookie(response)
            return response

        return handle


def create_router(provider: Callable[[Request], Refresh]) -> APIRouter:
    router = APIRouter(prefix="/v1/auth", tags=["authentication"], route_class=RefreshRoute)

    @router.post(
        "/refresh",
        operation_id="refreshToken",
        response_model=RefreshResponse,
        description=(
            "Read the refreshToken cookie, rotate it once, and return a signed access token. "
            "Reusing a consumed token revokes its entire family. No bearer token is required."
        ),
        responses={
            **{code: {"model": ErrorResponse} for code in (400, 401, 503)},
            "default": {"model": ErrorResponse},
            200: {
                "headers": {
                    "Set-Cookie": {
                        "schema": {"type": "string"},
                        "description": (
                            "Rotated refreshToken; Secure; HttpOnly; SameSite=Strict; Path=/v1/auth"
                        ),
                    }
                }
            },
        },
    )
    def refresh(
        request: Request,
        response: Response,
        service: Annotated[Refresh, Depends(provider)],
        refresh_token: Annotated[str | None, Cookie(alias="refreshToken")] = None,
    ) -> RefreshResponse:
        if refresh_token is None or re.fullmatch(r"[A-Za-z0-9_-]{43}", refresh_token) is None:
            raise AuthFailure(AuthRefusal("REFRESH_TOKEN_INVALID"))
        peer = request.client.host if request.client else "unknown-peer"
        result = service.execute(
            RefreshCommand(
                token_hash=sha256(refresh_token.encode()).digest(),
                ip_hash=sha256(peer.encode()).digest(),
            )
        )
        set_refresh_cookie(response, result.refresh_token, result.refresh_max_age)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return RefreshResponse(accessToken=result.access_token)

    return router
