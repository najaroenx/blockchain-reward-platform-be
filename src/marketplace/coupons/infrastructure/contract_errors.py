from typing import Literal

from marketplace.coupons.application.exceptions import RedeemErrorCode

COUPON_ERRORS = {
    "CouponTypeNotFound": RedeemErrorCode.COUPON_TYPE_NOT_FOUND,
    "InsufficientBalance": RedeemErrorCode.INSUFFICIENT_BALANCE,
    "CouponNotStarted": RedeemErrorCode.COUPON_NOT_STARTED,
    "CouponExpired": RedeemErrorCode.COUPON_EXPIRED,
    "NoRedeemCodeAvailable": RedeemErrorCode.NO_REDEEM_CODE_AVAILABLE,
}


def classify_redeem_revert(
    error_name: str,
    *,
    origin: Literal["coupon", "callback", "unknown"],
    reason: str | None = None,
) -> RedeemErrorCode:
    """Classify an ABI-decoded failure with independently established origin.

    Coupon and Vault share InsufficientBalance(). An RPC adapter must not infer
    origin from the selector or the transaction destination. This helper neither
    decodes RPC payloads nor verifies a deployment. It targets backend signing.
    """
    if origin != "coupon":
        return RedeemErrorCode.CONTRACT_CALL_FAILED
    if error_name == "Error" and reason == "Not authorized to redeem":
        return RedeemErrorCode.SIGNER_UNAUTHORIZED
    return COUPON_ERRORS.get(error_name, RedeemErrorCode.CONTRACT_CALL_FAILED)
