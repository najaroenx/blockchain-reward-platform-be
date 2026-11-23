import hashlib
import json

from marketplace.coupons.application.dto import CouponTypePage, CreateCouponType
from marketplace.coupons.application.exceptions import CouponTypeNotFound
from marketplace.coupons.application.ports import CouponTypeRepository
from marketplace.coupons.domain.entities import CouponType


def fingerprint(data: CreateCouponType) -> str:
    canonical = json.dumps(
        {
            "name": data.name,
            "startDate": data.start_date,
            "expireDate": data.expire_date,
            "faceValue": data.face_value,
            "totalSupply": data.total_supply,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class CouponTypeRegistry:
    def __init__(self, coupon_types: CouponTypeRepository) -> None:
        self.coupon_types = coupon_types

    def create(self, data: CreateCouponType, *, idempotency_key: str) -> CouponType:
        return self.coupon_types.create_or_replay(
            data, idempotency_key=idempotency_key, request_fingerprint=fingerprint(data)
        )

    def get(self, coupon_type_id: str) -> CouponType:
        coupon_type = self.coupon_types.find(coupon_type_id)
        if coupon_type is None:
            raise CouponTypeNotFound
        return coupon_type

    def list(self, *, limit: int, offset: int) -> CouponTypePage:
        items, total = self.coupon_types.list(limit=limit, offset=offset)
        return CouponTypePage(items=items, total=total)
