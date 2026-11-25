import hashlib
import json

from marketplace.coupons.application.dto import (
    CouponTypePage,
    CreateCouponType,
    RedeemCouponCommand,
    RedeemCouponPayload,
    RedeemCouponResult,
)
from marketplace.coupons.application.exceptions import CouponTypeNotFound
from marketplace.coupons.application.interfaces.coupons import (
    CouponDeploymentResolver,
    CouponTypeRepository,
    RedeemAdmissionStore,
)
from marketplace.coupons.domain.entities.coupon import CouponTypeDraft


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


# Coordinates idempotent creation and paginated reads of coupon type drafts.
class CouponTypeRegistry:
    def __init__(self, coupon_types: CouponTypeRepository) -> None:
        self.coupon_types = coupon_types

    def create(self, data: CreateCouponType, *, idempotency_key: str) -> CouponTypeDraft:
        return self.coupon_types.create_or_replay(
            data, idempotency_key=idempotency_key, request_fingerprint=fingerprint(data)
        )

    def get(self, coupon_type_id: str) -> CouponTypeDraft:
        coupon_type = self.coupon_types.find(coupon_type_id)
        if coupon_type is None:
            raise CouponTypeNotFound
        return coupon_type

    def list(self, *, limit: int, offset: int) -> CouponTypePage:
        items, total = self.coupon_types.list(limit=limit, offset=offset)
        return CouponTypePage(items=items, total=total)


# Builds a redeem payload from a verified wallet and deployment, then submits it for admission.
class RedeemCoupons:
    def __init__(
        self, deployments: CouponDeploymentResolver, admissions: RedeemAdmissionStore
    ) -> None:
        self.deployments = deployments
        self.admissions = admissions

    def execute(self, command: RedeemCouponCommand) -> RedeemCouponResult:
        deployment = self.deployments.resolve(command.type_id)
        payload = RedeemCouponPayload(
            chain_id=deployment.chain_id,
            coupon_contract_address=deployment.contract_address,
            wallet_address=command.wallet.wallet_address,
            type_id=command.type_id,
        )
        return self.admissions.admit(
            user_id=command.wallet.user_id,
            idempotency_key=command.idempotency_key,
            payload=payload,
        )
