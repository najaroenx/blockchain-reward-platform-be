from collections.abc import Callable

from eth_utils.crypto import keccak

from marketplace.coupons.application.dto import CouponDeployment
from marketplace.coupons.application.interfaces.activity import (
    CouponActivityReader,
    CouponActivityUnavailable,
)
from marketplace.coupons.domain.entities.type_id import CouponTypeId

IS_COUPON_ACTIVE = keccak(text="isCouponActive(uint256)")[:4]
ABI_FALSE = bytes(32)
ABI_TRUE = (1).to_bytes(32, "big")

type RpcParams = list[str | dict[str, str]]


class RpcCouponActivityReader(CouponActivityReader):
    """Read from a verified deployment. The RPC callable returns results or raises OSError."""

    def __init__(self, rpc: Callable[[str, RpcParams], object]) -> None:
        self.rpc = rpc

    def is_active(self, deployment: CouponDeployment, type_id: CouponTypeId) -> bool:
        data = "0x" + (IS_COUPON_ACTIVE + type_id.value.to_bytes(32, "big")).hex()
        try:
            chain_id = self.rpc("eth_chainId", [])
            if (
                not isinstance(chain_id, str)
                or not chain_id.startswith("0x")
                or int(chain_id, 16) != deployment.chain_id
            ):
                raise CouponActivityUnavailable("The RPC chain differs from the coupon deployment")
            result = self.rpc(
                "eth_call", [{"to": deployment.contract_address, "data": data}, "latest"]
            )
            if not isinstance(result, str) or len(result) != 66 or not result.startswith("0x"):
                raise CouponActivityUnavailable("The RPC result is not an ABI-encoded boolean")
            word = bytes.fromhex(result[2:])
        except (OSError, ValueError) as error:
            raise CouponActivityUnavailable("The coupon activity call failed") from error

        if word == ABI_TRUE:
            return True
        if word == ABI_FALSE:
            return False
        raise CouponActivityUnavailable("The RPC result is not an ABI-encoded boolean")
