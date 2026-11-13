from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from marketplace.auth.application.ports import SigningKeyPublisher
from marketplace.auth.application.refresh import Refresh
from marketplace.auth.application.use_cases import Login
from marketplace.auth.domain.entities import LoginPolicy
from marketplace.auth.infrastructure.passwords import Argon2PasswordVerifier
from marketplace.auth.infrastructure.refresh_repository import SqlRefreshStore
from marketplace.auth.infrastructure.repository import SqlLoginStore
from marketplace.auth.infrastructure.signing import Es256TokenSigner, UnavailableTokenSigner
from marketplace.auth.presentation.jwks import create_router as create_jwks_router
from marketplace.auth.presentation.refresh import create_router as create_refresh_router
from marketplace.auth.presentation.router import AuthDependencies
from marketplace.auth.presentation.router import create_router as create_auth_router
from marketplace.infrastructure.database import Database
from marketplace.listings.application.exceptions import (
    ListingNotFound,
    ProjectionUnavailable,
)
from marketplace.listings.application.use_cases import Catalog
from marketplace.listings.infrastructure.repository import SqlListingReader
from marketplace.listings.presentation.router import create_router
from marketplace.logging_config import configure_logging
from marketplace.settings import ApiSettings, load_settings


class HealthResponse(BaseModel):
    ok: Literal[True] = True
    role: Literal["api"] = "api"


def create_app(
    settings: ApiSettings | None = None, *, auth: AuthDependencies | None = None
) -> FastAPI:
    config = settings if settings is not None else load_settings("api")
    logger = configure_logging("api", config.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if auth is None:
            signing = config.jwt_signing
            signer = (
                Es256TokenSigner(
                    Path(signing.private_key_file.get_secret_value()),
                    issuer=signing.issuer,
                    audience=signing.audience,
                )
                if signing
                else UnavailableTokenSigner()
            )
            dependencies = AuthDependencies(
                Argon2PasswordVerifier(),
                signer,
                policy=LoginPolicy(
                    ip_limit=config.login_ip_limit, ip_window=config.login_ip_window_seconds
                ),
                key_publisher=signer,
            )
        else:
            dependencies = auth
        app.state.signing_keys = dependencies.key_publisher or UnavailableTokenSigner()
        app.state.database = Database(config.database_url.get_secret_value())
        app.state.login = Login(
            SqlLoginStore(app.state.database.sessions),
            dependencies.hasher,
            dependencies.signer,
            policy=dependencies.policy,
            clock=dependencies.clock,
        )
        app.state.refresh = Refresh(
            SqlRefreshStore(app.state.database.sessions),
            dependencies.signer,
            policy=dependencies.policy,
            clock=dependencies.clock,
        )
        logger.info("api_started")
        try:
            yield
        finally:
            app.state.database.close()
            logger.info("api_stopped")

    app = FastAPI(
        title="Blockchain Reward Platform",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Marketplace projections and merchant login. Transaction submission is not enabled."
        ),
    )

    @app.get("/healthz", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/readyz", tags=["health"])
    def ready(request: Request) -> JSONResponse:
        try:
            with request.app.state.database.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse({"ok": False, "database": "unavailable"}, status_code=503)
        return JSONResponse({"ok": True, "database": "ready"})

    @app.exception_handler(ProjectionUnavailable)
    async def projection_unavailable(
        request: Request, error: ProjectionUnavailable
    ) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": "projection_unavailable",
                    "message": "No indexed data is available for this marketplace.",
                }
            },
            status_code=503,
        )

    @app.exception_handler(ListingNotFound)
    async def listing_not_found(request: Request, error: ListingNotFound) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "listing_not_found", "message": "Listing not found."}},
            status_code=404,
        )

    def provide_catalog(request: Request) -> Iterator[Catalog]:
        with request.app.state.database.sessions() as session:
            try:
                yield Catalog(SqlListingReader(session))
            except SQLAlchemyError:
                raise ProjectionUnavailable from None

    app.include_router(create_router(provide_catalog))

    def provide_login(request: Request) -> Login:
        service: Login = request.app.state.login
        return service

    app.include_router(create_auth_router(provide_login))

    def provide_signing_keys(request: Request) -> SigningKeyPublisher:
        publisher: SigningKeyPublisher = request.app.state.signing_keys
        return publisher

    app.include_router(create_jwks_router(provide_signing_keys))

    def provide_refresh(request: Request) -> Refresh:
        service: Refresh = request.app.state.refresh
        return service

    app.include_router(create_refresh_router(provide_refresh))
    return app


def main() -> None:
    settings = load_settings("api")
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=False,
        proxy_headers=False,
    )
