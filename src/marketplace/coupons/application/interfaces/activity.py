from abc import ABC, abstractmethod

from marketplace.coupons.application.dto import CouponDeployment
from marketplace.coupons.domain.entities.type_id import CouponTypeId


class CouponActivityUnavailable(Exception):
    """The configured chain did not provide a valid coupon activity result."""


class CouponActivityReader(ABC):
    @abstractmethod
    def is_active(self, deployment: CouponDeployment, type_id: CouponTypeId) -> bool: ...
