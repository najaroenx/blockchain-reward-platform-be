from marketplace.coupons.application.dto import (
    RedeemCouponCommand,
    RedeemCouponPayload,
    RedeemCouponResult,
)
from marketplace.coupons.application.ports import (
    CouponDeploymentResolver,
    RedeemAdmissionStore,
)


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
