from dataclasses import dataclass
from typing import Literal

LoginErrorCode = Literal[
    "INVALID_CREDENTIALS", "ACCOUNT_LOCKED", "RATE_LIMITED", "AUTH_UNAVAILABLE", "VALIDATION_FAILED"
]


@dataclass(frozen=True)
class LoginRefusal:
    code: LoginErrorCode
    retry_after: int | None = None


class LoginFailure(Exception):
    def __init__(self, refusal: LoginRefusal) -> None:
        super().__init__(refusal.code)
        self.refusal = refusal
