from typing import Protocol

from marketplace.coupons.application.dto import CreateCouponType
from marketplace.coupons.domain.entities import CouponType


class CouponTypeRepository(Protocol):
    def create_or_replay(
        self, data: CreateCouponType, *, idempotency_key: str, request_fingerprint: str
    ) -> CouponType:
        """Inserts a coupon type, or replays the result of an earlier identical request.

        Raises DuplicateCouponTypeName when `data.name` belongs to a different coupon type,
        and IdempotencyKeyConflict when `idempotency_key` was already used with a different
        request body.
        """
        ...

    def find(self, coupon_type_id: str) -> CouponType | None: ...

    def list(self, *, limit: int, offset: int) -> tuple[tuple[CouponType, ...], int]: ...
