from enum import StrEnum


# Defines stable failure codes that can be translated into deposit API responses.
class DepositError(StrEnum):
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    IDEMPOTENCY_KEY_IN_PROGRESS = "IDEMPOTENCY_KEY_IN_PROGRESS"
    DEPOSIT_UNAVAILABLE = "DEPOSIT_UNAVAILABLE"
    PAYMENT_UNAVAILABLE = "PAYMENT_UNAVAILABLE"


# Carries an expected deposit error code from the use case or repository to the route.
class DepositFailure(Exception):
    def __init__(self, code: DepositError) -> None:
        self.code = code
        super().__init__(code.value)


# Signals that the requested merchant has no matching deposit.
class DepositNotFound(Exception):
    pass
