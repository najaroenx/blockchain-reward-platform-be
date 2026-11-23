from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from marketplace.listings.application.use_cases import Catalog
from marketplace.listings.domain.entities import Marketplace
from marketplace.listings.presentation.schemas import (
    Address,
    DetailResponse,
    ListingResponse,
    PageResponse,
    Uint256,
)


def create_router(catalog_provider: Callable[[Request], Iterator[Catalog]]) -> APIRouter:
    router = APIRouter(prefix="/api/v1/marketplaces", tags=["marketplace catalog"])

    @router.get("/{chain_id}/{marketplace_address}/listings", response_model=PageResponse)
    def active_listings(
        chain_id: Annotated[int, Path(gt=0, le=2**63 - 1)],
        marketplace_address: Address,
        catalog: Annotated[Catalog, Depends(catalog_provider)],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> PageResponse:
        result = catalog.active(
            Marketplace(chain_id, marketplace_address), limit=limit, offset=offset
        )
        return PageResponse(
            items=[ListingResponse.from_domain(item) for item in result.items],
            total=result.total,
            limit=limit,
            offset=offset,
            indexed_block=result.indexed_block,
        )

    @router.get(
        "/{chain_id}/{marketplace_address}/listings/{listing_id}", response_model=DetailResponse
    )
    def listing_detail(
        chain_id: Annotated[int, Path(gt=0, le=2**63 - 1)],
        marketplace_address: Address,
        listing_id: Uint256,
        catalog: Annotated[Catalog, Depends(catalog_provider)],
    ) -> DetailResponse:
        listing, block = catalog.get(Marketplace(chain_id, marketplace_address), int(listing_id))
        return DetailResponse(listing=ListingResponse.from_domain(listing), indexed_block=block)

    return router
