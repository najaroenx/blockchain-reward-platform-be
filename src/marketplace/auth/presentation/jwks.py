from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from marketplace.auth.application.ports import SigningKeyPublisher
from marketplace.auth.presentation.router import AuthRoute
from marketplace.auth.presentation.schemas import ErrorResponse


class PublicJwk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kid: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    x: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    y: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    kty: Literal["EC"] = "EC"
    crv: Literal["P-256"] = "P-256"
    alg: Literal["ES256"] = "ES256"
    use: Literal["sig"] = "sig"


class JwksResponse(BaseModel):
    keys: list[PublicJwk]


def create_router(provider: Callable[[Request], SigningKeyPublisher]) -> APIRouter:
    router = APIRouter(tags=["authentication"], route_class=AuthRoute)

    @router.get(
        "/.well-known/jwks.json",
        operation_id="signingPublicKeys",
        response_model=JwksResponse,
        responses={503: {"model": ErrorResponse}},
        description="Public ES256 verification keys. Contains no private key material.",
    )
    def public_keys(
        response: Response,
        publisher: Annotated[SigningKeyPublisher, Depends(provider)],
    ) -> JwksResponse:
        keys = publisher.public_keys()
        response.headers["Cache-Control"] = "public, max-age=60"
        return JwksResponse(keys=[PublicJwk.model_validate(asdict(key)) for key in keys])

    return router
