from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Literal

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from marketplace.coupons.application.exceptions import (
    CouponTypeNotFound,
    DuplicateCouponTypeName,
    IdempotencyKeyConflict,
)
from marketplace.coupons.application.use_cases import CouponTypeRegistry
from marketplace.coupons.infrastructure.repository import SqlCouponTypeRepository
from marketplace.coupons.presentation.router import create_router as create_coupons_router
from marketplace.infrastructure.database import Database
from marketplace.listings.application.exceptions import (
    ListingNotFound,
    ProjectionUnavailable,
)
from marketplace.listings.application.use_cases import Catalog
from marketplace.listings.infrastructure.repository import SqlListingReader
from marketplace.listings.presentation.router import create_router as create_listings_router
from marketplace.logging_config import configure_logging
from marketplace.settings import ApiSettings, load_settings


class HealthResponse(BaseModel):
    ok: Literal[True] = True
    role: Literal["api"] = "api"


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    config = settings if settings is not None else load_settings("api")
    logger = configure_logging("api", config.log_level)

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
        description="Read-only marketplace projections. Transaction submission is not enabled.",
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

    @app.exception_handler(CouponTypeNotFound)
    async def coupon_type_not_found(request: Request, error: CouponTypeNotFound) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "coupon_type_not_found", "message": "Coupon type not found."}},
            status_code=404,
        )

    @app.exception_handler(DuplicateCouponTypeName)
    async def duplicate_coupon_type_name(
        request: Request, error: DuplicateCouponTypeName
    ) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": "duplicate_coupon_type_name",
                    "message": "A coupon type with this name already exists.",
                }
            },
            status_code=409,
        )

    @app.exception_handler(IdempotencyKeyConflict)
    async def idempotency_key_conflict(
        request: Request, error: IdempotencyKeyConflict
    ) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": "idempotency_key_conflict",
                    "message": "This idempotency key was already used with a different request.",
                }
            },
            status_code=409,
        )

    def provide_catalog(request: Request) -> Iterator[Catalog]:
        with request.app.state.database.sessions() as session:
            try:
                yield Catalog(SqlListingReader(session))
            except SQLAlchemyError:
                raise ProjectionUnavailable from None

    app.include_router(create_listings_router(provide_catalog))

    if config.coupon_types_enabled:

        def provide_coupon_registry(request: Request) -> Iterator[CouponTypeRegistry]:
            with request.app.state.database.sessions() as session:
                yield CouponTypeRegistry(SqlCouponTypeRepository(session))

        app.include_router(create_coupons_router(provide_coupon_registry))

    return app


def main() -> None:
    settings = load_settings("api")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)
