from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Literal

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from marketplace.coupons.application.dto import AuthenticatedWallet
from marketplace.coupons.infrastructure.mock_auth import MockTokenAuthenticator
from marketplace.coupons.presentation.router import (
    RedeemDependencies,
)
from marketplace.coupons.presentation.router import (
    create_router as create_redeem_router,
)
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
    settings: ApiSettings | None = None, *, redeem: RedeemDependencies | None = None
) -> FastAPI:
    config = settings if settings is not None else load_settings("api")
    logger = configure_logging("api", config.log_level)
    if redeem is None and config.mock_auth_enabled:
        redeem = RedeemDependencies(
            authenticate=MockTokenAuthenticator(
                config.mock_auth_token.get_secret_value(),
                AuthenticatedWallet(
                    user_id=config.mock_auth_user_id,
                    wallet_address=config.mock_auth_wallet_address,
                ),
            )
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.database = Database(config.database_url.get_secret_value())
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
            "Marketplace projections and coupon admission. "
            "Redeem admission requires configured adapters; transaction submission is not enabled."
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
    app.include_router(create_redeem_router(redeem))
    return app


def main() -> None:
    settings = load_settings("api")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)
