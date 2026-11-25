import pytest

from marketplace.coupons.application.dto import CouponDeployment
from marketplace.coupons.application.interfaces.activity import CouponActivityUnavailable
from marketplace.coupons.application.interfaces.coupons import CouponDeploymentResolver
from marketplace.coupons.application.use_cases.check_active import CheckCouponActive
from marketplace.coupons.infrastructure.repositories.activity import (
    RpcCouponActivityReader,
    RpcParams,
)


class FixedCouponDeployment(CouponDeploymentResolver):
    def resolve(self, type_id: int) -> CouponDeployment:
        return CouponDeployment(1337, "0x" + "ab" * 20)


def test_check_coupon_active_rejects_an_out_of_range_type_id() -> None:
    def rpc(method: str, params: RpcParams) -> object:
        pytest.fail("An invalid type ID must not trigger an RPC call")

    check = CheckCouponActive(FixedCouponDeployment(), RpcCouponActivityReader(rpc))

    with pytest.raises(ValueError, match="uint256"):
        check.execute(2**256)


def test_check_coupon_active_rejects_the_wrong_chain_before_reading_the_contract() -> None:
    def rpc(method: str, params: RpcParams) -> object:
        assert method == "eth_chainId"
        return "0x1"

    check = CheckCouponActive(FixedCouponDeployment(), RpcCouponActivityReader(rpc))

    with pytest.raises(CouponActivityUnavailable, match="RPC chain"):
        check.execute(1)


def test_check_coupon_active_reports_unavailable_for_an_invalid_contract_response() -> None:
    def rpc(method: str, params: RpcParams) -> object:
        return "0x539" if method == "eth_chainId" else "0x" + (2).to_bytes(32, "big").hex()

    check = CheckCouponActive(FixedCouponDeployment(), RpcCouponActivityReader(rpc))

    with pytest.raises(CouponActivityUnavailable, match="ABI-encoded boolean"):
        check.execute(1)
