from enum import StrEnum


class DepositError(StrEnum):
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    IDEMPOTENCY_KEY_IN_PROGRESS = "IDEMPOTENCY_KEY_IN_PROGRESS"
    DEPOSIT_UNAVAILABLE = "DEPOSIT_UNAVAILABLE"
    PAYMENT_UNAVAILABLE = "PAYMENT_UNAVAILABLE"


class DepositFailure(Exception):
    def __init__(self, code: DepositError) -> None:
        self.code = code
        super().__init__(code.value)


class DepositNotFound(Exception):
    pass
