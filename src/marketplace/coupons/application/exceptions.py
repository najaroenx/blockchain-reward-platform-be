from enum import StrEnum


# Signals a name conflict when creating a coupon type draft.
class DuplicateCouponTypeName(Exception):
    pass


# Signals that a coupon draft key was reused for different request data.
class IdempotencyKeyConflict(Exception):
    pass


# Signals a lookup for a coupon type draft that does not exist.
class CouponTypeNotFound(Exception):
    pass


# Defines the stable failure codes shared by redeem use cases, adapters, and HTTP.
class RedeemErrorCode(StrEnum):
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    WALLET_FORBIDDEN = "WALLET_FORBIDDEN"
    COUPON_TYPE_NOT_FOUND = "COUPON_TYPE_NOT_FOUND"
    IDEMPOTENCY_KEY_IN_PROGRESS = "IDEMPOTENCY_KEY_IN_PROGRESS"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    COUPON_NOT_STARTED = "COUPON_NOT_STARTED"
    COUPON_EXPIRED = "COUPON_EXPIRED"
    NO_REDEEM_CODE_AVAILABLE = "NO_REDEEM_CODE_AVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    REDEEM_UNAVAILABLE = "REDEEM_UNAVAILABLE"
    SIGNER_UNAUTHORIZED = "SIGNER_UNAUTHORIZED"
    CONTRACT_CALL_FAILED = "CONTRACT_CALL_FAILED"
    ADMISSION_UNAVAILABLE = "ADMISSION_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class RedeemFailure(Exception):
    """Normalized adapter failure; raw infrastructure details stay outside the API."""

    def __init__(self, code: RedeemErrorCode, *, retry_after: int | None = None) -> None:
        super().__init__(code.value)
        self.code = code
        self.retry_after = retry_after
