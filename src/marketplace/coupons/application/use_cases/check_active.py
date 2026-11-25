from marketplace.coupons.application.interfaces.activity import CouponActivityReader
from marketplace.coupons.application.interfaces.coupons import CouponDeploymentResolver
from marketplace.coupons.domain.entities.type_id import CouponTypeId


class CheckCouponActive:
    def __init__(
        self, deployments: CouponDeploymentResolver, activity: CouponActivityReader
    ) -> None:
        self.deployments = deployments
        self.activity = activity

    def execute(self, type_id: int) -> bool:
        coupon_type = CouponTypeId(type_id)
        deployment = self.deployments.resolve(coupon_type.value)
        return self.activity.is_active(deployment, coupon_type)
