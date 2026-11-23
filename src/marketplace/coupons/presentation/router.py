from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, status

from marketplace.coupons.application.dto import CreateCouponType
from marketplace.coupons.application.use_cases import CouponTypeRegistry
from marketplace.coupons.presentation.schemas import (
    CouponTypeResponse,
    CreateCouponTypeRequest,
    PageResponse,
)


def create_router(
    registry_provider: Callable[[Request], Iterator[CouponTypeRegistry]],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/coupon-types", tags=["coupon types"])

    @router.post("", response_model=CouponTypeResponse, status_code=status.HTTP_201_CREATED)
    def create_coupon_type(
        body: CreateCouponTypeRequest,
        registry: Annotated[CouponTypeRegistry, Depends(registry_provider)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
    ) -> CouponTypeResponse:
        coupon_type = registry.create(
            CreateCouponType(
                name=body.name,
                start_date=body.start_date,
                expire_date=body.expire_date,
                face_value=body.face_value,
                total_supply=body.total_supply,
            ),
            idempotency_key=idempotency_key,
        )
        return CouponTypeResponse.from_domain(coupon_type)

    @router.get("", response_model=PageResponse)
    def list_coupon_types(
        registry: Annotated[CouponTypeRegistry, Depends(registry_provider)],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> PageResponse:
        page = registry.list(limit=limit, offset=offset)
        return PageResponse(
            items=[CouponTypeResponse.from_domain(item) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )

    @router.get("/{coupon_type_id}", response_model=CouponTypeResponse)
    def get_coupon_type(
        coupon_type_id: Annotated[str, Path(min_length=1)],
        registry: Annotated[CouponTypeRegistry, Depends(registry_provider)],
    ) -> CouponTypeResponse:
        return CouponTypeResponse.from_domain(registry.get(coupon_type_id))

    return router
